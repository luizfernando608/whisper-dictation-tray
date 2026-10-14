"""Seekable PCM16 reader backed by PyAV, including RF64 files."""
from __future__ import annotations

from pathlib import Path

import av
import numpy as np


class PCM16Reader:
    def __init__(self, path: Path):
        self.container = av.open(str(path))
        if not self.container.streams.audio:
            self.container.close()
            raise ValueError("Arquivo sem áudio.")
        self.stream = self.container.streams.audio[0]
        self.rate = self.stream.codec_context.sample_rate
        self.channels = len(self.stream.codec_context.layout.channels)
        self.frames = round(float(self.stream.duration * self.stream.time_base) * self.rate) if self.stream.duration else round((self.container.duration or 0) / av.time_base * self.rate)
        self.position = 0
        self._iterator = self.container.decode(self.stream)
        self._pending = np.empty((0, self.channels), dtype=np.int16)
        self._resampler = av.AudioResampler(format="s16", layout=self.stream.codec_context.layout, rate=self.rate)

    def getframerate(self):
        return self.rate

    def getnframes(self):
        return self.frames

    def getnchannels(self):
        return self.channels

    def tell(self):
        return self.position

    def setpos(self, position):
        position = max(0, min(self.frames, int(position)))
        self.container.seek(int(position / self.rate / self.stream.time_base), stream=self.stream, backward=True)
        self._iterator = self.container.decode(self.stream)
        self._pending = np.empty((0, self.channels), dtype=np.int16)
        self._resampler = av.AudioResampler(format="s16", layout=self.stream.codec_context.layout, rate=self.rate)
        self.position = position

    def readframes(self, count):
        pieces, remaining = [], count
        while remaining:
            if not len(self._pending):
                try:
                    frame = next(self._iterator)
                except StopIteration:
                    break
                start = round(float(frame.pts * frame.time_base) * self.rate) if frame.pts is not None else self.position
                skip = max(0, self.position - start)
                converted = self._resampler.resample(frame)
                if not converted:
                    continue
                data = np.concatenate([value.to_ndarray().reshape(-1, self.channels) for value in converted])
                self._pending = data[skip:]
                if not len(self._pending):
                    continue
            take = min(remaining, len(self._pending))
            pieces.append(self._pending[:take])
            self._pending = self._pending[take:]
            remaining -= take
            self.position += take
        return np.concatenate(pieces).astype("<i2", copy=False).tobytes() if pieces else b""

    def close(self):
        self.container.close()
