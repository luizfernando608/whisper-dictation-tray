from __future__ import annotations

import tempfile
import json
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from whisper_dictation import meeting_processing as module
from whisper_dictation.settings import AppConfig


def make_audio(path: Path, seconds: float):
    pcm = np.zeros(int(seconds * 16000), dtype=np.int16)
    with wave.open(str(path), "wb") as audio:
        audio.setparams((1, 2, 16000, 0, "NONE", ""))
        audio.writeframes(pcm.tobytes())


class MeetingProcessingTests(unittest.TestCase):
    def test_local_asr_uses_shared_cache_without_community_model(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "asr-prepared.json").write_text(json.dumps({"asr_model": "small", "asr_path": "local-whisper"}), encoding="utf-8")
            model = Mock()
            model.transcribe.return_value = ([], SimpleNamespace(language="pt"))
            with patch("faster_whisper.WhisperModel", return_value=model) as load:
                result = module._transcribe_chunk(root / "audio.wav", 1, AppConfig(), "local", "small", {}, root)
            self.assertEqual(result["language"], "pt")
            load.assert_called_once()
            self.assertEqual(load.call_args.args[0], "local-whisper")
            self.assertTrue(load.call_args.kwargs["local_files_only"])
            with self.assertRaisesRegex(RuntimeError, "ainda não foi preparado"):
                module._transcribe_chunk(root / "audio.wav", 1, AppConfig(), "local", "medium", {}, root)

    def test_merge_preserves_gaps_and_ambiguous_overlapping_voices(self):
        transcript = {"segments": [{"start": 0, "end": 6, "text": "a b c d",
                                    "words": [{"start": x, "end": x + 0.5, "word": w}
                                              for x, w in [(0, "a"), (2, "b"), (4, "c"), (5, "d")]]}]}
        turns = [{"start": 0, "end": 1, "speaker": "A"}, {"start": 4, "end": 6, "speaker": "A"},
                 {"start": 4, "end": 4.5, "speaker": "B"}]
        segments = module.merge_transcript(transcript, turns)
        self.assertEqual([s["speaker"] for s in segments], ["A", None, None, "A"])
        self.assertEqual([s["start"] for s in segments], [0, 2, 4, 5])

    def test_text_only_provider_uses_explicit_timestamp_alternative(self):
        config = SimpleNamespace(transcription_provider="openai", openai_model="gpt-4o-transcribe",
                                 meeting_timestamp_provider="local", model_size="small")
        provider, model, warning = module._effective_provider(config)
        self.assertEqual((provider, model), ("local", "small"))
        self.assertIn("timestamps", warning)
        config.meeting_timestamp_provider = "whisper-1"
        self.assertEqual(module._effective_provider(config)[:2], ("openai", "whisper-1"))

    def test_long_single_speaker_transcript_keeps_every_word_and_punctuation(self):
        words = [{"start": index * 0.2, "end": index * 0.2 + 0.15, "word": "word"}
                 for index in range(10000)]
        words[-1]["word"] = "."
        transcript = {"segments": [{"start": 0, "end": 2000, "text": "", "words": words}]}
        segments = module.merge_transcript(transcript, [{"start": 0, "end": 2000, "speaker": "A"}])
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0]["text"].count("word"), 9999)
        self.assertTrue(segments[0]["text"].endswith("word."))
        self.assertNotIn("_parts", segments[0])

    def test_chunk_offsets_follow_sample_counts_and_cancellation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audio = root / "audio.wav"
            make_audio(audio, 2.5)
            cancel = module._FileCancellation(root / "cancel.request")
            chunks = []
            for index, offset, duration, path in module._chunks(audio, root, cancel, seconds=1):
                self.assertTrue(path.is_file())
                chunks.append((index, offset, duration))
            self.assertEqual(chunks, [(0, 0.0, 1.0), (1, 1.0, 1.0), (2, 2.0, 0.5)])
            cancel.path.touch()
            with self.assertRaises(module.ProcessingCancelled):
                list(module._chunks(audio, root, cancel, seconds=1))

    def test_resume_does_not_repeat_completed_cloud_calls_or_duplicate_times(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checkpoints = root / "checkpoints"
            audio = root / "audio.wav"
            make_audio(audio, 2)
            config = AppConfig(transcription_provider="groq")
            cancel = module._FileCancellation(root / "cancel.request")
            response = {"text": "hello", "language": "en", "segments": [
                {"start": 0.2, "end": 0.8, "text": "hello", "words": [{"start": 0.2, "end": 0.8, "word": "hello"}]}]}
            with patch.object(module, "_transcribe_chunk", return_value=response) as call:
                first = module.transcribe_recording(audio, checkpoints, config, cancel)
                second = module.transcribe_recording(audio, checkpoints, config, cancel)
                self.assertEqual(call.call_count, 1)
            self.assertEqual(first, second)
            self.assertEqual(second["segments"][0]["start"], 0.2)
            self.assertTrue((root / "transcript.partial.json").exists())

    def test_failed_diarization_keeps_transcript_and_never_marks_ready(self):
        from whisper_dictation.recording_store import RecordingStore
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.json"
            AppConfig().save(config_path)
            store = RecordingStore(root / "library")
            record = store.create("test", "meeting")
            folder = store.directory(record["id"])
            audio = folder / "audio.wav"
            make_audio(audio, 2)
            store.update(record["id"], audio_path=str(audio), duration=2)
            response = {"text": "hello", "segments": [{"start": 0.2, "end": 0.8, "text": "hello"}],
                        "provider": "groq", "model": "whisper-large-v3", "duration": 2, "language": "en"}
            with patch.object(module, "transcribe_recording", return_value=response), \
                    patch.object(module, "diarize", side_effect=RuntimeError("model missing")):
                self.assertIsNone(module.process_recording(store.root, record["id"], config_path))
            self.assertEqual(store.get(record["id"])["status"], "failed")
            self.assertTrue((folder / "transcript.json").exists())
            self.assertFalse((folder / "result.json").exists())

    def test_cancellation_never_marks_success(self):
        from whisper_dictation.recording_store import RecordingStore
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.json"
            AppConfig().save(config_path)
            store = RecordingStore(root / "library")
            record = store.create("test", "meeting")
            folder = store.directory(record["id"])
            (folder / "cancel.request").touch()
            with patch.object(module, "transcribe_recording") as transcribe:
                module.process_recording(store.root, record["id"], config_path)
            transcribe.assert_not_called()
            self.assertEqual(store.get(record["id"])["status"], "cancelled")


if __name__ == "__main__":
    unittest.main()
