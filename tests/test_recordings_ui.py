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
    def test_scaled_window_keeps_all_export_and_action_buttons_in_work_area(self):
        import customtkinter as ctk
        labels = {"Copiar", "Exportar texto", "Exportar áudio", "Renomear", "Tentar novamente", "Cancelar tarefa", "Excluir"}
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        try:
            for scaling in (1.25, 1.5):
                ctk.set_window_scaling(scaling)
                ctk.set_widget_scaling(scaling)
                with patch("whisper_dictation.ui_utils.window_work_area", return_value=(0, 0, 1920, 1040)):
                    fit_window(self.window.root)
                self.window.root.deiconify()
                self.window.root.update()
                found = []
                for button in descendants(self.window.root):
                    if isinstance(button, ctk.CTkButton) and button.cget("text") in labels:
                        found.append(button.cget("text"))
                        self.assertLessEqual(button.winfo_rooty() + button.winfo_height(), 1040, (scaling, button.cget("text")))
                        self.assertGreaterEqual(button.winfo_rooty(), 0)
                self.assertEqual(set(found), labels)
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
        self.window = RecordingsWindow(self.directory)
        self.window.root.withdraw()
        self.window.refresh_list()
        self.window.root.update()

    def tearDown(self):
        self.window.close()
        self.load.stop()
        self.temp.cleanup()

    def test_filter_selects_visible_item_and_never_exports_hidden_previous_record(self):
        self.window.tree.selection_set(self.rows[0]["id"])
        self.window.select()
        self.assertEqual(self.window.selected, self.rows[0]["id"])
        self.window.search.insert(0, "B")
        self.window.refresh_list()
        self.window.root.update()
        self.window.select()
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
        self.window.tree.selection_set(row["id"])
        self.window.select()
        self.store.update(row["id"], status="queued")
        self.window.cancel()
        self.assertEqual(self.store.get(row["id"])["status"], "cancelled")
        self.assertTrue((self.store.directory(row["id"]) / "audio.wav").exists())
        self.window.retry()
        self.assertEqual(self.store.get(row["id"])["status"], "queued")
        self.assertFalse((self.store.directory(row["id"]) / "cancel.request").exists())

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
