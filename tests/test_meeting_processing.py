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
    def test_changed_speaker_identity_archives_names_before_clearing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            previous = {"turns": [{"start": 0, "end": 1, "speaker": "SPEAKER_00"}]}
            (root / "result.json").write_text(json.dumps(previous))
            record = {"id": "test", "speaker_names": {"SPEAKER_00": "Previous participant"}}
            output = {"turns": [{"start": 0, "end": 1, "speaker": "SPEAKER_00", "source": "microphone"}],
                      "speaker_sources": {"SPEAKER_00": "microphone"}}
            store = Mock()
            module._write_result(root, record, output, store)
            store.update.assert_called_once_with("test", speaker_names={})
            archives = list(root.glob("speaker-names-*.json"))
            self.assertEqual(len(archives), 1)
            archive = json.loads(archives[0].read_text())
            self.assertEqual(archive["speaker_names"], record["speaker_names"])
            self.assertEqual(archive["result"], previous)
            self.assertEqual(json.loads((root / "result.json").read_text()), output)

    def test_unchanged_speaker_turns_preserve_names(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = {"turns": [{"start": 0, "end": 1, "speaker": "SPEAKER_00"}]}
            (root / "result.json").write_text(json.dumps(output))
            store = Mock()
            module._write_result(root, {"id": "test", "speaker_names": {"SPEAKER_00": "Name"}}, output, store)
            store.update.assert_not_called()
            self.assertEqual(list(root.glob("speaker-names-*.json")), [])

    def test_combined_source_transcript_orders_words_across_source_segments(self):
        shared = {"provider": "groq", "model": "whisper-large-v3", "language": "pt", "duration": 5}
        combined = module._combined_transcript({
            "microphone": {**shared, "text": "a c", "segments": [
                {"start": 0, "end": 5, "text": "a c", "words": [
                    {"start": 0, "end": 1, "word": "a"}, {"start": 4, "end": 5, "word": "c"}]}]},
            "computer": {**shared, "text": "b", "segments": [
                {"start": 2, "end": 3, "text": "b", "words": [{"start": 2, "end": 3, "word": "b"}]}]}})
        self.assertEqual(combined["text"], "a b c")
        self.assertEqual([s["source"] for s in combined["segments"]], ["microphone", "computer", "microphone"])

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
            assets = root / "local-whisper"
            assets.mkdir()
            for name in ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt"):
                (assets / name).write_bytes(b"fixture")
            (root / "asr-prepared.json").write_text(json.dumps({"asr_model": "small", "asr_path": str(assets)}), encoding="utf-8")
            model = Mock()
            model.transcribe.return_value = ([], SimpleNamespace(language="pt"))
            with patch("faster_whisper.WhisperModel", return_value=model) as load:
                result = module._transcribe_chunk(root / "audio.wav", 1, AppConfig(), "local", "small", {}, root)
            self.assertEqual(result["language"], "pt")
            load.assert_called_once()
            self.assertEqual(load.call_args.args[0], str(assets))
            self.assertTrue(load.call_args.kwargs["local_files_only"])
            with self.assertRaisesRegex(RuntimeError, "ainda não foi preparado"):
                module._transcribe_chunk(root / "audio.wav", 1, AppConfig(), "local", "medium", {}, root)

    def test_local_asr_prepares_missing_and_incomplete_cache_without_account(self):
        from whisper_dictation import nemotron_diarization
        for cache_state in ("missing", "incomplete", "invalid_path"):
            with self.subTest(cache_state=cache_state), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                assets = root / "whisper"
                assets.mkdir()
                if cache_state != "missing":
                    module.atomic_json(root / "asr-prepared.json", {"asr_model": "tiny",
                                       "asr_path": str(assets) if cache_state == "incomplete" else None})
                config = AppConfig(transcription_provider="local", model_size="tiny")
                model = Mock()
                model.transcribe.return_value = ([], SimpleNamespace(language="pt"))
                stages = []
                def setup(config, cache, progress=None, cancelled=None):
                    self.assertEqual(cache, root)
                    self.assertFalse(cancelled())
                    progress("Preparando Whisper tiny", .5)
                    for name in ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt"):
                        (assets / name).write_bytes(b"fixture")
                    module.atomic_json(root / "asr-prepared.json", {"asr_model": "tiny", "asr_path": str(assets)})
                with patch.object(nemotron_diarization, "prepare_asr_models", side_effect=setup, create=True) as prepare, \
                        patch("faster_whisper.WhisperModel", return_value=model) as load:
                    result = module._transcribe_chunk(root / "audio.wav", 1, config, "local", "tiny", {}, root,
                                                     preparation_progress=lambda message, fraction: stages.append(message),
                                                     cancelled=lambda: False)
                prepare.assert_called_once()
                self.assertEqual(load.call_args.args[0], str(assets))
                self.assertTrue(load.call_args.kwargs["local_files_only"])
                self.assertEqual(result["language"], "pt")
                self.assertIn("Preparando Whisper tiny", stages)

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


class SourceProcessingTests(unittest.TestCase):
    def setUp(self):
        from whisper_dictation.recording_store import RecordingStore
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.config_path = self.root / "config.json"
        AppConfig().save(self.config_path)
        self.store = RecordingStore(self.root / "library")
        self.record = self.store.create("source test", "meeting")
        self.folder = self.store.directory(self.record["id"])
        self.audio = self.folder / "audio.wav"
        make_audio(self.audio, 4)
        self.store.update(self.record["id"], audio_path=str(self.audio), duration=4)
        self.sources = {}
        for name in ("microphone", "computer"):
            path = self.folder / f"{name}.wav"
            make_audio(path, 4)
            self.sources[name] = path
        self.prepared = {"sources": self.sources, "processing": {"echo_reduction": "verified"}, "warnings": []}
        # These orchestration fixtures intentionally use silent WAVs. Keep their
        # source plumbing independent of VAD decisions, tested separately below.
        packer = patch.object(module, "pack_speech", create=True,
                              side_effect=lambda audio, destination, cancellation:
                              {"audio": audio, "duration": 4, "chunks": [{"start": 0, "end": 64000}]})
        packer.start()
        self.addCleanup(packer.stop)

    @staticmethod
    def transcript(name):
        words = [{"start": .2, "end": .7, "word": name + " first"},
                 {"start": 2.2, "end": 2.7, "word": name + " second"}]
        return {"text": " ".join(word["word"] for word in words), "segments": [
            {"start": word["start"], "end": word["end"], "text": word["word"], "words": [word]}
            for word in words], "provider": "groq", "model": "whisper-large-v3", "duration": 4, "language": "pt"}

    @staticmethod
    def diarization(name):
        return {"turns": [{"start": 0, "end": 1, "speaker": "SPEAKER_00"},
                          {"start": 2, "end": 3, "speaker": "SPEAKER_01"}],
                "exclusive_turns": [], "backend": name + "-cpu", "model": "nemotron",
                "license": "OpenMDW", "fallback_reasons": [], "benchmark_seconds": {"cpu": .1}}

    def run_processing(self):
        return module.process_recording(self.store.root, self.record["id"], self.config_path)

    def test_simultaneous_words_keep_source_and_four_distinct_identities(self):
        updates = []
        original_update = self.store.update
        def capture_update(store, recording_id, **fields):
            updates.append(fields)
            return original_update(recording_id, **fields)
        def transcribe(audio, directory, config, cancellation, progress=None, **kwargs):
            if progress:
                progress(2)
                progress(4)
            return self.transcript(audio.stem)
        def diarization(audio, config, directory, progress, cancelled, **kwargs):
            progress("Identificando", .5)
            progress("Identificando", 1)
            return self.diarization(audio.stem)
        with patch.object(module, "prepare_sources", return_value=self.prepared, create=True), \
                patch.object(module, "transcribe_recording", side_effect=transcribe), \
                patch.object(module, "diarize", side_effect=diarization), \
                patch("whisper_dictation.recording_store.RecordingStore.update", new=capture_update):
            output = self.run_processing()
        self.assertIsNotNone(output)
        self.assertEqual(len(output["segments"]), 4)
        self.assertEqual(len({segment["speaker"] for segment in output["segments"]}), 4)
        self.assertEqual([segment["source"] for segment in output["segments"]],
                         ["microphone", "computer", "microphone", "computer"])
        self.assertEqual([segment["speaker"] for segment in output["segments"]],
                         ["SPEAKER_00", "SPEAKER_01", "SPEAKER_02", "SPEAKER_03"])
        self.assertEqual({turn["source"] for turn in output["turns"]}, {"microphone", "computer"})
        self.assertEqual(output["speaker_sources"]["SPEAKER_01"], "computer")
        self.assertEqual(output["processing"]["echo_reduction"], "verified")
        self.assertEqual(set(output["source_backends"]), {"microphone", "computer"})
        self.assertEqual(output["source_backends"]["microphone"]["backend"], "microphone-cpu")
        self.assertEqual(self.store.get(self.record["id"])["status"], "ready")
        progress = [update["progress"] for update in updates if "progress" in update]
        self.assertEqual(progress, sorted(progress))

    def test_import_and_unverified_sources_keep_mixed_flow(self):
        for origin, prepared in (("import", self.prepared), ("meeting", None)):
            with self.subTest(origin=origin):
                self.store.update(self.record["id"], origin=origin)
                with patch.object(module, "prepare_sources", return_value=prepared, create=True) as prepare, \
                        patch.object(module, "transcribe_recording", return_value=self.transcript("mixed")) as transcribe, \
                        patch.object(module, "diarize", return_value=self.diarization("mixed")) as diarization:
                    output = self.run_processing()
                self.assertEqual(transcribe.call_args.args[0], self.audio)
                self.assertEqual(diarization.call_args.args[0], self.audio)
                self.assertNotIn("source", output["segments"][0])
                if origin == "import":
                    prepare.assert_not_called()

    def test_guided_counts_use_mixed_flow_with_visible_warning(self):
        for count_origin in ("record", "config"):
            with self.subTest(count_origin=count_origin):
                self.store.update(self.record["id"], num_speakers=3 if count_origin == "record" else None)
                AppConfig(diarization_min_speakers=2 if count_origin == "config" else 0).save(self.config_path)
                with patch.object(module, "prepare_sources", return_value=self.prepared, create=True) as prepare, \
                        patch.object(module, "transcribe_recording", return_value=self.transcript("mixed")) as transcribe, \
                        patch.object(module, "diarize", return_value=self.diarization("mixed")):
                    output = self.run_processing()
                prepare.assert_not_called()
                self.assertEqual(transcribe.call_args.args[0], self.audio)
                self.assertIn("falantes", output["transcription_warning"])
                self.assertIn("mistur", output["transcription_warning"])

    def test_unavailable_echo_runtime_keeps_mixed_flow_and_diagnostics(self):
        warning = "Cancelamento de eco indisponível; usando áudio misturado: ImportError."
        prepared = {"sources": {}, "processing": {"mode": "mixed", "reason": "aec-unavailable"},
                    "warnings": [warning]}
        with patch.object(module, "prepare_sources", return_value=prepared), \
                patch.object(module, "transcribe_recording", return_value=self.transcript("mixed")) as transcribe, \
                patch.object(module, "diarize", return_value=self.diarization("mixed")):
            output = self.run_processing()
        self.assertIsNotNone(output)
        self.assertEqual(transcribe.call_args.args[0], self.audio)
        self.assertEqual(output["processing"]["reason"], "aec-unavailable")
        self.assertEqual(output["warnings"], [warning])
        self.assertEqual(output["transcription_warning"], warning)
        self.assertEqual(self.store.get(self.record["id"])["transcription_warning"], warning)
        self.assertEqual(self.store.get(self.record["id"])["status"], "ready")

    def test_source_failure_keeps_parent_partial_and_never_ready(self):
        def transcribe(audio, directory, config, cancellation, progress=None, **kwargs):
            if audio.stem == "computer":
                raise RuntimeError("second source failed")
            return self.transcript(audio.stem)
        with patch.object(module, "prepare_sources", return_value=self.prepared, create=True), \
                patch.object(module, "transcribe_recording", side_effect=transcribe), \
                patch.object(module, "diarize", return_value=self.diarization("microphone")):
            self.assertIsNone(self.run_processing())
        self.assertEqual(self.store.get(self.record["id"])["status"], "failed")
        partial = json.loads((self.folder / "transcript.partial.json").read_text(encoding="utf-8"))
        self.assertIn("microphone", partial["text"])
        self.assertTrue(partial["partial"])
        self.assertFalse((self.folder / "result.json").exists())
        self.assertEqual(Path(self.store.get(self.record["id"])["partial_result_path"]), self.folder / "transcript.partial.json")

    def test_real_source_checkpoints_resume_without_repeating_asr_calls(self):
        response = {"text": "hello", "language": "pt", "segments": [
            {"start": .2, "end": .7, "text": "hello", "words": [{"start": .2, "end": .7, "word": "hello"}]}]}
        with patch.object(module, "prepare_sources", return_value=self.prepared, create=True), \
                patch.object(module, "_transcribe_chunk", return_value=response) as transcribe, \
                patch.object(module, "diarize", return_value=self.diarization("local")):
            first = self.run_processing()
            second = self.run_processing()
        self.assertEqual(transcribe.call_count, 2)
        self.assertEqual(first, second)
        for name in self.sources:
            self.assertTrue((self.folder / "sources" / name / "transcription-checkpoints" / "transcript-00000.raw.json").is_file())
        partial = json.loads((self.folder / "transcript.partial.json").read_text(encoding="utf-8"))
        self.assertEqual(len(partial["segments"]), 2)
        self.assertEqual({segment["source"] for segment in partial["segments"]}, set(self.sources))

    def test_cancelled_source_keeps_checkpoints_and_never_ready(self):
        response = {"text": "hello", "language": "pt", "segments": [
            {"start": .2, "end": .7, "text": "hello", "words": [{"start": .2, "end": .7, "word": "hello"}]}]}
        def cancelled_diarization(*args, **kwargs):
            (self.folder / "cancel.request").touch()
            return self.diarization("local")
        with patch.object(module, "prepare_sources", return_value=self.prepared, create=True), \
                patch.object(module, "_transcribe_chunk", return_value=response) as transcribe, \
                patch.object(module, "diarize", side_effect=cancelled_diarization):
            self.assertIsNone(self.run_processing())
        self.assertEqual(self.store.get(self.record["id"])["status"], "cancelled")
        self.assertFalse((self.folder / "result.json").exists())
        checkpoint = self.folder / "sources/microphone/transcription-checkpoints/transcript-00000.raw.json"
        self.assertTrue(checkpoint.is_file())
        (self.folder / "cancel.request").unlink()
        with patch.object(module, "prepare_sources", return_value=self.prepared, create=True), \
                patch.object(module, "_transcribe_chunk", return_value=response) as transcribe, \
                patch.object(module, "diarize", return_value=self.diarization("local")):
            self.assertIsNotNone(self.run_processing())
            self.assertEqual(transcribe.call_count, 1)


class SpeechOnlyTranscriptionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.audio = self.root / "audio.wav"
        make_audio(self.audio, 10)
        self.checkpoints = self.root / "checkpoints"
        self.config = AppConfig(transcription_provider="groq")
        self.cancellation = module._FileCancellation(self.root / "cancel.request")
        self.speech_map = [{"start": 16000, "end": 32000}, {"start": 112000, "end": 128000}]
        self.packed = {"audio": self.root / "packed.wav", "duration": 2, "chunks": self.speech_map}
        self.response = {"text": "first second", "segments": [], "words": [
            {"start": .2, "end": .6, "word": "first"}, {"start": 1.2, "end": 1.6, "word": "second"}], "language": "pt"}

    def transcribe(self, **kwargs):
        return module.transcribe_recording(self.audio, self.checkpoints, self.config, self.cancellation,
                                           speech_only=True, **kwargs)

    def test_packed_words_restore_original_times_and_duration(self):
        progress = []
        with patch.object(module, "pack_speech", return_value=self.packed, create=True), \
                patch.object(module, "_transcribe_chunk", return_value=self.response) as provider:
            result = self.transcribe(progress=progress.append)
        self.assertEqual(provider.call_args.args[:2], (self.packed["audio"], 2))
        words = result["segments"][0]["words"]
        self.assertEqual([word["start"] for word in words], [1.2, 7.2])
        self.assertEqual([word["end"] for word in words], [1.6, 7.6])
        self.assertEqual(result["duration"], 10)
        self.assertEqual(progress, [10])

    def test_packed_coarse_segments_restore_without_inventing_words(self):
        response = {"text": "first second", "segments": [
            {"start": .2, "end": .6, "text": "first"}, {"start": 1.2, "end": 1.6, "text": "second"}], "language": "pt"}
        with patch.object(module, "pack_speech", return_value=self.packed, create=True), \
                patch.object(module, "_transcribe_chunk", return_value=response):
            result = self.transcribe()
        self.assertEqual([segment["start"] for segment in result["segments"]], [1.2, 7.2])
        self.assertFalse(any(segment.get("words") for segment in result["segments"]))

    def test_empty_speech_never_calls_provider_and_replays_empty_checkpoint(self):
        packed = {"audio": self.root / "empty.wav", "duration": 0, "chunks": []}
        with patch.object(module, "pack_speech", return_value=packed, create=True) as pack, \
                patch.object(module, "_transcribe_chunk") as provider:
            first = self.transcribe()
            second = self.transcribe()
        provider.assert_not_called()
        pack.assert_called_once()
        self.assertEqual(first, second)
        self.assertEqual(first["text"], "")
        self.assertEqual(first["segments"], [])
        self.assertEqual(first["duration"], 10)

    def test_raw_checkpoint_keeps_provider_times_and_map_and_replays(self):
        with patch.object(module, "pack_speech", return_value=self.packed, create=True), \
                patch.object(module, "_transcribe_chunk", return_value=self.response):
            first = self.transcribe()
        raw = json.loads((self.checkpoints / "transcript-00000.raw.json").read_text(encoding="utf-8"))
        self.assertEqual(raw["transcript"], self.response)
        self.assertEqual(raw["speech_map"], self.speech_map)
        self.assertEqual(raw["packed_duration"], 2)
        (self.checkpoints / "transcript-00000.json").unlink()
        with patch.object(module, "pack_speech", create=True) as pack, patch.object(module, "_transcribe_chunk") as provider:
            second = self.transcribe()
        pack.assert_not_called()
        provider.assert_not_called()
        self.assertEqual(first, second)

    def test_packed_timestamps_are_validated_before_mapping(self):
        invalid = {**self.response, "words": [{"start": 2.5, "end": 3, "word": "invalid"}]}
        with patch.object(module, "pack_speech", return_value=self.packed, create=True), \
                patch.object(module, "_transcribe_chunk", return_value=invalid), \
                self.assertRaisesRegex(ValueError, "faixa"):
            self.transcribe()
        self.assertTrue((self.checkpoints / "transcript-00000.raw.json").is_file())
        self.assertFalse((self.checkpoints / "transcript-00000.json").exists())


if __name__ == "__main__":
    unittest.main()
