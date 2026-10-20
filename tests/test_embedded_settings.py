"""Settings share the library's Tk interpreter and keep drafts transactional."""
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import customtkinter as ctk

from whisper_dictation import settings_ui, settings_window
from whisper_dictation.recording_store import RecordingStore
from whisper_dictation.settings import AppConfig
from whisper_dictation.ui_utils import close_window


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


class EmbeddedSettingsTests(unittest.TestCase):
    def setUp(self):
        self.root = ctk.CTk()
        self.root.withdraw()
        self.temp = tempfile.TemporaryDirectory()
        self.errors = []
        self.root.tk.createcommand("bgerror", self.errors.append)
        self.patches = [
            patch("whisper_dictation.settings_window.secrets_store.get_api_key", return_value=None),
            patch("whisper_dictation.settings_window.secrets_store.set_api_key"),
            patch("whisper_dictation.settings_window.list_input_devices", return_value=[]),
            patch("whisper_dictation.meeting_audio.list_meeting_devices", return_value={"inputs": [], "outputs": []}),
            patch("whisper_dictation.settings_window.messagebox.showerror"),
            patch("whisper_dictation.settings_window.messagebox.showinfo"),
        ]
        self.mocks = [item.start() for item in self.patches]
        self.closed = Mock()
        self.saved = Mock(return_value=True)
        self.fallback_save = Mock(return_value=True)

    def tearDown(self):
        close_window(self.root)
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def build(self, context=None):
        with patch("whisper_dictation.settings_window.ctk.CTk", side_effect=AssertionError("extra Tk root")), \
             patch("whisper_dictation.settings_window.ctk.CTkToplevel", side_effect=AssertionError("extra Toplevel")):
            self.frame = settings_window.build_settings_frame(
                self.root, AppConfig(), self.fallback_save,
                context or settings_window.SettingsContext(commit_settings=self.saved),
                on_close=self.closed,
            )
        self.frame.pack(fill="both", expand=True)
        return self.frame

    def button(self, text):
        return next(w for w in descendants(self.frame) if isinstance(w, ctk.CTkButton) and w.cget("text") == text)

    def pump(self, milliseconds=150):
        self.root.after(milliseconds, self.root.quit)
        self.root.mainloop()

    def test_cancel_discards_draft_without_closing_shared_root_or_writing_keys(self):
        self.build()
        self.button("Cancelar").invoke()
        self.closed.assert_called_once_with(None)
        self.assertTrue(self.root.winfo_exists())
        self.saved.assert_not_called()
        self.mocks[1].assert_not_called()

    def test_successful_save_calls_transaction_then_returns_config_without_closing_root(self):
        self.build()
        combo = next(w for w in descendants(self.frame) if isinstance(w, ctk.CTkComboBox))
        combo.set("custom-speech-model")
        self.button("Salvar").invoke()
        config, keys = self.saved.call_args.args
        self.assertEqual(config.groq_model, "custom-speech-model")
        self.assertEqual(keys, {})
        self.closed.assert_called_once_with(config)
        self.assertTrue(self.root.winfo_exists())
        self.fallback_save.assert_not_called()
        self.mocks[1].assert_not_called()

    def test_busy_queue_blocks_save_and_failed_commit_keeps_draft(self):
        allowed = Mock(return_value=False)
        self.build(settings_window.SettingsContext(can_save=allowed, commit_settings=self.saved))
        self.button("Salvar").invoke()
        self.saved.assert_not_called()
        self.closed.assert_not_called()
        allowed.return_value = True
        self.saved.side_effect = RuntimeError("synthetic rejected commit")
        combo = next(w for w in descendants(self.frame) if isinstance(w, ctk.CTkComboBox))
        combo.set("draft-model")
        self.button("Salvar").invoke()
        self.assertEqual(combo.get(), "draft-model")
        self.closed.assert_not_called()
        self.mocks[1].assert_not_called()

    def test_hidden_page_preserves_draft_and_deactivates_hotkey_capture_and_unbinds_on_destroy(self):
        self.build()
        combo = next(w for w in descendants(self.frame) if isinstance(w, ctk.CTkComboBox))
        combo.set("draft-model")
        external = self.root.bind("<KeyPress>", lambda event: None, add="+")
        self.button("Gravar atalho").invoke()
        self.frame.set_active(False)
        self.frame.pack_forget()
        self.assertEqual(self.button("Gravar atalho").cget("text"), "Gravar atalho")
        self.frame.pack(fill="both", expand=True)
        self.frame.set_active(True)
        self.assertEqual(combo.get(), "draft-model")
        self.frame.destroy()
        self.assertIn(external, self.root.bind("<KeyPress>"))
        self.assertEqual(self.root.bind("<KeyPress>" ).count("if {"), 1)
        self.assertFalse(self.root.bind("<KeyRelease>"))
        self.assertTrue(self.root.winfo_exists())

    def test_model_preparation_blocks_save_cancel_and_continues_while_hidden(self):
        self.build()
        process = Mock(returncode=0)
        process.poll.return_value = None
        job = Mock()
        with patch("whisper_dictation.settings_window.data_dir", return_value=Path(self.temp.name)), \
             patch("whisper_dictation.settings_window.subprocess.Popen", return_value=process), \
             patch("whisper_dictation.child_job.ChildJob", return_value=job):
            self.button("Preparar modelos").invoke()
        self.assertFalse(self.frame.can_close())
        self.button("Salvar").invoke()
        self.button("Cancelar").invoke()
        self.saved.assert_not_called()
        self.closed.assert_not_called()
        self.frame.set_active(False)
        self.frame.pack_forget()
        process.poll.return_value = 0
        self.pump(550)
        self.assertTrue(self.frame.can_close())
        self.assertEqual(self.button("Preparar modelos").cget("state"), "normal")
        job.close.assert_called()
        self.assertEqual(self.errors, [])

    def test_changed_huggingface_token_cannot_be_persisted_or_used_before_commit(self):
        self.build()
        engine = next(w for w in descendants(self.frame) if isinstance(w, ctk.CTkOptionMenu) and "Nemotron" in w.get())
        engine.set("Community-1 — avançado (contagem orientada)")
        engine._command(engine.get())
        token = next(w for w in descendants(self.frame) if isinstance(w, ctk.CTkEntry) and w.cget("show") == "•" and not w.cget("textvariable"))
        token.insert(0, "synthetic-draft-token")
        with patch("whisper_dictation.settings_window.subprocess.Popen") as launch:
            self.button("Preparar modelos").invoke()
        launch.assert_not_called()
        self.mocks[1].assert_not_called()
        self.assertTrue(any("Salve" in str(w.cget("text")) for w in descendants(self.frame) if isinstance(w, ctk.CTkLabel)))
        self.button("Salvar").invoke()
        self.assertEqual(self.saved.call_args.args[1], {"huggingface": "synthetic-draft-token"})

    def test_destroy_cancels_update_polling_and_owned_model_job_without_touching_library_timer(self):
        release = threading.Event()
        def check_update():
            release.wait(3)
            return None
        self.build(settings_window.SettingsContext(check_update=check_update))
        self.button("Verificar atualizações").invoke()
        process, job = Mock(), Mock()
        self.frame._model_setup = (process, job)
        library_tick = Mock()
        self.root.after(120, library_tick)
        self.frame.destroy()
        release.set()
        self.pump(250)
        job.close.assert_called_once()
        library_tick.assert_called_once()
        self.assertEqual(self.errors, [])

    def test_update_callback_waits_for_model_preparation_before_requesting_restart(self):
        update = Mock(version="synthetic-update")
        apply_update = Mock()
        self.build(settings_window.SettingsContext(check_update=lambda: update, run_update=apply_update))
        self.button("Verificar atualizações").invoke()
        self.pump()
        process, job = Mock(), Mock()
        process.poll.return_value = None
        self.frame._model_setup = (process, job)
        self.button("Reiniciar para atualizar").invoke()
        apply_update.assert_not_called()
        process.poll.return_value = 0
        self.button("Reiniciar para atualizar").invoke()
        apply_update.assert_called_once_with(update)
        self.assertTrue(self.root.winfo_exists())
        self.assertEqual(self.errors, [])

    def test_settings_controls_fit_main_content_pane_at_minimum_size_and_scaled_dpi(self):
        with patch("customtkinter.ScalingTracker.get_window_dpi_scaling", return_value=1):
            close_window(self.root)
            self.root = ctk.CTk()
            self.build()
            self.root.deiconify()
            self.root.update()
            tabview = next(w for w in descendants(self.frame) if isinstance(w, ctk.CTkTabview))
            try:
                for scaling in (1, 1.25, 1.5):
                    ctk.set_window_scaling(scaling)
                    ctk.set_widget_scaling(scaling)
                    self.root.minsize(1, 1)
                    self.root.maxsize(10000, 10000)
                    for width, height in ((832, 720), (562, 590)):
                        self.root.geometry(f"{width}x{height}")
                        self.root.update()
                        left, top = self.frame.winfo_rootx(), self.frame.winfo_rooty()
                        right = left + self.frame.winfo_width()
                        bottom = top + self.frame.winfo_height()
                        def assert_visible(widget):
                            detail = (scaling, width, height, tabview.get(), type(widget).__name__)
                            self.assertTrue(widget.winfo_ismapped(), detail)
                            self.assertGreaterEqual(widget.winfo_rootx(), left, detail)
                            self.assertGreaterEqual(widget.winfo_rooty(), top, detail)
                            self.assertLessEqual(widget.winfo_rootx() + widget.winfo_width(), right, detail)
                            self.assertLessEqual(widget.winfo_rooty() + widget.winfo_height(), bottom, detail)
                        for page in ("Transcrição", "Áudio", "Atalho", "Sobre"):
                            tabview.set(page)
                            self.pump()
                            for widget in descendants(tabview.tab(page)):
                                if isinstance(widget, (ctk.CTkEntry, ctk.CTkComboBox, ctk.CTkOptionMenu, ctk.CTkButton)):
                                    assert_visible(widget)
                            assert_visible(self.button("Salvar"))
                            assert_visible(self.button("Cancelar"))
                        tabview.set("Reuniões")
                        self.pump()
                        meeting = next(w for w in descendants(tabview.tab("Reuniões")) if isinstance(w, ctk.CTkScrollableFrame))
                        meeting._parent_canvas.yview_moveto(1)
                        self.root.update()
                        assert_visible(self.button("Preparar modelos"))
            finally:
                ctk.set_window_scaling(1)
                ctk.set_widget_scaling(1)


