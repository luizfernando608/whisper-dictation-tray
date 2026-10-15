from __future__ import annotations

import tempfile
import json
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from whisper_dictation import meeting_processing as module
from whisper_dictation.settings import AppConfig


def make_audio(path: Path, seconds: float):
    pcm = np.zeros(int(seconds * 16000), dtype=np.int16)
    with wave.open(str(path), "wb") as audio:
        audio.setparams((1, 2, 16000, 0, "NONE", ""))
        audio.writeframes(pcm.tobytes())


class MeetingProcessingTests(unittest.TestCase):
    def cloud_response(self, response, duration=4):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audio = root / "audio.wav"
            make_audio(audio, duration)
            client = Mock()
            client.audio.transcriptions.create.return_value = response
            raw = module._transcribe_chunk(audio, duration, AppConfig(), "groq", "whisper-large-v3", {"groq": client}, root)
            result = module._normalise_transcript(raw, duration)
            module._validate_transcript(result, duration)
            return result

    def test_overlapping_provider_segments_never_duplicate_global_words(self):
        response = {"text": "a b c d", "language": "pt", "segments": [
            {"start": 0, "end": 2, "text": "a b c"}, {"start": 1, "end": 3, "text": "b c d"}],
            "words": [{"start": start, "end": start + .2, "word": word}
                      for start, word in ((.2, "a"), (1.2, "b"), (1.7, "c"), (2.2, "d"))]}
        result = self.cloud_response(response)
        merged = module.merge_transcript(result, [{"start": 0, "end": 4, "speaker": "A"}])
        self.assertEqual(merged[0]["text"], "a b c d")
        self.assertEqual(len(result["segments"][0]["words"]), 4)

    def test_unsorted_orphan_and_identical_words_are_preserved_stably(self):
        result = self.cloud_response({"text": "a a b c", "segments": [{"start": 0, "end": 1, "text": "a"}],
                                      "words": [{"start": 2, "end": 2.3, "word": "c"},
                                                {"start": 0, "end": .2, "word": "a"},
                                                {"start": 0, "end": .2, "word": "a"},
                                                {"start": 1, "end": 1.2, "word": "b"}]})
        self.assertEqual([w["word"] for w in result["segments"][0]["words"]], ["a", "a", "b", "c"])

    def test_normalisation_still_rejects_invalid_provider_intervals(self):
        for start, end in ((float("nan"), 1), (0, float("inf")), (2, 1), (-.1, 1), (0, 5), (None, 1)):
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                self.cloud_response({"text": "a", "words": [{"start": start, "end": end, "word": "a"}]})

    def test_segments_without_words_are_sorted_without_inventing_word_alignment(self):
        result = self.cloud_response({"text": "a b", "segments": [{"start": 2, "end": 3, "text": "b"},
                                                                 {"start": 0, "end": 1, "text": "a"}]})
        self.assertEqual([s["text"] for s in result["segments"]], ["a", "b"])
        self.assertFalse(any(s.get("words") for s in result["segments"]))

    def test_partial_global_words_preserve_uncovered_coarse_segments(self):
        result = self.cloud_response({"text": "a b", "segments": [{"start": 0, "end": 1, "text": "a"},
                                                                 {"start": 2, "end": 3, "text": "b"}],
                                      "words": [{"start": .2, "end": .4, "word": "a"}]})
        self.assertEqual(result["text"], "a b")
        merged = module.merge_transcript(result, [{"start": 0, "end": 1, "speaker": "A"}, {"start": 2, "end": 3, "speaker": "B"}])
        self.assertEqual([(s["text"], s["speaker"]) for s in merged], [("a", "A"), ("b", "B")])
        self.assertFalse(result["segments"][-1].get("words"))

    def test_nested_local_words_are_globally_ordered_for_diarization(self):
        raw = {"text": "a b c", "segments": [
            {"start": 0, "end": 3, "text": "a c", "words": [{"start": 0, "end": .5, "word": "a"}, {"start": 2, "end": 3, "word": "c"}]},
            {"start": 1, "end": 2, "text": "b", "words": [{"start": 1, "end": 2, "word": "b"}]}]}
        result = module._normalise_transcript(raw, 3)
        module._validate_transcript(result, 3)
        merged = module.merge_transcript(result, [{"start": 0, "end": 1, "speaker": "A"}, {"start": 1, "end": 3, "speaker": "B"}])
        self.assertEqual([(s["text"], s["speaker"]) for s in merged], [("a", "A"), ("b c", "B")])

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

    def test_raw_response_survives_normalisation_failure_and_is_replayed_without_api(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audio = root / "audio.wav"
            make_audio(audio, 2)
            checkpoints = root / "checkpoints"
            config = AppConfig(transcription_provider="groq")
            cancel = module._FileCancellation(root / "cancel.request")
            response = {"text": "a b", "segments": [], "words": [
                {"start": 1, "end": 1.5, "word": "b"}, {"start": 0, "end": .5, "word": "a"}], "language": "pt"}
            with patch.object(module, "_transcribe_chunk", return_value=response) as call, \
                    patch.object(module, "_normalise_transcript", side_effect=ValueError("old normalizer")):
                with self.assertRaises(ValueError):
                    module.transcribe_recording(audio, checkpoints, config, cancel)
                call.assert_called_once()
            self.assertTrue((checkpoints / "transcript-00000.raw.json").exists())
            with patch.object(module, "_transcribe_chunk") as call:
                result = module.transcribe_recording(audio, checkpoints, config, cancel)
                call.assert_not_called()
            self.assertEqual(result["text"], "a b")
            # Invalidating normalization alone never changes the ASR fingerprint;
            # changing the actual model does require a fresh provider response.
            config.groq_model = "whisper-large-v3-turbo"
            with patch.object(module, "_transcribe_chunk", return_value=response) as call:
                module.transcribe_recording(audio, checkpoints, config, cancel)
                call.assert_called_once()

    def test_invalid_raw_timestamps_are_preserved_but_retry_fetches_fresh_response(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audio = root / "audio.wav"
            make_audio(audio, 2)
            config = AppConfig(transcription_provider="groq")
            cancel = module._FileCancellation(root / "cancel.request")
            checkpoints = root / "checkpoints"
            invalid = {"text": "a", "segments": [], "words": [{"start": -1, "end": 1, "word": "a"}]}
            with patch.object(module, "_transcribe_chunk", return_value=invalid):
                with self.assertRaisesRegex(ValueError, "faixa"):
                    module.transcribe_recording(audio, checkpoints, config, cancel)
            valid = {**invalid, "words": [{"start": 0, "end": 1, "word": "a"}]}
            with patch.object(module, "_transcribe_chunk", return_value=valid) as call:
                result = module.transcribe_recording(audio, checkpoints, config, cancel)
                call.assert_called_once()
            self.assertEqual(result["text"], "a")

    def test_legacy_checkpoint_remains_reusable_without_new_raw_response(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audio = root / "audio.wav"
            make_audio(audio, 2)
            config = AppConfig(transcription_provider="groq")
            cancel = module._FileCancellation(root / "cancel.request")
            response = {"text": "a", "segments": [{"start": 0, "end": 1, "text": "a", "words": []}]}
            with patch.object(module, "_transcribe_chunk", return_value=response):
                module.transcribe_recording(audio, root / "checkpoints", config, cancel)
            (root / "checkpoints/transcript-00000.raw.json").unlink()
            with patch.object(module, "_transcribe_chunk") as call:
                result = module.transcribe_recording(audio, root / "checkpoints", config, cancel)
                call.assert_not_called()
            self.assertEqual(result["text"], "a")

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
