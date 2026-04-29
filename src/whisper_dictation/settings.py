from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class AppConfig:
    hotkey: str = "Ctrl+Shift+H"
    language: str = "pt"
    model_size: str = "small"
    compute_type: str = "int8"
    sample_rate: int = 16000
    input_device: str | None = None
    max_record_seconds: int = 120
    beam_size: int = 1
    vad_min_silence_ms: int = 400
    insert_mode: str = "paste"
    restore_clipboard: bool = True
    typing_interval_ms: int = 4
    cpu_threads: int = 0

    @classmethod
    def load(cls, path: Path) -> "AppConfig":
        if not path.exists():
            config = cls()
            config.save(path)
            return config

        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(**cls._sanitize(data))

    def save(self, path: Path) -> None:
        path.write_text(
            json.dumps(asdict(self), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    @classmethod
    def _sanitize(cls, data: dict[str, Any]) -> dict[str, Any]:
        defaults = asdict(cls())
        sanitized = {**defaults}
        for key in defaults:
            if key in data:
                sanitized[key] = data[key]

        if sanitized["insert_mode"] not in {"paste", "type"}:
            sanitized["insert_mode"] = defaults["insert_mode"]

        sanitized["sample_rate"] = max(8000, int(sanitized["sample_rate"]))
        sanitized["max_record_seconds"] = max(5, int(sanitized["max_record_seconds"]))
        sanitized["beam_size"] = max(1, int(sanitized["beam_size"]))
        sanitized["vad_min_silence_ms"] = max(100, int(sanitized["vad_min_silence_ms"]))
        sanitized["typing_interval_ms"] = max(0, int(sanitized["typing_interval_ms"]))
        sanitized["cpu_threads"] = max(0, int(sanitized["cpu_threads"]))
        sanitized["hotkey"] = str(sanitized["hotkey"]).strip() or defaults["hotkey"]
        sanitized["language"] = str(sanitized["language"]).strip() or defaults["language"]
        sanitized["model_size"] = str(sanitized["model_size"]).strip() or defaults["model_size"]
        sanitized["compute_type"] = str(sanitized["compute_type"]).strip() or defaults["compute_type"]
        sanitized["input_device"] = sanitized["input_device"] or None
        sanitized["restore_clipboard"] = bool(sanitized["restore_clipboard"])
        return sanitized


def config_path(project_root: Path) -> Path:
    return project_root / "config.json"


def logs_dir(project_root: Path) -> Path:
    path = project_root / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def temp_dir(project_root: Path) -> Path:
    path = project_root / "temp"
    path.mkdir(parents=True, exist_ok=True)
    return path
