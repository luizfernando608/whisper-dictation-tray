from __future__ import annotations

import inspect
import logging
import os
import re
import tempfile
import wave
from pathlib import Path

import numpy as np

from whisper_dictation.audio import CapturedAudio
from whisper_dictation.settings import AppConfig


class WhisperTranscriber:
    def __init__(self, config: AppConfig, temp_root: Path, logger: logging.Logger) -> None:
        self.config = config
        self.temp_root = temp_root
        self.logger = logger
        self._groq_client = None
        self._local_model = None

    def transcribe(self, audio: CapturedAudio) -> str:
        if audio.samples.size == 0 or _is_effectively_silent(audio.samples):
            return ""

        wav_path = self._write_temp_wav(audio)
        try:
            if self.config.transcription_provider == "groq":
                try:
                    return self._transcribe_with_groq(wav_path)
                except Exception as exc:
                    self.logger.warning(
                        "Groq transcription failed; falling back to faster-whisper: %s",
                        exc,
                        exc_info=True,
                    )
            return self._transcribe_with_local_model(wav_path)
        finally:
            wav_path.unlink(missing_ok=True)

    def _transcribe_with_groq(self, wav_path: Path) -> str:
        api_key = os.environ.get(self.config.groq_api_key_env)
        if not api_key:
            raise RuntimeError(f"{self.config.groq_api_key_env} is not set")

        client = self._get_groq_client(api_key)
        language = _transcription_language(self.config.language)
        request = {
            "model": self.config.groq_model,
            "response_format": "json",
            "temperature": 0.0,
        }
        if language is not None:
            request["language"] = language

        with wav_path.open("rb") as audio_file:
            transcription = client.audio.transcriptions.create(
                file=audio_file,
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

    def _transcribe_with_local_model(self, wav_path: Path) -> str:
        model = self._get_local_model()
        language = _transcription_language(self.config.language)
        try:
            segments, info = model.transcribe(
                str(wav_path),
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


def _normalize_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    return text


def _transcription_language(language: str) -> str | None:
    normalized = language.strip().lower()
    return None if normalized in {"", "auto"} else normalized


def _is_effectively_silent(samples: np.ndarray) -> bool:
    return float(np.max(np.abs(samples))) <= 1e-6
