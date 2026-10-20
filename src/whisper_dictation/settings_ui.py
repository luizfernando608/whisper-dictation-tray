"""Settings hooks shared by the main UI and its legacy settings entry point."""

from __future__ import annotations

import os
import json
import time
from dataclasses import asdict
from pathlib import Path

from whisper_dictation._version import __version__
from whisper_dictation.settings import AppConfig, config_path, logs_dir
from whisper_dictation.settings_window import SettingsContext, SaveCallback
from whisper_dictation.single_instance import (
    COPY_LAST_TRANSCRIPT,
    RELOAD_CONFIG,
    send_command,
    APPLY_UPDATE,
    CAPABILITIES, SAVE_SETTINGS, request, probe,
)


def run_settings_ui(project_root: Path) -> None:
    from whisper_dictation.recordings_window import run_recordings_window
    run_recordings_window(project_root, initial_page="settings")


def create_settings_context(project_root: Path, config: AppConfig) -> tuple[SaveCallback, SettingsContext]:
    path = config_path(project_root)
    log_dir = logs_dir(project_root)

    def can_save() -> bool:
        from whisper_dictation.recording_store import RecordingStore, BUSY_STATUSES
        from whisper_dictation.settings import recordings_dir
        current = AppConfig.load(path)
        directory = recordings_dir(current)
        try:
            runtime = json.loads((directory / "runtime.json").read_text(encoding="utf-8"))
            if time.time() - runtime.get("updated_at", 0) < 5 and (runtime.get("active") or runtime.get("finalizing")):
                return False
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        return not any(row["status"] in BUSY_STATUSES for row in RecordingStore(directory).list())

    def on_save(new_config: AppConfig) -> bool:
        if not can_save():
            return False
        new_config.save(path)
        # Ask the running tray app to apply the new config immediately.
        send_command(RELOAD_CONFIG)
        return True

    def commit(new_config: AppConfig, keys: dict[str, str]) -> bool:
        try:
            capabilities = request(CAPABILITIES, timeout=3)
        except ConnectionRefusedError:
            from whisper_dictation.settings_commit import commit_settings
            if not can_save():
                return False
            commit_settings(path, new_config, keys)
            return True
        if not capabilities.get("save_settings") or Path(capabilities.get("project_root", "")).resolve() != project_root.resolve():
            raise RuntimeError("As configurações pertencem a outra instalação. Abra o PACE desta pasta.")
        response = request({"command": SAVE_SETTINGS, "config": asdict(new_config), "keys": keys})
        if not response.get("ok"):
            raise RuntimeError(response.get("error", "Não foi possível salvar agora."))
        return True

    def run_update(update: object) -> None:
        if not send_command(APPLY_UPDATE):
            raise RuntimeError("Abra o PACE na bandeja antes de atualizar.")

    def check_update():
        from whisper_dictation import updater

        update = updater.check_for_update()
        if update:
            updater.prepare_update(update)
        return update

    context = SettingsContext(
        version=__version__,
        open_logs=lambda: os.startfile(log_dir),
        open_project_folder=lambda: os.startfile(project_root),
        copy_last_transcript=lambda: send_command(COPY_LAST_TRANSCRIPT),
        check_update=check_update,
        run_update=run_update,
        can_save=can_save,
        commit_settings=commit,
    )

    return on_save, context
