from __future__ import annotations

import subprocess
import threading
import time
import uuid
from enum import StrEnum
from pathlib import Path

import pyperclip
import pystray
from PIL import Image, ImageDraw
from pystray import Menu, MenuItem

from whisper_dictation import updater
from whisper_dictation._version import __version__
from whisper_dictation.audio import AudioRecorder, CapturedAudio
from whisper_dictation.logging_utils import configure_logging
from whisper_dictation.overlay import RecordingOverlay
from whisper_dictation.meeting_service import MeetingService, runtime_command
from whisper_dictation.settings import AppConfig, config_path, load_env_file, logs_dir, temp_dir, validate_hotkeys
from whisper_dictation.single_instance import (
    COPY_LAST_TRANSCRIPT,
    OPEN_SETTINGS,
    QUIT,
    RELOAD_CONFIG,
    SingleInstance,
    OPEN_RECORDINGS, MEETING_TOGGLE, MEETING_PAUSE, APPLY_UPDATE,
    CAPABILITIES, SAVE_SETTINGS, IMPORT_MEDIA,
)
from whisper_dictation.text_inserter import TextInserter
from whisper_dictation.transcription import WhisperTranscriber
from whisper_dictation.win32_hotkey import GlobalHotkeyManager


class AppState(StrEnum):
    IDLE = "idle"
    RECORDING = "recording"
    TRANSCRIBING = "transcribing"
    ERROR = "error"


