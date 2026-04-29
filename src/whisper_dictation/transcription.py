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
        self._model = None

    def transcribe(self, audio: CapturedAudio) -> str:
        if audio.samples.size == 0:
            return ""

        model = self._get_model()
        wav_path = self._write_temp_wav(audio)
        try:
            segments, info = model.transcribe(
                str(wav_path),
                language=self.config.language,
                beam_size=self.config.beam_size,
                condition_on_previous_text=False,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": self.config.vad_min_silence_ms},
            )
            collected = [segment.text.strip() for segment in segments if segment.text.strip()]
            text = _normalize_text(" ".join(collected))
            self.logger.info(
                "Transcription finished language=%s probability=%.3f text_length=%s",
                getattr(info, "language", self.config.language),
                float(getattr(info, "language_probability", 0.0)),
                len(text),
            )
            return text
        finally:
            wav_path.unlink(missing_ok=True)

    def _get_model(self):
        if self._model is not None:
            return self._model

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
        self._model = WhisperModel(self.config.model_size, **kwargs)
        return self._model

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

