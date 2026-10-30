from __future__ import annotations

import base64
import inspect
import json
import logging
import os
import re
import tempfile
import wave
from pathlib import Path

import numpy as np

from whisper_dictation import secrets_store
from whisper_dictation.audio import CapturedAudio
from whisper_dictation.providers import CLOUD_PROVIDERS, models_for
from whisper_dictation.settings import AppConfig


_CLOUD_MAX_FILE_BYTES = 25_000_000
_GEMINI_MAX_INLINE_BYTES = 20_000_000


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

        max_chunk_sec = 1500.0
        if len(audio.samples) > int(max_chunk_sec * audio.sample_rate):
            return self._transcribe_chunked(audio, chunk_seconds=max_chunk_sec)

        return self._transcribe_single(audio)

    def _transcribe_chunked(self, audio: CapturedAudio, chunk_seconds: float) -> str:
        samples = audio.samples
        chunk_size = max(1, int(chunk_seconds * audio.sample_rate))
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
                if audio_path.stat().st_size > _CLOUD_MAX_FILE_BYTES:
                    # Retry smaller PCM16 chunks when encoding falls back to WAV.
                    max_samples = (_CLOUD_MAX_FILE_BYTES - 44) // 2
                    if len(audio.samples) > max_samples:
                        return self._transcribe_chunked(audio, max_samples / audio.sample_rate)
                try:
                    return self._transcribe_with_cloud(provider, audio_path, audio.duration_seconds)
                except Exception as exc:
                    self.logger.warning(
                        "%s transcription failed (%s); falling back to faster-whisper",
                        provider,
                        type(exc).__name__,
                    )
            return self._transcribe_with_local_model(audio_path)
        finally:
            audio_path.unlink(missing_ok=True)

    def _write_temp_audio(self, audio: CapturedAudio) -> Path:
        # Prefer lossless WAV for short recordings; check actual bytes before upload.
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

        try:
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
        except Exception:
            mp3_path.unlink(missing_ok=True)
            raise

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
        if audio_path.stat().st_size > _CLOUD_MAX_FILE_BYTES:
            raise RuntimeError("Arquivo de áudio excede o limite de 25 MB")
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

        text = _require_transcript(getattr(transcription, "text", None))
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
            request["languages" if self.config.openai_model == "gpt-transcribe" else "language"] = (
                [language] if self.config.openai_model == "gpt-transcribe" else language
            )
        if self.config.openai_model == "gpt-4o-transcribe-diarize":
            request["chunking_strategy"] = "auto"

        timeout = max(self.config.groq_timeout_seconds, 60.0 + duration_seconds * 0.15)

        try:
            with audio_path.open("rb") as audio_file:
                transcription = client.audio.transcriptions.create(
                    file=audio_file,
                    timeout=timeout,
                    **request,
                )
        finally:
            client.close()
            self._openai_client = None

        text = _require_transcript(getattr(transcription, "text", None))
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
        model = self.config.gemini_model
        modern = model in models_for("gemini")
        dedicated = model == "gemini-3.5-transcribe"
        uploaded = None
        try:
            # Include base64 expansion and JSON/prompt overhead in the inline budget.
            encoded_size = 4 * ((audio_path.stat().st_size + 2) // 3)
            overhead = len(json.dumps({"model": model, "prompt": prompt}).encode("utf-8")) + 2048
            if encoded_size + overhead >= _GEMINI_MAX_INLINE_BYTES:
                uploaded = client.files.upload(file=audio_path, config={"mime_type": mime_type})
                if not uploaded.uri or not uploaded.name:
                    raise RuntimeError("Upload de áudio do Gemini inválido")
                audio_input = {"type": "audio", "uri": uploaded.uri, "mime_type": mime_type}
                audio_part = types.Part.from_uri(file_uri=uploaded.uri, mime_type=mime_type)
            else:
                data = audio_path.read_bytes()
                audio_input = {"type": "audio", "data": base64.b64encode(data).decode("ascii"), "mime_type": mime_type}
                audio_part = types.Part.from_bytes(data=data, mime_type=mime_type)
            if modern:
                request = {"model": model, "input": [audio_input], "store": False}
                if dedicated:
                    # UI languages are pt/en; retain explicit regional custom hints.
                    locale = {"pt": "pt-BR", "en": "en-US"}.get(language, language)
                    request["generation_config"] = {"transcription_config": {
                        "language_codes": [locale] if locale else [],
                        "mode": {"type": "verbatim"},
                    }}
                else:
                    request["input"].insert(0, {"type": "text", "text": prompt})
                response = client.interactions.create(**request)
                if response.status != "completed" or response.errors:
                    raise RuntimeError("Transcrição do Gemini bloqueada ou incompleta")
                if any(getattr(step, "error", None) for step in (response.steps or [])):
                    raise RuntimeError("Transcrição do Gemini bloqueada ou incompleta")
                text = _require_transcript(response.output_text)
            else:
                response = client.models.generate_content(model=model, contents=[prompt, audio_part])
                candidates = response.candidates or []
                if not candidates or any(candidate.finish_reason != types.FinishReason.STOP for candidate in candidates):
                    raise RuntimeError("Transcrição do Gemini bloqueada ou incompleta")
                if response.prompt_feedback and response.prompt_feedback.block_reason:
                    raise RuntimeError("Transcrição do Gemini bloqueada")
                text = _require_transcript(response.text)
        finally:
            try:
                if uploaded is not None and uploaded.name:
                    try:
                        client.files.delete(name=uploaded.name)
                    except Exception as exc:
                        self.logger.warning("Gemini upload cleanup failed (%s)", type(exc).__name__)
            finally:
                client.close()
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


def _require_transcript(value: object) -> str:
    text = _normalize_text(value) if isinstance(value, str) else ""
    if not text:
        raise RuntimeError("Resposta de transcrição vazia")
    return text


def _transcription_language(language: str) -> str | None:
    normalized = language.strip().lower()
    return None if normalized in {"", "auto"} else normalized


def _is_effectively_silent(samples: np.ndarray) -> bool:
    return float(np.max(np.abs(samples))) <= 1e-6
