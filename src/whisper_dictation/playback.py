"""Bounded-memory WAV playback, seeking and transcript export helpers."""
from __future__ import annotations

import threading
from pathlib import Path

import numpy as np
import sounddevice as sd
from whisper_dictation.wav_reader import PCM16Reader
from whisper_dictation.transcript_format import speaker_label, timestamp, transcript_text  # noqa: F401  (re-exported)


class WavePlayer:
    def __init__(self):
        self._reader = None
        self._stream = None
        self._lock = threading.RLock()
        self.path: Path | None = None

    def load(self, path: Path) -> None:
        self.close()
        reader = PCM16Reader(path)
        self._reader, self.path = reader, path

    @property
    def duration(self) -> float:
        return self._reader.getnframes() / self._reader.getframerate() if self._reader else 0

    @property
    def position(self) -> float:
        with self._lock:
            return self._reader.tell() / self._reader.getframerate() if self._reader else 0

    @property
    def playing(self) -> bool:
        return bool(self._stream and self._stream.active)

    def seek(self, seconds: float) -> None:
        with self._lock:
            if self._reader:
                self._reader.setpos(min(self._reader.getnframes(), max(0, int(seconds * self._reader.getframerate()))))

    def toggle(self) -> None:
        if not self._reader:
            return
        if self.playing:
            self._stream.stop()
            return
        if self.position >= self.duration:
            self.seek(0)
        if self._stream:
            self._stream.close()
        self._stream = sd.OutputStream(samplerate=self._reader.getframerate(), channels=self._reader.getnchannels(),
                                       dtype="int16", callback=self._callback)
        self._stream.start()

    def _callback(self, outdata, frames, time_info, status) -> None:
        with self._lock:
            channels = self._reader.getnchannels()
            samples = np.frombuffer(self._reader.readframes(frames), dtype=np.int16).reshape(-1, channels)
        outdata.fill(0)
        outdata[:len(samples)] = samples
        if len(samples) < frames:
            raise sd.CallbackStop()

    def close(self) -> None:
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        if self._reader:
            self._reader.close()
            self._reader = None
        self.path = None
