from __future__ import annotations

import os
import threading
import time
from enum import StrEnum
from pathlib import Path

import pyperclip
import pystray
from PIL import Image, ImageDraw
from pystray import Menu, MenuItem

from whisper_dictation.audio import AudioRecorder, CapturedAudio, list_input_devices
from whisper_dictation.logging_utils import configure_logging
from whisper_dictation.overlay import RecordingOverlay
from whisper_dictation.settings import AppConfig, config_path, load_env_file, logs_dir, temp_dir
from whisper_dictation.text_inserter import TextInserter
from whisper_dictation.transcription import WhisperTranscriber
from whisper_dictation.win32_hotkey import GlobalHotkeyManager


class AppState(StrEnum):
    IDLE = "idle"
    RECORDING = "recording"
    TRANSCRIBING = "transcribing"
    ERROR = "error"


class DictationApp:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        load_env_file(project_root)
        self.config_path = config_path(project_root)
        self.log_dir = logs_dir(project_root)
        self.temp_root = temp_dir(project_root)
        self.logger = configure_logging(self.log_dir / "whisper_dictation.log")

        self.config = AppConfig.load(self.config_path)
        self.recorder = AudioRecorder(
            target_sample_rate=self.config.sample_rate,
            input_device=self.config.input_device,
            logger=self.logger,
        )
        self.transcriber = WhisperTranscriber(
            config=self.config,
            temp_root=self.temp_root,
            logger=self.logger,
        )
        self.inserter = TextInserter(
            mode=self.config.insert_mode,
            restore_clipboard=self.config.restore_clipboard,
            typing_interval_ms=self.config.typing_interval_ms,
            logger=self.logger,
        )
        self.hotkey_manager = GlobalHotkeyManager(
            hotkey=self.config.hotkey,
            callback=self.toggle_recording,
            logger=self.logger,
        )

        self._state = AppState.IDLE
        self._status_message = "Pronto"
        self._last_transcript = ""
        self._last_error = ""
        self._auto_stop_timer: threading.Timer | None = None
        self._worker_thread: threading.Thread | None = None
        self._state_lock = threading.RLock()
        self._shutdown = threading.Event()
        self._overlay = RecordingOverlay()

        self.icon = pystray.Icon(
            "whisper-dictation-tray",
            self._build_icon_image(self._state),
            title=self._tooltip(),
            menu=self._build_menu(),
        )

    def run(self) -> None:
        self.logger.info("Starting Whisper Dictation Tray")
        self.hotkey_manager.start()
        self.icon.run()

    def toggle_recording(self) -> None:
        with self._state_lock:
            state = self._state

        if state == AppState.IDLE:
            self._start_recording()
            return
        if state == AppState.RECORDING:
            self._stop_recording()
            return

        self.logger.info("Hotkey ignored because app state=%s", state)

    def _start_recording(self) -> None:
        with self._state_lock:
            if self._state != AppState.IDLE:
                return
            try:
                self.recorder.start()
            except Exception as exc:
                self._set_error(f"Falha ao iniciar o microfone: {exc}")
                return

            self._set_state(AppState.RECORDING, "Gravando")
            self._overlay.show_recording()
            self._schedule_auto_stop()
            self.logger.info("Recording started")

    def _stop_recording(self) -> None:
        with self._state_lock:
            if self._state != AppState.RECORDING:
                return
            self._cancel_auto_stop()
            audio = self.recorder.stop()
            if audio.duration_seconds < 0.2 or audio.samples.size == 0:
                self._overlay.hide()
                self._set_state(AppState.IDLE, "Áudio muito curto")
                self.logger.info("Recording discarded because it was too short")
                return

            self._set_state(AppState.TRANSCRIBING, "Transcrevendo")
            self._overlay.show_transcribing()
            self._worker_thread = threading.Thread(
                target=self._transcribe_worker,
                name="TranscriptionWorker",
                args=(audio,),
                daemon=True,
            )
            self._worker_thread.start()

    def _transcribe_worker(self, audio: CapturedAudio) -> None:
        try:
            start_time = time.perf_counter()
            text = self.transcriber.transcribe(audio)
            elapsed = time.perf_counter() - start_time
            self.logger.info("Transcription pipeline finished in %.2fs", elapsed)

            if not text:
                self._overlay.hide()
                self._set_state(AppState.IDLE, "Nenhuma fala detectada")
                return

            self._last_transcript = text
            self._overlay.hide()
            inserted = self.inserter.insert_text(text)
            if inserted:
                self._set_state(AppState.IDLE, "Texto inserido")
                self._notify("Whisper Dictation", self._truncate_notification(text))
            else:
                self._set_state(AppState.IDLE, "Texto transcrito")
        except Exception as exc:
            self.logger.exception("Unhandled transcription failure")
            self._overlay.hide()
            self._set_error(f"Erro durante a transcrição: {exc}")

    def _schedule_auto_stop(self) -> None:
        self._cancel_auto_stop()
        self._auto_stop_timer = threading.Timer(self.config.max_record_seconds, self._stop_recording)
        self._auto_stop_timer.daemon = True
        self._auto_stop_timer.start()

    def _cancel_auto_stop(self) -> None:
        timer = self._auto_stop_timer
        self._auto_stop_timer = None
        if timer is not None:
            timer.cancel()

    def _reload_config(self, icon: pystray.Icon, item: MenuItem) -> None:
        del icon, item
        with self._state_lock:
            if self._state == AppState.RECORDING:
                self._notify("Whisper Dictation", "Pare a gravação antes de recarregar a configuração.")
                return
            if self._state == AppState.TRANSCRIBING:
                self._notify("Whisper Dictation", "Espere a transcrição terminar antes de recarregar.")
                return

        try:
            self.hotkey_manager.stop()
            self.config = AppConfig.load(self.config_path)
            self.recorder = AudioRecorder(
                target_sample_rate=self.config.sample_rate,
                input_device=self.config.input_device,
                logger=self.logger,
            )
            self.transcriber = WhisperTranscriber(
                config=self.config,
                temp_root=self.temp_root,
                logger=self.logger,
            )
            self.inserter = TextInserter(
                mode=self.config.insert_mode,
                restore_clipboard=self.config.restore_clipboard,
                typing_interval_ms=self.config.typing_interval_ms,
                logger=self.logger,
            )
            self.hotkey_manager = GlobalHotkeyManager(
                hotkey=self.config.hotkey,
                callback=self.toggle_recording,
                logger=self.logger,
            )
            self.hotkey_manager.start()
            self._set_state(AppState.IDLE, "Configuração recarregada")
            self._notify("Whisper Dictation", f"Novo atalho: {self.config.hotkey}")
        except Exception as exc:
            self._set_error(f"Falha ao recarregar config: {exc}")

    def _copy_last_transcript(self, icon: pystray.Icon, item: MenuItem) -> None:
        del icon, item
        if not self._last_transcript:
            return
        pyperclip.copy(self._last_transcript)
        self._notify("Whisper Dictation", "Última transcrição copiada.")

    def _open_config(self, icon: pystray.Icon, item: MenuItem) -> None:
        del icon, item
        os.startfile(self.config_path)

    def _open_logs(self, icon: pystray.Icon, item: MenuItem) -> None:
        del icon, item
        os.startfile(self.log_dir)

    def _open_project_folder(self, icon: pystray.Icon, item: MenuItem) -> None:
        del icon, item
        os.startfile(self.project_root)

    def _log_input_devices(self, icon: pystray.Icon, item: MenuItem) -> None:
        del icon, item
        devices = list_input_devices()
        if not devices:
            self.logger.warning("No input devices found")
            self._notify("Whisper Dictation", "Nenhum microfone encontrado.")
            return
        for index, name, sample_rate in devices:
            self.logger.info(
                "Input device [%s] %s | default_sample_rate=%s",
                index,
                name,
                sample_rate,
            )
        self._notify("Whisper Dictation", "Lista de microfones enviada para o log.")

    def _quit(self, icon: pystray.Icon, item: MenuItem) -> None:
        del item
        self._shutdown.set()
        self._cancel_auto_stop()
        self.recorder.abort()
        self.hotkey_manager.stop()
        icon.stop()
        self.logger.info("Application stopped")

    def _set_error(self, message: str) -> None:
        self._last_error = message
        self.logger.error(message)
        self._set_state(AppState.ERROR, message)
        self._notify("Whisper Dictation", message)

    def _set_state(self, state: AppState, status_message: str) -> None:
        with self._state_lock:
            self._state = state
            self._status_message = status_message
            self.icon.icon = self._build_icon_image(state)
            self.icon.title = self._tooltip()
            self.icon.update_menu()

    def _build_menu(self) -> Menu:
        return Menu(
            MenuItem(lambda *_: self._status_label(), None, enabled=False),
            MenuItem(lambda *_: self._toggle_label(), self._menu_toggle, default=True),
            MenuItem(
                "Copiar última transcrição",
                self._copy_last_transcript,
                enabled=lambda *_: bool(self._last_transcript),
            ),
            MenuItem("Abrir config.json", self._open_config),
            MenuItem("Recarregar configuração", self._reload_config),
            MenuItem("Listar microfones no log", self._log_input_devices),
            MenuItem("Abrir logs", self._open_logs),
            MenuItem("Abrir pasta do projeto", self._open_project_folder),
            MenuItem("Sair", self._quit),
        )

    def _menu_toggle(self, icon: pystray.Icon, item: MenuItem) -> None:
        del icon, item
        self.toggle_recording()

    def _status_label(self) -> str:
        with self._state_lock:
            return f"Estado: {self._status_message} | Hotkey: {self.config.hotkey}"

    def _toggle_label(self) -> str:
        with self._state_lock:
            if self._state == AppState.RECORDING:
                return "Parar gravação"
            if self._state == AppState.TRANSCRIBING:
                return "Transcrevendo..."
            return "Iniciar gravação"

    def _tooltip(self) -> str:
        return f"Whisper Dictation | {self._status_message}"

    def _notify(self, title: str, message: str) -> None:
        if not getattr(self.icon, "HAS_NOTIFICATION", False):
            return
        try:
            self.icon.notify(message, title)
        except Exception:
            self.logger.exception("Tray notification failed")

    def _truncate_notification(self, text: str) -> str:
        if len(text) <= 120:
            return text
        return text[:117].rstrip() + "..."

    def _build_icon_image(self, state: AppState) -> Image.Image:
        palette = {
            AppState.IDLE: ("#1f2937", "#2563eb"),
            AppState.RECORDING: ("#3f0d12", "#ef4444"),
            AppState.TRANSCRIBING: ("#1f2937", "#10b981"),
            AppState.ERROR: ("#451a03", "#f59e0b"),
        }
        background, accent = palette[state]
        size = 128
        image = Image.new("RGBA", (size, size), background)
        draw = ImageDraw.Draw(image)

        draw.rounded_rectangle((18, 18, 110, 110), radius=26, fill=accent)
        draw.rounded_rectangle((46, 28, 82, 74), radius=16, fill="white")
        draw.rectangle((58, 72, 70, 92), fill="white")
        draw.rounded_rectangle((40, 90, 88, 98), radius=4, fill="white")
        draw.rectangle((58, 98, 70, 108), fill="white")
        return image
