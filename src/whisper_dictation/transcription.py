from __future__ import annotations

import inspect
import logging
import os
import re
import tempfile
import wave
from pathlib import Path

import numpy as np

from whisper_dictation import secrets_store
from whisper_dictation.audio import CapturedAudio
from whisper_dictation.providers import CLOUD_PROVIDERS
from whisper_dictation.settings import AppConfig


class WhisperTranscriber:
    def __init__(self, config: AppConfig, temp_root: Path, logger: logging.Logger) -> None:
        self.config = config
        self.temp_root = temp_root
        self.logger = logger
        self._groq_client = None
        self._openai_client = None
        self._gemini_client = None
        self._local_model = None

    def transcribe(self, audio: CapturedAudio) -> str:
        if audio.samples.size == 0 or _is_effectively_silent(audio.samples):
            return ""

        # Chunk any audio longer than 25 minutes (1500s) to safely fit under cloud payload limits
        max_chunk_sec = 1500.0
        if audio.duration_seconds > max_chunk_sec:
            return self._transcribe_chunked(audio, chunk_seconds=max_chunk_sec)

        return self._transcribe_single(audio)

    def _transcribe_chunked(self, audio: CapturedAudio, chunk_seconds: float) -> str:
        samples = audio.samples
        chunk_size = int(chunk_seconds * audio.sample_rate)
        total_samples = len(samples)
        results: list[str] = []

        start = 0
        chunk_idx = 1
        total_chunks = (total_samples + chunk_size - 1) // chunk_size
        self.logger.info(
            "Transcribing long audio in %d chunks (total duration: %.1fs)",
            total_chunks,
            audio.duration_seconds,
        )

        while start < total_samples:
            end = min(start + chunk_size, total_samples)
            chunk_samples = samples[start:end]
            chunk_audio = CapturedAudio(
                samples=chunk_samples,
                sample_rate=audio.sample_rate,
                duration_seconds=len(chunk_samples) / float(audio.sample_rate),
            )
            self.logger.info(
                "Transcribing chunk %d/%d (%.1fs)...",
                chunk_idx,
                total_chunks,
                chunk_audio.duration_seconds,
            )
            chunk_text = self._transcribe_single(chunk_audio)
            if chunk_text:
                results.append(chunk_text)
            start = end
            chunk_idx += 1

        return " ".join(results)

    def _transcribe_single(self, audio: CapturedAudio) -> str:
        audio_path = self._write_temp_audio(audio)
        try:
            provider = self.config.transcription_provider
            if provider in CLOUD_PROVIDERS:
                try:
                    return self._transcribe_with_cloud(provider, audio_path, audio.duration_seconds)
                except Exception as exc:
                    self.logger.warning(
                        "%s transcription failed; falling back to faster-whisper: %s",
                        provider,
                        exc,
                        exc_info=True,
                    )
            return self._transcribe_with_local_model(audio_path)
        finally:
            audio_path.unlink(missing_ok=True)

    def _write_temp_audio(self, audio: CapturedAudio) -> Path:
        # Recordings up to 10 min (<= 600s) are small (< 20MB) in WAV; fast and lossless.
        if audio.duration_seconds <= 600:
            return self._write_temp_wav(audio)
        try:
            return self._write_temp_mp3(audio)
        except Exception:
            self.logger.warning("Falha ao codificar áudio em MP3; usando WAV.", exc_info=True)
            return self._write_temp_wav(audio)

    def _write_temp_mp3(self, audio: CapturedAudio, bit_rate: int = 64000) -> Path:
        import av

        fd, raw_path = tempfile.mkstemp(prefix="dictation_", suffix=".mp3", dir=self.temp_root)
        os.close(fd)
        mp3_path = Path(raw_path)

        out = av.open(str(mp3_path), mode="w", format="mp3")
        try:
            stream = out.add_stream("mp3", rate=audio.sample_rate)
            stream.bit_rate = bit_rate
            stream.layout = "mono"

            frame = av.AudioFrame.from_ndarray(
                audio.samples.reshape(1, -1), format="flt", layout="mono"
            )
            frame.sample_rate = audio.sample_rate

            for packet in stream.encode(frame):
                out.mux(packet)
            for packet in stream.encode(None):
                out.mux(packet)
        finally:
            out.close()

        return mp3_path

    def _write_temp_wav(self, audio: CapturedAudio) -> Path:
        fd, raw_path = tempfile.mkstemp(prefix="dictation_", suffix=".wav", dir=self.temp_root)
        os.close(fd)
        wav_path = Path(raw_path)
        pcm = np.clip(audio.samples, -1.0, 1.0)
        pcm = (pcm * 32767.0).astype(np.int16)

        with wave.open(str(wav_path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(audio.sample_rate)
            wav_file.writeframes(pcm.tobytes())

        return wav_path

    def _transcribe_with_cloud(self, provider: str, audio_path: Path, duration_seconds: float) -> str:
        if provider == "groq":
            return self._transcribe_with_groq(audio_path, duration_seconds)
        if provider == "openai":
            return self._transcribe_with_openai(audio_path, duration_seconds)
        if provider == "gemini":
            return self._transcribe_with_gemini(audio_path, duration_seconds)
        raise ValueError(f"Unknown cloud provider: {provider}")

    def _transcribe_with_groq(self, audio_path: Path, duration_seconds: float = 0.0) -> str:
        api_key = secrets_store.get_api_key("groq", self.config.groq_api_key_env)
        if not api_key:
            raise RuntimeError("Chave de API do Groq não configurada")

        client = self._get_groq_client(api_key)
        language = _transcription_language(self.config.language)
        request = {
            "model": self.config.groq_model,
            "response_format": "json",
            "temperature": 0.0,
        }
        if language is not None:
            request["language"] = language

        timeout = max(self.config.groq_timeout_seconds, 60.0 + duration_seconds * 0.15)

        with audio_path.open("rb") as audio_file:
            transcription = client.audio.transcriptions.create(
                file=audio_file,
                timeout=timeout,
                **request,
            )

        text = _normalize_text(str(getattr(transcription, "text", "") or ""))
        self.logger.info(
            "Groq transcription finished model=%s language=%s text_length=%s",
            self.config.groq_model,
            language or "auto",
            len(text),
        )
        return text

    def _get_groq_client(self, api_key: str):
        if self._groq_client is not None:
            return self._groq_client

        from groq import Groq

        self.logger.info("Initializing Groq transcription client model=%s", self.config.groq_model)
        self._groq_client = Groq(api_key=api_key, timeout=self.config.groq_timeout_seconds)
        return self._groq_client

    def _transcribe_with_openai(self, audio_path: Path, duration_seconds: float = 0.0) -> str:
        api_key = secrets_store.get_api_key("openai")
        if not api_key:
            raise RuntimeError("Chave de API da OpenAI não configurada")

        client = self._get_openai_client(api_key)
        language = _transcription_language(self.config.language)
        request: dict = {
            "model": self.config.openai_model,
            "response_format": "json",
        }
        if language is not None:
            request["language"] = language

        timeout = max(self.config.groq_timeout_seconds, 60.0 + duration_seconds * 0.15)

        with audio_path.open("rb") as audio_file:
            transcription = client.audio.transcriptions.create(
                file=audio_file,
                timeout=timeout,
                **request,
            )

        text = _normalize_text(str(getattr(transcription, "text", "") or ""))
        self.logger.info(
            "OpenAI transcription finished model=%s language=%s text_length=%s",
            self.config.openai_model,
            language or "auto",
            len(text),
        )
        return text

    def _get_openai_client(self, api_key: str):
        if self._openai_client is not None:
            return self._openai_client

        from openai import OpenAI

        self.logger.info("Initializing OpenAI transcription client model=%s", self.config.openai_model)
        self._openai_client = OpenAI(api_key=api_key, timeout=self.config.groq_timeout_seconds)
        return self._openai_client

    def _transcribe_with_gemini(self, audio_path: Path, duration_seconds: float = 0.0) -> str:
        api_key = secrets_store.get_api_key("gemini")
        if not api_key:
            raise RuntimeError("Chave de API do Gemini não configurada")

        client = self._get_gemini_client(api_key, duration_seconds=duration_seconds)
        from google.genai import types

        language = _transcription_language(self.config.language)
        prompt = (
            "Transcreva o áudio a seguir literalmente, palavra por palavra. "
            "Responda somente com o texto transcrito, com pontuação natural, "
            "sem comentários, rótulos ou explicações."
        )
        if language is not None:
            prompt += f" O idioma falado é '{language}'."

        mime_type = "audio/mp3" if audio_path.suffix.lower() == ".mp3" else "audio/wav"
        audio_part = types.Part.from_bytes(
            data=audio_path.read_bytes(),
            mime_type=mime_type,
        )
        response = client.models.generate_content(
            model=self.config.gemini_model,
            contents=[prompt, audio_part],
        )

        text = _normalize_text(str(getattr(response, "text", "") or ""))
        self.logger.info(
            "Gemini transcription finished model=%s language=%s text_length=%s",
            self.config.gemini_model,
            language or "auto",
            len(text),
        )
        return text

    def _get_gemini_client(self, api_key: str, duration_seconds: float = 0.0):
        from google import genai

        timeout_ms = int(max(self.config.groq_timeout_seconds, 60.0 + duration_seconds * 0.15) * 1000)
        return genai.Client(
            api_key=api_key,
            http_options={"timeout": timeout_ms},
        )

    def _transcribe_with_local_model(self, audio_path: Path) -> str:
        model = self._get_local_model()
        language = _transcription_language(self.config.language)
        try:
            segments, info = model.transcribe(
                str(audio_path),
                language=language,
                beam_size=self.config.beam_size,
                condition_on_previous_text=False,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": self.config.vad_min_silence_ms},
            )
            collected = [segment.text.strip() for segment in segments if segment.text.strip()]
            text = _normalize_text(" ".join(collected))
            self.logger.info(
                "Local transcription finished language=%s probability=%.3f text_length=%s",
                getattr(info, "language", language or "auto"),
                float(getattr(info, "language_probability", 0.0)),
                len(text),
            )
            return text
        except Exception:
            self.logger.exception("Local faster-whisper transcription failed")
            raise

    def _get_local_model(self):
        if self._local_model is not None:
            return self._local_model

        if self.config.cpu_threads > 0:
            os.environ["OMP_NUM_THREADS"] = str(self.config.cpu_threads)

        from faster_whisper import WhisperModel

        kwargs = {
            "device": "cpu",
            "compute_type": self.config.compute_type,
        }
        signature = inspect.signature(WhisperModel.__init__)
        if "cpu_threads" in signature.parameters and self.config.cpu_threads > 0:
            kwargs["cpu_threads"] = self.config.cpu_threads

        self.logger.info(
            "Loading Whisper model model_size=%s compute_type=%s cpu_threads=%s",
            self.config.model_size,
            self.config.compute_type,
            self.config.cpu_threads or "<default>",
        )
        self._local_model = WhisperModel(self.config.model_size, **kwargs)
        return self._local_model


def _normalize_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    return text


def _transcription_language(language: str) -> str | None:
    normalized = language.strip().lower()
    return None if normalized in {"", "auto"} else normalized


def _is_effectively_silent(samples: np.ndarray) -> bool:
    return float(np.max(np.abs(samples))) <= 1e-6