class SettingsContextTests(unittest.TestCase):
    def test_save_guard_reloads_committed_library_and_blocks_active_finalizing_or_busy_queue(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            path = project / "config.json"
            initial = AppConfig(meeting_storage_dir=str(project / "old"))
            current = AppConfig(meeting_storage_dir=str(project / "current"))
            current.save(path)
            store = RecordingStore(Path(current.meeting_storage_dir))
            with patch("whisper_dictation.settings_ui.config_path", return_value=path):
                on_save, context = settings_ui.create_settings_context(project, initial)
            self.assertTrue(callable(on_save))
            self.assertTrue(context.can_save())
            runtime = store.root / "runtime.json"
            for state in ({"active": True}, {"finalizing": True}):
                runtime.write_text(json.dumps({**state, "updated_at": time.time()}), encoding="utf-8")
                self.assertFalse(context.can_save())
            runtime.write_text(json.dumps({"active": True, "updated_at": time.time() - 60}), encoding="utf-8")
            self.assertTrue(context.can_save())
            store.create("Synthetic queued meeting", "meeting", status="queued")
            self.assertFalse(context.can_save())

    def test_legacy_settings_entry_opens_shared_window_on_settings_page(self):
        project = Path("synthetic-installation")
        with patch("whisper_dictation.recordings_window.run_recordings_window") as run:
            settings_ui.run_settings_ui(project)
        run.assert_called_once_with(project, initial_page="settings")
