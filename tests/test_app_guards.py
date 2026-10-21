import threading
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from whisper_dictation import updater
from whisper_dictation.app import AppState, DictationApp
from whisper_dictation.recording_store import RecordingStore


class AppGuardTests(unittest.TestCase):
    def test_media_import_is_enqueued_by_owner_in_current_library(self):
        with tempfile.TemporaryDirectory() as directory:
            app = self.app()
            app.meetings._lock = threading.RLock()
            app.meetings.root = Path(directory) / "current-library"
            app.meetings.store = RecordingStore(app.meetings.root)
            app._meeting_changed = Mock()
            source = Path(directory) / "Vídeo com espaços.mp4"
            source.write_bytes(b"original")
            result = app._import_media_request({"paths": [str(source)]})
            self.assertTrue(result["ok"])
            row = app.meetings.store.get(result["ids"][0])
            self.assertEqual(row["status"], "queued")
            self.assertEqual(row["source_path"], str(source.resolve()))
            self.assertEqual(result["store_root"], str(app.meetings.root))
            self.assertEqual(source.read_bytes(), b"original")
            app._restarting = True
            self.assertFalse(app._import_media_request({"paths": [str(source)]})["ok"])
            app._restarting = False
            self.assertFalse(app._import_media_request({"paths": [str(source / "missing")]})["ok"])
            self.assertEqual(len(app.meetings.store.list()), 1)

    def test_disk_failure_during_batch_import_preserves_only_complete_queued_rows(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as directory:
            app = self.app()
            app.meetings._lock = threading.RLock()
            app.meetings.root = Path(directory) / "library"
            app.meetings.store = RecordingStore(app.meetings.root)
            app._meeting_changed = Mock()
            sources = [Path(directory) / name for name in ("a.wav", "b.wav")]
            for source in sources:
                source.write_bytes(b"original")
            original = app.meetings.store.create
            calls = []
            def fail_second(*args, **kwargs):
                calls.append(args)
                if len(calls) == 2:
                    raise sqlite3.OperationalError("synthetic disk full")
                return original(*args, **kwargs)
            with patch.object(app.meetings.store, "create", side_effect=fail_second):
                response = app._import_media_request({"paths": [str(s) for s in sources]})
            self.assertFalse(response["ok"])
            self.assertEqual(len(response["ids"]), 1)
            records = app.meetings.store.list()
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["status"], "queued")
            self.assertEqual(records[0]["source_path"], str(sources[0].resolve()))

    def app(self):
        app = DictationApp.__new__(DictationApp)
        app._state_lock = threading.RLock()
        app._state = AppState.IDLE
        app._shutdown = threading.Event()
        app._restarting = False
        app._update_lock = threading.Lock()
        app._notify = Mock()
        app.icon = Mock()
        app.logger = Mock()
        app.meetings = Mock()
        app.meetings.busy = False
        app.project_root = Path("C:/PACE-test")
        return app

    def test_restart_guard_inside_recording_lock_prevents_racing_dictation_start(self):
        app = self.app()
        app._restarting = True
        app.recorder = Mock()
        app._start_recording()
        app.recorder.start.assert_not_called()

    def test_new_download_failure_never_leaves_restart_ready_for_wrong_version(self):
        app = self.app()
        app._update_ready = True
        app._update_available = updater.UpdateInfo("9.0.0", "v9.0.0", "old")
        latest = updater.UpdateInfo("9.1.0", "v9.1.0", "new")
        with patch("whisper_dictation.app.updater.check_for_update", return_value=latest), \
             patch("whisper_dictation.app.updater.prepare_update", side_effect=RuntimeError("offline")):
            app._check_updates()
        self.assertFalse(app._update_ready)
        self.assertEqual(app._update_available.version, "9.0.0")

    def test_active_meeting_or_processing_prevents_restart(self):
        app = self.app()
        app.meetings.busy = True
        with patch("whisper_dictation.app.updater.restart_to_update") as restart:
            app._run_update(updater.UpdateInfo("9.1.0", "v9.1.0", "new"))
        restart.assert_not_called()
        self.assertFalse(app._restarting)

    def test_helper_bootstrap_failure_never_shuts_down_the_application(self):
        app = self.app()
        app._ui_lock = threading.Lock()
        app._recordings_proc = None
        app._meeting_overlay_proc = None
        app._shutdown_app = Mock()
        update = updater.UpdateInfo("9.1.0", "v9.1.0", "new")
        with patch("whisper_dictation.app.updater.restart_to_update", side_effect=updater.UpdateError("helper not ready")) as restart:
            app._run_update(update)
        restart.assert_called_once_with(update, app.project_root)
        app._shutdown_app.assert_not_called()
        self.assertFalse(app._restarting)
        self.assertFalse(app._shutdown.is_set())
        self.assertIn("helper not ready", app._notify.call_args.args[1])

    def test_confirmation_only_runs_in_ready_tray_callback(self):
        app = self.app()
        with patch("whisper_dictation.app.updater.confirm_running_version") as confirm:
            app._tray_ready(app.icon)
        self.assertTrue(app.icon.visible)
        confirm.assert_called_once_with(app.project_root)


if __name__ == "__main__":
    unittest.main()
