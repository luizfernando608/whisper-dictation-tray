from __future__ import annotations

import json
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import numpy as np

from whisper_dictation import meeting_sources as module
from whisper_dictation.meeting_processing import _FileCancellation


def wav(path, samples, rate=16000):
    with wave.open(str(path), "wb") as handle:
        handle.setparams((1, 2, rate, 0, "NONE", ""))
        handle.writeframes(np.asarray(samples, dtype="<i2").tobytes())


class PassProcessor:
    def set_stream_format(self, *args):
        pass

    def set_reverse_stream_format(self, *args):
        pass

    def set_stream_delay(self, *args):
        pass

    def process_reverse_stream(self, data):
        pass

    def process_stream(self, data):
        return data


class MeetingSourcesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.cancel = _FileCancellation(self.root / "cancel.request")
        self.mic = np.arange(4001, dtype=np.int16) - 2000
        self.pc = np.zeros(4001, dtype=np.int16)
        for name, samples in (("audio", self.mic), ("microphone", self.mic), ("computer", self.pc)):
            wav(self.root / f"{name}.wav", samples)
        self.capture = {"status": "stopped", "duration": len(self.mic) / 16000,
                        "sources": {"microphone": {"rate": 48000}, "computer": {"rate": 48000}}}
        (self.root / "capture.json").write_text(json.dumps(self.capture))

    def prepare(self):
        return module.prepare_sources(self.root / "audio.wav", self.root, self.cancel)

    def test_import_without_capture_keeps_mixed_path(self):
        (self.root / "capture.json").unlink()
        self.assertIsNone(self.prepare())

    def test_non_object_capture_metadata_keeps_mixed_path(self):
        (self.root / "capture.json").write_text("[]")
        self.assertIsNone(self.prepare())

    def test_non_object_preparation_cache_is_rebuilt(self):
        with patch.object(module, "_processor", return_value=PassProcessor()) as processor:
            self.prepare()
            (self.root / "sources/prepared.json").write_text("[]")
            self.prepare()
            self.assertEqual(processor.call_count, 2)

    def test_mismatched_source_geometry_never_processes_as_aligned(self):
        wav(self.root / "computer.wav", np.zeros(2000, dtype=np.int16))
        self.assertIsNone(self.prepare())

    def test_invalid_capture_never_enables_source_processing(self):
        for duration in (float("nan"), -1, 42):
            with self.subTest(duration=duration):
                (self.root / "capture.json").write_text(json.dumps({**self.capture, "duration": duration}))
                self.assertIsNone(self.prepare())

    def test_silent_reference_keeps_microphone_exact_and_partial_last_frame(self):
        with patch.object(module, "_processor", return_value=PassProcessor()):
            prepared = self.prepare()
        with wave.open(str(prepared["sources"]["microphone"]), "rb") as handle:
            self.assertEqual(handle.getnframes(), len(self.mic))
            self.assertEqual(handle.readframes(len(self.mic)), self.mic.tobytes())
        self.assertEqual(Path(prepared["sources"]["computer"]), self.root / "computer.wav")

    def test_runtime_unavailable_has_explicit_mixed_fallback(self):
        with patch.object(module, "_processor", side_effect=ImportError("runtime absent")):
            prepared = self.prepare()
        self.assertFalse(prepared["sources"])
        self.assertTrue(prepared["warnings"])
        self.assertFalse((self.root / "sources/microphone/aec.wav").exists())

    def test_cancelled_preparation_leaves_no_completed_audio(self):
        self.cancel.path.touch()
        with patch.object(module, "_processor", return_value=PassProcessor()), self.assertRaises(module.ProcessingCancelled):
            self.prepare()
        self.assertFalse((self.root / "sources/microphone/aec.wav").exists())

    def test_valid_cache_reuses_audio_but_source_change_invalidates(self):
        with patch.object(module, "_processor", return_value=PassProcessor()) as processor:
            first = self.prepare()
            self.prepare()
            self.assertEqual(processor.call_count, 1)
            wav(self.root / "microphone.wav", -self.mic)
            self.prepare()
            self.assertEqual(processor.call_count, 2)
        self.assertTrue(first["sources"]["microphone"].is_file())

    def test_corrupt_cached_derivative_is_rebuilt(self):
        with patch.object(module, "_processor", return_value=PassProcessor()) as processor:
            prepared = self.prepare()
            prepared["sources"]["microphone"].write_bytes(b"corrupt")
            self.prepare()
            self.assertEqual(processor.call_count, 2)

    def test_native_processing_failure_keeps_mixed_fallback(self):
        processor = PassProcessor()
        processor.process_stream = lambda data: (_ for _ in ()).throw(RuntimeError("native error"))
        with patch.object(module, "_processor", return_value=processor):
            result = self.prepare()
        self.assertFalse(result["sources"])
        self.assertEqual(result["processing"]["reason"], "aec-failed")
        self.assertTrue(result["warnings"])
        self.assertFalse(list((self.root / "sources/microphone").glob("*.part")))

    def test_speech_packing_contains_only_real_selected_samples(self):
        chunks = [{"start": 200, "end": 900}, {"start": 2000, "end": 3500}]
        packed = self.root / "packed.wav"
        with patch.object(module, "_speech_chunks", return_value=chunks):
            result = module.pack_speech(self.root / "microphone.wav", packed, self.cancel)
        with wave.open(str(result["audio"]), "rb") as handle:
            expected = np.concatenate((self.mic[200:900], self.mic[2000:3500]))
            self.assertEqual(handle.readframes(handle.getnframes()), expected.tobytes())
        self.assertEqual(result["duration"], 2200 / 16000)
        self.assertEqual(result["chunks"], chunks)

    def test_silence_has_no_packed_audio(self):
        packed = self.root / "packed.wav"
        with patch.object(module, "_speech_chunks", return_value=[]):
            result = module.pack_speech(self.root / "microphone.wav", packed, self.cancel)
        self.assertEqual(result["duration"], 0)
        self.assertFalse(packed.exists())

    def test_restore_words_uses_removed_sample_offsets_without_changing_raw(self):
        chunks = [{"start": 16000, "end": 32000}, {"start": 80000, "end": 96000}]
        original = {"text": "a b", "segments": [{"start": .1, "end": 1.4, "text": "a b", "words": [
            {"start": .1, "end": .3, "word": "a"}, {"start": 1.2, "end": 1.4, "word": "b"}]}]}
        result = module.restore_speech(original, chunks)
        self.assertEqual([(w["start"], w["end"]) for w in result["segments"][0]["words"]],
                         [(1.1, 1.3), (5.2, 5.4)])
        self.assertEqual(original["segments"][0]["words"][0]["start"], .1)

    def test_restore_coarse_preserves_text_without_creating_word_alignment(self):
        chunks = [{"start": 16000, "end": 32000}, {"start": 80000, "end": 96000}]
        result = module.restore_speech({"text": "a b", "segments": [
            {"start": .1, "end": 1.4, "text": "a b", "words": []}]}, chunks)
        self.assertEqual(result["segments"][0]["start"], 1.1)
        self.assertEqual(result["segments"][0]["end"], 5.4)
        self.assertEqual(result["segments"][0]["words"], [])


if __name__ == "__main__":
    unittest.main()
