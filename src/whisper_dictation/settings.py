from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from whisper_dictation.providers import PROVIDERS


@dataclass(slots=True)
class AppConfig:
    hotkey: str = "Ctrl+Shift+H"
    language: str = "auto"
    transcription_provider: str = "groq"
    groq_model: str = "whisper-large-v3"
    openai_model: str = "gpt-transcribe"
    gemini_model: str = "gemini-3.8-flash"
    groq_api_key_env: str = "GROQ_API_KEY"
    groq_timeout_seconds: float = 30.0
    model_size: str = "small"
    compute_type: str = "int8"
    sample_rate: int = 16000
    input_device: int | str | None = None
    max_record_seconds: int = 0
    beam_size: int = 1
    vad_min_silence_ms: int = 400
    insert_mode: str = "paste"
    restore_clipboard: bool = True
    typing_interval_ms: int = 4
    cpu_threads: int = 0
    meeting_hotkey: str = "Ctrl+Shift+R"
    meeting_pause_hotkey: str = "Ctrl+Shift+P"
    meeting_input_device: str | None = None
    meeting_output_device: str | None = None
    meeting_storage_dir: str = ""
    meeting_show_overlay: bool = False
    meeting_timestamp_provider: str = "local"
    diarization_engine: str = "nemotron"
    diarization_backend: str = "auto"
    diarization_model_dir: str = ""
    diarization_num_speakers: int = 0
    diarization_min_speakers: int = 0
    diarization_max_speakers: int = 0

    @classmethod
    def load(cls, path: Path) -> "AppConfig":
        if not path.exists():
            config = cls()
            config.save(path)
            return config

        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return cls(**cls._sanitize(data))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix="config-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(asdict(self), stream, indent=2, ensure_ascii=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    @classmethod
    def _sanitize(cls, data: dict[str, Any]) -> dict[str, Any]:
        defaults = asdict(cls())
        sanitized = {**defaults}
        for key in defaults:
            if key in data:
                sanitized[key] = data[key]

        sanitized["insert_mode"] = str(sanitized["insert_mode"]).strip()
        sanitized["transcription_provider"] = str(sanitized["transcription_provider"]).strip().lower()

        if sanitized["insert_mode"] not in {"paste", "type"}:
            sanitized["insert_mode"] = defaults["insert_mode"]
        if sanitized["transcription_provider"] not in PROVIDERS:
            sanitized["transcription_provider"] = defaults["transcription_provider"]

        sanitized["sample_rate"] = max(8000, int(sanitized["sample_rate"]))
        raw_max_rec = int(sanitized["max_record_seconds"])
        sanitized["max_record_seconds"] = 0 if raw_max_rec <= 0 else max(5, raw_max_rec)
        sanitized["beam_size"] = max(1, int(sanitized["beam_size"]))
        sanitized["vad_min_silence_ms"] = max(100, int(sanitized["vad_min_silence_ms"]))
        sanitized["typing_interval_ms"] = max(0, int(sanitized["typing_interval_ms"]))
        sanitized["cpu_threads"] = max(0, int(sanitized["cpu_threads"]))
        sanitized["groq_timeout_seconds"] = max(5.0, float(sanitized["groq_timeout_seconds"]))
        sanitized["hotkey"] = str(sanitized["hotkey"]).strip() or defaults["hotkey"]
        sanitized["language"] = str(sanitized["language"]).strip().lower() or defaults["language"]
        sanitized["groq_model"] = str(sanitized["groq_model"]).strip() or defaults["groq_model"]
        sanitized["openai_model"] = str(sanitized["openai_model"]).strip() or defaults["openai_model"]
        sanitized["gemini_model"] = str(sanitized["gemini_model"]).strip() or defaults["gemini_model"]
        sanitized["groq_api_key_env"] = str(sanitized["groq_api_key_env"]).strip() or defaults["groq_api_key_env"]
        sanitized["model_size"] = str(sanitized["model_size"]).strip() or defaults["model_size"]
        sanitized["compute_type"] = str(sanitized["compute_type"]).strip() or defaults["compute_type"]
        input_device = sanitized["input_device"]
        if input_device is None:
            sanitized["input_device"] = None
        elif isinstance(input_device, int):
            sanitized["input_device"] = max(0, input_device)
        else:
            input_device_text = str(input_device).strip()
            sanitized["input_device"] = (
                int(input_device_text) if input_device_text.isdigit() else input_device_text or None
            )
        sanitized["restore_clipboard"] = bool(sanitized["restore_clipboard"])
        for key in ("meeting_hotkey", "meeting_pause_hotkey"):
            sanitized[key] = str(sanitized[key]).strip()
        sanitized["meeting_hotkey"] = sanitized["meeting_hotkey"] or defaults["meeting_hotkey"]
        for key in ("meeting_input_device", "meeting_output_device"):
            sanitized[key] = str(sanitized[key]).strip() if sanitized[key] is not None else None
            sanitized[key] = sanitized[key] or None
        for key in ("meeting_storage_dir", "diarization_model_dir"):
            sanitized[key] = str(sanitized[key]).strip()
        sanitized["meeting_show_overlay"] = bool(sanitized["meeting_show_overlay"])
        sanitized["diarization_num_speakers"] = max(0, int(sanitized["diarization_num_speakers"]))
        if sanitized["diarization_num_speakers"] > 50:
            raise ValueError("A quantidade de falantes deve estar entre 0 e 50.")
        for key in ("diarization_min_speakers", "diarization_max_speakers"):
            sanitized[key] = max(0, int(sanitized[key]))
            if sanitized[key] > 50:
                raise ValueError("A quantidade de falantes deve estar entre 0 e 50.")
        if sanitized["diarization_min_speakers"] and sanitized["diarization_max_speakers"] and sanitized["diarization_min_speakers"] > sanitized["diarization_max_speakers"]:
            raise ValueError("O mínimo de falantes não pode superar o máximo.")
        if sanitized["diarization_engine"] not in {"nemotron", "community-1"}:
            sanitized["diarization_engine"] = "nemotron"
        if sanitized["diarization_backend"] not in {"auto", "cpu", "cuda", "vulkan", "openvino", "directml"}:
            sanitized["diarization_backend"] = "auto"
        if sanitized["meeting_timestamp_provider"] not in {"local", "whisper-1"}:
            sanitized["meeting_timestamp_provider"] = "local"
        return sanitized