class DictationApp:
    def __init__(
        self,
        project_root: Path,
        instance: SingleInstance | None = None,
        open_settings_on_start: bool = False,
    ) -> None:
        self.project_root = project_root
        self._instance = instance
        self._open_settings_on_start = open_settings_on_start
        self._update_available: updater.UpdateInfo | None = None
        self._recordings_proc: subprocess.Popen | None = None
        self._ui_lock = threading.Lock()
        self._ui_navigation = {"id": "", "page": "recordings"}
        self._meeting_overlay_proc: subprocess.Popen | None = None
        self._update_ready = False
        self._update_lock = threading.Lock()
        self._restarting = False
        self._tray_update_event = threading.Event()
        load_env_file(project_root)
        self.config_path = config_path(project_root)
        self.log_dir = logs_dir(project_root)
        self.temp_root = temp_dir(project_root)
        self.logger = configure_logging(self.log_dir / "whisper_dictation.log")

        self.config = AppConfig.load(self.config_path)
        validate_hotkeys(self.config)
        (
            self.recorder,
            self.transcriber,
            self.inserter,
            self.hotkey_manager,
        ) = self._build_runtime_components(self.config)

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
            "pace",
            self._build_icon_image(self._state),
            title=self._tooltip(),
            menu=self._build_menu(),
        )
        self.meetings = MeetingService(self.config, self.project_root, self.config_path, self._meeting_changed)
        self._meeting_hotkeys = self._build_meeting_hotkeys(self.config)

        if self._instance is not None:
            self._instance.register(OPEN_SETTINGS, self._open_settings_now)
            self._instance.register(RELOAD_CONFIG, self._reload_config_from_disk)
            self._instance.register(COPY_LAST_TRANSCRIPT, self._copy_last_transcript_action)
            self._instance.register(QUIT, self._shutdown_app)
            self._instance.register(OPEN_RECORDINGS, self._open_recordings_now)
            self._instance.register(MEETING_TOGGLE, self.toggle_meeting)
            self._instance.register(MEETING_PAUSE, self.pause_meeting)
            self._instance.register(APPLY_UPDATE, self._request_restart_update)
            self._instance.register(CAPABILITIES, self._capabilities)
            self._instance.register_request(IMPORT_MEDIA, self._import_media_request)
            self._instance.register_request(SAVE_SETTINGS, self._commit_settings_request)

    def run(self) -> None:
        self.logger.info("Starting PACE v%s", __version__)
        self.hotkey_manager.start()
        for hotkey in self._meeting_hotkeys:
            try:
                hotkey.start()
            except Exception as exc:
                self._notify("PACE", f"Atalho de reunião indisponível: {exc}. Use o menu da bandeja.")
        self.meetings.start()
        threading.Thread(target=self._tray_updates, name="MeetingTrayUpdates", daemon=True).start()
        if self._instance is not None:
            self._instance.start_listener()
        threading.Thread(
            target=self._check_updates, name="StartupUpdateCheck", daemon=True
        ).start()
        if self._open_settings_on_start:
            self._open_settings_now()
        try:
            self.icon.run(setup=self._tray_ready)
        finally:
            self._shutdown_app()

    def _tray_ready(self, icon):
        icon.visible = True
        updater.confirm_running_version(self.project_root)

    def _build_runtime_components(
        self,
        config: AppConfig,
    ) -> tuple[AudioRecorder, WhisperTranscriber, TextInserter, GlobalHotkeyManager]:
        return (
            AudioRecorder(
                target_sample_rate=config.sample_rate,
                input_device=config.input_device,
                logger=self.logger,
            ),
            WhisperTranscriber(
                config=config,
                temp_root=self.temp_root,
                logger=self.logger,
            ),
            TextInserter(
                mode=config.insert_mode,
                restore_clipboard=config.restore_clipboard,
                typing_interval_ms=config.typing_interval_ms,
                logger=self.logger,
            ),
            GlobalHotkeyManager(
                hotkey=config.hotkey,
                callback=self.toggle_recording,
                logger=self.logger,
            ),
        )

    def toggle_recording(self) -> None:
        if self._restarting:
            return
        if self.meetings.active:
            self._notify("PACE", "Encerre a reunião antes de usar o ditado.")
            return
        with self._state_lock:
            state = self._state

        if state in {AppState.IDLE, AppState.ERROR}:
            self._start_recording()
            return
        if state == AppState.RECORDING:
            self._stop_recording()
            return

        self.logger.info("Hotkey ignored because app state=%s", state)

    def _start_recording(self) -> None:
        with self._state_lock:
            if self._restarting or self._shutdown.is_set():
                return
            if self._state not in {AppState.IDLE, AppState.ERROR}:
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
            copied = self._copy_transcript_to_clipboard(text)
            if inserted:
                status = "Texto inserido e copiado" if copied else "Texto inserido"
                self._set_state(AppState.IDLE, status)
                self._notify("PACE", self._truncate_notification(text))
            else:
                status = "Texto transcrito e copiado" if copied else "Texto transcrito"
                self._set_state(AppState.IDLE, status)
        except Exception as exc:
            self.logger.exception("Unhandled transcription failure")
            self._overlay.hide()
            self._set_error(f"Erro durante a transcrição: {exc}")

    def _schedule_auto_stop(self) -> None:
        self._cancel_auto_stop()
        if self.config.max_record_seconds <= 0:
            return
        self._auto_stop_timer = threading.Timer(self.config.max_record_seconds, self._stop_recording)
        self._auto_stop_timer.daemon = True
        self._auto_stop_timer.start()

    def _cancel_auto_stop(self) -> None:
        timer = self._auto_stop_timer
        self._auto_stop_timer = None
        if timer is not None:
            timer.cancel()

    def _can_change_config(self, action: str) -> bool:
        if self.meetings.busy:
            self._notify("PACE", f"Encerre a reunião e aguarde a fila antes de {action}.")
            return False
        with self._state_lock:
            state = self._state

        if state == AppState.RECORDING:
            self._notify("PACE", f"Pare a gravação antes de {action}.")
            return False
        if state == AppState.TRANSCRIBING:
            self._notify("PACE", f"Espere a transcrição terminar antes de {action}.")
            return False
        return True

    def _apply_config(self, config: AppConfig, suspend_service: bool = False) -> None:
        with self._state_lock, self.meetings._lock:
            self._apply_config_locked(config, suspend_service)

    def _apply_config_locked(self, config: AppConfig, suspend_service: bool) -> None:
        validate_hotkeys(config)
        from whisper_dictation.settings import recordings_dir
        old_service = self.meetings
        replace_service = recordings_dir(config).resolve() != old_service.root.resolve()
        new_service = MeetingService(config, self.project_root, self.config_path, self._meeting_changed) if replace_service else None
        recorder, transcriber, inserter, hotkey_manager = self._build_runtime_components(config)
        previous_hotkey_manager = self.hotkey_manager
        meeting_hotkeys = self._build_meeting_hotkeys(config)

        previous_hotkey_manager.stop()
        for previous in self._meeting_hotkeys:
            previous.stop()
        try:
            hotkey_manager.start()
            for manager in meeting_hotkeys:
                manager.start()
            if new_service:
                new_service.start(suspended=suspend_service)
        except Exception:
            self.logger.exception("Failed to apply new runtime config; restoring previous hotkey.")
            hotkey_manager.stop()
            for manager in meeting_hotkeys:
                manager.stop()
            if new_service:
                new_service.shutdown(join=False)
            try:
                previous_hotkey_manager.start()
                for previous in self._meeting_hotkeys:
                    previous.start()
            except Exception:
                self.logger.exception("Failed to restore previous hotkey after config apply error.")
            raise

        self.config = config
        self.recorder = recorder
        self.transcriber = transcriber
        self.inserter = inserter
        self.hotkey_manager = hotkey_manager
        self._meeting_hotkeys = meeting_hotkeys
        if new_service:
            self.meetings = new_service
            old_service.shutdown(join=False)
        else:
            self.meetings.config = config

    def _build_meeting_hotkeys(self, config):
        result = [GlobalHotkeyManager(config.meeting_hotkey, self.toggle_meeting, self.logger)]
        if config.meeting_pause_hotkey:
            result.append(GlobalHotkeyManager(config.meeting_pause_hotkey, self.pause_meeting, self.logger))
        return result

    def _import_media_request(self, payload):
        with self._state_lock, self.meetings._lock:
            if self._restarting or self._shutdown.is_set():
                return {"ok": False, "error": "Aguarde o PACE reiniciar antes de importar."}
            paths = payload.get("paths")
            if not isinstance(paths, list) or not paths or len(paths) > 100 or any(not isinstance(p, str) for p in paths):
                return {"ok": False, "error": "Selecione até 100 arquivos de áudio ou vídeo."}
            sources = [Path(value).resolve() for value in paths]
            if any(not path.is_file() for path in sources):
                return {"ok": False, "error": "Um dos arquivos selecionados não foi encontrado."}
            imported = []
            for source in sources:
                try:
                    row = self.meetings.store.create(source.stem, "import", source_path=str(source), status="queued")
                except Exception:
                    self.logger.exception("Não foi possível enfileirar arquivo importado.")
                    return {"ok": False, "ids": imported,
                            "error": "Falha ao salvar a importação. Confira a pasta da biblioteca e o espaço livre. Arquivos já enfileirados permanecem na biblioteca."}
                imported.append(row["id"])
            self._meeting_changed()
            return {"ok": True, "ids": imported, "store_root": str(self.meetings.root)}

    def _commit_settings_request(self, payload):
        from whisper_dictation.settings_commit import commit_settings
        with self._state_lock, self.meetings._lock:
            if self._restarting or self._shutdown.is_set() or self._state in {AppState.RECORDING, AppState.TRANSCRIBING} or self.meetings.busy:
                return {"ok": False, "error": "Conclua as gravações e a fila antes de salvar alterações."}
            try:
                data = payload.get("config")
                if not isinstance(data, dict):
                    raise ValueError("Configurações inválidas.")
                config = AppConfig(**AppConfig._sanitize(data))
                validate_hotkeys(config)
                previous = self.config
                try:
                    self._apply_config(config, suspend_service=True)
                    commit_settings(self.config_path, config, payload.get("keys", {}))
                    self.meetings._queue_suspended = False
                except Exception:
                    if self.config is not previous:
                        self._apply_config(previous)
                    raise
                return {"ok": True}
            except Exception as exc:
                self.logger.warning("Settings commit failed: %s", type(exc).__name__)
                return {"ok": False, "error": str(exc)}

    def toggle_meeting(self):
        with self._state_lock:
            if self._restarting or self._shutdown.is_set():
                return
            if self._state in {AppState.RECORDING, AppState.TRANSCRIBING}:
                self._notify("PACE", "Conclua o ditado antes de iniciar uma reunião.")
                return
            try:
                self.meetings.toggle()
                if self.meetings.active and self.config.meeting_show_overlay:
                    self._meeting_overlay_proc = subprocess.Popen(runtime_command(self.project_root, "--meeting-overlay"), creationflags=0x08000000)
            except Exception as exc:
                self._notify("PACE", f"Não foi possível gravar a reunião: {exc}")

    def pause_meeting(self):
        self.meetings.pause()

    def _meeting_changed(self):
        self._tray_update_event.set()

    def _tray_updates(self):
        while not self._shutdown.is_set():
            if not self._tray_update_event.wait(0.5):
                continue
            self._tray_update_event.clear()
            if self._shutdown.is_set():
                break
            try:
                with self._state_lock:
                    self.icon.icon = self._build_icon_image(AppState.RECORDING if self.meetings.active else self._state)
                    self.icon.title = self._tooltip()
                    self.icon.update_menu()
            except Exception:
                self.logger.debug("Tray meeting update failed", exc_info=True)

    def _capabilities(self):
        with self._ui_lock:
            navigation = dict(self._ui_navigation)
        return {"save_settings": True, "meetings": True, "import_media": True,
                "project_root": str(self.project_root), "version": __version__,
                "ui_navigation": navigation}

    def _open_recordings_now(self):
        self._open_main_window("recordings")

    def _open_main_window(self, page):
        try:
            with self._ui_lock:
                if self._restarting or self._shutdown.is_set():
                    return
                self._ui_navigation = {"id": uuid.uuid4().hex, "page": page}
                if self._recordings_proc is None or self._recordings_proc.poll() is not None:
                    self._recordings_proc = subprocess.Popen(
                        runtime_command(self.project_root, "--recordings-ui", "--ui-page", page),
                        creationflags=0x08000000,
                    )
        except Exception:
            self.logger.exception("Não foi possível abrir a janela do PACE.")
            self._notify("PACE", "Não foi possível abrir a janela. Tente novamente pelo menu da bandeja.")

    def _meeting_label(self, *_):
        if getattr(self, "meetings", None) and self.meetings._finalizing:
            return "Salvando reunião…"
        if getattr(self, "meetings", None) and self.meetings.active:
            return "Encerrar reunião"
        return "Gravar reunião"

    def _pause_label(self, *_):
        recorder = getattr(getattr(self, "meetings", None), "recorder", None)
        return "Retomar reunião" if recorder and recorder.paused else "Pausar reunião"

    def _open_settings(self, icon: pystray.Icon, item: MenuItem) -> None:
        del icon, item
        self._open_settings_now()

    def _open_settings_now(self) -> None:
        self._open_main_window("settings")

    def _reload_config_from_disk(self) -> None:
        if not self._can_change_config("aplicar as configurações"):
            return
        try:
            self._apply_config(AppConfig.load(self.config_path))
            self._set_state(AppState.IDLE, "Configurações aplicadas")
            self._notify("PACE", "Configurações atualizadas.")
        except Exception as exc:
            self._set_error(f"Falha ao aplicar configurações: {exc}")

    def _copy_last_transcript_action(self) -> None:
        if not self._last_transcript:
            return
        self._copy_transcript_to_clipboard(self._last_transcript)
        self._notify("PACE", "Última transcrição copiada.")

    def _copy_transcript_to_clipboard(self, text: str) -> bool:
        try:
            pyperclip.copy(text)
        except pyperclip.PyperclipException:
            self.logger.exception("Could not copy transcript to clipboard.")
            return False
        return True

    # ---------------------------- atualização ----------------------------
    def _check_updates(self, notify_when_current: bool = False) -> None:
        try:
            update = updater.check_for_update()
        except Exception as exc:
            self.logger.warning("Update check failed: %s", exc)
            if notify_when_current:
                self._notify("PACE", f"Não foi possível verificar atualizações: {exc}")
            return
        if update is not None:
            if not self._update_lock.acquire(blocking=False):
                return
            try:
                self._update_ready = False
                updater.prepare_update(update)
                self._update_available = update
                self._update_ready = True
            except Exception as exc:
                self._notify("PACE", f"Falha ao preparar atualização: {exc}")
                return
            finally:
                self._update_lock.release()
            try:
                self.icon.update_menu()
            except Exception:
                self.logger.debug("Could not refresh tray menu after update check.")
            self._notify(
                "PACE",
                f"v{update.version} pronta. Reinicie pelo menu para atualizar.",
            )
        elif notify_when_current:
            self._notify("PACE", "Você já está na versão mais recente.")

    def _run_update(self, update: updater.UpdateInfo) -> None:
        if not self._update_lock.acquire(blocking=False):
            return
        try:
            with self._state_lock:
                if self._state in {AppState.RECORDING, AppState.TRANSCRIBING} or self.meetings.busy:
                    self._notify("PACE", "A atualização está pronta. Conclua as gravações e a fila antes de reiniciar.")
                    return
                self._restarting = True
            with self._ui_lock:
                ui_process = self._recordings_proc
                if ui_process is not None and ui_process.poll() is None:
                    self._ui_navigation = {"id": uuid.uuid4().hex, "page": "close"}
            if ui_process is not None and ui_process.poll() is None:
                try:
                    ui_process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    raise updater.UpdateError("Conclua o preparo dos modelos ou feche a janela antes de atualizar.") from None
            if self._meeting_overlay_proc is not None and self._meeting_overlay_proc.poll() is None:
                self._meeting_overlay_proc.terminate()
                self._meeting_overlay_proc.wait(timeout=10)
            updater.restart_to_update(update, self.project_root)
            self._shutdown_app()
        except Exception as exc:
            self._restarting = False
            self._notify("PACE", f"Não foi possível aplicar a atualização: {exc}")
        finally:
            self._update_lock.release()

    def _request_restart_update(self):
        def ready_and_restart():
            if not self._update_available or not self._update_ready:
                self._check_updates(True)
            if self._update_available and self._update_ready:
                self._run_update(self._update_available)
        threading.Thread(target=ready_and_restart, daemon=True).start()

    def _menu_update(self, icon: pystray.Icon, item: MenuItem) -> None:
        del icon, item
        if self._update_available is not None and self._update_ready:
            threading.Thread(
                target=self._run_update,
                args=(self._update_available,),
                name="UpdateRun",
                daemon=True,
            ).start()
        else:
            self._notify("PACE", "Verificando atualizações…")
            threading.Thread(
                target=self._check_updates,
                args=(True,),
                name="UpdateCheck",
                daemon=True,
            ).start()

    def _quit(self, icon: pystray.Icon, item: MenuItem) -> None:
        del icon, item
        self._shutdown_app()

    def _shutdown_app(self) -> None:
        if self._shutdown.is_set():
            return
        self._shutdown.set()
        self._cancel_auto_stop()
        self.meetings.shutdown()
        self.recorder.abort()
        self.hotkey_manager.stop()
        for manager in self._meeting_hotkeys:
            manager.stop()
        self._overlay.stop()
        if self._instance is not None:
            self._instance.stop()
        self.icon.stop()
        self.logger.info("Application stopped")

    def _set_error(self, message: str) -> None:
        self._last_error = message
        self.logger.error(message)
        self._set_state(AppState.ERROR, message)
        self._notify("PACE", message)

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
            MenuItem("Abrir PACE", lambda *_: self._open_recordings_now(), default=True),
            Menu.SEPARATOR,
            MenuItem(lambda *_: self._toggle_label(), self._menu_toggle),
            MenuItem(self._meeting_label, lambda *_: self.toggle_meeting()),
            MenuItem(self._pause_label, lambda *_: self.pause_meeting(), enabled=lambda _: bool(getattr(self, "meetings", None) and self.meetings.active)),
            MenuItem("Gravações...", lambda *_: self._open_recordings_now()),
            Menu.SEPARATOR,
            MenuItem("Configurações...", self._open_settings),
            MenuItem(lambda *_: self._update_label(), self._menu_update),
            Menu.SEPARATOR,
            MenuItem("Sair", self._quit),
        )

    def _update_label(self) -> str:
        if self._update_available is not None and self._update_ready:
            return f"Reiniciar para atualizar · v{self._update_available.version}"
        return "Verificar atualizações"

    def _menu_toggle(self, icon: pystray.Icon, item: MenuItem) -> None:
        del icon, item
        self.toggle_recording()

    def _status_label(self) -> str:
        with self._state_lock:
            return f"PACE v{__version__} | {self._status_message}"

    def _toggle_label(self) -> str:
        with self._state_lock:
            if self._state == AppState.RECORDING:
                return "Parar gravação"
            if self._state == AppState.TRANSCRIBING:
                return "Transcrevendo..."
            return "Iniciar gravação"

    def _tooltip(self) -> str:
        meetings = getattr(self, "meetings", None)
        if meetings and meetings.active:
            return "PACE | Reunião pausada" if meetings.recorder.paused else "PACE | Gravando reunião"
        return f"PACE | {self._status_message}"

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
        """Draw the official PACE acoustic cursor icon with dynamic state colors."""
        target_size = 64
        canvas_size = 128
        img = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # Base Squircle: Grafite Acetinado (#10121A) com borda discreta
        draw.rounded_rectangle(
            (8, 8, 120, 120),
            radius=28,
            fill=(14, 16, 23, 255),
            outline=(38, 43, 60, 255),
            width=2,
        )

        # Cor dinâmica do cursor lúmen central
        state_colors = {
            AppState.IDLE: (255, 255, 255, 255),          # Branco lúmen
            AppState.RECORDING: (239, 68, 68, 255),       # Vermelho elétrico
            AppState.TRANSCRIBING: (52, 211, 153, 255),   # Esmeralda vibrante
            AppState.ERROR: (245, 158, 11, 255),          # Âmbar
        }
        center_color = state_colors.get(state, (255, 255, 255, 255))
        side_color = (68, 76, 102, 255)

        # Geometria das 5 hastes acústicas simétricas
        # w = 7px, pitch = 12.5px
        cx, cy = 64.0, 64.0
        w = 7.0
        r = w / 2.0
        bars = [
            (-25.0, 19.0, False),
            (-12.5, 38.0, False),
            (0.0,   60.0, True),
            (12.5,  38.0, False),
            (25.0,  19.0, False),
        ]

        for offset_x, h, is_center in bars:
            x0 = cx + offset_x - r
            x1 = cx + offset_x + r
            y0 = cy - h / 2.0
            y1 = cy + h / 2.0
            color = center_color if is_center else side_color
            draw.rounded_rectangle((x0, y0, x1, y1), radius=r, fill=color)

        return img.resize((target_size, target_size), Image.Resampling.LANCZOS)

