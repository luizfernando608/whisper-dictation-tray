"""Token-free, stateful eight-speaker diarization on native runtimes.

Only official, pinned NVIDIA artifacts are downloaded. CPU/Vulkan binaries are
separate subprocesses, so drivers cannot take down PACE. No Hugging Face token
is sent or required. One persistent stream preserves speaker slots while
bounded reads avoid loading entire meetings into the native CLI's audio buffer.
"""
from __future__ import annotations

import json
import hashlib
import math
import re
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

import numpy as np

from whisper_dictation.diarization import ProcessingCancelled, atomic_json, _hash, model_root, _hardware_signature

MODEL = "nvidia/Nemotron-3-Diarization"
REVISION = "f667ed73aee57d40cc39428eb768b4fd87a0a29e"
MODEL_FILE = "Nemotron-3-Diarization.q8_0.gguf"
MODEL_SHA = "08456d9e22cd9a323c0364d98375f3746d6e68507ebb705cd46438c534c7a3a1"
MODEL_SIZE = 107012128
MODEL_LICENSE = "OpenMDW-1.1"
RELEASE = "https://github.com/NVIDIA/NeMo-Speech.cpp/releases/download/v0.2.0/"
RUNTIMES = {
    "cpu": ("nemo-speech-0.2.0-windows-x86_64-cpu.zip", 5528418,
            "82c80451086e86194aba9af19f800da77cd66d1c28d993d0efceffd6cdb49bb8"),
    "vulkan": ("nemo-speech-0.2.0-windows-x86_64-vulkan.zip", 19302464,
               "edf15a04ba98740aa0ab75ae4681ac7ad0a488b22b35ce2cbebd10590bb1c1e1"),
}


