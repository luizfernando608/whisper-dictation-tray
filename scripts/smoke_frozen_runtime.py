"""Exercise the packaged private inference CLI using synthetic model weights."""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np
import torch
from pyannote.audio.core.task import Problem, Resolution, Specifications
from pyannote.audio.models.segmentation import PyanNet

from whisper_dictation.diarization import _RemoteRunner, _export_segmentation, _validate_scores


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("executable", type=Path)
    args = parser.parse_args()
    executable = args.executable.resolve()
    torch.manual_seed(2026)
    torch.set_num_threads(2)
    model = PyanNet()
    model.specifications = Specifications(problem=Problem.MONO_LABEL_CLASSIFICATION, resolution=Resolution.FRAME,
        duration=10, classes=["a", "b", "c"], powerset_max_classes=2)
    model.build()
    model.eval()
    probe = np.random.default_rng(42).normal(0, 0.02, (1, 1, 160000)).astype(np.float32)
    with torch.inference_mode():
        reference = model(torch.from_numpy(probe)).numpy()
    with tempfile.TemporaryDirectory(prefix="pace-frozen-runtime-") as temporary:
        path = Path(temporary) / "segmentation.onnx"
        _export_segmentation(SimpleNamespace(_segmentation=SimpleNamespace(model=model, duration=10)), path)
        with patch.object(sys, "frozen", True, create=True), patch.object(sys, "executable", str(executable)):
            for backend in ("onnx-cpu", "openvino-cpu", "openvino-gpu", "directml"):
                runner = _RemoteRunner(backend, path)
                try:
                    result = runner(probe)
                    _validate_scores(reference, result)
                    print(f"frozen {backend}: PASS; max error {np.max(np.abs(reference-result)):.3g}", flush=True)
                except Exception:
                    print((path.parent / ("runtime-" + backend + ".log")).read_text(encoding="utf-8", errors="replace"), flush=True)
                    raise
                finally:
                    runner.close()


if __name__ == "__main__":
    main()
