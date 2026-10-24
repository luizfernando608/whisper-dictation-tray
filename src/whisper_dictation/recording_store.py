"""Cross-process recording metadata; audio stays outside the application binaries."""
from __future__ import annotations

import json
from contextlib import contextmanager
import os
from pathlib import Path
import re
import shutil
import sqlite3
from datetime import datetime, timezone
import uuid


BUSY_STATUSES = frozenset({"new", "starting", "recording", "paused", "importing", "queued", "transcribing", "diarizing", "finalizing", "preparing", "processing", "cancelling", "resuming"})
# Statuses in which a worker process should be running for the recording.
ACTIVE_STATUSES = frozenset({"transcribing", "diarizing", "importing", "processing", "resuming"})
_FIELDS = frozenset({"title", "origin", "duration", "status", "progress", "error", "audio_path", "result_path", "partial_result_path", "stage", "transcription_warning", "backend", "backend_override", "speaker_names", "source_path", "worker_pid", "fallback_reason", "num_speakers", "min_speakers", "max_speakers", "requested_by", "source_sha256", "job_options", "resume_count"})


def default_recordings_root() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "PACEData" / "recordings"


class RecordingStore:
    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root or default_recordings_root()).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.database = self.root / "library.sqlite3"
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("CREATE TABLE IF NOT EXISTS recordings (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, record TEXT NOT NULL)")

    @contextmanager
    def _connection(self):
        connection = sqlite3.connect(self.database, timeout=30)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def directory(self, recording_id: str) -> Path:
        if not isinstance(recording_id, str) or not re.fullmatch(r"[0-9a-f]{32}", recording_id):
            raise ValueError("Identificador de gravação inválido.")
        path = self.root / recording_id
        if path.resolve().parent != self.root or path.is_symlink():
            raise ValueError("Diretório de gravação fora da biblioteca.")
        return path

    def create(self, title: str, origin: str, **fields) -> dict:
        if origin not in {"meeting", "import"}:
            raise ValueError("Origem de gravação inválida.")
        recording_id = uuid.uuid4().hex
        record = dict(id=recording_id, title=str(title).strip() or "Gravação", origin=origin,
                      created_at=datetime.now(timezone.utc).isoformat(), duration=0.0,
                      status="new", progress=0.0, error=None, audio_path=None,
                      result_path=None, backend=None, speaker_names={})
        record.update(self._validated_fields(recording_id, fields))
        directory = self.directory(recording_id)
        directory.mkdir()
        try:
            with self._connection() as connection:
                connection.execute("INSERT INTO recordings VALUES (?, ?, ?)", (recording_id, record["created_at"], json.dumps(record)))
        except Exception:
            directory.rmdir()
            raise
        return record

    def get(self, recording_id: str) -> dict:
        self.directory(recording_id)
        with self._connection() as connection:
            row = connection.execute("SELECT record FROM recordings WHERE id=?", (recording_id,)).fetchone()
        if row is None:
            raise KeyError(recording_id)
        return json.loads(row[0])

    def list(self) -> list[dict]:
        with self._connection() as connection:
            return [json.loads(row[0]) for row in connection.execute("SELECT record FROM recordings ORDER BY created_at DESC")]

    def _validated_fields(self, recording_id: str, fields: dict) -> dict:
        directory = self.directory(recording_id)
        fields = dict(fields)
        if set(fields) - _FIELDS:
            raise ValueError(f"Campos desconhecidos: {set(fields) - _FIELDS}")
        for name in ("audio_path", "result_path", "partial_result_path"):
            if fields.get(name):
                path = Path(fields[name])
                path = path if path.is_absolute() else directory / path
                if not path.resolve().is_relative_to(directory.resolve()):
                    raise ValueError("Arquivo gerado fora do diretório da gravação.")
                fields[name] = str(path.resolve())
        return fields

    def update(self, recording_id: str, **fields) -> dict:
        fields = self._validated_fields(recording_id, fields)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT record FROM recordings WHERE id=?", (recording_id,)).fetchone()
            if row is None:
                raise KeyError(recording_id)
            record = json.loads(row[0])
            record.update(fields)
            connection.execute("UPDATE recordings SET record=? WHERE id=?", (json.dumps(record), recording_id))
        return record

    def transition(self, recording_id: str, expected_status, new_status: str, **fields) -> bool:
        """Atomically claim/cancel/retry work without overwriting a concurrent state change."""
        fields = self._validated_fields(recording_id, dict(fields, status=new_status))
        expected = {expected_status} if isinstance(expected_status, str) else set(expected_status)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT record FROM recordings WHERE id=?", (recording_id,)).fetchone()
            if row is None:
                return False
            record = json.loads(row[0])
            if record["status"] not in expected:
                return False
            record.update(fields)
            connection.execute("UPDATE recordings SET record=? WHERE id=?", (json.dumps(record), recording_id))
        return True

    def delete(self, recording_id: str) -> None:
        directory = self.directory(recording_id)
        trash = self.root / (".deleted-" + recording_id + "-" + uuid.uuid4().hex)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT record FROM recordings WHERE id=?", (recording_id,)).fetchone()
            if row is None:
                raise KeyError(recording_id)
            if json.loads(row[0])["status"] in BUSY_STATUSES:
                raise RuntimeError("Encerre ou cancele o trabalho antes de excluir a gravação.")
            if directory.exists():
                directory.rename(trash)
            try:
                connection.execute("DELETE FROM recordings WHERE id=?", (recording_id,))
                connection.commit()
            except Exception:
                if trash.exists():
                    trash.rename(directory)
                raise
        if trash.exists():
            shutil.rmtree(trash)
