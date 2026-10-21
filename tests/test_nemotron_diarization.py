from __future__ import annotations

import hashlib
import io
import json
import sys
import tempfile
import unittest
import wave
import zipfile
import urllib.error
import threading
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from whisper_dictation import nemotron_diarization as module


@contextmanager
def official_fixture():
    """Small pinned artifacts exercise the real download/extract/seal path."""
    payloads, runtimes = {}, {}
    for backend in ("cpu", "vulkan"):
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("bin/nemo-speech.exe", ("official " + backend).encode())
            archive.writestr("bin/nemo_speech_asr_c.dll", b"official DLL")
        content = output.getvalue()
        filename = backend + ".zip"
        payloads[filename] = content
        runtimes[backend] = (filename, len(content), hashlib.sha256(content).hexdigest())
    weights = b"official trained model"
    payloads[module.MODEL_FILE] = weights
    class Response(io.BytesIO):
        url = "https://official.test/artifact"
    def response(request, **kwargs):
        filename = request.full_url.rsplit("/", 1)[-1]
        return Response(payloads.get(filename, b"official legal document"))
    with patch.object(module, "RUNTIMES", runtimes), \
            patch.object(module, "MODEL_SIZE", len(weights)), \
            patch.object(module, "MODEL_SHA", hashlib.sha256(weights).hexdigest()):
        yield response


