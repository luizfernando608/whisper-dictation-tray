"""Streaming media import using the bundled PyAV decoder (no external FFmpeg)."""
from __future__ import annotations

import os
from pathlib import Path
import threading
import uuid
from fractions import Fraction
from collections.abc import Callable

import numpy as np


class MediaCancelled(RuntimeError):
    pass


class PCM16WaveWriter:
    """WAV writer promoting automatically to RF64 beyond the RIFF 4 GiB limit."""
    def __init__(self, path: Path | str, rf64: str = "auto"):
        import av
        self._av = av
        self._container = av.open(str(path), "w", format="wav", options={"rf64": rf64})
        self._stream = self._container.add_stream("pcm_s16le", rate=16000)
        self._stream.layout = "mono"
        self._samples = 0

    def __enter__(self):
        return self

    def writeframesraw(self, data: bytes):
        if not data:
            return
        frame = self._av.AudioFrame.from_ndarray(np.frombuffer(data, dtype="<i2").reshape(1, -1), format="s16", layout="mono")
        frame.sample_rate = 16000
        frame.time_base = Fraction(1, 16000)
        frame.pts = self._samples
        self._samples += frame.samples
        for packet in self._stream.encode(frame):
            self._container.mux(packet)

    def __exit__(self, exc_type, exc, traceback):
        try:
            if exc_type is None:
                for packet in self._stream.encode(None):
                    self._container.mux(packet)
        finally:
            self._container.close()


def decode_media(source: Path | str, destination: Path | str,
                 cancel_event: threading.Event | None = None,
                 progress: Callable[[float], None] | None = None) -> float:
    import av

    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source == destination:
        raise ValueError("A importação não pode sobrescrever o arquivo original.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + "." + uuid.uuid4().hex + ".part")
    samples = 0
    try:
        with av.open(str(source)) as container:
            if not container.streams.audio:
                raise ValueError("O arquivo não contém uma faixa de áudio.")
            stream = container.streams.audio[0]
            total = float(stream.duration * stream.time_base) if stream.duration else (container.duration or 0) / av.time_base
            resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
            with PCM16WaveWriter(temporary) as output:
                for frame in container.decode(stream):
                    if cancel_event and cancel_event.is_set():
                        raise MediaCancelled("Importação cancelada.")
                    # Input PTS discontinuities must not make the resampler reject an otherwise valid file.
                    frame.pts = None
                    for converted in resampler.resample(frame):
                        output.writeframesraw(converted.to_ndarray().tobytes())
                        samples += converted.samples
                    if progress:
                        progress(min(0.99, samples / 16000 / total) if total > 0 else 0.0)
                if samples:
                    for converted in resampler.resample(None):
                        output.writeframesraw(converted.to_ndarray().tobytes())
                        samples += converted.samples
            if cancel_event and cancel_event.is_set():
                raise MediaCancelled("Importação cancelada.")
            if not samples:
                raise ValueError("O arquivo não contém áudio decodificável.")
        os.replace(temporary, destination)
        if progress:
            progress(1.0)
        return samples / 16000.0
    finally:
        temporary.unlink(missing_ok=True)
