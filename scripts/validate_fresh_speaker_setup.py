"""Opt-in first-use processing with empty model/HF caches and public test audio.

Runs normal workers without a manual prepare step, API keys, UI or microphones.
The second independent recording uses the prepared cache with HF offline and an
unreachable per-process HTTP proxy; no checkpoints from the first are reused.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from whisper_dictation.child_job import ChildJob
from whisper_dictation.recording_store import RecordingStore
from whisper_dictation.settings import AppConfig


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--executable", type=Path)
    args = parser.parse_args()
    root, fixture = args.output.resolve(), args.fixture.resolve()
    assert not root.exists() and fixture.is_file()
    # The supplied public NVIDIA/AMI excerpt is fixed and never user audio.
    assert digest(fixture) == "f00f92e53115a4a6724aec0ddaf9c675df6d5fabd3a489b43fffe56ba52a3ae9"
    root.mkdir(parents=True)
    models, hf = root / "models", root / "hf-home"
    assert not models.exists() and not hf.exists()
    config = AppConfig(transcription_provider="local", model_size="tiny", language="en",
                       diarization_engine="nemotron", diarization_backend="cpu", diarization_model_dir=str(models))
    config_path = root / "config.json"
    config.save(config_path)
    environment = os.environ.copy()
    for name in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "GROQ_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        environment.pop(name, None)
    environment.update(HF_HOME=str(hf), HF_HUB_CACHE=str(hf / "hub"), HUGGINGFACE_HUB_CACHE=str(hf / "hub"),
                       XDG_CACHE_HOME=str(root / "xdg"), LOCALAPPDATA=str(root / "appdata"),
                       HF_HUB_DISABLE_IMPLICIT_TOKEN="1", PYTHONUTF8="1", PYINSTALLER_RESET_ENVIRONMENT="1")
    environment.pop("HF_HUB_OFFLINE", None)
    environment.pop("TRANSFORMERS_OFFLINE", None)
    original = digest(fixture)
    store = RecordingStore(root / "library")
    report = {"passed": False, "empty_model_and_hf_cache_at_start": True,
              "manual_prepare": False, "models_seeded": False, "source_injection": False,
              "public_fixture_sha256": original, "executable": str(args.executable.resolve()) if args.executable else None,
              "checks": []}
    report_path = root / "report.json"
    def save():
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    save()
    try:
        for offline in (False, True):
            row = store.create("Public AMI first-use test", "import")
            folder = store.directory(row["id"])
            assert not list(folder.iterdir()), "Second run must not reuse recording checkpoints"
            store.update(row["id"], source_path=str(fixture), status="queued")
            child_environment = environment.copy()
            if offline:
                config.diarization_backend = "auto"
                config.save(config_path)
                child_environment.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", NO_PROXY="", no_proxy="")
                for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
                    child_environment[name] = "http://127.0.0.1:9"
            command = ([str(args.executable.resolve())] if args.executable else
                       [sys.executable, "-X", "utf8", str(Path(__file__).resolve().parents[1] / "main.py")])
            command += ["--process-recording", row["id"], "--store-root", str(store.root), "--config-path", str(config_path)]
            job = ChildJob()
            process = None
            stages = []
            started = time.monotonic()
            try:
                with (folder / "worker.log").open("wb") as output:
                    process = subprocess.Popen(command, env=child_environment, stdout=output, stderr=output,
                                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    job.add(process.pid)
                    last = ""
                    while process.poll() is None:
                        if time.monotonic() - started > 1500:
                            raise TimeoutError("Fresh-cache worker exceeded 25 minutes")
                        current = store.get(row["id"])
                        stage = str(current.get("stage", ""))
                        if stage and stage != last:
                            stages.append(stage)
                            print(("offline: " if offline else "fresh: ") + stage, flush=True)
                            last = stage
                        time.sleep(.5)
                    assert process.returncode == 0, f"Worker exit {process.returncode}; inspect its private log"
            finally:
                job.close()
                if process is not None and process.poll() is None:
                    process.terminate()
                    process.wait(timeout=15)
            final = store.get(row["id"])
            assert final["status"] == "ready", final.get("error") or final
            result = json.loads(Path(final["result_path"]).read_text(encoding="utf-8"))
            assert result["text"].strip() and result["turns"] and any(s.get("speaker") for s in result["segments"])
            assert result["backend"] == "nemotron-cpu"
            manifest = json.loads((models / "nemotron-3" / "prepared.json").read_text(encoding="utf-8"))
            asr = json.loads((models / "asr-prepared.json").read_text(encoding="utf-8"))
            assert Path(asr["asr_path"]).resolve().is_relative_to(root)
            assert manifest.get("runtimes") == ["cpu"]
            assert not (models / "nemotron-3" / "vulkan").exists()
            report["checks"].append({"offline_proxy_and_hf": offline, "recording_id": row["id"],
                                     "requested_backend": config.diarization_backend,
                                     "elapsed_seconds": time.monotonic()-started, "backend": result["backend"],
                                     "segments": len(result["segments"]), "turns": len(result["turns"]),
                                     "speakers": len({t["speaker"] for t in result["turns"]}), "stages": stages,
                                     "independent_recording_no_checkpoint_seed": True})
            save()
        assert digest(fixture) == original
        report.update(passed=True, fixture_unchanged=True)
    finally:
        save()
    print("Fresh-cache and independent offline processing PASS", flush=True)


if __name__ == "__main__":
    main()
