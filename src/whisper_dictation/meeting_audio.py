"""WASAPI microphone + loopback recording with bounded memory and crash journals."""
from __future__ import annotations

from contextlib import ExitStack
import itertools
import json
import logging
import math
import os
from pathlib import Path
import queue
import re
import threading
import time

import numpy as np

from .media import PCM16WaveWriter


RATE = 16000
# Logical tracks. Each opened device is journaled under its own key ("computer",
# "computer.1", ...) and keys of the same group are summed during recovery.
SOURCES = ("microphone", "computer")
_KEY = re.compile(r"^(microphone|computer)(?:\.(\d{1,3}))?$")
_RENDER_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render"
_RETRY_DELAYS = (10, 30, 60)  # seconds before reopening a capture that missed an output


def _group(key) -> str | None:
    match = _KEY.match(key) if isinstance(key, str) else None
    return match.group(1) if match else None


def _render_endpoints() -> frozenset | None:
    """Active playback endpoints (headphones, headsets, speakers, monitors), or None if unknown."""
    try:
        import winreg
    except ImportError:
        return None
    active = set()
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _RENDER_KEY) as root:
            for index in itertools.count():
                try:
                    name = winreg.EnumKey(root, index)
                except OSError:
                    break
                try:
                    with winreg.OpenKey(root, name) as key:
                        state, _ = winreg.QueryValueEx(key, "DeviceState")
                except OSError:
                    continue
                if int(state) & 0xF == 1:  # DEVICE_STATE_ACTIVE
                    active.add(name)
    except OSError:
        return None
    return frozenset(active)


def _audio_module():
    try:
        import pyaudiowpatch
    except ImportError as exc:
        raise RuntimeError("Captura WASAPI indisponível. Reinstale o PACE com PyAudioWPatch.") from exc
    return pyaudiowpatch


def _devices(audio, module):
    api = audio.get_host_api_info_by_type(module.paWASAPI)
    devices = [audio.get_device_info_by_index(i) for i in range(audio.get_device_count())]
    inputs = [d for d in devices if d["hostApi"] == api["index"] and d["maxInputChannels"] > 0 and not d.get("isLoopbackDevice")]
    outputs = list(audio.get_loopback_device_info_generator())
    return api, inputs, outputs


def list_meeting_devices() -> dict[str, list[dict]]:
    module = _audio_module()
    with module.PyAudio() as audio:
        _, inputs, outputs = _devices(audio, module)
        # Names survive the re-indexing PortAudio performs when devices change.
        def entries(devices):
            return [{"id": (f"wasapi:{int(d['index'])}:{d['name']}" if sum(other["name"] == d["name"] for other in devices) > 1 else d["name"]),
                     "name": d["name"]} for d in devices]
        return {"inputs": entries(inputs), "outputs": entries(outputs)}


def _choose(devices, selection, default_index):
    if selection is None:
        matches = [d for d in devices if d["index"] == default_index]
    elif isinstance(selection, int):
        matches = [d for d in devices if d["index"] == selection]
    elif isinstance(selection, str) and selection.startswith("wasapi:"):
        parts = selection.split(":", 2)
        matches = [d for d in devices if len(parts) == 3 and parts[1] == str(int(d["index"])) and parts[2] == d["name"]]
    else:
        matches = [d for d in devices if d["name"] == selection]
    if len(matches) != 1:
        raise RuntimeError(f"Dispositivo de áudio indisponível ou ambíguo: {selection or 'padrão'}. Selecione outro nas configurações.")
    return matches[0]


def _atomic_json(path: Path, data: dict) -> None:
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as output:
        json.dump(data, output)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)


