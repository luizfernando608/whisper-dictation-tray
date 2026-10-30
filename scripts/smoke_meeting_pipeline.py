"""Run real local ASR and diarization on an explicitly supplied public fixture.

Creates its own isolated library; does not open microphones or read API keys.
"""
import argparse
import json
import sys
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from whisper_dictation.diarization import prepare_models
from whisper_dictation.meeting_processing import process_recording
from whisper_dictation.playback import transcript_text
from whisper_dictation.recording_store import RecordingStore
from whisper_dictation.settings import AppConfig


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("fixture", type=Path)
    parser.add_argument("model_root", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--skip-prepare", action="store_true")
    parser.add_argument("--executable", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    config = AppConfig(transcription_provider="local", model_size="tiny", language="en",
                       diarization_engine="nemotron", diarization_model_dir=str(args.model_root.resolve()))
    config_file = args.output / "config.json"
    config.save(config_file)
    if not args.skip_prepare:
        prepare_models(config, args.model_root, lambda message, fraction: print(message, flush=True))
    store = RecordingStore(args.output / "library")
    row = store.create("Public AMI fixture — pipeline validation", "import")
    store.update(row["id"], source_path=str(args.fixture.resolve()), status="processing")
    if args.executable:
        with (args.output / "frozen-worker.log").open("wb") as log:
            subprocess.run([str(args.executable.resolve()), "--process-recording", row["id"],
                            "--store-root", str(store.root.resolve()), "--config-path", str(config_file.resolve())],
                           stdout=log, stderr=log, check=True, timeout=600,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        record = store.get(row["id"])
        result_file = Path(record.get("result_path") or store.directory(row["id"]) / "result.json")
        result = json.loads(result_file.read_text(encoding="utf-8")) if result_file.exists() else None
    else:
        result = process_recording(store.root, row["id"], config_file)
    final = store.get(row["id"])
    if result is None:
        raise RuntimeError(final["error"] or final["status"])
    if final["status"] != "ready" or not result["segments"] or not result["turns"]:
        raise AssertionError("Pipeline did not produce both transcript and speaker turns.")
    folder = store.directory(row["id"])
    (folder / "transcript.txt").write_text(transcript_text(result), encoding="utf-8")
    (folder / "transcript.srt").write_text(transcript_text(result, srt=True), encoding="utf-8")
    print(json.dumps({"status": final["status"], "duration": final["duration"],
                      "segments": len(result["segments"]), "turns": len(result["turns"]),
                      "speakers": sorted({t["speaker"] for t in result["turns"]}),
                      "backend": result["backend"], "output": str(folder)}, indent=2), flush=True)


if __name__ == "__main__":
    main()
