from __future__ import annotations

import hashlib
import io
import json
import sys
import tempfile
import unittest
import wave
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from whisper_dictation import nemotron_diarization as module


class NativeDiarizationTests(unittest.TestCase):
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
                    patch.object(module, "_prepared", return_value={"runtime_version": "0.2.0"}), \
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
