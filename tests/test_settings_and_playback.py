import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

from whisper_dictation.child_job import ChildJob
from whisper_dictation.media import PCM16WaveWriter
from whisper_dictation.playback import transcript_text, timestamp
from whisper_dictation.settings import AppConfig, validate_hotkeys
from whisper_dictation.wav_reader import PCM16Reader


class SettingsAndPlaybackTests(unittest.TestCase):
    def test_unlimited_default_preserves_explicit_old_limits_after_upgrade(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.json"
            self.assertEqual(AppConfig.load(path).max_record_seconds, 0)
            path.write_text(json.dumps({"max_record_seconds": 120}), encoding="utf-8")
            self.assertEqual(AppConfig.load(path).max_record_seconds, 120)
            path.write_text(json.dumps({"max_record_seconds": 0}), encoding="utf-8")
            saved = AppConfig.load(path)
            saved.save(path)
            self.assertEqual(AppConfig.load(path).max_record_seconds, 0)
            self.assertEqual(list(Path(temporary).glob("*.tmp")), [])

    def test_hotkeys_reject_equivalent_combinations_and_allow_disabled_pause(self):
        with self.assertRaises(ValueError):
            validate_hotkeys(AppConfig(meeting_hotkey="shift+ctrl+h"))
        validate_hotkeys(AppConfig(meeting_pause_hotkey=""))

    def test_speaker_range_is_validated(self):
        with self.assertRaises(ValueError):
            AppConfig._sanitize({"diarization_min_speakers": 5, "diarization_max_speakers": 2})

    def test_export_keeps_timestamps_and_user_assigned_names(self):
        result = {"segments": [{"start": 61.25, "end": 62.75, "speaker": "SPEAKER_00", "text": "Vamos começar."},
                                {"start": 63, "end": 64, "speaker": None, "text": "Trecho incerto."}]}
        text = transcript_text(result)
        self.assertIn("[00:01:01] Falante 1", text)
        self.assertIn("Falante não identificado", text)
        srt = transcript_text(result, {"SPEAKER_00": "Maria"}, srt=True)
        self.assertIn("00:01:01,250 --> 00:01:02,750", srt)
        self.assertIn("Maria: Vamos começar.", srt)
        self.assertEqual(timestamp(3599.9996, True), "01:00:00,000")

    def test_rf64_player_reader_seeks_exact_samples_with_bounded_reads(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "large.wav"
            samples = np.arange(16000, dtype=np.int16)
            with PCM16WaveWriter(path, rf64="always") as writer:
                writer.writeframesraw(samples.tobytes())
            self.assertEqual(path.read_bytes()[:4], b"RF64")
            reader = PCM16Reader(path)
            try:
                self.assertEqual(reader.getnframes(), 16000)
                for position in (0, 3333, 15900, 16000):
                    reader.setpos(position)
                    self.assertEqual(reader.readframes(100), samples[position:position + 100].tobytes())
            finally:
                reader.close()

    def test_closing_owned_job_terminates_only_its_child_process(self):
        job = ChildJob()
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], creationflags=0x08000000 if os.name == "nt" else 0)
        independent = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], creationflags=0x08000000 if os.name == "nt" else 0)
        try:
            job.add(child.pid)
            job.close()
            if os.name == "nt":
                child.wait(timeout=5)
                self.assertIsNotNone(child.returncode)
            self.assertIsNone(independent.poll())
        finally:
            for process in (child, independent):
                if process.poll() is None:
                    process.terminate()
                process.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
