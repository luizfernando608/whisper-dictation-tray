"""Own capture and persistent queue in the tray; processing runs in child processes."""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from whisper_dictation.meeting_audio import MeetingRecorder, recover_recording
from whisper_dictation.child_job import ChildJob
from whisper_dictation.recording_store import RecordingStore
from whisper_dictation.settings import AppConfig, recordings_dir


def runtime_command(project_root: Path, *arguments: str) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, *arguments]
    python = project_root / ".venv" / "Scripts" / "pythonw.exe"
    return [str(python) if python.exists() else sys.executable, str(project_root / "main.py"), *arguments]


class MeetingService:
    def __init__(self, config: AppConfig, project_root: Path, config_path: Path, on_change=None):
        self.config = config
        self.project_root = project_root
        self.config_path = config_path
        self.store = RecordingStore(recordings_dir(config))
        self.root = recordings_dir(config)
        self.logger = logging.getLogger("whisper_dictation.meetings")
        self.on_change = on_change or (lambda: None)
        self.recorder: MeetingRecorder | None = None
        self.recording_id: str | None = None
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._worker: subprocess.Popen | None = None
        self._worker_id: str | None = None
        self._worker_job: ChildJob | None = None
        self._worker_progress = None
        self._worker_last_progress = 0.0
        self._cancel_started: float | None = None
        self._thread: threading.Thread | None = None
        self._finalizing = False
        self._finalizer: threading.Thread | None = None
        self._queue_suspended = False

    @property
    def active(self) -> bool:
        return self.recorder is not None

    @property
    def busy(self) -> bool:
        return self.active or self._finalizing or self._worker is not None or any(r["status"] == "queued" for r in self.store.list())

    def start(self, suspended: bool = False) -> None:
        self._queue_suspended = suspended
        self._recover()
        self._thread = threading.Thread(target=self._loop, name="MeetingQueue", daemon=True)
        self._thread.start()

    def _recover(self) -> None:
        for row in self.store.list():
            identifier = row["id"]
            if row["status"] in {"recording", "paused", "finalizing"}:
                try:
                    audio = recover_recording(self.store.directory(identifier))
                    self.store.update(identifier, audio_path=str(audio), status="queued", error="", progress=0)
                except Exception as exc:
                    self.store.update(identifier, status="interrupted", error=f"Gravação interrompida: {exc}")
            elif row["status"] in {"transcribing", "diarizing", "importing", "processing"}:
                status = "cancelled" if (self.store.directory(identifier) / "cancel.request").exists() else "queued"
                self.store.update(identifier, status=status, error="", progress=0)
            elif row["status"] in {"new", "starting"}:
                self.store.update(identifier, status="interrupted", error="A inicialização da gravação foi interrompida.")

    def toggle(self) -> None:
        with self._lock:
            if self._finalizing:
                raise RuntimeError("Aguarde o áudio da reunião anterior terminar de salvar.")
            if self.recorder:
                self.finish()
                return
            row = self.store.create(time.strftime("Reunião %d/%m/%Y %H:%M"), "meeting")
            recorder = MeetingRecorder(self.store.directory(row["id"]), input_device=self.config.meeting_input_device,
                                       output_device=self.config.meeting_output_device, logger=self.logger)
            try:
                recorder.start()
            except Exception as exc:
                self.store.update(row["id"], status="failed", error=f"Falha ao iniciar captura: {exc}")
                raise
            self.recorder, self.recording_id = recorder, row["id"]
            self.store.update(row["id"], status="recording")
            self._write_runtime()
            self.on_change()

    def pause(self) -> None:
        with self._lock:
            if self.recorder is None:
                return
            if self.recorder.paused:
                self.recorder.resume()
            else:
                self.recorder.pause()
            self.store.update(self.recording_id, status="paused" if self.recorder.paused else "recording")
            self._write_runtime()
            self.on_change()

    def finish(self, wait: bool = False) -> None:
        with self._lock:
            recorder, identifier = self.recorder, self.recording_id
            if recorder is not None and identifier is not None:
                self.store.update(identifier, status="finalizing")
                self.recorder, self.recording_id = None, None
                self._finalizing = True
                self._finalizer = threading.Thread(target=self._finalize, args=(recorder, identifier), name="MeetingFinalize", daemon=True)
                self._finalizer.start()
                self._write_runtime()
                self.on_change()
            finalizer = self._finalizer
        if wait and finalizer and finalizer is not threading.current_thread():
            finalizer.join()

    def _finalize(self, recorder, identifier):
        try:
            audio = recorder.stop()
            self.store.update(identifier, audio_path=str(audio), duration=recorder.duration,
                              status="interrupted" if recorder.error else "queued", progress=0, error=recorder.error or "")
        except Exception as exc:
            self.store.update(identifier, status="interrupted", duration=recorder.duration,
                              error=f"Áudio preservado; falha ao finalizar: {exc}")
            self.logger.exception("Meeting finalization failed")
        finally:
            with self._lock:
                self._finalizing = False
                self._write_runtime()
                self.on_change()

    def _write_runtime(self) -> None:
        recorder = self.recorder
        state = {"recording_id": self.recording_id, "active": recorder is not None, "finalizing": self._finalizing,
                 "paused": bool(recorder and recorder.paused), "duration": recorder.duration if recorder else 0,
                 "levels": [recorder.levels.get("microphone", 0), recorder.levels.get("computer", 0)] if recorder else [0, 0],
                 "pid": os.getpid(), "updated_at": time.time()}
        temporary = self.root / "runtime.tmp"
        temporary.write_text(json.dumps(state), encoding="utf-8")
        os.replace(temporary, self.root / "runtime.json")

    def _loop(self) -> None:
        while not self._stop.wait(0.5):
            try:
                with self._lock:
                    if self.recorder:
                        self.store.update(self.recording_id, duration=self.recorder.duration)
                        if self.recorder.error:
                            self.finish()
                    self._write_runtime()
                    self._advance_queue()
            except Exception:
                self.logger.exception("Meeting queue tick failed")

    def _advance_queue(self) -> None:
        if self._worker:
            row = self.store.get(self._worker_id)
            signature = (row.get("status"), row.get("progress"), row.get("stage"))
            if signature != self._worker_progress:
                self._worker_progress, self._worker_last_progress = signature, time.monotonic()
            if time.monotonic() - self._worker_last_progress > 1200 and self._worker.poll() is None:
                self._worker_job.close()
                self._worker.terminate()
                self._worker.wait(timeout=10)
                if not row.get("backend_override"):
                    self.store.update(self._worker_id, status="queued", backend_override="cpu", progress=0,
                                      fallback_reason="Processamento sem progresso por 20 minutos; retomando em CPU.")
                else:
                    self.store.update(self._worker_id, status="failed", error="Processamento não respondeu em CPU. O áudio e os checkpoints foram preservados.")
                self._worker = None
                self._worker_id = None
                self._worker_job = None
                return
            cancel = self.store.directory(self._worker_id) / "cancel.request"
            if cancel.exists():
                self._cancel_started = self._cancel_started or time.monotonic()
                if time.monotonic() - self._cancel_started > 10 and self._worker.poll() is None:
                    self._worker_job.close()
                    self._worker.terminate()
            code = self._worker.poll()
            if code is None:
                return
            row = self.store.get(self._worker_id)
            if row and row["status"] not in {"ready", "failed", "cancelled"}:
                self.store.update(self._worker_id, status="cancelled" if cancel.exists() else "failed",
                                  error="Processamento cancelado." if cancel.exists() else f"Processamento terminou inesperadamente ({code}).")
            self._worker = None
            if self._worker_job:
                self._worker_job.close()
                self._worker_job = None
            self._worker_id = None
            self._cancel_started = None
            self.on_change()
        if self.active or self._finalizing or self._stop.is_set() or self._queue_suspended:
            return
        queued = [r for r in self.store.list() if r["status"] == "queued"]
        if not queued:
            return
        row = sorted(queued, key=lambda r: r["created_at"])[0]
        directory = self.store.directory(row["id"])
        if (directory / "cancel.request").exists():
            self.store.transition(row["id"], "queued", "cancelled")
            return
        if not self.store.transition(row["id"], "queued", "processing", progress=0, error=""):
            return
        self._worker_id = row["id"]
        try:
            snapshot = directory / "worker_config.json"
            self.config.save(snapshot)
            with (directory / "worker.log").open("ab") as log:
                self._worker_job = ChildJob()
                self._worker = subprocess.Popen(runtime_command(self.project_root, "--process-recording", row["id"],
                    "--store-root", str(self.root), "--config-path", str(snapshot)),
                    stdout=log, stderr=log, creationflags=0x08000000 if os.name == "nt" else 0)
                self._worker_job.add(self._worker.pid)
                self._worker_progress = None
                self._worker_last_progress = time.monotonic()
        except Exception as exc:
            if self._worker and self._worker.poll() is None:
                self._worker.terminate()
                self._worker.wait(timeout=10)
            self._worker = None
            if self._worker_job:
                self._worker_job.close()
                self._worker_job = None
            self.store.update(row["id"], status="failed", error=f"Não foi possível iniciar processamento: {exc}")
            self._worker_id = None

    def shutdown(self, join: bool = True) -> None:
        self._stop.set()
        self.finish(wait=True)
        with self._lock:
            if self._worker and self._worker.poll() is None:
                if self._worker_job:
                    self._worker_job.close()
                self._worker.terminate()
                self._worker.wait(timeout=10)
                self.store.update(self._worker_id, status="queued", error="", progress=0)
            self._worker = None
            self._write_runtime()
        if join and self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=10)
