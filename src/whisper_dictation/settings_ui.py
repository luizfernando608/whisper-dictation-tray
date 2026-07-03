"""Entry point for the dedicated settings-window process.

The tray app spawns ``<exe> --settings-ui`` so CustomTkinter runs on a pristine
main thread (its dropdowns deadlock off the main thread). This process only
shows the settings window; it persists changes to ``config.json`` and the
credential vault, then tells the running tray app to reload over the loopback
control channel.
"""

from __future__ import annotations

import os
from pathlib import Path

from whisper_dictation._version import __version__
from whisper_dictation.settings import AppConfig, config_path, load_env_file, logs_dir
from whisper_dictation.settings_window import SettingsContext, run_settings_window
from whisper_dictation.single_instance import (
    COPY_LAST_TRANSCRIPT,
    QUIT,
    RELOAD_CONFIG,
    send_command,
)


def run_settings_ui(project_root: Path) -> None:
    load_env_file(project_root)
    path = config_path(project_root)
    config = AppConfig.load(path)
    log_dir = logs_dir(project_root)

    def on_save(new_config: AppConfig) -> bool:
        new_config.save(path)
        # Ask the running tray app to apply the new config immediately.
        send_command(RELOAD_CONFIG)
        return True

    def run_update(update: object) -> None:
        from whisper_dictation import updater

        try:
            updater.download_and_launch(update)  # type: ignore[arg-type]
        except Exception:
            return
        send_command(QUIT)

    def check_update():
        from whisper_dictation import updater

        return updater.check_for_update()

    context = SettingsContext(
        version=__version__,
        open_logs=lambda: os.startfile(log_dir),
        open_project_folder=lambda: os.startfile(project_root),
        copy_last_transcript=lambda: send_command(COPY_LAST_TRANSCRIPT),
        check_update=check_update,
        run_update=run_update,
    )

    run_settings_window(config, on_save, context)
