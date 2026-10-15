"""Exercise real Tk widgets against an isolated synthetic library."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

from whisper_dictation.media import PCM16WaveWriter
from whisper_dictation.recording_store import RecordingStore
from whisper_dictation.recordings_window import RecordingsWindow
from whisper_dictation.settings import AppConfig
from whisper_dictation.settings_window import SettingsContext, _run_window, _build_meeting_tab
from whisper_dictation.ui_utils import close_window, fit_window


class RecordingsUITests(unittest.TestCase):
    def test_scaled_window_keeps_primary_controls_and_document_in_work_area(self):
        import customtkinter as ctk
        try:
            for scaling in (1.25, 1.5):
                ctk.set_window_scaling(scaling)
                ctk.set_widget_scaling(scaling)
                with patch("whisper_dictation.ui_utils.window_work_area", return_value=(0, 0, 1920, 1040)):
                    fit_window(self.window.root)
                self.window.root.deiconify()
                self.window.root.update()
                for widget in (self.window.record_button, self.window.import_button, self.window.play_button,
                               self.window.copy_button, self.window.export_button, self.window.more_button, self.window.document):
                    self.assertTrue(widget.winfo_ismapped(), (scaling, str(widget), widget.__class__.__name__))
                    self.assertLessEqual(widget.winfo_rooty() + widget.winfo_height(), 1040, scaling)
                    self.assertGreaterEqual(widget.winfo_rooty(), 0)
                    self.assertLessEqual(widget.winfo_rootx() + widget.winfo_width(), 1920, scaling)
                self.assertGreater(self.window.document.winfo_height(), 150)
                self.assertEqual(len(self.window.menu_actions("export")), 2)
                self.assertEqual(len(self.window.menu_actions("more")), 7)
        finally:
            ctk.set_window_scaling(1)
            ctk.set_widget_scaling(1)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.config = AppConfig(meeting_storage_dir=str(self.directory / "library"))
        self.store = RecordingStore(self.directory / "library")
        self.rows = []
        for title in ("Reunião A", "Reunião B"):
            row = self.store.create(title, "meeting")
            folder = self.store.directory(row["id"])
            audio = folder / "audio.wav"
            with PCM16WaveWriter(audio) as writer:
                writer.writeframesraw(np.zeros(1600, dtype="<i2").tobytes())
            result = folder / "result.json"
            result.write_text(json.dumps({"segments": [{"start": 0, "end": 0.1, "speaker": "SPEAKER_00", "text": title}]}), encoding="utf-8")
            self.store.update(row["id"], status="ready", audio_path=str(audio), result_path=str(result), duration=0.1)
            self.rows.append(row)
        self.load = patch("whisper_dictation.recordings_window.AppConfig.load", return_value=self.config)
        self.load.start()
        # These fixtures simulate physical 100/125/150% independently of the
        # desktop's current DPI (otherwise 150% on a 300% monitor becomes 450%).
        self.dpi = patch("customtkinter.ScalingTracker.get_window_dpi_scaling", return_value=1)
        self.dpi.start()
        self.window = RecordingsWindow(self.directory)
        self.window.root.withdraw()
        self.window.refresh_list()
        self.window.root.update()

    def tearDown(self):
        self.window.close()
        self.load.stop()
        self.dpi.stop()
        self.temp.cleanup()

    def test_filter_selects_visible_item_and_never_exports_hidden_previous_record(self):
        self.window.select_recording(self.rows[0]["id"])
        self.assertEqual(self.window.selected, self.rows[0]["id"])
        self.window.search.insert(0, "B")
        self.window.refresh_list()
        self.window.root.update()
        self.assertEqual(self.window.selected, self.rows[1]["id"])
        self.assertIn("Reunião B", self.window.text.get("1.0", "end"))
        self.assertIn("Falante 1", self.window.text.get("1.0", "end"))
        self.assertEqual(self.window.player.path.parent, self.store.directory(self.rows[1]["id"]))
        self.window.search.delete(0, "end")
        self.window.search.insert(0, "Nenhum resultado")
        self.window.refresh_list()
        self.assertIsNone(self.window.selected)
        self.assertIsNone(self.window.player.path)
        self.assertEqual(self.window.text.get("1.0", "end").strip(), "")

    def test_cancel_and_retry_preserve_audio_and_use_record_state(self):
        row = self.rows[0]
        self.window.select_recording(row["id"])
        self.store.update(row["id"], status="queued")
        self.window.cancel()
        self.assertEqual(self.store.get(row["id"])["status"], "cancelled")
        self.assertTrue((self.store.directory(row["id"]) / "audio.wav").exists())
        self.window.retry()
        self.assertEqual(self.store.get(row["id"])["status"], "queued")
        self.assertFalse((self.store.directory(row["id"]) / "cancel.request").exists())

    def test_menu_opens_and_export_commands_are_dispatched(self):
        self.window.root.deiconify()
        self.window.root.update()
        with patch.object(self.window, "export_text") as export_text, patch.object(self.window, "export_audio") as export_audio:
            self.window.open_menu("export")
            self.window.root.update()
            self.assertTrue(self.window._menu.winfo_ismapped())
            self.window._menu_buttons[0].invoke()
            self.assertIsNone(self.window._menu)
            self.window.open_menu("export")
            self.window._menu_buttons[1].invoke()
            export_text.assert_called_once()
            export_audio.assert_called_once()

    def test_context_actions_follow_processing_state(self):
        row = self.window.row()
        actions = lambda: {label: enabled for label, _, enabled in self.window.menu_actions("more")}
        self.assertTrue(actions()["Excluir gravação"])
        self.assertFalse(actions()["Cancelar processamento"])
        self.store.update(row["id"], status="transcribing")
        self.assertFalse(actions()["Excluir gravação"])
        self.assertFalse(actions()["Processar novamente"])
        self.assertTrue(actions()["Cancelar processamento"])
        self.store.update(row["id"], status="recording")
        self.assertFalse(actions()["Cancelar processamento"])

    def test_popover_keyboard_skips_disabled_actions_and_has_no_stale_timers(self):
        self.window.root.deiconify()
        row = self.window.row()
        self.store.update(row["id"], status="transcribing", result_path=None)
        (self.store.directory(row["id"]) / "result.json").unlink()
        self.window.refresh_list()
        self.window.refresh_detail()
        errors = []
        self.window.root.tk.createcommand("bgerror", errors.append)
        def pump():
            self.window.root.after(150, self.window.root.quit)
            self.window.root.mainloop()
        self.window.open_menu("more")
        pump()
        buttons = self.window._menu_buttons
        self.assertEqual(self.window.root.focus_get(), buttons[2]._canvas)
        buttons[2]._canvas.event_generate("<Tab>")
        pump()
        self.assertEqual(self.window.root.focus_get(), buttons[4]._canvas)
        self.window._menu.event_generate("<Escape>")
        pump()
        self.assertIsNone(self.window._menu)
        self.assertEqual(self.window.root.focus_get(), self.window.more_button._canvas)
        with patch.object(self.window, "export_audio") as export_audio:
            self.window.open_menu("export")
            button = self.window._menu_buttons[1]
            button._on_enter()
            button._on_release()
            pump()
            export_audio.assert_called_once()
        for _ in range(3):
            self.window.open_menu("more")
            self.window.close_menu()
        pump()
        self.assertEqual(errors, [])
        self.assertEqual(len(self.window._menus), 2)

    def test_long_title_preserves_document_and_action_access_at_minimum_size(self):
        self.window.root.deiconify()
        self.window.root.geometry("850x590")
        row = self.window.row()
        title = "Planejamento e acompanhamento detalhado da equipe " * 6
        self.store.update(row["id"], title=title)
        self.window.refresh_list()
        self.window.refresh_detail()
        self.window.root.update()
        self.assertLessEqual(len(self.window.title.cget("text").splitlines()), 2)
        self.assertTrue(self.window.title.cget("text").endswith("…"))
        self.assertEqual(self.window.row()["title"], title)
        self.assertTrue(self.window.document.winfo_ismapped())
        self.assertGreater(self.window.document.winfo_height(), 130)
        self.assertTrue(self.window.more_button.winfo_ismapped())

    def test_minimum_window_keeps_transcript_readable_with_capture_warning_and_search(self):
        self.window.root.deiconify()
        self.window.root.geometry("850x590")
        row = self.window.row()
        self.store.update(row["id"], title="Reunião de planejamento detalhado e acompanhamento das atividades da equipe " * 4,
                          error="Aviso sobre processamento e recuperação da transcrição. " * 4)
        with patch("whisper_dictation.recordings_window.read_runtime", return_value={"active": True, "paused": True, "duration": 123}):
            self.window.refresh()
            self.window.toggle_find(True)
            self.window.root.update()
        self.assertTrue(self.window.capture_bar.winfo_ismapped())
        self.assertTrue(self.window.notice.winfo_ismapped())
        self.assertTrue(self.window.find_row.winfo_ismapped())
        self.assertGreaterEqual(self.window.document.winfo_height(), 90)

    def test_empty_transcript_does_not_replace_clipboard(self):
        row = self.window.row()
        result = self.store.directory(row["id"]) / "result.json"
        result.write_text('{"segments": [], "text": ""}', encoding="utf-8")
        self.window.refresh_detail()
        self.assertIn("Nenhuma fala", self.window.text.get("1.0", "end"))
        self.assertEqual(self.window.copy_button.cget("state"), "disabled")
        with patch.object(self.window.root, "clipboard_clear") as clear:
            self.window.copy()
            clear.assert_not_called()

    def test_refresh_preserves_card_keyboard_focus(self):
        self.window.root.deiconify()
        self.window.root.update()
        selected = self.window.selected
        self.window.list_rows[selected]._canvas.focus_force()
        self.window.root.update()
        other = next(row for row in self.rows if row["id"] != selected)
        self.store.update(other["id"], duration=10)
        self.window.refresh_list()
        self.window.root.update()
        self.assertEqual(self.window.root.focus_get(), self.window.list_rows[selected]._canvas)
        self.window.list_rows[selected]._canvas.event_generate("<Down>")
        self.window.root.update()
        self.assertIn(self.window.selected, self.window.list_rows)
        self.assertNotEqual(self.window.selected, selected)

    def test_keyboard_navigation_search_and_rename_preserve_selection(self):
        self.window.select_recording(self.rows[0]["id"])
        self.window.move_selection(-1)
        first = list(self.window.list_rows)[0]
        self.assertEqual(self.window.selected, first)
        self.window.move_selection(1)
        self.assertNotEqual(self.window.selected, first)
        selected = self.window.selected
        with patch("whisper_dictation.recordings_window.simpledialog.askstring", return_value="Título revisado"):
            self.window.rename()
        self.assertEqual(self.window.selected, selected)
        self.assertEqual(self.window.title.cget("text"), "Título revisado")
        self.window.toggle_find(True)
        self.window.find_entry.insert(0, "Reunião")
        self.window.find()
        self.assertTrue(self.window.text.tag_ranges("found"))
        self.window.toggle_find(False)
        self.assertFalse(self.window.text.tag_ranges("found"))

    def test_pause_only_appears_during_capture_and_playback_is_blocked(self):
        self.window.root.deiconify()
        for state in ({}, {"active": True, "duration": 22}, {"active": True, "paused": True, "duration": 22}, {"finalizing": True}):
            with patch("whisper_dictation.recordings_window.read_runtime", return_value=state):
                self.window.refresh()
                self.window.root.update()
            self.assertEqual(bool(self.window.capture_bar.winfo_ismapped()), bool(state))
            if state.get("active"):
                self.assertEqual(self.window.play_button.cget("state"), "disabled")
                self.assertEqual(self.window.pause_button.cget("text"), "Retomar" if state.get("paused") else "Pausar")
            if state.get("finalizing"):
                self.assertEqual(self.window.record_button.cget("state"), "disabled")

    def test_import_with_spaces_keeps_original_and_enqueues_in_same_library(self):
        source = self.directory / "Meu vídeo com espaços.mp4"
        source.write_bytes(b"original")
        def import_response(payload, **kwargs):
            if isinstance(payload, str):
                return {"meetings": True, "import_media": True, "project_root": str(self.directory)}
            row = self.store.create(source.stem, "import")
            self.store.update(row["id"], source_path=payload["paths"][0], status="queued")
            return {"ok": True, "ids": [row["id"]], "store_root": str(self.store.root)}
        with patch("whisper_dictation.recordings_window.request", side_effect=import_response):
            self.window.import_files([str(source)])
        imported = next(r for r in self.store.list() if r["origin"] == "import")
        self.assertEqual(imported["status"], "queued")
        self.assertEqual(imported["source_path"], str(source))
        self.assertEqual(source.read_bytes(), b"original")

    def test_old_or_other_installation_cannot_control_or_enqueue_meeting(self):
        source = self.directory / "test.wav"
        source.write_bytes(b"original")
        for capabilities in ({}, {"meetings": True, "project_root": str(self.directory / "other")}):
            with patch("whisper_dictation.recordings_window.request", return_value=capabilities), \
                 patch("whisper_dictation.recordings_window.send_command") as send, \
                 patch("whisper_dictation.recordings_window.messagebox.showerror") as error:
                self.window.control("meeting-toggle")
                self.window.import_files([str(source)])
                send.assert_not_called()
                self.assertEqual(error.call_count, 2)
        self.assertEqual(len(self.store.list()), 2)


class SettingsUITests(unittest.TestCase):
    def test_token_free_engine_disables_counts_and_switches_backend_choices(self):
        import customtkinter as ctk
        root = ctk.CTk()
        root.withdraw()
        try:
            with patch("whisper_dictation.meeting_audio.list_meeting_devices", return_value={"inputs": [], "outputs": []}), \
                 patch("whisper_dictation.settings_window.secrets_store.get_api_key", return_value=None):
                collect = _build_meeting_tab(root, AppConfig(diarization_num_speakers=12, diarization_backend="openvino"), {}, root)
            values = collect()
            self.assertEqual(values["diarization_engine"], "nemotron")
            self.assertEqual(values["diarization_backend"], "auto")
            self.assertEqual(values["diarization_num_speakers"], 0)
            self.assertEqual(collect.token_value(), "")
            def descendants(widget):
                for child in widget.winfo_children():
                    yield child
                    yield from descendants(child)
            engine = next(w for w in descendants(root) if isinstance(w, ctk.CTkOptionMenu) and "Nemotron" in w.get())
            engine.set("Community-1 — avançado (contagem orientada)")
            engine._command(engine.get())
            self.assertEqual(collect()["diarization_engine"], "community-1")
            self.assertEqual(collect()["diarization_num_speakers"], 12)
        finally:
            close_window(root)

    def test_rejected_save_cannot_change_provider_or_huggingface_credentials(self):
        import customtkinter as ctk
        import tkinter as tk
        root = ctk.CTk()
        on_save = Mock()
        errors = []
        def invoke_save():
            def buttons(widget):
                for child in widget.winfo_children():
                    if isinstance(child, ctk.CTkButton) and child.cget("text") == "Salvar":
                        child.invoke()
                    else:
                        buttons(child)
            buttons(root)
            close_window(root)
        root.after(150, invoke_save)
        with patch("whisper_dictation.settings_window.ctk.CTk", return_value=root), \
             patch("whisper_dictation.settings_window.secrets_store.get_api_key", return_value=None), \
             patch("whisper_dictation.settings_window.secrets_store.set_api_key") as set_key, \
             patch("whisper_dictation.settings_window._persist_keys") as persist_keys, \
             patch.object(root, "mainloop", side_effect=lambda: tk.Tk.mainloop(root)), \
             patch("whisper_dictation.settings_window.messagebox.showerror", side_effect=lambda *args: errors.append(args)):
            _run_window(AppConfig(), on_save, SettingsContext(can_save=lambda: False))
        self.assertTrue(errors)
        on_save.assert_not_called()
        persist_keys.assert_not_called()
        set_key.assert_not_called()


if __name__ == "__main__":
    unittest.main()
