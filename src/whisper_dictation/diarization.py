"""Local, complete speaker diarization and explicitly prepared model assets.

Community-1 provides segmentation, voice embeddings and global VBx clustering.
ONNX/OpenVINO/DirectML replace only segmentation; the rest of that same complete
pipeline stays on CPU. CUDA moves the complete pipeline. A detected accelerator
is never enough: its outputs must agree with the CPU reference on actual audio.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.machinery
import io
import json
import os
import platform
import queue
import statistics
import subprocess
import struct
import sys
import threading
import time
import types
from pathlib import Path
from typing import Callable

import numpy as np

MODEL_ID = "pyannote/speaker-diarization-community-1"
MODEL_URL = "https://huggingface.co/pyannote/speaker-diarization-community-1"
MODEL_LICENSE = "CC-BY-4.0"
Progress = Callable[[str, float], None]
_DLL_HANDLES = []


class ProcessingCancelled(RuntimeError):
    pass


def atomic_json(path: Path, data: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def model_root(config, default: Path | None = None) -> Path:
    configured = str(getattr(config, "diarization_model_dir", "") or "").strip()
    return Path(configured).expanduser() if configured else (
        default or Path(os.environ.get("LOCALAPPDATA", Path.home())) / "PaceData" / "models"
    )


def _version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "unavailable"


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_pipeline(root: Path):
    # Explicit local snapshots avoid both accidental network access during a job
    # and torchcodec's dependency on a separately installed Windows FFmpeg.
    os.environ.setdefault("PYANNOTE_METRICS_ENABLED", "0")
    from pyannote.audio import Pipeline

    pipeline = Pipeline.from_pretrained(root / "community-1", token=False)
    if pipeline is None:
        raise RuntimeError("Não foi possível abrir o pipeline local de diarização.")
    return pipeline


def _export_segmentation(pipeline, destination: Path) -> None:
    import torch

    model = pipeline._segmentation.model.cpu().eval()
    duration = pipeline._segmentation.duration
    samples = int(duration * model.audio.sample_rate)
    example = torch.zeros((1, 1, samples), dtype=torch.float32)
    temporary = destination.with_name("segmentation.pending.onnx")
    try:
        torch.onnx.export(
            model, example, str(temporary), input_names=["waveform"],
            output_names=["scores"], dynamic_axes={"waveform": {0: "batch"}, "scores": {0: "batch"}},
            opset_version=17, dynamo=False,
        )
        run = _runner("onnx-cpu", temporary)
        rng = np.random.default_rng(2026)
        probe = rng.normal(0, 0.02, example.shape).astype(np.float32)
        with torch.inference_mode():
            expected = model(torch.from_numpy(probe)).numpy()
        try:
            _validate_scores(expected, run(probe))
        finally:
            run.close()
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def prepare_models(config, model_root: Path, progress: Progress | None = None) -> dict:
    """Called only by the visible Prepare models action; never by a recording job.

    The user must accept the model conditions at MODEL_URL and configure HF_TOKEN.
    Downloads use the original gated repository, preserving its access conditions.
    """
    if getattr(config, "diarization_engine", "community-1") == "nemotron":
        from whisper_dictation.nemotron_diarization import prepare_models as prepare_nemotron
        return prepare_nemotron(config, model_root, progress)
    from whisper_dictation import secrets_store

    report = progress or (lambda message, fraction: None)
    root = Path(model_root)
    root.mkdir(parents=True, exist_ok=True)
    report("Verificando acesso e licença CC-BY-4.0 do Community-1…", 0.0)
    from huggingface_hub import snapshot_download

    snapshot = root / "community-1"
    needs_download = not (root / "prepared.json").exists()
    if not needs_download:
        try:
            _read_prepared(root)
        except RuntimeError:
            needs_download = True
    if needs_download:
        token = secrets_store.get_api_key("huggingface", env_var="HF_TOKEN")
        if not token:
            raise RuntimeError(
                f"Aceite as condições em {MODEL_URL} e salve um token Hugging Face nas configurações."
            )
        report("Baixando segmentação, embeddings e agrupamento Community-1…", 0.1)
        try:
            snapshot_download(
                repo_id=MODEL_ID, token=token, local_dir=str(snapshot),
                allow_patterns=["config.yaml", "segmentation/**", "embedding/**", "plda/**", "README.md", "LICENSE*"],
                max_workers=2, force_download=(root / "prepared.json").exists(),
            )
        except Exception as exc:
            # Do not include request headers or token in user-visible diagnostics.
            raise RuntimeError(
                "Download Community-1 falhou. Confira conexão, token e aceite das condições do modelo."
            ) from exc
    report("Carregando e validando o pipeline completo em CPU…", 0.5)
    pipeline = _load_pipeline(root)
    import torch
    torch.set_num_threads(max(1, int(getattr(config, "cpu_threads", 0)) or min(4, os.cpu_count() or 1)))
    pipeline.to(torch.device("cpu"))
    pipeline.segmentation_batch_size = 1
    pipeline.embedding_batch_size = 1
    pipeline({"waveform": torch.zeros((1, 160000)), "sample_rate": 16000, "uri": "pace-model-preparation"})
    synthetic = torch.from_numpy(np.random.default_rng(2026).normal(0, 0.02, (1, 1, 160000)).astype(np.float32))
    embeddings = pipeline._embedding(synthetic)
    if embeddings.ndim != 2 or embeddings.shape[0] != 1 or not np.isfinite(embeddings).all():
        raise RuntimeError("Modelo de embeddings falhou na inferência de preparação.")
    # Silence can exit the full pipeline before embeddings/VBx. Exercise the
    # embedding model and its PLDA projection explicitly with synthetic input.
    projected = pipeline._plda(embeddings)
    if not np.isfinite(projected).all():
        raise RuntimeError("Modelo PLDA falhou na preparação.")
    export_error = ""
    report("Preparando segmentação ONNX com validação contra o modelo original…", 0.65)
    try:
        _export_segmentation(pipeline, root / "segmentation.onnx")
    except Exception as exc:
        export_error = f"Exportação ONNX indisponível ({type(exc).__name__}); CPU PyTorch continua disponível."
        report(export_error, 0.75)

    provider = str(config.transcription_provider)
    timestamp_fallback = str(getattr(config, "meeting_timestamp_provider", "local"))
    uses_local = provider == "local" or (
        timestamp_fallback == "local" and (provider == "gemini" or (provider == "openai" and config.openai_model != "whisper-1"))
    )
    asr_path = ""
    if uses_local:
        from faster_whisper.utils import download_model
        report("Preparando Whisper local para transcrição com timestamps…", 0.8)
        if Path(config.model_size).is_dir():
            asr_path = str(Path(config.model_size).resolve())
        else:
            asr_path = download_model(config.model_size, cache_dir=str(root / "asr"))

    files = {str(path.relative_to(root)): _hash(path) for path in snapshot.rglob("*")
             if path.is_file() and ".cache" not in path.parts}
    if (root / "segmentation.onnx").is_file():
        files["segmentation.onnx"] = _hash(root / "segmentation.onnx")
    manifest = {"schema": 1, "model": MODEL_ID, "source": MODEL_URL, "license": MODEL_LICENSE,
                "files": files, "pyannote_version": _version("pyannote.audio"),
                "onnx_export_error": export_error, "asr_path": asr_path, "asr_model": config.model_size}
    atomic_json(root / "prepared.json", manifest)
    if asr_path:
        atomic_json(root / "asr-prepared.json", {"asr_path": asr_path, "asr_model": config.model_size})
    report("Modelos preparados. Aceleração será validada no áudio de cada tarefa.", 1.0)
    return manifest


def _validate_scores(reference: np.ndarray, candidate: np.ndarray) -> None:
    if reference.shape != candidate.shape or not np.isfinite(candidate).all():
        raise RuntimeError("Backend retornou dimensões incorretas ou valores inválidos.")
    if not np.allclose(reference, candidate, atol=0.015, rtol=0.015):
        raise RuntimeError("Backend divergiu da referência CPU; configuração rejeitada.")
    # Segmentation is permutation-sensitive here: comparing before powerset
    # conversion also rejects accelerators that silently reorder model classes.
    if np.mean(np.argmax(reference, axis=-1) == np.argmax(candidate, axis=-1)) < 0.995:
        raise RuntimeError("Backend mudou decisões de segmentação; configuração rejeitada.")


def _inprocess_runner(backend: str, model: Path):
    if backend.startswith("openvino-"):
        import openvino as ov
        device = backend.removeprefix("openvino-").upper()
        core = ov.Core()
        if device not in core.available_devices:
            raise RuntimeError(f"OpenVINO não detectou {device}.")
        compiled = core.compile_model(str(model), device,
                                      {"PERFORMANCE_HINT": "LATENCY", "INFERENCE_PRECISION_HINT": "f32"})
        execution_devices = compiled.get_property("EXECUTION_DEVICES")
        if not any(str(actual).startswith(device) for actual in execution_devices):
            raise RuntimeError(f"OpenVINO não executará no dispositivo solicitado: {device}.")
        return lambda data: np.asarray(compiled([data])[compiled.output(0)])
    if backend == "directml":
        vendor = _directml_root()
        if vendor.is_dir():
            # PyInstaller's frozen importer precedes sys.path. A finder scoped
            # to this one namespace ensures the child loads the vendor wheel,
            # while the application's CPU runtime keeps its normal package.
            class VendorFinder:
                def find_spec(self, fullname, path=None, target=None):
                    if fullname == "onnxruntime" or fullname.startswith("onnxruntime."):
                        return importlib.machinery.PathFinder.find_spec(fullname, path)
                    return None
            if any(name == "onnxruntime" or name.startswith("onnxruntime.") for name in sys.modules):
                raise RuntimeError("Runtime CPU já importado no processo; DirectML exige processo isolado.")
            sys.path.insert(0, str(vendor))
            sys.meta_path.insert(0, VendorFinder())
            if os.name == "nt":
                for folder in (vendor, vendor / "onnxruntime" / "capi"):
                    if folder.is_dir():
                        _DLL_HANDLES.append(os.add_dll_directory(str(folder)))
            import onnxruntime as ort
            if not Path(ort.__file__).resolve().is_relative_to(vendor.resolve()):
                raise RuntimeError("DirectML não carregou o runtime isolado esperado.")
        else:
            import onnxruntime as ort
    else:
        import onnxruntime as ort
    provider = {"onnx-cpu": "CPUExecutionProvider", "directml": "DmlExecutionProvider"}[backend]
    if provider not in ort.get_available_providers():
        raise RuntimeError(f"Runtime {provider} não está instalado neste ambiente.")
    options = ort.SessionOptions()
    options.intra_op_num_threads = 1
    if backend == "directml":
        options.enable_mem_pattern = False
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.enable_profiling = True
        options.profile_file_prefix = str(model.parent / ("directml-profile-" + str(os.getpid())))
    session = ort.InferenceSession(str(model), sess_options=options, providers=[provider])
    if session.get_providers()[0] != provider:
        raise RuntimeError(f"Runtime recusou {provider}; não declarar aceleração.")
    profiled = False
    def infer(data):
        nonlocal profiled
        result = session.run(None, {session.get_inputs()[0].name: data})[0]
        if backend == "directml" and not profiled:
            trace = Path(session.end_profiling())
            try:
                events = json.loads(trace.read_text(encoding="utf-8"))
                if not any(event.get("args", {}).get("provider") == provider for event in events):
                    raise RuntimeError("Modelo executou integralmente em CPU; DirectML não foi aplicado.")
            finally:
                trace.unlink(missing_ok=True)
            profiled = True
        return result
    return infer


def _directml_root() -> Path:
    override = os.environ.get("PACE_ORT_VENDOR", "").strip()
    if override:
        return Path(override).resolve()
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "dml_runtime"
    return Path(__file__).resolve().parents[2] / ".runtime-directml"


_MAX_MESSAGE = 32 * 1024 * 1024


def _read_message(stream) -> bytes:
    header = stream.read(4)
    if len(header) != 4:
        raise EOFError("Processo de inferência encerrou inesperadamente.")
    length = struct.unpack("<I", header)[0]
    if length <= 0 or length > _MAX_MESSAGE:
        raise RuntimeError("Mensagem do runtime excede o limite permitido.")
    data = bytearray()
    while len(data) < length:
        block = stream.read(length - len(data))
        if not block:
            raise EOFError("Resposta incompleta do runtime.")
        data.extend(block)
    return bytes(data)


def _write_message(stream, data: bytes) -> None:
    if not 0 < len(data) <= _MAX_MESSAGE:
        raise ValueError("Entrada de inferência excede o limite permitido.")
    stream.write(struct.pack("<I", len(data)))
    stream.write(data)
    stream.flush()


def runtime_server(model, backend: str) -> int:
    """Private subprocess entry point; stdout contains only framed binary data."""
    if sys.stdin is None or sys.stdout is None or sys.stderr is None:
        # Windowed PyInstaller executables have no Python stdio objects even
        # when Popen supplies valid Windows pipe handles.
        if os.name != "nt":
            raise RuntimeError("Runtime sem canais de comunicação.")
        import ctypes
        import msvcrt
        from ctypes import wintypes
        get_handle = ctypes.windll.kernel32.GetStdHandle
        get_handle.argtypes = [wintypes.DWORD]
        get_handle.restype = wintypes.HANDLE
        def channel(number, flags, mode):
            handle = get_handle(number & 0xffffffff)
            if not handle or handle == ctypes.c_void_p(-1).value:
                raise RuntimeError("Pipe do runtime não foi disponibilizado.")
            return os.fdopen(msvcrt.open_osfhandle(handle, flags | os.O_BINARY), mode, buffering=0)
        source = channel(-10, os.O_RDONLY, "rb")
        target = channel(-11, os.O_WRONLY, "wb")
        error = channel(-12, os.O_WRONLY, "wb")
    else:
        source, target, error = sys.stdin.buffer, sys.stdout.buffer, sys.stderr.buffer
    # Redirect Python/native library chatter away from the protocol stream.
    protocol_fd = os.dup(target.fileno())
    os.dup2(error.fileno(), target.fileno())
    sys.stdout = io.TextIOWrapper(os.fdopen(os.dup(error.fileno()), "wb"), write_through=True)
    protocol = os.fdopen(protocol_fd, "wb", buffering=0)
    try:
        run = _inprocess_runner(backend, Path(model))
        _write_message(protocol, b'{"ready":true}')
        while True:
            try:
                payload = _read_message(source)
            except EOFError:
                return 0
            try:
                data = np.load(io.BytesIO(payload), allow_pickle=False)
                if not isinstance(data, np.ndarray) or data.dtype != np.float32 or data.ndim != 3 or data.shape[:2] != (1, 1):
                    raise ValueError("Formato de entrada de inferência inválido.")
                out = np.asarray(run(data), dtype=np.float32)
                buffer = io.BytesIO()
                np.save(buffer, out, allow_pickle=False)
                _write_message(protocol, buffer.getvalue())
            except Exception as exc:
                _write_message(protocol, json.dumps({"error": f"{type(exc).__name__}: {exc}"}).encode())
    except Exception as exc:
        _write_message(protocol, json.dumps({"error": f"{type(exc).__name__}: {exc}"}).encode())
        return 1
    finally:
        protocol.close()


class _RemoteRunner:
    """Keep accelerator crashes/hangs out of the job worker and bound requests."""
    def __init__(self, backend: str, model: Path):
        self.backend, self.model = backend, Path(model)
        self.process = None
        self.responses = None
        self.log = None

    def _receive(self, output, responses):
        try:
            while True:
                responses.put(_read_message(output))
        except Exception as exc:
            responses.put(exc)

    def _wait(self, timeout: float):
        try:
            response = self.responses.get(timeout=timeout)
        except queue.Empty as exc:
            self.close()
            raise TimeoutError(f"{self.backend} excedeu {timeout:g}s; runtime interrompido.") from exc
        if isinstance(response, Exception):
            self.close()
            raise response
        if response.startswith(b"{"):
            message = json.loads(response)
            if message.get("error"):
                self.close()
                raise RuntimeError(message["error"])
            return message
        return response

    def _start(self):
        if self.process is not None:
            return
        if getattr(sys, "frozen", False):
            command = [sys.executable, "--diarization-runtime", "--runtime-model", str(self.model),
                       "--runtime-backend", self.backend]
        else:
            command = [sys.executable, "-m", "whisper_dictation.diarization", "--runtime-model", str(self.model),
                       "--runtime-backend", self.backend]
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent) + os.pathsep + environment.get("PYTHONPATH", "")
        self.log = (self.model.parent / ("runtime-" + self.backend + ".log")).open("ab")
        self.responses = queue.Queue()
        try:
            self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log,
                                            env=environment, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            threading.Thread(target=self._receive, args=(self.process.stdout, self.responses), daemon=True).start()
            if self._wait(90.0) != {"ready": True}:
                raise RuntimeError("Runtime não confirmou inicialização.")
        except Exception:
            self.close()
            raise

    def __call__(self, data):
        self._start()
        buffer = io.BytesIO()
        np.save(buffer, np.asarray(data, dtype=np.float32), allow_pickle=False)
        payload = buffer.getvalue()
        if len(payload) > _MAX_MESSAGE:
            raise ValueError("Entrada de inferência excede o limite permitido.")
        stream, responses = self.process.stdin, self.responses
        def send():
            try:
                _write_message(stream, payload)
            except Exception as exc:
                responses.put(exc)
        threading.Thread(target=send, daemon=True).start()
        result = self._wait(60.0)
        return np.load(io.BytesIO(result), allow_pickle=False)

    def close(self):
        process, self.process = self.process, None
        if process is not None:
            if process.poll() is None:
                process.kill()
            try:
                process.wait(timeout=5)
            finally:
                for stream in (process.stdin, process.stdout):
                    if stream:
                        stream.close()
        if self.log:
            self.log.close()
            self.log = None


def _runner(backend: str, model: Path):
    return _RemoteRunner(backend, model)


def _candidate_names(requested: str, onnx_exists: bool) -> list[str]:
    names = ["torch-cpu"]
    if onnx_exists:
        names += ["onnx-cpu", "openvino-cpu", "openvino-gpu", "directml"]
    names += ["cuda"]
    aliases = {"cpu": "torch-cpu", "onnx": "onnx-cpu", "openvino": "openvino-gpu"}
    requested = aliases.get(requested, requested)
    if requested != "auto":
        if requested not in {"torch-cpu", "onnx-cpu", "openvino-cpu", "openvino-gpu", "directml", "cuda"}:
            raise ValueError(f"Backend desconhecido: {requested}")
        names = [requested] + ([] if requested == "torch-cpu" else ["torch-cpu"])
    return names


def _hardware_signature(root: Path) -> str:
    identity = {"machine": platform.machine(), "cpu": platform.processor(), "os": platform.version(),
                "versions": {name: _version(name) for name in ("torch", "onnxruntime", "onnxruntime-directml", "openvino", "pyannote.audio")},
                "onnx": _hash(root / "segmentation.onnx") if (root / "segmentation.onnx").exists() else "",
                "prepared": _hash(root / "prepared.json") if (root / "prepared.json").exists() else ""}
    vendor_manifest = _directml_root() / "pace-runtime.json"
    if vendor_manifest.exists():
        identity["directml_vendor"] = _hash(vendor_manifest)
    if os.name == "nt":
        try:
            info = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                 "Get-CimInstance Win32_VideoController | Select-Object Name,DriverVersion,AdapterRAM | ConvertTo-Json -Compress"],
                check=True, capture_output=True, text=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            identity["drivers"] = info.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            # Unknown driver identity disables reuse: don't trust a stale GPU
            # decision when the environment cannot be inspected.
            identity["drivers"] = str(time.time_ns())
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


def _select_backend(pipeline, root: Path, probes: list[np.ndarray], requested: str, progress: Progress,
                    cancelled: Callable[[], bool]) -> tuple[str, object, list[str]]:
    import torch

    model = pipeline._segmentation.model.cpu().eval()
    original = model.forward
    def cpu(data):
        with torch.inference_mode():
            return original(torch.from_numpy(data)).cpu().numpy()
    references = [cpu(probe) for probe in probes]
    accepted = []
    failures = []
    identity = _hardware_signature(root)
    candidates = _candidate_names(requested, (root / "segmentation.onnx").exists())
    cache_path = root / "backend-selection.json"
    cached_winner = None
    if requested == "auto" and cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if cached.get("identity") == identity and cached.get("selection_mode") == "auto" and cached.get("backend") in candidates:
                winner = cached["backend"]
                cached_winner = winner if cached.get("schema") == 1 and cached.get("validated_on_audio") else None
                candidates = [winner] + [name for name in candidates if name != winner]
        except (OSError, ValueError, KeyError):
            pass
    for backend in candidates:
        if cancelled():
            raise ProcessingCancelled()
        progress(f"Validando {backend} com inferência real…", 0.0)
        runner = None
        try:
            if backend == "torch-cpu":
                runner = cpu
            elif backend == "cuda":
                if not torch.cuda.is_available():
                    raise RuntimeError("CUDA não disponível.")
                free_memory, _ = torch.cuda.mem_get_info()
                if free_memory < 512 * 1024 * 1024:
                    raise RuntimeError("CUDA tem menos de 512 MiB livres; mantendo CPU.")
                model.to("cuda")
                def runner(data):
                    with torch.inference_mode():
                        out = original(torch.from_numpy(data).to("cuda"))
                        torch.cuda.synchronize()
                        return out.cpu().numpy()
            else:
                model.cpu()
                runner = _runner(backend, root / "segmentation.onnx")
            elapsed = []
            for probe, reference in zip(probes, references):
                started = time.perf_counter()
                _validate_scores(reference, runner(probe))
                elapsed.append(time.perf_counter() - started)
            accepted.append((statistics.median(elapsed), backend, runner))
            if requested != "auto" or backend == cached_winner:
                break
        except ProcessingCancelled:
            raise
        except Exception as exc:
            failures.append(f"{backend}: {type(exc).__name__}: {exc}")
        finally:
            model.cpu()
            if isinstance(runner, _RemoteRunner):
                runner.close()
    if not accepted:
        raise RuntimeError("Nenhum backend passou a validação: " + "; ".join(failures))
    # CUDA accelerates both neural stages; the ONNX candidates only accelerate
    # segmentation. Comparing segmentation alone would mis-rank a meeting.
    if requested == "auto" and len(accepted) > 1:
        comparative = []
        from pyannote.metrics.diarization import DiarizationErrorRate
        representative = torch.from_numpy(max(probes, key=lambda probe: float(np.mean(probe ** 2)))[0])
        pipeline.to(torch.device("cpu"))
        original_output = pipeline({"waveform": representative, "sample_rate": 16000, "uri": "pace-backend-reference"})
        reference_annotation = original_output.speaker_diarization
        for _, backend, runner in accepted:
            if cancelled():
                raise ProcessingCancelled()
            try:
                pipeline.to(torch.device("cuda" if backend == "cuda" else "cpu"))
                if backend not in {"torch-cpu", "cuda"}:
                    def accelerated(self, data, _run=runner):
                        return torch.from_numpy(_run(data.detach().cpu().numpy()))
                    model.forward = types.MethodType(accelerated, model)
                else:
                    model.forward = original
                started = time.perf_counter()
                output = pipeline({"waveform": representative, "sample_rate": 16000, "uri": "pace-backend-probe"})
                elapsed = time.perf_counter() - started
                error = DiarizationErrorRate(collar=0.0, skip_overlap=False)(reference_annotation, output.speaker_diarization)
                if error > 0.01:
                    raise RuntimeError(f"Diarização no backend divergiu {error:.1%} da referência CPU.")
                comparative.append((elapsed, backend, runner))
            except ProcessingCancelled:
                raise
            except Exception as exc:
                failures.append(f"{backend}: {type(exc).__name__}: {exc}")
            finally:
                model.forward = original
                pipeline.to(torch.device("cpu"))
                if isinstance(runner, _RemoteRunner):
                    runner.close()
        accepted = comparative
    if not accepted:
        raise RuntimeError("Backends falharam na validação do pipeline completo.")
    _, backend, runner = min(accepted, key=lambda candidate: candidate[0])
    if requested == "auto":
        atomic_json(cache_path, {"schema": 1, "selection_mode": "auto", "identity": identity, "backend": backend,
                                 "validated_on_audio": True, "timings": {name: seconds for seconds, name, _ in accepted}})
    if backend == "cuda":
        pipeline.to(torch.device("cuda"))
    else:
        pipeline.to(torch.device("cpu"))
    return backend, runner, failures


def _read_prepared(root: Path) -> dict:
    try:
        manifest = json.loads((root / "prepared.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError("Prepare os modelos em Configurações antes de processar gravações.") from exc
    if manifest.get("model") != MODEL_ID or manifest.get("schema") != 1:
        raise RuntimeError("Cache de modelos incompatível; prepare os modelos novamente.")
    for name, digest in manifest["files"].items():
        path = (root / name).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file() or _hash(path) != digest:
            raise RuntimeError("Arquivos do modelo ausentes ou alterados; prepare os modelos novamente.")
    return manifest


def _waveform_map(wav: Path, directory: Path, cancelled: Callable[[], bool]):
    import av

    # RF64 is not accepted by Python's wave module. Decode incrementally and
    # memory-map normalized floats so multi-hour audio need not fit in RAM.
    path = directory / "waveform.f32"
    with av.open(str(wav)) as source, path.open("wb") as target:
        streams = list(source.streams.audio)
        if not streams:
            raise ValueError("Arquivo sem faixa de áudio.")
        resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
        for frame in source.decode(streams[0]):
            if cancelled():
                raise ProcessingCancelled()
            for normalized in resampler.resample(frame):
                pcm = normalized.to_ndarray().reshape(-1)
                target.write((pcm.astype(np.float32) / 32768.0).tobytes())
        for normalized in resampler.resample(None):
            pcm = normalized.to_ndarray().reshape(-1)
            target.write((pcm.astype(np.float32) / 32768.0).tobytes())
    if path.stat().st_size == 0:
        return np.zeros(0, dtype=np.float32)
    return np.memmap(path, dtype=np.float32, mode="r+")


def _turns(annotation, duration: float) -> list[dict]:
    result = []
    for turn, _, speaker in annotation.itertracks(yield_label=True):
        start, end = float(turn.start), float(turn.end)
        if not np.isfinite(start) or not np.isfinite(end):
            continue
        start, end = max(0.0, start), min(duration, end)
        if end > start:
            result.append({"start": start, "end": end, "speaker": str(speaker)})
    return sorted(result, key=lambda turn: (turn["start"], turn["end"], turn["speaker"]))


def diarize(wav: Path, config, directory: Path, progress: Progress | None = None,
            cancelled: Callable[[], bool] | None = None, *, num_speakers: int | None = None,
            min_speakers: int | None = None, max_speakers: int | None = None) -> dict:
    if getattr(config, "diarization_engine", "community-1") == "nemotron":
        from whisper_dictation.nemotron_diarization import diarize as diarize_nemotron
        return diarize_nemotron(wav, config, directory, progress, cancelled,
                               num_speakers=num_speakers, min_speakers=min_speakers, max_speakers=max_speakers)
    import torch

    report = progress or (lambda message, fraction: None)
    cancel = cancelled or (lambda: False)
    root = model_root(config)
    manifest = _read_prepared(root)
    torch.set_num_threads(max(1, int(getattr(config, "cpu_threads", 0)) or min(4, os.cpu_count() or 1)))
    pipeline = _load_pipeline(root)
    pipeline.segmentation_batch_size = 1
    pipeline.embedding_batch_size = 1
    waveform = _waveform_map(Path(wav), Path(directory), cancel)
    duration = waveform.size / 16000
    if not waveform.size:
        return {"turns": [], "exclusive_turns": [], "backend": "torch-cpu", "fallback_reasons": []}
    window_size = int(pipeline._segmentation.duration * 16000)
    probes = []
    offsets = {0, max(0, waveform.size // 2 - window_size // 2), max(0, waveform.size - window_size)}
    transcript_path = Path(directory) / "transcript.json"
    try:
        speech_segments = json.loads(transcript_path.read_text(encoding="utf-8")).get("segments", [])
        if speech_segments:
            offsets = {max(0, min(waveform.size - window_size, int(float(speech_segments[index]["start"]) * 16000)))
                       for index in (0, len(speech_segments) // 2, len(speech_segments) - 1)}
    except (ValueError, OSError, KeyError, TypeError):
        pass
    for offset in sorted(offsets):
        probe = np.asarray(waveform[offset:offset + window_size])
        probes.append(np.pad(probe, (0, max(0, window_size - probe.size)))[None, None, :].astype(np.float32))
    requested = str(getattr(config, "diarization_backend", "auto"))
    backend, runner, failures = _select_backend(pipeline, root, probes, requested, report, cancel)
    model = pipeline._segmentation.model
    original = model.forward
    if backend not in {"torch-cpu", "cuda"}:
        def forward(self, tensors):
            return torch.from_numpy(runner(tensors.detach().cpu().numpy()))
        model.forward = types.MethodType(forward, model)
    audio = {"waveform": torch.from_numpy(waveform).unsqueeze(0), "sample_rate": 16000, "uri": Path(wav).stem}
    checkpoints = Path(directory) / "diarization-checkpoints"
    checkpoints.mkdir(exist_ok=True)
    fingerprint = hashlib.sha256(json.dumps({"audio": _hash(Path(wav)), "files": manifest["files"],
                                             "version": _version("pyannote.audio")}, sort_keys=True).encode()).hexdigest()
    def hook(step, artifact=None, **kwargs):
        if cancel():
            raise ProcessingCancelled()
        total, completed = kwargs.get("total", 0), kwargs.get("completed", 0)
        report(step, completed / total if total else 0.0)
    # Only complete stages are checkpointed. Global clustering is always rerun
    # over all embeddings, so restarting never creates per-chunk speaker IDs.
    for name in ("get_segmentations", "get_embeddings"):
        method = getattr(pipeline, name)
        path = checkpoints / (name + ".npz")
        def cached(*args, _method=method, _path=path, **kwargs):
            from pyannote.core import SlidingWindow, SlidingWindowFeature
            if cancel():
                raise ProcessingCancelled()
            if _path.exists():
                try:
                    with np.load(_path, allow_pickle=False) as saved:
                        if str(saved["fingerprint"].item()) == fingerprint:
                            if "window" in saved:
                                start, duration_, step = saved["window"]
                                return SlidingWindowFeature(saved["data"], SlidingWindow(start=start, duration=duration_, step=step))
                            return saved["data"]
                except (ValueError, OSError, KeyError):
                    pass
            value = _method(*args, **kwargs)
            content = {"fingerprint": np.asarray(fingerprint), "data": value.data if hasattr(value, "sliding_window") else value}
            if hasattr(value, "sliding_window"):
                window = value.sliding_window
                content["window"] = np.array([window.start, window.duration, window.step])
            temporary = _path.with_suffix(".tmp")
            with temporary.open("wb") as stream:
                np.savez(stream, **content)
            os.replace(temporary, _path)
            return value
        setattr(pipeline, name, cached)
    kwargs = {}
    count = int(num_speakers if num_speakers is not None else getattr(config, "diarization_num_speakers", 0))
    if count > 0:
        kwargs["num_speakers"] = count
    else:
        if min_speakers is None:
            min_speakers = getattr(config, "diarization_min_speakers", 0)
        if max_speakers is None:
            max_speakers = getattr(config, "diarization_max_speakers", 0)
        if min_speakers is not None and int(min_speakers) > 0:
            kwargs["min_speakers"] = int(min_speakers)
        if max_speakers is not None and int(max_speakers) > 0:
            kwargs["max_speakers"] = int(max_speakers)
        if kwargs.get("min_speakers", 1) > kwargs.get("max_speakers", float("inf")):
            raise ValueError("Quantidade mínima de falantes excede a máxima.")
    try:
        try:
            output = pipeline(audio, hook=hook, **kwargs)
        except ProcessingCancelled:
            raise
        except Exception as exc:
            if backend == "torch-cpu":
                raise
            failures.append(f"{backend} falhou durante o processamento: {type(exc).__name__}: {exc}; usando CPU.")
            model.forward = original
            pipeline.to(torch.device("cpu"))
            backend = "torch-cpu"
            output = pipeline(audio, hook=hook, **kwargs)
        if cancel():
            raise ProcessingCancelled()
        return {"turns": _turns(output.speaker_diarization, duration),
                "exclusive_turns": _turns(output.exclusive_speaker_diarization, duration),
                "backend": backend if backend in {"torch-cpu", "cuda"} else backend + "+cpu-embeddings-vbx",
                "fallback_reasons": failures, "model": MODEL_ID, "license": MODEL_LICENSE,
                "hardware": platform.machine()}
    finally:
        model.forward = original
        if isinstance(runner, _RemoteRunner):
            runner.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-model", required=True)
    parser.add_argument("--runtime-backend", required=True)
    args = parser.parse_args()
    raise SystemExit(runtime_server(args.runtime_model, args.runtime_backend))
