"""Transcription jobs requested by AI agents (pace-cli and the MCP server).

A job is an ordinary library recording (origin ``import``) processed by the same
``Pace.exe --process-recording`` worker the tray uses, so results appear in the
PACE window and survive restarts. The worker is detached from the caller: an
agent's tool timeout or a closed terminal never loses the transcription.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from whisper_dictation.processes import worker_alive
from whisper_dictation.recording_store import ACTIVE_STATUSES, RecordingStore

MEDIA_EXTENSIONS = frozenset({".wav", ".mp3", ".m4a", ".ogg", ".oga", ".opus", ".flac", ".aac", ".wma",
                              ".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".3gp", ".amr"})
DONE_STATUSES = frozenset({"ready", "failed", "cancelled", "interrupted"})
MAX_RESUMES = 2


class JobError(RuntimeError):
    """User-facing failure with a stable exit code and an actionable hint."""

    def __init__(self, message: str, hint: str = "", exit_code: int = 1, code: str = "error"):
        super().__init__(message)
        self.message, self.hint, self.exit_code, self.code = message, hint, exit_code, code

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "hint": self.hint}


@dataclass
class Context:
    project_root: Path  # installation folder: config.json, .env, Pace.exe
    code_root: Path | None = None  # running from source: where main.py and .venv live

    def __post_init__(self):
        # Workers run with the recording folder as working directory.
        self.project_root = Path(self.project_root).resolve()
        self.code_root = Path(self.code_root).resolve() if self.code_root else None

    @property
    def config_path(self) -> Path:
        from whisper_dictation.settings import config_path
        return config_path(self.project_root)

    def config(self):
        from whisper_dictation.settings import AppConfig, load_env_file
        load_env_file(self.project_root)  # legacy .env keys are inherited by the worker
        return AppConfig.load(self.config_path)

    def store(self, config=None) -> RecordingStore:
        from whisper_dictation.settings import recordings_dir
        return RecordingStore(recordings_dir(config or self.config()))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def effective_provider(config) -> tuple[str, str]:
    from whisper_dictation.meeting_processing import _effective_provider
    provider, model, _ = _effective_provider(config)
    return provider, model


def key_configured(config, provider: str) -> bool:
    if provider == "local":
        return True
    from whisper_dictation import secrets_store
    return secrets_store.has_api_key(provider, config.groq_api_key_env if provider == "groq" else None)


def _validate_count(name: str, value) -> int | None:
    if value in (None, "", 0):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise JobError(f"{name} deve ser um número inteiro.", exit_code=2, code="invalid_argument") from None
    if not 1 <= number <= 50:
        raise JobError(f"{name} deve estar entre 1 e 50.", exit_code=2, code="invalid_argument")
    return number


def _worker_command(context: Context, *arguments: str) -> list[str]:
    if getattr(sys, "frozen", False):
        return [str(context.project_root / "Pace.exe"), *arguments]
    code = context.code_root or context.project_root
    python = code / ".venv" / "Scripts" / "pythonw.exe"
    return [str(python) if python.exists() else sys.executable, str(code / "main.py"), *arguments]


def _spawn_worker(context: Context, store: RecordingStore, recording_id: str) -> int:
    directory = store.directory(recording_id)
    snapshot = directory / "worker_config.json"
    command = _worker_command(context, "--process-recording", recording_id,
                              "--store-root", str(store.root), "--config-path", str(snapshot))
    environment = os.environ.copy()
    environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    options: dict = {"close_fds": True, "stdin": subprocess.DEVNULL, "cwd": str(directory), "env": environment}
    flags = 0
    if os.name == "nt":
        # No console window; own process group; survive the agent closing its terminal.
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    with (directory / "worker.log").open("ab") as log:
        options.update(stdout=log, stderr=log)
        try:
            # Agents often run commands inside a kill-on-close job object; leave it
            # so the transcription continues if the agent's tool call times out.
            process = subprocess.Popen(command, creationflags=flags | 0x01000000, **options)  # BREAKAWAY_FROM_JOB
        except OSError:
            process = subprocess.Popen(command, creationflags=flags, **options)
    return process.pid


def start(context: Context, source: str | os.PathLike, *, speakers=None, min_speakers=None, max_speakers=None,
          language: str | None = None, local: bool = False, force: bool = False, title: str | None = None) -> tuple[dict, bool]:
    """Queue a file for transcription + diarization. Returns (record, reused)."""
    path = Path(os.path.expandvars(str(source))).expanduser()
    try:
        path = path.resolve(strict=True)
    except OSError:
        raise JobError(f"Arquivo não encontrado: {source}", "Confira o caminho completo (use aspas se tiver espaços).",
                       exit_code=2, code="file_not_found") from None
    if not path.is_file():
        raise JobError(f"Não é um arquivo: {path}", exit_code=2, code="file_not_found")
    if path.stat().st_size == 0:
        raise JobError(f"Arquivo vazio: {path}", exit_code=2, code="empty_file")
    speakers = _validate_count("speakers", speakers)
    min_speakers = _validate_count("min_speakers", min_speakers)
    max_speakers = _validate_count("max_speakers", max_speakers)
    if min_speakers and max_speakers and min_speakers > max_speakers:
        raise JobError("min_speakers não pode ser maior que max_speakers.", exit_code=2, code="invalid_argument")
    language = (language or "").strip().lower() or None

    config = context.config()
    if local:
        config.transcription_provider = "local"
    if language:
        config.language = language
    provider, model = effective_provider(config)
    if not key_configured(config, provider):
        raise JobError(f"Sem chave de API configurada para {provider}.",
                       "Abra PACE → Configurações e cole a chave do Groq (console.groq.com/keys), "
                       "ou transcreva offline com --local.", code="missing_api_key")

    store = context.store(config)
    options = {"speakers": speakers, "min_speakers": min_speakers, "max_speakers": max_speakers,
               "language": config.language, "provider": provider, "model": model}
    digest = _sha256(path)
    if not force:
        for row in store.list():
            if row.get("source_sha256") != digest or row.get("job_options") != options:
                continue
            if row["status"] == "ready" and row.get("result_path") and Path(row["result_path"]).is_file():
                return row, True
            if row["status"] in ACTIVE_STATUSES | {"queued"}:
                return ensure_running(context, row["id"]), True

    record = store.create(title or path.stem, "import", source_path=str(path), status="processing",
                          requested_by="agent", source_sha256=digest, job_options=options,
                          num_speakers=speakers, min_speakers=min_speakers, max_speakers=max_speakers,
                          stage="Na fila")
    try:
        config.save(store.directory(record["id"]) / "worker_config.json")
        pid = _spawn_worker(context, store, record["id"])
    except Exception as exc:
        store.update(record["id"], status="failed", error=f"Não foi possível iniciar o processamento: {exc}")
        raise JobError(f"Não foi possível iniciar o processamento: {exc}", code="worker_failed") from exc
    return store.update(record["id"], worker_pid=pid), False


def get(context: Context, recording_id: str) -> dict:
    store = context.store()
    try:
        return store.get(recording_id)
    except (KeyError, ValueError):
        raise JobError(f"Transcrição não encontrada: {recording_id}", "Use list para ver os ids.",
                       exit_code=2, code="not_found") from None


def ensure_running(context: Context, recording_id: str) -> dict:
    """Resume a job whose worker died (computer slept, update, crash).

    The worker reuses checkpoints. A job queued while the PACE tray is running is
    left to the tray's queue; otherwise this process claims and resumes it.
    """
    record = get(context, recording_id)
    status = record["status"]
    if status in ACTIVE_STATUSES - {"resuming"} and worker_alive(record.get("worker_pid")):
        return record
    if status == "queued":
        from whisper_dictation.single_instance import probe
        if probe():
            return record
    if status not in (ACTIVE_STATUSES | {"queued"}) - {"resuming"}:
        return record
    store = context.store()
    attempts = int(record.get("resume_count") or 0)
    if attempts >= MAX_RESUMES:
        detail = _log_tail(store.directory(recording_id) / "worker.log")
        store.transition(recording_id, status, "failed", stage="Falha",
                         error="O processamento parou sem concluir." + (f" Último registro: {detail}" if detail else ""))
        return store.get(recording_id)
    if not store.transition(recording_id, status, "resuming", stage="Retomando", resume_count=attempts + 1):
        return store.get(recording_id)
    try:
        snapshot = store.directory(recording_id) / "worker_config.json"
        if not snapshot.is_file():
            context.config().save(snapshot)
        pid = _spawn_worker(context, store, recording_id)
    except Exception as exc:
        return store.update(recording_id, status="failed", error=f"Não foi possível retomar: {exc}")
    return store.update(recording_id, status="processing", worker_pid=pid)


def _log_tail(path: Path, limit: int = 300) -> str:
    try:
        lines = [line.strip() for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
    except OSError:
        return ""
    return lines[-1][-limit:] if lines else ""


def wait(context: Context, recording_id: str, timeout: float | None = None,
         on_progress: Callable[[dict], None] | None = None, interval: float = 1.0) -> dict:
    """Wait until done or ``timeout`` seconds (None = no limit). Never cancels the job."""
    deadline = None if timeout is None else time.monotonic() + max(0.0, timeout)
    last_check = 0.0
    signature = None
    while True:
        record = get(context, recording_id)
        if record["status"] in DONE_STATUSES:
            return record
        now = time.monotonic()
        if now - last_check >= 10:
            record = ensure_running(context, recording_id)
            last_check = now
        current = (record["status"], record.get("stage"), round(float(record.get("progress") or 0), 2))
        if on_progress and current != signature:
            on_progress(record)
        signature = current
        if deadline is not None and now >= deadline:
            return record
        time.sleep(interval if deadline is None else min(interval, max(0.05, deadline - now)))


def cancel(context: Context, recording_id: str) -> dict:
    record = get(context, recording_id)
    if record["status"] in DONE_STATUSES:
        return record
    store = context.store()
    (store.directory(recording_id) / "cancel.request").write_text("agent", encoding="utf-8")
    if not worker_alive(record.get("worker_pid")):
        store.transition(recording_id, record["status"], "cancelled", stage="Cancelado", error="")
    return store.get(recording_id)


def result(context: Context, recording_id: str) -> tuple[dict, dict]:
    record = get(context, recording_id)
    if record["status"] != "ready":
        if record["status"] in {"failed", "interrupted"}:
            raise JobError(f"A transcrição falhou: {record.get('error') or record['status']}",
                           "Rode transcribe de novo com force para reprocessar.", exit_code=3, code="job_failed")
        if record["status"] == "cancelled":
            raise JobError("A transcrição foi cancelada.", exit_code=5, code="job_cancelled")
        raise JobError(f"A transcrição ainda não terminou ({record.get('stage') or record['status']}).",
                       "Aguarde com wait.", exit_code=4, code="not_ready")
    path = Path(record.get("result_path") or context.store().directory(recording_id) / "result.json")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise JobError(f"Resultado ilegível: {path}", exit_code=3, code="job_failed") from exc
    return record, data


def summary(record: dict) -> dict:
    """Public, key-free view of a job for agents."""
    return {"id": record["id"], "title": record.get("title"), "status": record["status"],
            "stage": record.get("stage") or "", "progress": round(float(record.get("progress") or 0), 3),
            "duration_seconds": round(float(record.get("duration") or 0), 1), "created_at": record.get("created_at"),
            "source": record.get("source_path") or record.get("audio_path"), "error": record.get("error") or "",
            "origin": record.get("origin"), "requested_by": record.get("requested_by") or "app",
            "speaker_names": record.get("speaker_names") or {}}


def payload(record: dict, data: dict) -> dict:
    from whisper_dictation.transcript_format import speaker_turns
    names = record.get("speaker_names") or {}
    turns = speaker_turns(data, names)
    speakers = sorted({turn["speaker"] for turn in turns if turn["speaker"]})
    directory = Path(record["result_path"]).parent if record.get("result_path") else None
    return {**summary(record), "language": data.get("language"), "duration_seconds": round(float(data.get("duration") or 0), 1),
            "speakers": [{"id": speaker, "name": names.get(speaker)} for speaker in speakers],
            "turns": [{"start": round(turn["start"], 2), "end": round(turn["end"], 2), "speaker": turn["speaker"],
                       "label": turn["label"], "text": turn["text"]} for turn in turns],
            "warnings": [w for w in [data.get("transcription_warning")] + list(data.get("warnings") or []) if w],
            "files": {"result_json": str(directory / "result.json") if directory else None,
                      "audio": record.get("audio_path")}}


def doctor(context: Context) -> dict:
    from whisper_dictation._version import __version__
    from whisper_dictation.single_instance import probe
    info: dict = {"version": __version__, "install_dir": str(context.project_root)}
    try:
        config = context.config()
        provider, model = effective_provider(config)
        info.update(provider=provider, model=model, language=config.language,
                    api_key_configured=key_configured(config, provider),
                    library=str(context.store(config).root))
    except Exception as exc:  # report, never crash diagnostics
        info["config_error"] = f"{type(exc).__name__}: {exc}"
    info["pace_running"] = probe()
    try:
        from whisper_dictation import agents
        info["agents"] = agents.status(context.project_root)
    except Exception as exc:
        info["agents_error"] = f"{type(exc).__name__}: {exc}"
    return info