def config_path(project_root: Path) -> Path:
    target = project_root / "config.json"
    if not target.exists():
        legacy = project_root.parent / "WhisperDictation" / "config.json"
        if legacy.exists():
            try:
                import shutil
                shutil.copy2(legacy, target)
            except Exception:
                pass
    return target


def load_env_file(project_root: Path) -> None:
    env_path = project_root / ".env"
    if not env_path.exists():
        legacy = project_root.parent / "WhisperDictation" / ".env"
        if legacy.exists():
            try:
                import shutil
                shutil.copy2(legacy, env_path)
            except Exception:
                pass
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export ") :].strip()
        if not key or key in os.environ:
            continue

        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ[key] = value


def logs_dir(project_root: Path) -> Path:
    path = project_root / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def temp_dir(project_root: Path) -> Path:
    path = project_root / "temp"
    path.mkdir(parents=True, exist_ok=True)
    return path


def data_dir() -> Path:
    """User data survives installation/runtime replacement."""
    root = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".local" / "share"))) / "PaceData"
    root.mkdir(parents=True, exist_ok=True)
    return root


def recordings_dir(config: AppConfig) -> Path:
    root = Path(config.meeting_storage_dir).expanduser() if config.meeting_storage_dir else data_dir() / "recordings"
    root.mkdir(parents=True, exist_ok=True)
    return root


def validate_hotkeys(config: AppConfig) -> None:
    from whisper_dictation.win32_hotkey import parse_hotkey

    seen: set[tuple[int, int]] = set()
    for label, value in (("ditado", config.hotkey), ("reunião", config.meeting_hotkey), ("pausa", config.meeting_pause_hotkey)):
        if not value and label == "pausa":
            continue
        parsed = parse_hotkey(value)
        if parsed in seen:
            raise ValueError(f"O atalho de {label} já está sendo usado por outra ação do PACE.")
        seen.add(parsed)