def _download(url: str, destination: Path, size: int, digest: str, progress=None):
    if destination.is_file() and destination.stat().st_size == size and _hash(destination) == digest:
        return
    pending = destination.with_suffix(destination.suffix + ".pending")
    received = 0
    request = urllib.request.Request(url, headers={"User-Agent": "PACE-model-preparation"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response, pending.open("wb") as output:
            if not response.url.startswith("https://"):
                raise RuntimeError("Download redirecionou para protocolo inseguro.")
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                received += len(block)
                if received > size:
                    raise RuntimeError("Artefato excedeu o tamanho esperado.")
                output.write(block)
                if progress:
                    progress(received / size)
        if received != size or _hash(pending) != digest:
            raise RuntimeError("Checksum ou tamanho do artefato NVIDIA não corresponde ao publicado.")
        os.replace(pending, destination)
    finally:
        pending.unlink(missing_ok=True)


def _extract(archive: Path, destination: Path):
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as source:
        total = 0
        for info in source.infolist():
            name = PurePosixPath(info.filename.replace("\\", "/"))
            if name.is_absolute() or ".." in name.parts or ":" in str(name) or (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("Arquivo ZIP contém caminho ou link inválido.")
            total += info.file_size
            if total > 500 * 1024 * 1024:
                raise ValueError("Runtime descompactado excede o tamanho permitido.")
            target = (destination / str(name)).resolve()
            if not target.is_relative_to(destination.resolve()):
                raise ValueError("Arquivo ZIP tenta escrever fora do cache.")
        source.extractall(destination)


def _executable(root: Path, backend: str) -> Path:
    matches = list((root / backend).rglob("nemo-speech.exe"))
    if len(matches) != 1:
        raise RuntimeError(f"Runtime {backend} ausente ou ambíguo; prepare os modelos novamente.")
    return matches[0]


def prepare_models(config, root: Path, progress=None) -> dict:
    report = progress or (lambda message, fraction: None)
    root = Path(root) / "nemotron-3"
    root.mkdir(parents=True, exist_ok=True)
    try:
        manifest = _prepared(root)
    except RuntimeError:
        manifest = None
    if manifest is not None:
        _prepare_asr(config, root.parent, report)
        report("Modelos Nemotron 3 já preparados e íntegros.", 1.0)
        return manifest
    for index, (backend, (name, size, digest)) in enumerate(RUNTIMES.items()):
        report(f"Baixando runtime NVIDIA {backend} v0.2.0…", index * 0.15)
        archive = root / name
        _download(RELEASE + name, archive, size, digest,
                  lambda fraction: report(f"Baixando runtime {backend}…", index * 0.15 + fraction * 0.15))
        _extract(archive, root / backend)
        _executable(root, backend)
    report("Baixando modelo Nemotron 3 (107 MB), sem conta ou token…", 0.3)
    _download(f"https://huggingface.co/{MODEL}/resolve/{REVISION}/{MODEL_FILE}", root / MODEL_FILE,
              MODEL_SIZE, MODEL_SHA, lambda fraction: report("Baixando Nemotron 3…", 0.3 + 0.65 * fraction))
    # Keep the publisher model card and governing license beside the weights.
    request = urllib.request.Request(f"https://huggingface.co/{MODEL}/raw/{REVISION}/README.md", headers={"User-Agent": "PACE"})
    with urllib.request.urlopen(request, timeout=30) as response:
        (root / "MODEL-README.md").write_bytes(response.read(1024 * 1024))
    request = urllib.request.Request("https://openmdw.ai/license/1-1/", headers={"User-Agent": "PACE"})
    with urllib.request.urlopen(request, timeout=30) as response:
        (root / "LICENSE-OpenMDW-1.1.html").write_bytes(response.read(1024 * 1024))
    files = {str(path.relative_to(root)): _hash(path) for path in root.rglob("*") if path.is_file()
             and not path.name.endswith((".zip", ".json"))}
    manifest = {"schema": 1, "model": MODEL, "revision": REVISION, "model_license": MODEL_LICENSE,
                "runtime_version": "0.2.0", "runtime_license": "Apache-2.0", "max_speakers": 8,
                "model_sha256": MODEL_SHA, "files": files}
    atomic_json(root / "prepared.json", manifest)
    _prepare_asr(config, root.parent, report)
    report("Nemotron 3 preparado. CPU/Vulkan ainda serão validados com áudio real.", 1.0)
    return manifest


def _prepare_asr(config, root: Path, report) -> None:
    provider = str(getattr(config, "transcription_provider", "groq"))
    fallback = str(getattr(config, "meeting_timestamp_provider", "local"))
    needs_local = provider == "local" or (fallback == "local" and (
        provider == "gemini" or (provider == "openai" and getattr(config, "openai_model", "") != "whisper-1")))
    if not needs_local:
        return
    model_name = config.model_size
    marker = root / "asr-prepared.json"
    try:
        existing = json.loads(marker.read_text(encoding="utf-8"))
        if existing.get("asr_model") == model_name and Path(existing.get("asr_path", "")).is_dir():
            return
    except (OSError, ValueError):
        pass
    report(f"Preparando Whisper {model_name} para transcrição com timestamps…", 0.95)
    if Path(model_name).is_dir():
        path = str(Path(model_name).resolve())
    else:
        from faster_whisper.utils import download_model
        path = download_model(model_name, cache_dir=str(root / "asr"))
    atomic_json(marker, {"schema": 1, "asr_model": model_name, "asr_path": path})


def run_cli(command: list[str], directory: Path, *, cancelled=None, progress=None, timeout: float = 180.0,
            progress_path: Path | None = None) -> tuple[str, str]:
    """Supervisor for both runtime probing and long recording inference."""
    cancel = cancelled or (lambda: False)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    stdout_path, stderr_path = directory / "native.stdout", directory / "native.stderr"
    started, last_report = time.monotonic(), 0.0
    peak_memory = 0
    with stdout_path.open("wb") as output, stderr_path.open("wb") as error:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=output, stderr=error,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            while process.poll() is None:
                peak_memory = max(peak_memory, _peak_working_set(process))
                if cancel():
                    raise ProcessingCancelled()
                elapsed = time.monotonic() - started
                if elapsed > timeout:
                    raise TimeoutError(f"Runtime nativo excedeu {timeout:g}s.")
                if progress and elapsed - last_report >= 5:
                    if progress_path and progress_path.exists():
                        try:
                            current = json.loads(progress_path.read_text(encoding="utf-8"))
                            progress(current["message"], current["progress"])
                        except (OSError, ValueError, KeyError):
                            pass
                    else:
                        progress(f"Inicializando diarizador ({int(elapsed)} s)…", 0.0)
                    last_report = elapsed
                time.sleep(0.1)
            if process.returncode != 0:
                error.flush()
                detail = _tail(stderr_path, 4000)
                raise RuntimeError(f"Runtime nativo saiu com código {process.returncode}: {detail}")
        finally:
            if process.poll() is None:
                _terminate_tree(process)
                process.wait(timeout=10)
    if stdout_path.stat().st_size > 32 * 1024 * 1024:
        raise RuntimeError("Resposta do runtime nativo excede 32 MB.")
    atomic_json(directory / "native-metrics.json", {"elapsed_seconds": time.monotonic()-started,
                                                    "peak_working_set_bytes": peak_memory or None})
    return stdout_path.read_text(encoding="utf-8", errors="replace"), _tail(stderr_path, 8000)


def _tail(path: Path, size: int) -> str:
    with path.open("rb") as stream:
        stream.seek(max(0, path.stat().st_size-size))
        return stream.read(size).decode("utf-8", errors="replace")


def _terminate_tree(process) -> None:
    # A venv's Windows python.exe may launch another Python process. Killing
    # only that launcher leaves the native model/driver alive after cancel.
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=subprocess.CREATE_NO_WINDOW, timeout=10, check=False)
        except (OSError, subprocess.TimeoutExpired):
            pass
    if process.poll() is None:
        process.kill()


def _peak_working_set(process=None) -> int:
    if os.name != "nt":
        return 0
    import ctypes
    from ctypes import wintypes
    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                                               "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage",
                                               "PagefileUsage", "PeakPagefileUsage")]
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    api = ctypes.windll.psapi.GetProcessMemoryInfo
    api.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    api.restype = wintypes.BOOL
    if process is None:
        current = ctypes.windll.kernel32.GetCurrentProcess
        current.restype = wintypes.HANDLE
        handle = current()
    else:
        handle = int(process._handle)
    if not api(handle, ctypes.byref(counters), counters.cb):
        return 0
    return int(counters.PeakWorkingSetSize)


