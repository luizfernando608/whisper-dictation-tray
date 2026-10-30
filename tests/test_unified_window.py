"""Behavior of the shared PACE window, tray entry and repeated navigation."""
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import customtkinter as ctk
import numpy as np

from whisper_dictation._version import __version__
from whisper_dictation.app import AppState, DictationApp
from whisper_dictation.media import PCM16WaveWriter
from whisper_dictation.recording_store import RecordingStore
from whisper_dictation.recordings_window import RecordingsWindow
from whisper_dictation.settings import AppConfig
from whisper_dictation.settings_window import SettingsContext


class UnifiedRoutingTests(unittest.TestCase):
    def app(self):
        app = DictationApp.__new__(DictationApp)
        app.project_root = Path('C:/PACE-routing-test')
        app._ui_lock = threading.Lock()
        app._ui_navigation = {'id': '', 'page': 'recordings'}
        app._recordings_proc = None
        app._meeting_overlay_proc = None
        app._restarting = False
        app._shutdown = threading.Event()
        app._state_lock = threading.RLock()
        app._state = AppState.IDLE
        app._update_lock = threading.Lock()
        app.meetings = Mock(busy=False)
        app.logger = Mock()
        app._notify = Mock()
        return app

    def test_repeated_and_concurrent_entries_share_one_ui_child(self):
        app = self.app()
        child = Mock()
        child.poll.return_value = None
        with patch('whisper_dictation.app.subprocess.Popen', return_value=child) as launch:
            threads = [threading.Thread(target=app._open_settings_now) for _ in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            app._open_recordings_now()
            launch.assert_called_once()
            self.assertIn('--recordings-ui', launch.call_args.args[0])
            self.assertEqual(app._capabilities()['ui_navigation']['page'], 'recordings')
            first = app._capabilities()['ui_navigation']['id']
            app._open_recordings_now()
            self.assertNotEqual(app._capabilities()['ui_navigation']['id'], first)
        app._shutdown.set()
        with patch('whisper_dictation.app.subprocess.Popen') as launch:
            app._open_settings_now()
            launch.assert_not_called()

    def test_default_tray_action_opens_without_toggling_either_capture(self):
        app = self.app()
        app._open_recordings_now = Mock()
        app.toggle_recording = Mock()
        app.toggle_meeting = Mock()
        defaults = [item for item in app._build_menu().items if item.default]
        self.assertEqual(len(defaults), 1)
        defaults[0](Mock())
        app._open_recordings_now.assert_called_once()
        app.toggle_recording.assert_not_called()
        app.toggle_meeting.assert_not_called()

    def test_failed_graceful_ui_close_defers_update_without_killing_preparation(self):
        app = self.app()
        app._recordings_proc = Mock()
        app._recordings_proc.poll.return_value = None
        app._recordings_proc.wait.side_effect = subprocess.TimeoutExpired('Pace', 10)
        with patch('whisper_dictation.app.updater.restart_to_update') as restart:
            app._run_update(Mock())
        self.assertEqual(app._capabilities()['ui_navigation']['page'], 'close')
        app._recordings_proc.wait.assert_called_once()
        app._recordings_proc.terminate.assert_not_called()
        restart.assert_not_called()
        self.assertFalse(app._restarting)


class UnifiedWindowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.config = AppConfig(meeting_storage_dir=str(self.directory / 'library'))
        self.config.save(self.directory / 'config.json')
        store = RecordingStore(self.directory / 'library')
        row = store.create('Existing recording', 'meeting')
        audio = store.directory(row['id']) / 'audio.wav'
        with PCM16WaveWriter(audio) as writer:
            writer.writeframesraw(np.zeros(16000, dtype='<i2').tobytes())
        result = store.directory(row['id']) / 'result.json'
        result.write_text(json.dumps({'segments': [{'start': 0, 'end': 1, 'text': 'Preserved transcript'}]}))
        store.update(row['id'], status='ready', duration=1, audio_path=str(audio), result_path=str(result))
        self.frames = []
        self.callbacks = []
        def build(parent, *args, on_close=None, **kwargs):
            frame = ctk.CTkFrame(parent)
            frame.draft = ctk.CTkEntry(frame)
            frame.draft.pack()
            frame.can_close = Mock(return_value=True)
            frame.set_active = Mock()
            self.frames.append(frame)
            self.callbacks.append(on_close)
            return frame
        self.context_patch = patch('whisper_dictation.settings_ui.create_settings_context',
                                   return_value=(Mock(), SettingsContext()))
        self.builder_patch = patch('whisper_dictation.settings_window.build_settings_frame', side_effect=build)
        self.context_patch.start()
        self.builder = self.builder_patch.start()
        self.window = RecordingsWindow(self.directory)
        self.window.root.withdraw()
        self.window.refresh_list()
        self.window.root.update()

    def tearDown(self):
        for frame in self.frames:
            frame.can_close.return_value = True
        if not self.window._closing:
            self.window.close()
        self.builder_patch.stop()
        self.context_patch.stop()
        self.temp.cleanup()

    def test_navigation_preserves_root_selection_player_and_settings_draft(self):
        root, selected = self.window.root, self.window.selected
        self.window.player.seek(.5)
        self.window.show_settings()
        self.frames[0].draft.insert(0, 'unsaved draft')
        self.assertEqual(self.window.current_page, 'settings')
        self.assertIs(self.frames[0].winfo_toplevel(), root)
        self.window.show_library()
        self.assertEqual(self.window.current_page, 'recordings')
        self.assertEqual(self.window.selected, selected)
        self.assertAlmostEqual(self.window.player.position, .5)
        self.window.show_settings()
        self.builder.assert_called_once()
        self.assertEqual(self.frames[0].draft.get(), 'unsaved draft')
        self.assertIs(self.window.root, root)

    def test_save_and_cancel_return_to_library_without_closing_root(self):
        self.window.show_settings()
        self.callbacks[0](self.config)
        self.assertEqual(self.window.current_page, 'recordings')
        self.assertTrue(self.window.root.winfo_exists())
        self.window.show_settings()
        self.assertEqual(self.builder.call_count, 2)
        self.callbacks[1](None)
        self.assertEqual(self.window.current_page, 'recordings')
        self.assertTrue(self.window.root.winfo_exists())

    def test_navigation_is_scoped_and_focuses_only_new_requests(self):
        payload = {'project_root': str(self.directory), 'version': __version__,
                   'ui_navigation': {'id': 'request-a', 'page': 'settings'}}
        with patch.object(self.window.root, 'lift') as focus:
            self.window.apply_navigation({**payload, 'project_root': str(self.directory / 'other')})
            self.window.apply_navigation({**payload, 'version': '0.0.1'})
            self.assertEqual(self.window.current_page, 'recordings')
            self.window.apply_navigation(payload)
            self.window.apply_navigation(payload)
            self.assertEqual(self.window.current_page, 'settings')
            focus.assert_called_once()
            payload['ui_navigation'] = {'id': 'request-b', 'page': 'recordings'}
            self.window.apply_navigation(payload)
            self.assertEqual(self.window.current_page, 'recordings')
            self.assertEqual(focus.call_count, 2)

    def test_model_preparation_guards_close_even_when_settings_hidden(self):
        self.window.show_settings()
        self.frames[0].can_close.return_value = False
        self.window.show_library()
        self.window.close()
        self.assertFalse(self.window._closing)
        self.assertTrue(self.window.root.winfo_exists())

    def test_clicking_a_library_card_returns_from_settings_to_its_transcript(self):
        self.window.show_settings()
        self.window.root.deiconify()
        self.window.root.update()
        row_id = self.window.selected
        self.window.list_rows[row_id]._canvas.event_generate('<Button-1>', x=10, y=10)
        self.window.root.update()
        self.assertEqual(self.window.current_page, 'recordings')
        self.assertEqual(self.window.selected, row_id)


class CombinedHotkeyTests(unittest.TestCase):
    def test_ctrl_f_can_be_captured_and_saved_from_the_real_embedded_form(self):
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            config = AppConfig(meeting_storage_dir=str(path / 'library'))
            config.save(path / 'config.json')
            saved = Mock(return_value=True)
            context = SettingsContext(can_save=lambda: True, commit_settings=saved)
            with patch('whisper_dictation.settings_ui.create_settings_context', return_value=(Mock(), context)), \
                 patch('whisper_dictation.settings_window.secrets_store.get_api_key', return_value=None), \
                 patch('whisper_dictation.settings_window.list_input_devices', return_value=[]), \
                 patch('whisper_dictation.meeting_audio.list_meeting_devices', return_value={'inputs': [], 'outputs': []}):
                window = RecordingsWindow(path)
                try:
                    window.show_settings()
                    tabview = next(widget for widget in descendants(window.root) if isinstance(widget, ctk.CTkTabview))
                    tabview.set('Atalho')
                    window.root.deiconify()
                    window.root.update()
                    capture = next(widget for widget in descendants(tabview.tab('Atalho'))
                                   if isinstance(widget, ctk.CTkButton) and widget.cget('text') == 'Gravar atalho')
                    capture.invoke()
                    window.root.update()
                    window.root.event_generate('<KeyPress>', keysym='Control_L')
                    window.root.event_generate('<KeyPress>', keysym='f', state=4)
                    window.root.event_generate('<KeyRelease>', keysym='f', state=4)
                    window.root.event_generate('<KeyRelease>', keysym='Control_L')
                    window.root.update()
                    save = next(widget for widget in descendants(window.root)
                                if isinstance(widget, ctk.CTkButton) and widget.cget('text') == 'Salvar')
                    save.invoke()
                    self.assertEqual(saved.call_args.args[0].hotkey, 'Ctrl+F')
                    self.assertTrue(window.root.winfo_exists())
                finally:
                    window.close()


if __name__ == '__main__':
    unittest.main()
