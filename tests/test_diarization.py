from __future__ import annotations

import json
import tempfile
import unittest
import wave
import io
import queue
import struct
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from whisper_dictation import diarization as module


class DiarizationTests(unittest.TestCase):
    def test_default_engine_dispatches_without_reading_huggingface_token(self):
        from whisper_dictation.settings import AppConfig
        config = AppConfig()
        with patch("whisper_dictation.nemotron_diarization.prepare_models", return_value={"model": "nemotron"}) as prepare, \
             patch("whisper_dictation.nemotron_diarization.diarize", return_value={"turns": []}) as run, \
             patch("whisper_dictation.secrets_store.get_api_key") as credentials:
            self.assertEqual(module.prepare_models(config, Path("cache"))["model"], "nemotron")
            self.assertEqual(module.diarize(Path("audio.wav"), config, Path("record"))["turns"], [])
        prepare.assert_called_once()
        run.assert_called_once()
        credentials.assert_not_called()

    def test_rejects_backend_that_changes_speech_decisions(self):
        reference = np.array([[[0.2, 0.8], [0.8, 0.2]]], dtype=np.float32)
        with self.assertRaises(RuntimeError):
            module._validate_scores(reference, reference[..., ::-1])
        with self.assertRaises(RuntimeError):
            module._validate_scores(reference, np.full_like(reference, np.nan))
        module._validate_scores(reference, reference + 1e-5)

    def test_missing_or_tampered_models_require_explicit_preparation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(RuntimeError, "Prepare"):
                module._read_prepared(root)
            asset = root / "model.bin"
            asset.write_bytes(b"original")
            module.atomic_json(root / "prepared.json", {"schema": 1, "model": module.MODEL_ID,
                                                       "files": {"model.bin": module._hash(asset)}})
            self.assertEqual(module._read_prepared(root)["model"], module.MODEL_ID)
            asset.write_bytes(b"modified")
            with self.assertRaisesRegex(RuntimeError, "alterados"):
                module._read_prepared(root)

    def test_gate_requires_token_and_never_uses_mirror(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch("whisper_dictation.secrets_store.get_api_key", return_value=None), \
                patch("huggingface_hub.snapshot_download") as download:
            with self.assertRaisesRegex(RuntimeError, "Aceite as condições"):
                module.prepare_models(SimpleNamespace(transcription_provider="groq"), Path(directory))
            download.assert_not_called()

    def test_explicit_preparation_repairs_corrupted_cached_asset(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = root / "community-1"
            snapshot.mkdir()
            asset = snapshot / "model.bin"
            asset.write_bytes(b"original")
            module.atomic_json(root / "prepared.json", {"schema": 1, "model": module.MODEL_ID,
                              "files": {"community-1/model.bin": module._hash(asset)}})
            asset.write_bytes(b"corrupted")
            pipeline = Mock()
            pipeline._embedding.return_value = np.ones((1, 4), np.float32)
            pipeline._plda.return_value = np.ones((1, 2), np.float32)
            with patch("whisper_dictation.secrets_store.get_api_key", return_value="test-token"), \
                    patch("huggingface_hub.snapshot_download", side_effect=lambda **kw: asset.write_bytes(b"original")) as download, \
                    patch.object(module, "_load_pipeline", return_value=pipeline), \
                    patch.object(module, "_export_segmentation"):
                module.prepare_models(SimpleNamespace(transcription_provider="groq", cpu_threads=1, model_size="small"), root)
            download.assert_called_once()
            self.assertTrue(download.call_args.kwargs["force_download"])
            self.assertEqual(asset.read_bytes(), b"original")
            self.assertEqual(module._read_prepared(root)["model"], module.MODEL_ID)

    def test_audio_is_streamed_into_a_memory_map(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "audio.wav"
            pcm = np.arange(32000, dtype=np.int16)
            with wave.open(str(path), "wb") as audio:
                audio.setparams((1, 2, 16000, 0, "NONE", ""))
                audio.writeframes(pcm.tobytes())
            mapped = module._waveform_map(path, root, lambda: False)
            self.assertIsInstance(mapped, np.memmap)
            np.testing.assert_allclose(mapped, pcm.astype(np.float32) / 32768)
            del mapped
            with self.assertRaises(module.ProcessingCancelled):
                module._waveform_map(path, root, lambda: True)

    def test_fallback_keeps_cpu_when_optional_runtime_fails(self):
        try:
            import torch
        except ImportError:
            self.skipTest("Optional torch runtime is not installed")
        class Model(torch.nn.Module):
            def forward(self, x):
                return x[..., :2]
        model = Model()
        class Pipeline:
            _segmentation = SimpleNamespace(model=model)
            def to(self, device):
                model.to(device)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "segmentation.onnx").write_bytes(b"not loaded in mocked runner")
            with patch.object(module, "_runner", side_effect=RuntimeError("driver unsupported")):
                backend, run, failures = module._select_backend(
                    Pipeline(), root, [np.zeros((1, 1, 4), np.float32)], "openvino-gpu",
                    lambda *args: None, lambda: False,
                )
            self.assertEqual(backend, "torch-cpu")
            self.assertTrue(any("driver unsupported" in reason for reason in failures))
            self.assertEqual(run(np.ones((1, 1, 4), np.float32)).shape, (1, 1, 2))

    def test_runtime_protocol_rejects_large_or_truncated_payloads(self):
        with self.assertRaises(RuntimeError):
            module._read_message(io.BytesIO(struct.pack("<I", module._MAX_MESSAGE + 1)))
        with self.assertRaises(EOFError):
            module._read_message(io.BytesIO(struct.pack("<I", 5) + b"abc"))

    def test_runtime_timeout_kills_subprocess(self):
        from unittest.mock import Mock
        runner = module._RemoteRunner("directml", Path("model.onnx"))
        process = Mock()
        process.poll.return_value = None
        runner.process = process
        runner.responses = queue.Queue()
        with self.assertRaises(TimeoutError):
            runner._wait(0.01)
        process.kill.assert_called_once()
        process.wait.assert_called_once()
        self.assertIsNone(runner.process)

    def test_real_onnx_cpu_subprocess_inference(self):
        import onnx
        from onnx import TensorProto, helper
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model.onnx"
            model = helper.make_model(helper.make_graph(
                [helper.make_node("ReduceMean", ["waveform"], ["scores"], axes=[2], keepdims=1)],
                "runtime-test", [helper.make_tensor_value_info("waveform", TensorProto.FLOAT, [1, 1, 16])],
                [helper.make_tensor_value_info("scores", TensorProto.FLOAT, [1, 1, 1])]),
                opset_imports=[helper.make_opsetid("", 17)], ir_version=9)
            onnx.save(model, path)
            runner = module._runner("onnx-cpu", path)
            try:
                result = runner(np.arange(16, dtype=np.float32).reshape(1, 1, 16))
                np.testing.assert_allclose(result, [[[7.5]]])
            finally:
                runner.close()


if __name__ == "__main__":
    unittest.main()