def _prepared(root: Path) -> dict:
    try:
        manifest = json.loads((root / "prepared.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError("Prepare os modelos Nemotron 3 nas configurações.") from exc
    if (not isinstance(manifest, dict) or not isinstance(manifest.get("files"), dict)
            or any(not isinstance(name, str) or not isinstance(digest, str)
                   for name, digest in manifest["files"].items())):
        raise RuntimeError("Cache Nemotron inválido; prepare novamente.")
    if manifest.get("model") != MODEL or manifest.get("model_sha256") != MODEL_SHA or manifest.get("runtime_version") != "0.2.0":
        raise RuntimeError("Cache Nemotron incompatível; prepare novamente.")
    model = root / MODEL_FILE
    if not model.is_file() or model.stat().st_size != MODEL_SIZE or _hash(model) != MODEL_SHA:
        raise RuntimeError("Pesos Nemotron não correspondem ao checksum oficial; prepare novamente.")
    # Manifest JSON is useful provenance, not a trust anchor. Seal executables
    # and DLLs against contents of the officially pinned ZIPs themselves.
    for backend, (name, size, digest) in RUNTIMES.items():
        archive = root / name
        if not archive.is_file() or archive.stat().st_size != size or _hash(archive) != digest:
            raise RuntimeError(f"Pacote {backend} não corresponde ao checksum oficial; prepare novamente.")
        with zipfile.ZipFile(archive) as source:
            for info in source.infolist():
                if info.is_dir():
                    continue
                target = (root / backend / info.filename).resolve()
                expected = hashlib.sha256()
                with source.open(info) as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        expected.update(block)
                if not target.is_relative_to(root.resolve()) or not target.is_file() or _hash(target) != expected.hexdigest():
                    raise RuntimeError(f"Runtime {backend} foi alterado; prepare novamente.")
    for name, expected in manifest["files"].items():
        path = (root / name).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file() or _hash(path) != expected:
            raise RuntimeError("Cache Nemotron ausente ou alterado; prepare novamente.")
    return manifest


def parse_result(payload: str, duration: float) -> list[dict]:
    decoded = json.loads(payload)
    if not isinstance(decoded, dict) or not isinstance(decoded.get("segments"), list):
        raise ValueError("Resposta nativa não contém lista de segmentos.")
    turns = []
    for segment in decoded["segments"]:
        start, end = float(segment["start"]), float(segment["end"])
        speaker = segment["speaker"]
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or start >= duration or end <= start or end > duration + 0.05:
            raise ValueError("Runtime retornou timestamps inválidos.")
        if isinstance(speaker, bool) or not isinstance(speaker, int) or not 1 <= speaker <= 8:
            raise ValueError("Runtime retornou identidade de falante inválida.")
        turns.append({"start": start, "end": min(duration, end), "speaker": f"SPEAKER_{speaker-1:02d}"})
    return sorted(turns, key=lambda turn: (turn["start"], turn["end"], turn["speaker"]))


def exclusive_turns(turns: list[dict]) -> list[dict]:
    """Retain unambiguous intervals without throwing away the overlapping turns."""
    events = {}
    for turn in turns:
        events.setdefault(turn["start"], []).append((turn["speaker"], 1))
        events.setdefault(turn["end"], []).append((turn["speaker"], -1))
    active, result, previous = {}, [], None
    for timestamp in sorted(events):
        if previous is not None and timestamp > previous:
            speakers = [speaker for speaker, count in active.items() if count > 0]
            if len(speakers) == 1:
                if result and result[-1]["speaker"] == speakers[0] and result[-1]["end"] == previous:
                    result[-1]["end"] = timestamp
                else:
                    result.append({"start": previous, "end": timestamp, "speaker": speakers[0]})
        for speaker, delta in events[timestamp]:
            active[speaker] = active.get(speaker, 0) + delta
        previous = timestamp
    return result


def disagreement(reference: list[dict], candidate: list[dict], duration: float) -> float:
    """DER-style discrepancy versus CPU at 10 ms, with optimal label assignment.

    This is an accelerator equivalence gate, not a quality score against human
    annotations. Benchmark scripts separately report annotated-fixture DER.
    """
    def activity(turns):
        ids = sorted({turn["speaker"] for turn in turns})
        values = np.zeros((len(ids), math.ceil(duration * 100)), dtype=bool)
        for turn in turns:
            index = ids.index(turn["speaker"])
            values[index, max(0, round(turn["start"] * 100)):round(turn["end"] * 100)] = True
        return values
    ref, hyp = activity(reference), activity(candidate)
    count = max(len(ref), len(hyp))
    overlaps = np.zeros((count, count), np.int64)
    for a in range(len(ref)):
        for b in range(len(hyp)):
            overlaps[a, b] = np.count_nonzero(ref[a] & hyp[b])
    states = {0: 0}
    for row in range(count):
        updated = {}
        for mask, value in states.items():
            for col in range(count):
                if not mask & (1 << col):
                    next_mask = mask | (1 << col)
                    updated[next_mask] = max(updated.get(next_mask, -1), value + int(overlaps[row, col]))
        states = updated
    correct = states.get((1 << count) - 1, 0)
    total = int(ref.sum())
    errors = int(np.maximum(ref.sum(axis=0), hyp.sum(axis=0)).sum()) - correct
    return errors / total if total else (0.0 if not hyp.any() else 1.0)


def _probes(source: Path, directory: Path, duration: float) -> list[tuple[Path, float]]:
    import wave
    from whisper_dictation.wav_reader import PCM16Reader

    offsets = {0.0} if duration <= 120 else {0.0, max(0, duration/2-30), max(0, duration-60)}
    try:
        segments = json.loads((directory / "transcript.json").read_text(encoding="utf-8")).get("segments", [])
        if segments and duration > 120:
            offsets = {max(0.0, min(duration-60, float(segments[index]["start"])-1))
                       for index in (0, len(segments)//2, len(segments)-1)}
    except (OSError, ValueError, KeyError, TypeError):
        pass
    probes = []
    for index, offset in enumerate(sorted(offsets)):
        destination = directory / f"nemotron.probe-{index}.wav"
        reader = PCM16Reader(source)
        try:
            reader.setpos(round(offset * 16000))
            pcm = reader.readframes(16000 * 60)
        finally:
            reader.close()
        with wave.open(str(destination), "wb") as output:
            output.setparams((1, 2, 16000, 0, "NONE", ""))
            output.writeframes(pcm)
        if pcm:
            probes.append((destination, len(pcm) / 32000.0))
    return probes


def native_stream_worker(root, audio, device: str, result_path) -> int:
    """Supervised child: bounded PCM reads into one persistent native stream.

    ABI declarations match NVIDIA 0.2.0 include/nemo_speech/diar.h. No callbacks
    cross the C boundary. The DLL compacts old frame probabilities after ~20min.
    """
    import ctypes as c
    from whisper_dictation.wav_reader import PCM16Reader

    root, audio, result_path = Path(root).resolve(), Path(audio).resolve(), Path(result_path).resolve()
    backend = "cpu" if device == "cpu" else "vulkan"
    binary = _executable(root, backend).parent
    dll_folder = os.add_dll_directory(str(binary)) if os.name == "nt" else None
    model, stream = c.c_void_p(), c.c_void_p()
    library = None
    try:
        library = c.CDLL(str(binary / "nemo_speech_asr_c.dll"))
        class ModelConfig(c.Structure):
            _fields_ = [("size", c.c_size_t), ("model_path", c.c_char_p), ("gpu", c.c_int32), ("preset", c.c_char_p)] + [
                (name, c.c_int32) for name in ("chunk_frames", "right_context_frames", "left_context_frames",
                                              "fifo_frames", "spkcache_frames", "update_period_frames")]
        class Segment(c.Structure):
            _fields_ = [("start_time", c.c_double), ("end_time", c.c_double), ("speaker", c.c_int32)]
        library.nemo_speech_asr_last_error.argtypes = []
        library.nemo_speech_asr_last_error.restype = c.c_char_p
        library.nemo_speech_diar_create.argtypes = [c.POINTER(ModelConfig), c.POINTER(c.c_void_p)]
        library.nemo_speech_diar_create.restype = c.c_int
        library.nemo_speech_diar_destroy.argtypes = [c.c_void_p]
        library.nemo_speech_diar_destroy.restype = None
        library.nemo_speech_diar_num_speakers.argtypes = [c.c_void_p]
        library.nemo_speech_diar_num_speakers.restype = c.c_int32
        library.nemo_speech_diar_seconds_per_frame.argtypes = [c.c_void_p]
        library.nemo_speech_diar_seconds_per_frame.restype = c.c_double
        library.nemo_speech_diar_stream_open.argtypes = [c.c_void_p, c.POINTER(c.c_void_p)]
        library.nemo_speech_diar_stream_open.restype = c.c_int
        library.nemo_speech_diar_stream_push_f32.argtypes = [c.c_void_p, c.POINTER(c.c_float), c.c_size_t, c.c_int32]
        library.nemo_speech_diar_stream_push_f32.restype = c.c_int
        for name in ("nemo_speech_diar_stream_finish", "nemo_speech_diar_frame_count", "nemo_speech_diar_frame_probs_start"):
            function = getattr(library, name)
            function.argtypes = [c.c_void_p]
            function.restype = c.c_int if name.endswith("finish") else c.c_int64
        library.nemo_speech_diar_stream_close.argtypes = [c.c_void_p]
        library.nemo_speech_diar_stream_close.restype = None
        library.nemo_speech_diar_segments.argtypes = [c.c_void_p, c.c_void_p, c.POINTER(Segment), c.c_size_t, c.POINTER(c.c_size_t)]
        library.nemo_speech_diar_segments.restype = c.c_int
        def check(status):
            if status != 0:
                message = library.nemo_speech_asr_last_error() or b"unknown native error"
                raise RuntimeError(message.decode("utf-8", errors="replace"))
        device_index = -1 if device == "cpu" else int(device.split(":", 1)[1])
        cfg = ModelConfig()
        cfg.size = c.sizeof(ModelConfig)
        cfg.model_path = str(root / MODEL_FILE).encode("utf-8")
        cfg.gpu = device_index
        cfg.preset = b"v3-offline"
        cfg.left_context_frames = -1
        check(library.nemo_speech_diar_create(c.byref(cfg), c.byref(model)))
        if library.nemo_speech_diar_num_speakers(model) != 8:
            raise RuntimeError("Modelo nativo não confirmou capacidade de oito falantes.")
        cadence = library.nemo_speech_diar_seconds_per_frame(model)
        check(library.nemo_speech_diar_stream_open(model, c.byref(stream)))
        reader = PCM16Reader(audio)
        try:
            if reader.rate != 16000 or reader.channels != 1:
                raise ValueError("CAPI requer áudio já normalizado mono 16 kHz.")
            duration = reader.frames / reader.rate
            while True:
                pcm = reader.readframes(5 * 16000)
                if not pcm:
                    break
                values = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
                check(library.nemo_speech_diar_stream_push_f32(stream, values.ctypes.data_as(c.POINTER(c.c_float)), values.size, 16000))
                labeled_seconds = min(duration, library.nemo_speech_diar_frame_count(stream) * cadence)
                try:
                    atomic_json(result_path.with_name(result_path.name + ".progress.json"), {
                        "message": f"Identificando falantes: {labeled_seconds:.0f} / {duration:.0f} s",
                        "progress": labeled_seconds / duration if duration else 0,
                        "decoded_seconds": reader.position / reader.rate, "labeled_seconds": labeled_seconds})
                except PermissionError:
                    # Windows readers/antivirus can momentarily deny replace.
                    # Progress is advisory; the next push reports fresh state.
                    pass
        finally:
            reader.close()
        check(library.nemo_speech_diar_stream_finish(stream))
        count = c.c_size_t()
        check(library.nemo_speech_diar_segments(stream, None, None, 0, c.byref(count)))
        segments = (Segment * count.value)()
        check(library.nemo_speech_diar_segments(stream, None, segments, count.value, c.byref(count)))
        atomic_json(result_path, {"segments": [{"start": item.start_time, "end": item.end_time, "speaker": item.speaker}
                                               for item in segments[:count.value]],
                                 "diagnostics": {"mode": "streaming", "device": device, "device_index": device_index,
                                    "peak_working_set_bytes": _peak_working_set() or None,
                                    "capacity": 8, "frames": library.nemo_speech_diar_frame_count(stream),
                                    "frame_cadence": cadence, "retained_frame_start": library.nemo_speech_diar_frame_probs_start(stream)}})
        return 0
    except Exception as exc:
        atomic_json(result_path, {"error": f"{type(exc).__name__}: {exc}"})
        return 1
    finally:
        if library is not None and stream.value:
            library.nemo_speech_diar_stream_close(stream)
        if library is not None and model.value:
            library.nemo_speech_diar_destroy(model)
        if dll_folder:
            dll_folder.close()


def _infer(root: Path, wav: Path, duration: float, directory: Path, device: str,
           cancel, progress, timeout: float) -> tuple[list[dict], float, str]:
    backend = "cpu" if device == "cpu" else "vulkan"
    directory.mkdir(parents=True, exist_ok=True)
    result_path = directory / "stream-result.json"
    result_path.unlink(missing_ok=True)
    progress_path = result_path.with_name(result_path.name + ".progress.json")
    progress_path.unlink(missing_ok=True)
    command = [sys.executable]
    if not getattr(sys, "frozen", False):
        command += [str(Path(__file__).resolve().parents[2] / "main.py")]
    command += ["--nemotron-runtime", "--native-root", str(root), "--native-audio", str(wav),
                "--native-device", device, "--native-result", str(result_path)]
    started = time.monotonic()
    try:
        _, log = run_cli(command, directory, cancelled=cancel, progress=progress, timeout=timeout,
                         progress_path=progress_path)
    except RuntimeError as exc:
        if result_path.exists():
            failure = json.loads(result_path.read_text(encoding="utf-8"))
            if failure.get("error"):
                raise RuntimeError(failure["error"]) from exc
        raise
    output = result_path.read_text(encoding="utf-8")
    decoded = json.loads(output)
    diagnostic = decoded.get("diagnostics", {})
    expected_index = -1 if device == "cpu" else int(device.split(":", 1)[1])
    if diagnostic.get("mode") != "streaming" or diagnostic.get("device_index") != expected_index or diagnostic.get("capacity") != 8:
        raise RuntimeError("Runtime não confirmou modo stateful e dispositivo solicitado nos diagnósticos.")
    atomic_json(directory / "native-metrics.json", {
        "elapsed_seconds": time.monotonic() - started,
        "peak_working_set_bytes": diagnostic.get("peak_working_set_bytes"),
        "memory_measurement": "native-host-process"})
    return parse_result(output, duration), time.monotonic() - started, json.dumps(diagnostic) + "\n" + log


def diarize(wav: Path, config, directory: Path, progress=None, cancelled=None, *,
            num_speakers=None, min_speakers=None, max_speakers=None) -> dict:
    """Same worker contract as Community-1, using automatic count up to eight."""
    from whisper_dictation.media import decode_media, MediaCancelled

    report, cancel = progress or (lambda message, fraction: None), cancelled or (lambda: False)
    root = model_root(config) / "nemotron-3"
    manifest = _prepared(root)
    directory = Path(directory)
    if cancel():
        raise ProcessingCancelled()
    expected = num_speakers if num_speakers is not None else getattr(config, "diarization_num_speakers", 0)
    minimum = min_speakers if min_speakers is not None else getattr(config, "diarization_min_speakers", 0)
    maximum = max_speakers if max_speakers is not None else getattr(config, "diarization_max_speakers", 0)
    if any(int(value or 0) > 8 for value in (expected, minimum, maximum)):
        raise ValueError("Nemotron 3 suporta até 8 falantes. Para quantidade superior, selecione Community-1.")
    warnings = []
    if expected or minimum or maximum:
        warnings.append("Nemotron estima automaticamente até 8 falantes; quantidade/faixa informada não impõe IDs. Use Community-1 para contagem orientada.")
    requested = str(getattr(config, "diarization_backend", "auto"))
    if requested not in {"auto", "cpu", "vulkan"}:
        warnings.append(f"Backend {requested} pertence ao Community-1; Nemotron usará seleção CPU/Vulkan.")
        requested = "auto"
    identity = _hardware_signature(root)
    fingerprint = hashlib.sha256(json.dumps({"audio": _hash(Path(wav)), "model": MODEL_SHA,
                         "runtimes": RUNTIMES, "preset": "v3-offline", "version": "0.2.0",
                         "requested": requested, "hardware": identity,
                         "counts": [expected, minimum, maximum]}, sort_keys=True).encode()).hexdigest()
    checkpoint = directory / "nemotron.result.json"
    try:
        completed = json.loads(checkpoint.read_text(encoding="utf-8"))
        if completed.get("fingerprint") == fingerprint:
            result = completed["result"]
            result["reused_checkpoint"] = True
            report("Diarização completa recuperada do checkpoint.", 1.0)
            return result
    except (OSError, ValueError, KeyError):
        pass
    class CancelEvent:
        def is_set(self):
            return cancel()
    normalized = directory / "nemotron.normalized.wav"
    report("Preparando áudio para diarização local…", 0.0)
    try:
        duration = decode_media(wav, normalized, cancel_event=CancelEvent(),
                                progress=lambda fraction: report("Preparando áudio para diarização local…", fraction * 0.05))
    except MediaCancelled as exc:
        raise ProcessingCancelled() from exc
    probes = _probes(normalized, directory, duration)
    probe_duration = sum(length for path, length in probes)
    if not probes:
        raise ValueError("Áudio não contém amostras suficientes para validar o diarizador.")
    cpu_turns, cpu_time, cpu_log = [], 0.0, ""
    for index, (probe, length) in enumerate(probes):
        turns, elapsed, cpu_log = _infer(root, probe, length, directory / f"nemotron-benchmark-cpu-{index}",
                                        "cpu", cancel, report, 180)
        cpu_turns.append(turns)
        cpu_time += elapsed
    choices = [(cpu_time, "cpu", cpu_log)]
    report("CPU validada; verificando dispositivos Vulkan…", 0.05)
    cache_path = root / "backend-selection.json"
    cached = {}
    try:
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        if cached.get("identity") != identity:
            cached = {}
    except (OSError, ValueError):
        pass
    if requested != "cpu":
        try:
            doctor_output, _ = run_cli([str(_executable(root, "vulkan")), "doctor", "--json"], directory / "nemotron-devices",
                                       cancelled=cancel, timeout=60)
            doctor = json.loads(doctor_output)
            if not doctor.get("driver_runtime_compatible"):
                raise RuntimeError("Driver Vulkan incompatível com o runtime.")
            devices = [f"vulkan:{device['index']}" for device in doctor.get("devices", [])
                       if device.get("type") in {"integrated-gpu", "gpu", "discrete-gpu"}
                       and int(device.get("memory_free", 0)) >= 512 * 1024 * 1024]
            if cached.get("device") == "cpu" and requested == "auto":
                devices = []
            elif cached.get("device") in devices and requested == "auto":
                devices = [cached["device"]] + [device for device in devices if device != cached["device"]]
            for device in devices:
                try:
                    elapsed, log, discrepancy = 0.0, "", 0.0
                    for index, (probe, length) in enumerate(probes):
                        turns, measured, log = _infer(root, probe, length, directory / (f"nemotron-benchmark-{device.replace(':', '-')}-{index}"),
                                                      device, cancel, report, 180)
                        discrepancy = max(discrepancy, disagreement(cpu_turns[index], turns, length))
                        elapsed += measured
                    if discrepancy > 0.01:
                        warnings.append(f"{device} divergiu {discrepancy:.1%} da referência CPU; configuração rejeitada.")
                    else:
                        choices.append((elapsed, device, log))
                        if cached.get("device") == device and requested == "auto":
                            break
                except ProcessingCancelled:
                    raise
                except Exception as exc:
                    warnings.append(f"{device} indisponível: {type(exc).__name__}: {exc}; tentando outro dispositivo.")
        except ProcessingCancelled:
            raise
        except Exception as exc:
            warnings.append(f"Vulkan indisponível: {type(exc).__name__}: {exc}; CPU disponível.")
    if requested == "vulkan" and len(choices) > 1:
        _, device, device_log = min(choices[1:])
    else:
        _, device, device_log = min(choices)
        if requested == "vulkan":
            warnings.append("Nenhum dispositivo Vulkan passou a validação; usando CPU.")
    if requested == "auto":
        atomic_json(cache_path, {"schema": 1, "identity": identity, "device": device,
                                "reference": "cpu", "equivalence_threshold": 0.01,
                                "timings": {choice[1]: choice[0] for choice in choices}})
    estimate = duration / probe_duration * min(choice[0] for choice in choices if choice[1] == device)
    timeout = max(180.0, min(7200.0, estimate * 5 + 120))
    report(f"Identificando falantes em {device}…", 0.1)
    try:
        turns, elapsed, device_log = _infer(root, normalized, duration, directory / "nemotron-inference", device, cancel, report, timeout)
    except ProcessingCancelled:
        raise
    except Exception as exc:
        if device == "cpu":
            raise
        warnings.append(f"{device} falhou: {type(exc).__name__}: {exc}; repetindo em CPU com identidades globais.")
        device = "cpu"
        timeout = max(180.0, min(7200.0, duration / probe_duration * cpu_time * 5 + 120))
        turns, elapsed, device_log = _infer(root, normalized, duration, directory / "nemotron-inference-cpu", device, cancel, report, timeout)
    if cancel():
        raise ProcessingCancelled()
    result = {"turns": turns, "exclusive_turns": exclusive_turns(turns), "backend": "nemotron-" + device,
              "model": MODEL, "license": MODEL_LICENSE, "fallback_reasons": warnings,
              "max_speakers": 8, "speakers_detected": len({turn["speaker"] for turn in turns}),
              "duration": duration, "runtime_version": manifest["runtime_version"], "device_log": device_log,
              "benchmark_seconds": {choice[1]: choice[0] for choice in choices}, "elapsed_seconds": elapsed}
    atomic_json(checkpoint, {"fingerprint": fingerprint, "result": result})
    report("Diarização local concluída.", 1.0)
    return result