class MeetingRecorder:
    def __init__(self, directory: Path | str, input_device=None, output_device=None, logger=None) -> None:
        self.directory = Path(directory)
        self.input_device, self.output_device = input_device, output_device
        self.logger = logger or logging.getLogger(__name__)
        self._lock = threading.RLock()
        self._stop_lock = threading.Lock()
        self._queue = queue.Queue(maxsize=128)
        self._streams = {}
        self._audio = None
        self._module = None
        self._writer = None
        self._watcher = None
        self._shutdown = threading.Event()
        self._active = False
        self._paused = False
        self._epoch = 0.0
        self._last_callback_time = 0.0
        self._last_safe_capture_end = 0.0
        self._end = None
        self._pauses = []
        self._source_info = {}
        self._levels = {}
        self._level_times = {}
        self._channels_used = {}
        self._stopping = threading.Event()
        self._endpoints = None
        self._listed_outputs = None
        self._incomplete = False
        self._retry_at = None
        self._retry_step = 0
        self._reopens = []
        self._error = None

    @property
    def active(self) -> bool:
        with self._lock:
            return self._active

    @property
    def paused(self) -> bool:
        with self._lock:
            return self._paused

    @property
    def error(self) -> str | None:
        with self._lock:
            return self._error

    @property
    def duration(self) -> float:
        with self._lock:
            if not self._epoch:
                return 0.0
            now = self._end if self._end is not None else time.monotonic()
            return max(0.0, now - self._epoch - sum(max(0.0, min(end or now, now) - start) for start, end in self._pauses))

    @property
    def levels(self) -> dict[str, float]:
        with self._lock:
            now = time.monotonic()
            result = dict.fromkeys(SOURCES, 0.0)
            for key, value in self._levels.items():
                # A silent loopback delivers no callbacks, so stale levels must expire.
                if now - self._level_times.get(key, 0.0) <= 1:
                    result[_group(key)] = max(result[_group(key)], value)
            return result

    def _stream_plan(self):
        api, inputs, outputs = _devices(self._audio, self._module)
        plan = [("microphone", _choose(inputs, self.input_device, api["defaultInputDevice"]))]
        if self.output_device is not None:
            return plan + [("computer", _choose(outputs, self.output_device, -1))]
        # Meeting apps may play on headphones, a Bluetooth hands-free headset or a monitor
        # instead of the default output, so every active output is captured at once.
        if not outputs:
            raise RuntimeError("Nenhuma saída de áudio com captura loopback disponível.")
        try:
            default = self._audio.get_device_info_by_index(api["defaultOutputDevice"])["name"]
        except Exception:
            default = None
        outputs = sorted(outputs, key=lambda d: d["name"].removesuffix(" [Loopback]") != default)
        self._listed_outputs = len(outputs)
        return plan + [("computer", device) for device in outputs]

    def _key(self, group, device, rate, channels):
        """Reuse a device's journal after reopening; give new or changed devices their own."""
        identity = (device["name"], rate, channels)
        for key, info in self._source_info.items():
            if _group(key) == group and key not in self._streams and (info["name"], info["rate"], info["channels"]) == identity:
                return key
        for number in itertools.count():
            key = group if number == 0 else f"{group}.{number}"
            if key not in self._source_info:
                return key

    def _open_streams(self, endpoints=None) -> list:
        opened, skipped = [], False
        self._listed_outputs = None
        for group, device in self._stream_plan():
            rate = int(device["defaultSampleRate"])
            # Shared-mode loopback only accepts the output's own layout (monitors and virtual
            # surround headsets often use 6 or 8 channels); the callback downmixes to mono.
            channels = int(device["maxInputChannels"]) if group == "computer" else min(2, int(device["maxInputChannels"]))
            key = self._key(group, device, rate, channels)
            try:
                stream = self._audio.open(format=self._module.paFloat32, channels=channels,
                                          rate=rate, input=True, input_device_index=int(device["index"]),
                                          frames_per_buffer=1024, start=False,
                                          stream_callback=self._callback(key))
            except Exception:
                if group == "microphone" or self.output_device is not None:
                    raise
                self.logger.warning("Saída %s não pôde ser capturada; seguindo com as demais.", device["name"], exc_info=True)
                skipped = True
                continue
            with self._lock:
                self._source_info[key] = dict(rate=rate, channels=channels, name=device["name"],
                                              clock_offset=time.monotonic() - stream.get_time())
                self._streams[key] = stream
            # The writer creates the journal even if the device stays silent; a dead writer
            # must not hang the reopen (and stop(), which waits for it).
            self._queue.put((key, None, None), timeout=5)
            opened.append(stream)
        if not any(_group(key) == "computer" for key in self._streams):
            raise RuntimeError("Nenhuma saída de áudio pôde ser capturada. Selecione outra saída.")
        # A just-connected headset may be active in Windows before PortAudio can open it.
        self._incomplete = self.output_device is None and (skipped or (
            endpoints is not None and self._listed_outputs is not None and self._listed_outputs < len(endpoints)))
        return opened

    def _schedule_retry(self, reset: bool) -> None:
        """Retry an incomplete capture a few times without reopening forever."""
        if reset:
            self._retry_step = 0
        if self._incomplete and self._retry_step < len(_RETRY_DELAYS):
            self._retry_at = time.monotonic() + _RETRY_DELAYS[self._retry_step]
            self._retry_step += 1
        else:
            self._retry_at = None

    def start(self) -> None:
        with self._lock:
            if self._active:
                return
            if self._epoch:
                raise RuntimeError("Uma instância de gravação não pode ser reutilizada.")
        self.directory.mkdir(parents=True, exist_ok=True)
        if (self.directory / "capture.json").exists():
            raise RuntimeError("Este diretório já contém uma gravação. Recupere-a antes de iniciar outra.")
        self._module = _audio_module()
        self._audio = self._module.PyAudio()
        try:
            # Read before opening: a device activated meanwhile triggers one extra reopen.
            endpoints = _render_endpoints()
            self._open_streams(endpoints)
            self._endpoints = endpoints
            self._schedule_retry(reset=True)
            self._epoch = time.monotonic()
            self._last_callback_time = self._last_safe_capture_end = self._epoch
            self._active = True
            self._checkpoint("recording")
            self._writer = threading.Thread(target=self._write_loop, name="meeting-disk", daemon=True)
            self._writer.start()
            for stream in self._streams.values():
                stream.start_stream()
            self._watcher = threading.Thread(target=self._watch_loop, name="meeting-watchdog", daemon=True)
            self._watcher.start()
        except Exception:
            with self._lock:
                self._active = False
                self._end = time.monotonic() if self._epoch else None
            self._close_streams()
            self._shutdown.set()
            if self._writer:
                self._writer.join(timeout=10)
            raise

    def pause(self) -> None:
        with self._lock:
            if self._active and not self._paused:
                self._pauses.append([time.monotonic(), None])
                self._paused = True
                self._levels = {}

    def resume(self) -> None:
        with self._lock:
            if self._active and self._paused:
                self._pauses[-1][1] = time.monotonic()
                self._paused = False

    def _parts(self, start: float, frames: int, rate: int):
        """Return pieces outside pauses, mapped onto the compacted recording clock."""
        end = start + frames / rate
        cutoff = self._end if self._end is not None else end
        segments = [(max(start, self._epoch), min(end, cutoff))]
        for pause_start, pause_end in self._pauses:
            pause_end = pause_end if pause_end is not None else end
            segments = [(a, min(b, pause_start)) for a, b in segments if a < pause_start] + [(max(a, pause_end), b) for a, b in segments if b > pause_end]
        for a, b in sorted(segments):
            first, last = max(0, round((a - start) * rate)), min(frames, round((b - start) * rate))
            if last > first:
                removed = sum(max(0.0, min(pause_end or a, a) - pause_start) for pause_start, pause_end in self._pauses)
                yield max(0.0, a - self._epoch - removed), first, last

    def _callback(self, source):
        def callback(data, frames, info, status):
            try:
                with self._lock:
                    if not self._active or self._error:
                        return None, self._module.paAbort
                    observed = time.monotonic()
                    if observed - self._last_callback_time > 5:
                        self._set_error("Gravação interrompida após suspensão do computador. O áudio salvo será recuperado.", end=self._last_safe_capture_end)
                        return None, self._module.paAbort
                    metadata = self._source_info[source]
                    rate, channels = metadata["rate"], metadata["channels"]
                    adc, current = info.get("input_buffer_adc_time", 0), info.get("current_time", 0)
                    if adc > 0 and abs(current - adc) < 2:
                        start = adc + metadata["clock_offset"]
                    else:
                        start = time.monotonic() - frames / rate
                    if status & self._module.paInputOverflow:
                        self.logger.warning("Overflow na fonte %s; lacuna preservada na linha do tempo.", source)
                    samples = np.frombuffer(data, dtype="<f4").reshape(-1, channels)
                    if channels > 2:
                        # Stereo calls played on a 6/8-channel output leave most channels silent.
                        # The set only grows, so the downmix gain does not jump between blocks.
                        used = self._channels_used.get(source, np.zeros(channels, dtype=bool)) | np.any(samples != 0, axis=0)
                        self._channels_used[source] = used
                        samples = samples[:, used] if used.any() else samples
                    mono = samples.mean(axis=1)
                    if not np.isfinite(mono).all():
                        mono = np.nan_to_num(mono, nan=0, posinf=0, neginf=0)
                    self._levels[source] = 0.0 if self._paused else min(1.0, float(np.sqrt(np.mean(mono * mono))))
                    self._level_times[source] = observed
                    for timestamp, first, last in self._parts(start, len(mono), rate):
                        self._queue.put_nowait((source, timestamp, mono[first:last].astype("<f4").tobytes()))
                    self._last_callback_time = observed
                    self._last_safe_capture_end = max(self._last_safe_capture_end, min(observed, start + len(mono) / rate))
                return None, self._module.paContinue
            except Exception as exc:
                self._set_error(f"Falha na captura de {source}: {exc}")
                return None, self._module.paAbort
        return callback

    def _set_error(self, message, end=None):
        with self._lock:
            if self._error is not None:
                return
            self._error = message
            self._end = end if end is not None else time.monotonic()
        self.logger.error(message)

    def _checkpoint(self, status):
        with self._lock:
            snapshot = dict(status=status, duration=self.duration,
                            sources={key: dict(info) for key, info in self._source_info.items()},
                            pauses=[list(pause) for pause in self._pauses], error=self._error)
        _atomic_json(self.directory / "capture.json", snapshot)

    def _write_loop(self):
        try:
            with ExitStack() as stack:
                raw, journals = {}, {}
                previous = time.monotonic()
                while not self._shutdown.is_set() or not self._queue.empty():
                    try:
                        source, timestamp, data = self._queue.get(timeout=0.2)
                    except queue.Empty:
                        pass
                    else:
                        if source not in raw:
                            raw[source] = stack.enter_context((self.directory / f"{source}.pcm").open("wb"))
                            journals[source] = stack.enter_context((self.directory / f"{source}.jsonl").open("w", encoding="utf-8"))
                            previous = 0.0  # list the new journal in capture.json right away for crash recovery
                        if data is not None:
                            offset = raw[source].tell()
                            raw[source].write(data)
                            raw[source].flush()
                            journals[source].write(json.dumps(dict(offset=offset, frames=len(data) // 4, start=timestamp)) + "\n")
                            journals[source].flush()
                    if time.monotonic() - previous >= 1:
                        for name in raw:
                            os.fsync(raw[name].fileno())
                            os.fsync(journals[name].fileno())
                        self._checkpoint("paused" if self.paused else "recording")
                        previous = time.monotonic()
                for name in raw:
                    os.fsync(raw[name].fileno())
                    os.fsync(journals[name].fileno())
        except Exception as exc:
            self._set_error(f"Falha ao salvar áudio em disco: {exc}")

    def _watch_loop(self):
        previous = last_scan = time.monotonic()
        while not self._shutdown.wait(0.5):
            with self._lock:
                if not self._active:
                    return
                now = time.monotonic()
                if now - previous > 5:
                    self._set_error("Gravação interrompida após suspensão do computador. O áudio salvo será recuperado.", end=self._last_safe_capture_end)
                elif now - self._last_callback_time > 5:
                    self._set_error("Dispositivos de áudio não respondem; áudio salvo será recuperado.", end=self._last_safe_capture_end)
                previous = now
                reopen = False
                try:
                    # A headset that disconnects stops its stream; reopen instead of ending the meeting.
                    reopen = any(not stream.is_active() for stream in self._streams.values())
                except Exception as exc:
                    self._set_error(f"Dispositivo de áudio indisponível: {exc}")
                if self._error:
                    # Finalization is performed by the service, outside PortAudio callback threads.
                    return
            reset = True
            if not reopen and self.output_device is None:
                if self._retry_at is not None and now >= self._retry_at:
                    reopen, reset = True, False  # an output was missed last time
                elif now - last_scan >= 2:
                    # PortAudio lists devices only when initialized: a headset connected
                    # mid-meeting is picked up by reopening the capture.
                    last_scan = now
                    endpoints = _render_endpoints()
                    reopen = endpoints is not None and endpoints != self._endpoints
            if reopen:
                if not self._reopen(reset=reset):
                    return
                # Reopening may take seconds (Bluetooth profile switch); that is not a suspend.
                previous = time.monotonic()

    def _reopen(self, attempts=(0.5, 1, 2, 4, 8), reset=True) -> bool:
        if not self._stop_lock.acquire(blocking=False):
            return True  # stop() is closing the devices
        try:
            with self._lock:
                if not self._active or self._error:
                    return True
                now = time.monotonic()
                self._reopens = [moment for moment in self._reopens if now - moment < 60]
                if len(self._reopens) >= 6:
                    return True  # flapping device: try again on a later tick
                self._reopens.append(now)
            self.logger.info("Dispositivos de áudio mudaram; reabrindo a captura da reunião.")
            self._close_streams()
            for delay in (*attempts, None):
                try:
                    endpoints = _render_endpoints()  # before enumerating, see start()
                    self._audio = self._module.PyAudio()
                    started = self._open_streams(endpoints)
                    with self._lock:
                        # The gap while reopening is kept as silence, not reported as a stall.
                        self._last_callback_time = time.monotonic()
                    for stream in started:
                        stream.start_stream()
                    self._endpoints = endpoints
                    self._schedule_retry(reset)
                    break
                except Exception as exc:
                    self._close_streams()
                    # Endpoints vanish for a moment while a headset switches profile: retry.
                    if delay is None:
                        self._set_error(f"Dispositivo de áudio indisponível: {exc}", end=self._last_safe_capture_end)
                        return False
                    self.logger.warning("Reabertura da captura falhou (%s); nova tentativa em %s s.", exc, delay)
                    if self._stopping.wait(delay):
                        return True
            self.logger.info("Captura da reunião: %s", ", ".join(info["name"] for key, info in self._source_info.items() if key in self._streams))
            return True
        finally:
            self._stop_lock.release()

    def _close_streams(self):
        for stream in list(self._streams.values()):
            try:
                stream.stop_stream()
            except OSError:
                self.logger.exception("Falha ao parar dispositivo")
            finally:
                try:
                    stream.close()
                except OSError:
                    self.logger.exception("Falha ao fechar dispositivo")
        self._streams.clear()
        if self._audio:
            self._audio.terminate()
            self._audio = None

    def stop(self) -> Path:
        self._stopping.set()  # ends a reopen retry wait instead of blocking the user
        with self._stop_lock:
            with self._lock:
                if not self._epoch:
                    raise RuntimeError("Gravação não iniciada.")
                self._end = self._end if self._end is not None else time.monotonic()
                self._active = False
            self._close_streams()
            self._shutdown.set()
            if self._writer:
                self._writer.join(timeout=15)
                if self._writer.is_alive():
                    raise RuntimeError("Disco não respondeu. Áudio parcial mantido para recuperação.")
            if self._watcher and self._watcher is not threading.current_thread():
                self._watcher.join(timeout=2)
            self._checkpoint("interrupted" if self.error else "stopped")
            return recover_recording(self.directory)


class _SourceReader:
    def __init__(self, directory, name, rate, stack):
        self.rate = rate
        self.raw = stack.enter_context((directory / f"{name}.pcm").open("rb"))
        self.journal = stack.enter_context((directory / f"{name}.jsonl").open("r", encoding="utf-8"))
        self.size = os.fstat(self.raw.fileno()).st_size
        self.chunk = None
        self.end = 0
        self.converted = self._converted_chunks()

    def _native_chunks(self):
        for line in self.journal:
            try:
                item = json.loads(line)
                offset, frames, timestamp = int(item["offset"]), int(item["frames"]), float(item["start"])
                if offset < 0 or not 0 < frames <= 8192 or not math.isfinite(timestamp) or timestamp < 0 or offset + frames * 4 > self.size:
                    continue
            except (ValueError, KeyError, TypeError):
                continue
            self.raw.seek(offset)
            samples = np.frombuffer(self.raw.read(frames * 4), dtype="<f4")
            yield timestamp, samples

    def _converted_chunks(self):
        import av
        resampler = None
        anchor = expected = 0.0
        fed = emitted = 0

        def outputs(frame):
            nonlocal emitted
            for converted in resampler.resample(frame):
                values = converted.to_ndarray().reshape(-1)
                first = round(anchor * RATE) + emitted
                emitted += len(values)
                yield first, values

        for timestamp, samples in self._native_chunks():
            # Keep filter history and fractional phase across hardware blocks.
            # Reset only on a real gap or measured clock drift, preserving source alignment.
            discontinuity = (abs(timestamp - expected) > .002 or abs(timestamp - anchor - fed / self.rate) > .005)
            if resampler is None or discontinuity:
                if resampler is not None:
                    yield from outputs(None)
                resampler = av.AudioResampler(format="flt", layout="mono", rate=RATE)
                anchor, fed, emitted = timestamp, 0, 0
            frame = av.AudioFrame.from_ndarray(samples.reshape(1, -1), format="flt", layout="mono")
            frame.sample_rate = self.rate
            fed += len(samples)
            expected = timestamp + len(samples) / self.rate
            yield from outputs(frame)
        if resampler is not None:
            yield from outputs(None)

    def _next(self):
        for first, values in self.converted:
            # Small timestamp jitter must not duplicate or reorder previously emitted samples.
            skip = max(0, self.end - first)
            if skip >= len(values):
                continue
            self.chunk = (first + skip, values[skip:])
            self.end = first + len(values)
            return
        self.chunk = None

    def block(self, first, length):
        result = np.zeros(length, dtype=np.float32)
        if self.chunk is None:
            self._next()
        while self.chunk is not None:
            start, values = self.chunk
            end = start + len(values)
            if start >= first + length:
                break
            a, b = max(first, start), min(first + length, end)
            if b > a:
                result[a-first:b-first] = values[a-start:b-start]
            if end > first + length:
                break
            self._next()
        return result


def _rms(samples) -> float:
    return float(np.sqrt(np.mean(samples * samples))) if len(samples) else 0.0


def _envelope(samples, size=320):
    count = len(samples) // size  # 20 ms frames tolerate the latency between mirrored devices
    return np.sqrt(np.mean(samples[:count * size].reshape(count, size) ** 2, axis=1))


def _mirrors(a, b) -> bool:
    first, second = _envelope(a), _envelope(b)
    if len(first) < 10 or not first.std() or not second.std():
        return False
    return float(np.corrcoef(first, second)[0, 1]) > .9


def _mix_devices(blocks):
    """Sum distinct devices, keeping one copy of outputs that mirror each other.

    Virtual cables and mixers (Voicemeeter, VB-Cable, "Listen to this device") replay
    the real output; summing both copies would double the level and add echo.
    """
    loud = sorted((block for block in blocks if _rms(block) > 1e-4), key=_rms, reverse=True)
    kept = []
    for block in loud:
        if not any(_mirrors(block, other) for other in kept):
            kept.append(block)
    return sum(kept) if kept else blocks[0]


def recover_recording(directory: Path | str) -> Path:
    """Finalize persisted chunks, ignoring a torn last journal record after a crash."""
    directory = Path(directory)
    metadata = json.loads((directory / "capture.json").read_text(encoding="utf-8"))
    duration = float(metadata.get("duration", 0))
    if not math.isfinite(duration) or duration < 0:
        raise ValueError("Duração inválida no checkpoint de gravação.")
    names = [name for name in metadata.get("sources", {}) if _group(name)
             and (directory / f"{name}.pcm").exists() and (directory / f"{name}.jsonl").exists()]
    if not names:
        raise RuntimeError("Nenhum bloco de áudio disponível para recuperar.")
    groups = {group: [name for name in names if _group(name) == group] for group in SOURCES}
    groups = {group: members for group, members in groups.items() if members}
    # Find the persisted end without loading the journal or audio into memory.
    for name in names:
        rate = int(metadata["sources"][name]["rate"])
        if not 8000 <= rate <= 384000:
            raise ValueError("Taxa de amostragem inválida no checkpoint.")
        size = (directory / f"{name}.pcm").stat().st_size
        with (directory / f"{name}.jsonl").open(encoding="utf-8") as journal:
            for line in journal:
                try:
                    item = json.loads(line)
                    frames, offset, start = int(item["frames"]), int(item["offset"]), float(item["start"])
                    if 0 < frames <= 8192 and offset >= 0 and offset + frames * 4 <= size and math.isfinite(start) and start >= 0:
                        if metadata.get("status") not in {"stopped", "interrupted"}:
                            duration = max(duration, start + frames / rate)
                except (ValueError, KeyError, TypeError):
                    continue
    frames = round(duration * RATE)
    destination = directory / "audio.wav"
    temporary = destination.with_suffix(".part")
    with ExitStack() as stack:
        readers = {name: _SourceReader(directory, name, int(metadata["sources"][name]["rate"]), stack) for name in names}
        outputs = {group: stack.enter_context(PCM16WaveWriter(directory / f"{group}.wav")) for group in groups}
        mix = stack.enter_context(PCM16WaveWriter(temporary))
        for first in range(0, frames, RATE):
            length = min(RATE, frames - first)
            mixed = np.zeros(length, dtype=np.float32)
            for group, members in groups.items():
                # Devices of one track (every output, or a microphone swapped mid-meeting) are summed.
                samples = _mix_devices([readers[name].block(first, length) for name in members])
                outputs[group].writeframesraw((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
                mixed += samples
            # Preserve headroom when both sources speak simultaneously.
            mixed /= len(groups)
            mix.writeframesraw((np.clip(mixed, -1, 1) * 32767).astype("<i2").tobytes())
    os.replace(temporary, destination)
    metadata.update(duration=frames / RATE, recovered=True)
    _atomic_json(directory / "capture.json", metadata)
    return destination
