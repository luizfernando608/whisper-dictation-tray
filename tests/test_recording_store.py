import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from whisper_dictation.recording_store import RecordingStore


class RecordingStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = RecordingStore(self.temporary.name)

    def test_changes_persist_and_partial_updates_preserve_other_fields(self):
        item = self.store.create("Meeting", "meeting")
        self.store.update(item["id"], title="Renamed", speaker_names={"S1": "Ana"})
        other = RecordingStore(self.temporary.name)
        other.update(item["id"], status="ready", duration=3600)
        saved = self.store.get(item["id"])
        self.assertEqual(saved["speaker_names"], {"S1": "Ana"})
        self.assertEqual(saved["title"], "Renamed")
        self.assertEqual(self.store.list()[0]["duration"], 3600)

    def test_active_recordings_cannot_be_deleted_and_invalid_paths_are_rejected(self):
        item = self.store.create("Meeting", "meeting")
        with self.assertRaises(RuntimeError):
            self.store.delete(item["id"])
        self.store.update(item["id"], status="recording")
        with self.assertRaises(RuntimeError):
            self.store.delete(item["id"])
        with self.assertRaises(ValueError):
            self.store.directory("../outside")
        with self.assertRaises(ValueError):
            self.store.update(item["id"], audio_path=str(Path(self.temporary.name).parent / "outside.wav"))
        self.store.update(item["id"], status="interrupted")
        self.store.delete(item["id"])
        self.assertEqual(self.store.list(), [])
        self.assertFalse(self.store.directory(item["id"]).exists())

    def test_two_processes_update_distinct_fields_without_lost_updates(self):
        item = self.store.create("Meeting", "meeting")
        code = "from whisper_dictation.recording_store import RecordingStore; import sys; s=RecordingStore(sys.argv[1]); field=sys.argv[3]; [s.update(sys.argv[2], **{field: str(i)}) for i in range(60)]"
        processes = [subprocess.Popen([sys.executable, "-c", code, self.temporary.name, item["id"], field]) for field in ("title", "backend")]
        for process in processes:
            self.assertEqual(process.wait(timeout=30), 0)
        saved = self.store.get(item["id"])
        self.assertEqual((saved["title"], saved["backend"]), ("59", "59"))

    def test_cancel_and_worker_claim_have_exactly_one_winner_across_processes(self):
        item = self.store.create("Meeting", "meeting")
        self.store.update(item["id"], status="queued")
        code = "from whisper_dictation.recording_store import RecordingStore; import sys; s=RecordingStore(sys.argv[1]); print(s.transition(sys.argv[2], 'queued', sys.argv[3]), flush=True)"
        processes = [subprocess.Popen([sys.executable, "-c", code, self.temporary.name, item["id"], status], stdout=subprocess.PIPE, text=True) for status in ("processing", "cancelled")]
        results = [process.communicate(timeout=30)[0].strip() for process in processes]
        self.assertEqual(sorted(results), ["False", "True"])
        self.assertIn(self.store.get(item["id"])["status"], {"processing", "cancelled"})

    def test_transition_preserves_fields_and_rejects_stale_retry(self):
        item = self.store.create("Meeting", "meeting")
        self.store.update(item["id"], status="failed", speaker_names={"S1": "Ana"})
        self.assertTrue(self.store.transition(item["id"], {"failed", "cancelled"}, "queued", error="", progress=0))
        self.assertFalse(self.store.transition(item["id"], "failed", "queued", title="Should not overwrite"))
        self.assertEqual(self.store.get(item["id"])["title"], "Meeting")
        self.assertEqual(self.store.get(item["id"])["speaker_names"], {"S1": "Ana"})

    def test_processing_metadata_and_cpu_fallback_survive_reopening(self):
        item = self.store.create("Meeting", "meeting")
        self.store.update(item["id"], status="failed", stage="Identificando falantes", backend="cuda",
                          backend_override="cpu", fallback_reason="Memória insuficiente", num_speakers=3,
                          min_speakers=2, max_speakers=4, partial_result_path="partial.json")
        other = RecordingStore(self.temporary.name)
        self.assertTrue(other.transition(item["id"], "failed", "queued", progress=0))
        saved = self.store.get(item["id"])
        self.assertEqual(saved["backend_override"], "cpu")
        self.assertEqual((saved["num_speakers"], saved["min_speakers"], saved["max_speakers"]), (3, 2, 4))
        self.assertEqual(Path(saved["partial_result_path"]).parent, self.store.directory(item["id"]))
        with self.assertRaises(ValueError):
            self.store.update(item["id"], partial_result_path="../escaped.json")
