"""Opt-in real runtime test of PyanNet operators with synthetic, untrained weights.

Run PACE_RUN_MODEL_SMOKE=1. This checks deployment/numerical compatibility, not
speaker identification quality. Trained Community-1 requires explicit HF access.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from whisper_dictation.diarization import _export_segmentation, _runner, _validate_scores


@unittest.skipUnless(os.environ.get("PACE_RUN_MODEL_SMOKE") == "1", "Optional model/runtime smoke")
class ModelRuntimeSmoke(unittest.TestCase):
    def test_default_pyannet_operators_on_cpu_and_intel_gpu(self):
        import torch
        from pyannote.audio.core.task import Specifications, Problem, Resolution
        from pyannote.audio.models.segmentation import PyanNet

        torch.manual_seed(2026)
        torch.set_num_threads(2)
        model = PyanNet()
        model.specifications = Specifications(
            problem=Problem.MONO_LABEL_CLASSIFICATION, resolution=Resolution.FRAME,
            duration=10, classes=["a", "b", "c"], powerset_max_classes=2,
        )
        model.build()
        model.eval()
        pipeline = SimpleNamespace(_segmentation=SimpleNamespace(model=model, duration=10))
        probe = np.random.default_rng(42).normal(0, 0.02, (1, 1, 160000)).astype(np.float32)
        with torch.inference_mode():
            reference = model(torch.from_numpy(probe)).numpy()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "segmentation.onnx"
            _export_segmentation(pipeline, path)
            for backend in ("onnx-cpu", "openvino-cpu", "openvino-gpu", "directml"):
                with self.subTest(backend=backend):
                    runner = _runner(backend, path)
                    try:
                        result = runner(probe)
                        _validate_scores(reference, result)
                        print(f"{backend}: default PyanNet synthetic PASS; max error={np.max(np.abs(reference-result)):.3g}")
                    finally:
                        runner.close()


if __name__ == "__main__":
    unittest.main()
