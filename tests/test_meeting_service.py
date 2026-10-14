import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from whisper_dictation.meeting_service import MeetingService
from whisper_dictation.settings import AppConfig


class FakeRecorder:
    def __init__(self, directory, **kwargs):
        self.directory = directory
        self.active = False
        self.paused = False
        self.duration = 5.0
        self.error = None
        self.levels = {"microphone": 0.2, "computer": 0.4}
        self.stop_gate = None

    def start(self):
        self.active = True

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False

    def stop(self):
        if self.stop_gate:
            self.stop_gate.wait(5)
        self.active = False
        path = self.directory / "audio.wav"
        path.write_bytes(b"preserved audio")
        return path


class MeetingServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = AppConfig(meeting_storage_dir=str(self.root / "library"))
        self.config_path = self.root / "config.json"
        self.config.save(self.config_path)
        self.service = MeetingService(self.config, self.root, self.config_path)
        self.capture = patch("whisper_dictation.meeting_service.MeetingRecorder", FakeRecorder)
        self.capture.start()

    def tearDown(self):
        self.service.shutdown()
        self.capture.stop()
        self.temp.cleanup()

    def test_record_pause_resume_and_finish_does_not_touch_clipboard(self):
        self.service.toggle()
        identifier = self.service.recording_id
        self.assertEqual(self.service.store.get(identifier)["status"], "recording")
        self.service.pause()
        self.assertEqual(self.service.store.get(identifier)["status"], "paused")
        self.service.pause()
        self.assertFalse(self.service.recorder.paused)
        self.assertEqual(json.loads((self.service.root / "runtime.json").read_text())["levels"], [0.2, 0.4])
        self.service.finish(wait=True)
        record = self.service.store.get(identifier)
        self.assertEqual(record["status"], "queued")
        self.assertTrue(Path(record["audio_path"]).exists())
        self.assertFalse(self.service.active)

    def test_finalizing_is_async_and_rejects_another_capture(self):
        self.service.toggle()
        identifier = self.service.recording_id
        gate = threading.Event()
        self.service.recorder.stop_gate = gate
        try:
            self.service.finish()
            self.assertTrue(self.service.busy)
            self.assertEqual(self.service.store.get(identifier)["status"], "finalizing")
            with self.assertRaises(RuntimeError):
                self.service.toggle()
        finally:
            gate.set()
            self.service.finish(wait=True)

    def test_interruption_keeps_audio_and_reason_without_silent_automatic_processing(self):
        self.service.toggle()
        identifier = self.service.recording_id
        self.service.recorder.error = "Microfone desconectado"
        self.service.finish(wait=True)
        row = self.service.store.get(identifier)
        self.assertEqual(row["status"], "interrupted")
        self.assertIn("desconectado", row["error"])
        self.assertTrue(Path(row["audio_path"]).exists())

    def test_cancelled_queued_job_is_not_launched_or_uncancelled(self):
        row = self.service.store.create("Cancelada", "import")
        self.service.store.update(row["id"], status="queued", source_path="missing.mp4")
        (self.service.store.directory(row["id"]) / "cancel.request").touch()
        with patch("whisper_dictation.meeting_service.subprocess.Popen") as launch:
            self.service._advance_queue()
            launch.assert_not_called()
        self.assertEqual(self.service.store.get(row["id"])["status"], "cancelled")

    def test_shutdown_recovery_honors_cancel_marker(self):
        row = self.service.store.create("Interrompida", "import")
        self.service.store.update(row["id"], status="diarizing")
        (self.service.store.directory(row["id"]) / "cancel.request").touch()
        self.service._recover()
        self.assertEqual(self.service.store.get(row["id"])["status"], "cancelled")

    def test_stalled_worker_retries_cpu_once_then_preserves_failure(self):
        row = self.service.store.create("Travada", "import")
        self.service.store.update(row["id"], status="diarizing", progress=0.65)
        self.service._worker = Mock()
        self.service._worker.poll.return_value = None
        self.service._worker_id = row["id"]
        self.service._worker_job = Mock()
        self.service._worker_progress = ("diarizing", 0.65, None)
        self.service._worker_last_progress = 1
        with patch("whisper_dictation.meeting_service.time.monotonic", return_value=1500):
            self.service._advance_queue()
        saved = self.service.store.get(row["id"])
        self.assertEqual(saved["backend_override"], "cpu")
        self.assertEqual(saved["status"], "queued")
        self.service.store.update(row["id"], status="diarizing", progress=0.65)
        self.service._worker = Mock()
        self.service._worker.poll.return_value = None
        self.service._worker_id = row["id"]
        self.service._worker_job = Mock()
        self.service._worker_last_progress = 1
        with patch("whisper_dictation.meeting_service.time.monotonic", return_value=1500):
            self.service._advance_queue()
        self.assertEqual(self.service.store.get(row["id"])["status"], "failed")


if __name__ == "__main__":
    unittest.main()
