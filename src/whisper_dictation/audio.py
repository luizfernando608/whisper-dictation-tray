from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

import numpy as np
import sounddevice as sd


@dataclass(slots=True)
class CapturedAudio:
    samples: np.ndarray
    sample_rate: int
    duration_seconds: float


# Host APIs preferred for the mic list, best first. WASAPI/MME give one clean
# entry per real microphone; WDM-KS/DirectSound add duplicates and expose
# loopback/output endpoints as "inputs".
_PREFERRED_HOSTAPIS = ("Windows WASAPI", "MME")

# Common non-microphone capture endpoints to hide (loopback / "listen to output").
_EXCLUDED_NAME_HINTS = ("stereo mix", "mixagem est", "what u hear", "wave out mix", "o que você ouve")


def list_input_devices() -> list[tuple[int, str, int]]:
    """List real input microphones, deduplicated and free of driver/loopback noise.

    ``sd.query_devices()`` returns every device across every Windows host API
    (MME, DirectSound, WASAPI, WDM-KS), so the same mic shows up several times and
    WDM-KS even lists speakers/raw driver endpoints as inputs. We restrict to a
    single clean host API and drop obvious non-mic endpoints.
    """
    try:
        devices = sd.query_devices()
    except Exception:
        return []

    preferred = _preferred_hostapi(devices)
    result: list[tuple[int, str, int]] = []
    seen_names: set[str] = set()
    for index, device in enumerate(devices):
        if int(device.get("max_input_channels", 0)) <= 0:
            continue
        if preferred is not None and int(device.get("hostapi", -1)) != preferred:
            continue

        name = str(device.get("name", "Unknown input")).strip()
        lowered = name.lower()
        if any(hint in lowered for hint in _EXCLUDED_NAME_HINTS):
            continue
        if lowered in seen_names:
            continue
        seen_names.add(lowered)

        sample_rate = int(round(float(device.get("default_samplerate", 0))))
        result.append((index, name, sample_rate))
    return result


def _preferred_hostapi(devices) -> int | None:
    """Pick one host API index to list mics from (WASAPI > MME > default input)."""
    try:
        hostapis = sd.query_hostapis()
    except Exception:
        return None

    def has_input(api_index: int) -> bool:
        return any(
            int(d.get("max_input_channels", 0)) > 0 and int(d.get("hostapi", -1)) == api_index
            for d in devices
        )

    for preferred_name in _PREFERRED_HOSTAPIS:
        for api_index, hostapi in enumerate(hostapis):
            if hostapi.get("name") == preferred_name and has_input(api_index):
                return api_index

    try:
        default_input = sd.default.device[0]
        if isinstance(default_input, int) and default_input >= 0:
            return int(devices[default_input]["hostapi"])
    except Exception:
        pass
    return None


class AudioRecorder:
    def __init__(
        self,
        target_sample_rate: int,
        input_device: int | str | None,
        logger: logging.Logger,
    ) -> None:
        self.target_sample_rate = target_sample_rate
        self.input_device = input_device
        self.logger = logger
        self._lock = threading.RLock()
        self._stream: sd.InputStream | None = None
        self._buffers: list[np.ndarray] = []
        self._capture_sample_rate = target_sample_rate

    @property
    def is_recording(self) -> bool:
        with self._lock:
            return self._stream is not None

    def start(self) -> None:
        with self._lock:
            if self._stream is not None:
                return

            self._buffers = []
            self._capture_sample_rate = self._resolve_capture_sample_rate()
            self.logger.info(
                "Starting recording with device=%s capture_sample_rate=%s target_sample_rate=%s",
                self.input_device or "<default>",
                self._capture_sample_rate,
                self.target_sample_rate,
            )
            self._stream = sd.InputStream(
                samplerate=self._capture_sample_rate,
                channels=1,
                dtype="float32",
                blocksize=0,
                device=self.input_device,
                callback=self._audio_callback,
            )
            self._stream.start()

    def stop(self) -> CapturedAudio:
        with self._lock:
            stream = self._stream
            self._stream = None

        if stream is None:
            return CapturedAudio(
                samples=np.array([], dtype=np.float32),
                sample_rate=self.target_sample_rate,
                duration_seconds=0.0,
            )

        stream.stop()
        stream.close()

        with self._lock:
            chunks = self._buffers
            self._buffers = []
            capture_sample_rate = self._capture_sample_rate

        if not chunks:
            return CapturedAudio(
                samples=np.array([], dtype=np.float32),
                sample_rate=self.target_sample_rate,
                duration_seconds=0.0,
            )

        samples = np.concatenate(chunks, axis=0).reshape(-1)
        duration_seconds = len(samples) / float(capture_sample_rate)
        resampled = _resample_audio(
            samples=samples,
            source_rate=capture_sample_rate,
            target_rate=self.target_sample_rate,
        )
        return CapturedAudio(
            samples=resampled,
            sample_rate=self.target_sample_rate,
            duration_seconds=duration_seconds,
        )

    def abort(self) -> None:
        with self._lock:
            stream = self._stream
            self._stream = None
            self._buffers = []

        if stream is not None:
            stream.abort(ignore_errors=True)
            stream.close(ignore_errors=True)

    def _resolve_capture_sample_rate(self) -> int:
        device_info = sd.query_devices(device=self.input_device, kind="input")
        default_sample_rate = int(round(float(device_info.get("default_samplerate", 0))))
        return default_sample_rate if default_sample_rate > 0 else self.target_sample_rate

    def _audio_callback(
        self,
        indata: np.ndarray,
        frames: int,
        time_info: object,
        status: sd.CallbackFlags,
    ) -> None:
        del frames, time_info
        if status:
            self.logger.warning("Audio callback status: %s", status)
        with self._lock:
            if self._stream is None:
                return
            self._buffers.append(indata.copy())


def _resample_audio(samples: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    if source_rate == target_rate or len(samples) == 0:
        return np.asarray(samples, dtype=np.float32)

    duration = len(samples) / float(source_rate)
    target_length = max(1, int(round(duration * target_rate)))
    source_positions = np.linspace(0.0, duration, num=len(samples), endpoint=False)
    target_positions = np.linspace(0.0, duration, num=target_length, endpoint=False)
    resampled = np.interp(target_positions, source_positions, samples)
    return np.asarray(resampled, dtype=np.float32)
