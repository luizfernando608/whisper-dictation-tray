"""Replay an explicitly supplied capture in an isolated local benchmark library.

Uses the configured ASR provider, including its normal network calls. Original
audio/library metadata are never modified. Full transcripts stay in output.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from whisper_dictation.diarization import _hash, atomic_json
from whisper_dictation.meeting_processing import process_recording
from whisper_dictation.recording_store import RecordingStore
from whisper_dictation.settings import AppConfig, load_env_file


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recording", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("config", type=Path)
    parser.add_argument("--executable", type=Path)
    args = parser.parse_args()
    source, output = args.recording.resolve(), args.output.resolve()
    if output == source or output.is_relative_to(source):
        parser.error("Output must be outside the original recording directory.")
    output.mkdir(parents=True, exist_ok=True)
    load_env_file(ROOT)
    config = AppConfig.load(args.config)
    config.save(output / "config.json")
    capture = json.loads((source / "capture.json").read_text(encoding="utf-8"))
    store = RecordingStore(output / "library")
    rows = store.list()
    row = rows[0] if rows else store.create("Processing benchmark", "meeting", duration=capture["duration"])
    folder = store.directory(row["id"])
    originals = {}
    for name in ("audio.wav", "microphone.wav", "computer.wav", "capture.json"):
        originals[name] = _hash(source / name)
        target = folder / name
        if not target.exists() or _hash(target) != originals[name]:
            shutil.copyfile(source / name, target)
    store.update(row["id"], audio_path=str(folder / "audio.wav"), status="processing")
    started = time.monotonic()
    if args.executable:
        with (output / "frozen.log").open("wb") as log:
            subprocess.run([str(args.executable.resolve()), "--process-recording", row["id"],
                "--store-root", str(store.root), "--config-path", str(output / "config.json")],
                stdout=log, stderr=log, check=True, timeout=900,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    else:
        process_recording(store.root, row["id"], output / "config.json")
    record = store.get(row["id"])
    if record["status"] != "ready":
        raise RuntimeError(record.get("error") or record["status"])
    result = json.loads((folder / "result.json").read_text(encoding="utf-8"))
    unchanged = all(_hash(source / name) == digest for name, digest in originals.items())
    if not unchanged:
        raise AssertionError("Original recording changed during benchmark.")
    summary = {"status": record["status"], "elapsed_seconds": time.monotonic() - started,
        "result_path": str(folder / "result.json"), "originals_unchanged": unchanged,
        "processing": result.get("processing"), "segments": len(result["segments"]),
        "sources": {name: {"words": len(" ".join(s["text"] for s in result["segments"]
                      if s.get("source") == name).split()),
                      "speakers_with_text": len({s["speaker"] for s in result["segments"]
                      if s.get("source") == name and s.get("speaker")})} for name in ("microphone", "computer")}}
    atomic_json(output / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