class NativeDiarizationTests(unittest.TestCase):
    def test_fresh_cpu_preparation_is_verified_and_reused_without_network(self):
        with tempfile.TemporaryDirectory() as temporary, official_fixture() as response:
            root = Path(temporary)
            config = SimpleNamespace(diarization_backend="cpu", transcription_provider="groq")
            with patch("urllib.request.urlopen", side_effect=response) as requests:
                manifest = module.prepare_models(config, root)
            self.assertEqual(manifest["available_runtimes"], ["cpu"])
            self.assertFalse(any(request.args[0].full_url.endswith("vulkan.zip") for request in requests.call_args_list))
            with patch("urllib.request.urlopen") as requests:
                reused = module.prepare_models(config, root)
            requests.assert_not_called()
            self.assertEqual(reused["available_runtimes"], ["cpu"])

    def test_optional_vulkan_failure_does_not_invalidate_cpu_or_allow_unsealed_gpu(self):
        with tempfile.TemporaryDirectory() as temporary, official_fixture() as response:
            root = Path(temporary)
            config = SimpleNamespace(diarization_backend="auto", transcription_provider="groq")
            def partial_response(request, **kwargs):
                if request.full_url.endswith("vulkan.zip"):
                    raise urllib.error.HTTPError(request.full_url, 404, "unavailable", {}, None)
                return response(request, **kwargs)
            with patch("urllib.request.urlopen", side_effect=partial_response):
                manifest = module.prepare_models(config, root)
            self.assertEqual(manifest["available_runtimes"], ["cpu"])
            self.assertTrue(any("Vulkan opcional indisponível" in reason for reason in manifest["preparation_warnings"]))
            # A leftover executable is never admitted without a verified ZIP.
            (root / "nemotron-3/vulkan/bin").mkdir(parents=True)
            (root / "nemotron-3/vulkan/bin/nemo-speech.exe").write_bytes(b"untrusted")
            self.assertEqual(module._prepared(root / "nemotron-3")["available_runtimes"], ["cpu"])

    def test_legacy_cache_can_repair_optional_gpu_from_verified_zip_offline(self):
        with tempfile.TemporaryDirectory() as temporary, official_fixture() as response:
            root = Path(temporary)
            config = SimpleNamespace(diarization_backend="auto", transcription_provider="groq")
            with patch("urllib.request.urlopen", side_effect=response):
                module.prepare_models(config, root)
            native = root / "nemotron-3"
            manifest = json.loads((native / "prepared.json").read_text())
            manifest.pop("runtimes")
            module.atomic_json(native / "prepared.json", manifest)
            self.assertEqual(module._prepared(native)["available_runtimes"], ["cpu", "vulkan"])
            (native / "vulkan/bin/nemo_speech_asr_c.dll").write_bytes(b"damaged GPU")
            self.assertEqual(module._prepared(native)["available_runtimes"], ["cpu"])
            with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("offline")) as request:
                repaired = module.prepare_models(config, root)
            request.assert_not_called()
            self.assertEqual(repaired["available_runtimes"], ["cpu", "vulkan"])

    def test_manual_prepare_can_add_vulkan_to_existing_cpu_cache(self):
        with tempfile.TemporaryDirectory() as temporary, official_fixture() as response:
            root = Path(temporary)
            config = SimpleNamespace(diarization_backend="cpu", transcription_provider="groq")
            with patch("urllib.request.urlopen", side_effect=response):
                module.prepare_models(config, root)
            config.diarization_backend = "vulkan"
            with patch("urllib.request.urlopen", side_effect=response) as requests:
                upgraded = module.prepare_models(config, root)
            self.assertEqual(upgraded["available_runtimes"], ["cpu", "vulkan"])
            self.assertEqual([request.args[0].full_url.rsplit("/", 1)[-1] for request in requests.call_args_list], ["vulkan.zip"])

    def test_partial_asr_cache_is_repaired_without_huggingface_credentials(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            asr = root / "asr"
            asr.mkdir()
            config = SimpleNamespace(transcription_provider="local", model_size="tiny")
            module.atomic_json(root / "asr-prepared.json", {"asr_model": "tiny", "asr_path": str(asr)})
            def download(*args, **kwargs):
                for name in ("model.bin", "config.json", "tokenizer.json", "vocabulary.json"):
                    (asr / name).write_bytes(b"required artifact")
                return str(asr)
            with patch("faster_whisper.utils.download_model", side_effect=download) as request:
                module.prepare_asr_models(config, root)
            self.assertIs(request.call_args.kwargs["use_auth_token"], False)
            with patch("faster_whisper.utils.download_model") as request:
                module.prepare_asr_models(config, root)
            request.assert_not_called()

    def test_malformed_asr_marker_paths_are_repaired(self):
        for invalid_path in (None, 123, [], {}, "", "   "):
            with self.subTest(asr_path=invalid_path), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                asr = root / "asr"
                asr.mkdir()
                for name in ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt"):
                    (asr / name).write_bytes(b"required artifact")
                config = SimpleNamespace(transcription_provider="local", model_size="tiny")
                module.atomic_json(root / "asr-prepared.json", {"asr_model": "tiny", "asr_path": invalid_path})
                with patch("faster_whisper.utils.download_model", return_value=str(asr)) as request:
                    module.prepare_asr_models(config, root)
                request.assert_called_once()
                self.assertEqual(json.loads((root / "asr-prepared.json").read_text())["asr_path"], str(asr))

    def test_cancel_during_cache_validation_is_not_reclassified_as_missing_cache(self):
        with patch.object(module, "_prepared", side_effect=module.ProcessingCancelled()), \
                patch.object(module, "prepare_models") as prepare:
            with self.assertRaises(module.ProcessingCancelled):
                module.diarize(Path("audio.wav"), SimpleNamespace(), Path("output"))
        prepare.assert_not_called()

    def _mocked_diarize(self, backend, infer):
        doctor = {"driver_runtime_compatible": True, "devices": [
            {"index": 0, "type": "gpu", "memory_free": 1024**3}]}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "nemotron-3").mkdir()
            source = root / "audio.wav"
            source.write_bytes(b"audio fingerprint")
            directory = root / "recording"
            directory.mkdir()
            with patch.object(module, "model_root", return_value=root), \
                    patch.object(module, "_prepared", return_value={"runtime_version": "0.2.0", "available_runtimes": ["cpu", "vulkan"]}), \
                    patch.object(module, "_hardware_signature", return_value="test hardware"), \
                    patch("whisper_dictation.media.decode_media", return_value=1), \
                    patch.object(module, "_probes", return_value=[(source, 1)]), \
                    patch.object(module, "_executable", return_value=Path("runtime.exe")), \
                    patch.object(module, "run_cli", return_value=(json.dumps(doctor), "")), \
                    patch.object(module, "_infer", side_effect=infer):
                return module.diarize(source, SimpleNamespace(diarization_backend=backend), directory)

    def test_empty_full_gpu_result_retries_cpu_when_probe_detected_speech(self):
        calls = []
        reference = [{"start": 0, "end": 1, "speaker": "SPEAKER_00"}]
        def infer(root, wav, duration, directory, device, *args):
            calls.append(device)
            turns = [] if directory.name == "nemotron-inference" else reference
            return turns, 10 if device == "cpu" else 1, "diagnostic"
        result = self._mocked_diarize("auto", infer)
        self.assertEqual(calls, ["cpu", "vulkan:0", "vulkan:0", "cpu"])
        self.assertEqual(result["backend"], "nemotron-cpu")
        self.assertEqual(result["turns"], reference)

    def test_empty_full_cpu_result_is_error_when_probe_detected_speech(self):
        def infer(root, wav, duration, directory, device, *args):
            turns = [] if directory.name == "nemotron-inference" else [{"start": 0, "end": 1, "speaker": "SPEAKER_00"}]
            return turns, 1, "diagnostic"
        with self.assertRaisesRegex(RuntimeError, "não retornou falantes.*detectar vozes"):
            self._mocked_diarize("cpu", infer)

    def test_genuine_silence_with_no_probe_or_full_speech_remains_valid(self):
        result = self._mocked_diarize("cpu", lambda *args: ([], 1, "diagnostic"))
        self.assertEqual(result["turns"], [])
        self.assertEqual(result["speakers_detected"], 0)

    def test_offline_required_download_fails_without_manifest_or_partials(self):
        with tempfile.TemporaryDirectory() as temporary, \
                patch("urllib.request.urlopen", side_effect=urllib.error.URLError("offline")) as requests, \
                patch.object(module, "_pause"):
            root = Path(temporary)
            config = SimpleNamespace(diarization_backend="cpu", transcription_provider="groq")
            with self.assertRaisesRegex(RuntimeError, "Verifique a conexão.*Preparar modelos"):
                module.prepare_models(config, root)
            self.assertEqual(requests.call_count, 3)
            self.assertFalse((root / "nemotron-3/prepared.json").exists())
            self.assertEqual(list(root.rglob("*.pending")), [])

    def test_cancel_during_download_removes_unique_partial_and_never_promotes(self):
        cancelled = threading.Event()
        class Response(io.BytesIO):
            url = "https://official.test/model"
            def read(self, count):
                cancelled.set()
                return super().read(count)
        with tempfile.TemporaryDirectory() as temporary, \
                patch("urllib.request.urlopen", return_value=Response(b"weights")):
            target = Path(temporary) / "weights.gguf"
            with self.assertRaises(module.ProcessingCancelled):
                module._download("https://official.test/model", target, 7, "0"*64, cancelled=cancelled.is_set)
            self.assertFalse(target.exists())
            self.assertEqual(list(Path(temporary).glob("*.pending")), [])

    def test_cache_lock_wait_is_cancellable_and_lock_releases(self):
        cancelled, waiting = threading.Event(), threading.Event()
        outcomes = []
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            def second_preparer():
                try:
                    with module._cache_lock(root, cancelled.is_set, lambda *args: waiting.set()):
                        outcomes.append("unexpected acquisition")
                except module.ProcessingCancelled:
                    outcomes.append("cancelled")
            with module._cache_lock(root):
                thread = threading.Thread(target=second_preparer)
                thread.start()
                self.assertTrue(waiting.wait(2))
                cancelled.set()
                thread.join(2)
                self.assertFalse(thread.is_alive())
            with module._cache_lock(root):
                pass
        self.assertEqual(outcomes, ["cancelled"])

    def test_missing_cache_is_prepared_automatically_before_inference(self):
        config = SimpleNamespace(diarization_backend="cpu")
        report, cancel = lambda message, fraction: None, lambda: False
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(module, "model_root", return_value=Path(temporary)), \
                patch.object(module, "_prepared", side_effect=RuntimeError("missing cache")), \
                patch.object(module, "prepare_models", return_value={"runtime_version": "0.2.0"}) as prepare, \
                patch.object(module, "_hardware_signature", side_effect=RuntimeError("models ready")):
            with self.assertRaisesRegex(RuntimeError, "models ready"):
                module.diarize(Path("audio.wav"), config, Path(temporary), report, cancel)
            self.assertEqual(prepare.call_args.args[:2], (config, Path(temporary)))
            self.assertIs(prepare.call_args.kwargs["cancelled"], cancel)

    def test_cancelled_job_does_not_prepare_or_download(self):
        with patch.object(module, "prepare_models") as prepare, patch.object(module, "_prepared") as prepared:
            with self.assertRaises(module.ProcessingCancelled):
                module.diarize(Path("audio.wav"), SimpleNamespace(), Path("output"), cancelled=lambda: True)
        prepare.assert_not_called()
        prepared.assert_not_called()

    def test_transient_download_errors_retry_three_times(self):
        class Response(io.BytesIO):
            url = "https://example.test/model"
        payload = b"verified weights"
        with tempfile.TemporaryDirectory() as temporary, \
                patch("urllib.request.urlopen", side_effect=[urllib.error.URLError("offline"),
                      TimeoutError("network timeout"), Response(payload)]) as request, \
                patch.object(module.time, "sleep"):
            target = Path(temporary) / "weights.gguf"
            module._download("https://example.test/model", target, len(payload), hashlib.sha256(payload).hexdigest())
            self.assertEqual(target.read_bytes(), payload)
            self.assertEqual(request.call_count, 3)
            self.assertEqual(list(Path(temporary).glob("*.pending")), [])

    def test_invalid_manifest_shape_can_be_repaired_by_explicit_preparation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for manifest in ([], None, {}, {"files": []}, {"files": {"model": None}}):
                module.atomic_json(root / "prepared.json", manifest)
                with self.assertRaisesRegex(RuntimeError, "Cache Nemotron inválido"):
                    module._prepared(root)
            config = type("Config", (), {"transcription_provider": "groq"})()
            native = root / "nemotron-3"
            native.mkdir()
            module.atomic_json(native / "prepared.json", [])
            # Reaching the official download proves invalid cache isn't treated
            # as a fatal AttributeError/KeyError before preparation can repair it.
            with patch.object(module, "_download", side_effect=RuntimeError("repair download reached")) as download:
                with self.assertRaisesRegex(RuntimeError, "repair download reached"):
                    module.prepare_models(config, root)
                download.assert_called_once()

    def test_failed_gpu_does_not_skip_later_working_gpu(self):
        calls = []
        reference = [{"start": 0, "end": 1, "speaker": "SPEAKER_00"}]
        def infer(root, wav, duration, directory, device, *args):
            calls.append(device)
            if device == "vulkan:0":
                raise RuntimeError("broken driver")
            return reference, 10 if device == "cpu" else 1, "validated"
        doctor = {"driver_runtime_compatible": True, "devices": [
            {"index": index, "type": "gpu", "memory_free": 1024**3} for index in (0, 1)]}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "nemotron-3").mkdir()
            source = root / "audio.wav"
            source.write_bytes(b"test audio fingerprint")
            output = root / "recording"
            output.mkdir()
            config = SimpleNamespace(diarization_backend="auto")
            with patch.object(module, "model_root", return_value=root), \
                    patch.object(module, "_prepared", return_value={"runtime_version": "0.2.0", "available_runtimes": ["cpu", "vulkan"]}), \
                    patch.object(module, "_hardware_signature", return_value="test hardware"), \
                    patch("whisper_dictation.media.decode_media", return_value=1), \
                    patch.object(module, "_probes", return_value=[(source, 1)]), \
                    patch.object(module, "_executable", return_value=Path("runtime.exe")), \
                    patch.object(module, "run_cli", return_value=(json.dumps(doctor), "")), \
                    patch.object(module, "_infer", side_effect=infer):
                result = module.diarize(source, config, output)
        self.assertEqual(calls, ["cpu", "vulkan:0", "vulkan:1", "vulkan:1"])
        self.assertEqual(result["backend"], "nemotron-vulkan:1")
        self.assertTrue(any("broken driver" in reason for reason in result["fallback_reasons"]))

    def test_official_cpu_diagnostics_uses_negative_device_sentinel(self):
        def fake_worker(command, directory, **kwargs):
            result_path = Path(command[command.index("--native-result") + 1])
            module.atomic_json(result_path, {"segments": [], "diagnostics": {"mode": "streaming", "device_index": -1, "capacity": 8}})
            return "", ""
        with tempfile.TemporaryDirectory() as directory, patch.object(module, "run_cli", side_effect=fake_worker):
            turns, elapsed, log = module._infer(Path("cache"), Path("audio.wav"), 1, Path(directory),
                                                "cpu", lambda: False, None, 30)
        self.assertEqual(turns, [])

    def test_parses_overlapping_turns_and_rejects_invalid_clamped_intervals(self):
        turns = module.parse_result(json.dumps({"segments": [
            {"start": 0, "end": 1, "speaker": 1}, {"start": 0.5, "end": 1.5, "speaker": 2}]}), 2)
        self.assertEqual(len(turns), 2)
        self.assertEqual(module.exclusive_turns(turns), [
            {"start": 0.0, "end": 0.5, "speaker": "SPEAKER_00"},
            {"start": 1.0, "end": 1.5, "speaker": "SPEAKER_01"}])
        for value in ({"start": 1.02, "end": 1.04, "speaker": 1},
                      {"start": 0, "end": 0.5, "speaker": 9},
                      {"start": 0, "end": 0.5, "speaker": True}):
            with self.assertRaises(ValueError):
                module.parse_result(json.dumps({"segments": [value]}), 1)

    def test_equivalence_gate_permits_label_permutations_but_rejects_wrong_identity(self):
        reference = [{"start": 0, "end": 1, "speaker": "A"}, {"start": 1, "end": 2, "speaker": "B"}]
        permuted = [{"start": 0, "end": 1, "speaker": "B"}, {"start": 1, "end": 2, "speaker": "A"}]
        wrong = [{"start": 0, "end": 2, "speaker": "A"}]
        self.assertEqual(module.disagreement(reference, permuted, 2), 0.0)
        self.assertEqual(module.disagreement(reference, wrong, 2), 0.5)

    def test_zip_cannot_escape_runtime_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "invalid.zip"
            with zipfile.ZipFile(archive, "w") as stream:
                stream.writestr("../escape.exe", b"invalid")
            with self.assertRaises(ValueError):
                module._extract(archive, root / "runtime")
            self.assertFalse((root / "escape.exe").exists())

    def test_manifest_tampering_cannot_authorize_bad_model_or_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = b"known model"
            model = root / module.MODEL_FILE
            model.write_bytes(original)
            archive = root / "cpu.zip"
            with zipfile.ZipFile(archive, "w") as stream:
                stream.writestr("bin/nemo-speech.exe", b"known runtime")
            module._extract(archive, root / "cpu")
            digest = hashlib.sha256(original).hexdigest()
            module.atomic_json(root / "prepared.json", {"model": module.MODEL, "model_sha256": digest,
                               "runtime_version": "0.2.0", "files": {}})
            with patch.object(module, "MODEL_SHA", digest), patch.object(module, "MODEL_SIZE", len(original)), \
                    patch.object(module, "RUNTIMES", {"cpu": ("cpu.zip", archive.stat().st_size, module._hash(archive))}):
                module._prepared(root)
                model.write_bytes(b"bad model")
                with self.assertRaisesRegex(RuntimeError, "checksum oficial"):
                    module._prepared(root)
                model.write_bytes(original)
                (root / "cpu/bin/nemo-speech.exe").write_bytes(b"bad runtime")
                with self.assertRaisesRegex(RuntimeError, "Runtime cpu foi alterado"):
                    module._prepared(root)

    def test_download_rejects_wrong_sha256_and_removes_partial_file(self):
        class Response(io.BytesIO):
            url = "https://example.test/model"
        with tempfile.TemporaryDirectory() as directory, \
                patch("urllib.request.urlopen", return_value=Response(b"bad")):
            target = Path(directory) / "model.bin"
            with self.assertRaisesRegex(RuntimeError, "Checksum"):
                module._download("https://example.test/model", target, 3, "0" * 64)
            self.assertFalse(target.exists())
            self.assertFalse(target.with_suffix(".bin.pending").exists())

    def test_timeout_and_cancellation_terminate_native_process(self):
        with tempfile.TemporaryDirectory() as directory:
            command = [sys.executable, "-c", "import time; time.sleep(30)"]
            with self.assertRaises(TimeoutError):
                module.run_cli(command, Path(directory), timeout=0.05)
            with self.assertRaises(module.ProcessingCancelled):
                module.run_cli(command, Path(directory), cancelled=lambda: True, timeout=30)

    def test_long_audio_uses_multiple_probe_positions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "audio.wav"
            with wave.open(str(source), "wb") as output:
                output.setparams((1, 2, 16000, 0, "NONE", ""))
                for second in range(130):
                    output.writeframesraw(bytes(32000))
            probes = module._probes(source, root, 130)
            self.assertEqual(len(probes), 3)
            self.assertTrue(all(length == 60 for path, length in probes))


if __name__ == "__main__":
    unittest.main()
