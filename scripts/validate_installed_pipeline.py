"""Validate an installed PACE worker offline against a supplied public fixture.

Extends smoke_meeting_pipeline.py's isolated RecordingStore/worker contract with
audio/video import, failure recovery, export and seek checks. Never installs the
app, opens recording devices, reads API keys or downloads models. Evidence stays
in a new child directory under --output; existing files are never overwritten.
Playback/export checks call the source APIs; processing calls the installed EXE.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def cached_tiny(model_root: Path) -> Path:
    """Inspect model paths only; do not call Hugging Face or its token helpers."""
    candidates = []
    marker = model_root / "asr-prepared.json"
    if marker.is_file():
        prepared = json.loads(marker.read_text(encoding="utf-8"))
        if prepared.get("asr_model") == "tiny" and prepared.get("asr_path"):
            candidates.append(Path(prepared["asr_path"]))
    hubs = [model_root / "asr", Path.home() / ".cache" / "huggingface" / "hub"]
    for variable in ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE"):
        if os.environ.get(variable):
            hubs.append(Path(os.environ[variable]))
    if os.environ.get("HF_HOME"):
        hubs.append(Path(os.environ["HF_HOME"]) / "hub")
    for hub in hubs:
        candidates.extend(sorted((hub / "models--Systran--faster-whisper-tiny" / "snapshots").glob("*")))
    for candidate in candidates:
        if all((candidate / name).is_file() for name in ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt")):
            return candidate.resolve()
    raise RuntimeError("No complete cached faster-whisper-tiny found; no download was attempted.")


def fixture_audio(source: Path, destination: Path, seconds: float = 18.0) -> float:
    import av
    import numpy as np

    limit, written = round(seconds * 16000), 0
    with av.open(str(source)) as container, wave.open(str(destination), "wb") as output:
        if not container.streams.audio:
            raise ValueError("Supplied public fixture has no audio stream.")
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
        for original in container.decode(container.streams.audio[0]):
            original.pts = None
            for frame in resampler.resample(original):
                samples = frame.to_ndarray().astype("<i2", copy=False).reshape(-1)[:limit - written]
                output.writeframes(samples.tobytes())
                written += len(samples)
            if written >= limit:
                break
        if written < limit:
            for frame in resampler.resample(None):
                samples = frame.to_ndarray().astype("<i2", copy=False).reshape(-1)[:limit - written]
                output.writeframes(samples.tobytes())
                written += len(samples)
    if written < 10 * 16000:
        raise ValueError("Public fixture must contain at least ten seconds of audio.")
    with wave.open(str(destination), "rb") as check:
        samples = np.frombuffer(check.readframes(check.getnframes()), dtype="<i2")
        if not np.any(samples):
            raise ValueError("Fixture excerpt contains only digital silence.")
    return written / 16000


def fixture_video(audio: Path, destination: Path) -> None:
    """Black 2 fps FFV1 video and lossless PCM audio; no external FFmpeg."""
    import av
    import numpy as np

    with wave.open(str(audio), "rb") as source, av.open(str(destination), "w", format="matroska") as container:
        video = container.add_stream("ffv1", rate=2)
        video.width, video.height, video.pix_fmt = 160, 90, "yuv420p"
        sound = container.add_stream("pcm_s16le", rate=16000)
        sound.layout = "mono"
        offset, index = 0, 0
        while data := source.readframes(8000):
            picture = av.VideoFrame.from_ndarray(np.zeros((90, 160, 3), dtype=np.uint8), format="rgb24")
            picture.pts, picture.time_base = index, Fraction(1, 2)
            for packet in video.encode(picture):
                container.mux(packet)
            frame = av.AudioFrame.from_ndarray(np.frombuffer(data, dtype="<i2").reshape(1, -1), format="s16", layout="mono")
            frame.sample_rate, frame.pts, frame.time_base = 16000, offset, Fraction(1, 16000)
            for packet in sound.encode(frame):
                container.mux(packet)
            offset += frame.samples
            index += 1
        for stream in (video, sound):
            for packet in stream.encode(None):
                container.mux(packet)
    with av.open(str(destination)) as check:
        if len(check.streams.video) != 1 or len(check.streams.audio) != 1:
            raise AssertionError("Generated fixture must contain both video and audio.")


def run_worker(executable: Path, store, recording_id: str, config: Path, folder: Path) -> dict:
    # Explicit local provider + offline flags ensure this test has no cloud path.
    environment = os.environ.copy()
    environment.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1")
    environment.pop("PACE_DATA_DIR", None)
    command = [str(executable), "--process-recording", recording_id,
               "--store-root", str(store.root), "--config-path", str(config)]
    started = time.monotonic()
    with (folder / "installed-worker.log").open("wb") as log:
        process = subprocess.Popen(command, cwd=folder, env=environment, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=log,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            code = process.wait(timeout=600)
        except BaseException:
            # This PID was created above. Never enumerate or stop other PACE apps.
            if process.poll() is None:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                   stdout=log, stderr=log, timeout=30, check=False,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                else:
                    process.kill()
                process.wait(timeout=30)
            raise
    if code:
        raise RuntimeError(f"Installed worker exited with code {code}; inspect its isolated log.")
    record = store.get(recording_id)
    return {"exit_code": code, "status": record["status"], "elapsed_seconds": round(time.monotonic() - started, 3),
            "recording_id": recording_id, "evidence": str(folder)}


def validate_result(store, recording_id: str, expected_duration: float) -> dict:
    from whisper_dictation.playback import WavePlayer, transcript_text

    record = store.get(recording_id)
    folder = store.directory(recording_id)
    if record["status"] != "ready":
        raise AssertionError(f"Installed worker status is {record['status']}; inspect isolated library for the error.")
    result_path = Path(record["result_path"])
    result = json.loads(result_path.read_text(encoding="utf-8"))
    if result.get("transcription_provider") != "local" or result.get("transcription_model") != "tiny":
        raise AssertionError("Worker did not use the requested local tiny ASR.")
    if result.get("backend") != "nemotron-cpu":
        raise AssertionError("Worker did not use the requested Nemotron CPU backend.")
    audio = Path(record["audio_path"])
    with wave.open(str(audio), "rb") as stream:
        duration = stream.getnframes() / stream.getframerate()
        if (stream.getframerate(), stream.getnchannels(), stream.getsampwidth()) != (16000, 1, 2):
            raise AssertionError("Imported audio is not mono PCM16 at 16 kHz.")
    if abs(duration - expected_duration) > .02 or abs(float(result["duration"]) - duration) > .02:
        raise AssertionError("Decoded WAV/result duration differs from the fixture.")
    counts = {}
    for key in ("segments", "turns"):
        entries = result.get(key, [])
        if not entries:
            raise AssertionError(f"Worker produced no {key} on the public speech fixture.")
        previous = -1.0
        for entry in entries:
            start, end = float(entry["start"]), float(entry["end"])
            if not (math.isfinite(start) and math.isfinite(end) and 0 <= start <= end <= duration + .05):
                raise AssertionError(f"Invalid interval in {key}.")
            if start < previous:
                raise AssertionError(f"Nonchronological {key}.")
            previous = start
        counts[key] = len(entries)
    transcript = json.loads((folder / "transcript.json").read_text(encoding="utf-8"))
    words = [word for segment in transcript.get("segments", []) for word in segment.get("words", [])]
    if not words:
        raise AssertionError("ASR returned no word timestamps.")
    if any(not (0 <= float(word["start"]) <= float(word["end"]) <= duration + .05) for word in words):
        raise AssertionError("ASR returned invalid word timestamps.")
    exports = folder / "exports"
    exports.mkdir()
    (exports / "transcript.txt").write_text(transcript_text(result), encoding="utf-8")
    (exports / "transcript.srt").write_text(transcript_text(result, srt=True), encoding="utf-8")
    shutil.copy2(result_path, exports / "transcript.json")
    if not (exports / "transcript.txt").read_text(encoding="utf-8").strip():
        raise AssertionError("TXT export is empty.")
    srt = (exports / "transcript.srt").read_text(encoding="utf-8")
    if srt.count(" --> ") != counts["segments"]:
        raise AssertionError("SRT export lost segments.")
    if json.loads((exports / "transcript.json").read_text(encoding="utf-8")) != result:
        raise AssertionError("JSON export changed the result.")
    player = WavePlayer()
    try:
        player.load(audio)
        for target in (-1, 0, duration / 2, result["segments"][0]["start"], duration, duration + 10):
            player.seek(target)
            if abs(player.position - min(duration, max(0, target))) > 1 / 16000 + 1e-8:
                raise AssertionError("WavePlayer seek did not reach the requested clamped sample.")
        if player.playing:
            raise AssertionError("Seek unexpectedly started playback.")
    finally:
        player.close()
    if player.path is not None or player.duration != 0:
        raise AssertionError("WavePlayer close did not release its file.")
    return {**counts, "word_timestamps": len(words), "duration_seconds": duration,
            "backend": result["backend"], "speakers": len({turn["speaker"] for turn in result["turns"]}),
            "exports": ["TXT", "SRT", "JSON"], "playback_load_seek_close": "passed",
            "normalized_audio_sha256": sha256(audio)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("executable", "output", "model-root", "fixture"):
        parser.add_argument("--" + option, type=Path, required=True)
    args = parser.parse_args()
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    run = args.output / (datetime.now(timezone.utc).strftime("run-%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8])
    run.mkdir()
    report = {"schema": 1, "status": "running", "started_utc": datetime.now(timezone.utc).isoformat(),
              "output": str(run), "checks": [], "downloads_attempted": False,
              "scope": {"worker": "installed executable", "exports_and_seek": "source APIs, without audio output",
                        "microphone_capture": False, "cloud_services": False, "diarization_quality": False}}
    original_hash = None
    try:
        from whisper_dictation.recording_store import RecordingStore
        from whisper_dictation.settings import AppConfig

        executable, fixture, models = (path.resolve() for path in (args.executable, args.fixture, args.model_root))
        if not executable.is_file() or not fixture.is_file():
            raise FileNotFoundError("Installed executable and supplied public fixture must exist.")
        original_hash = sha256(fixture)
        report.update(executable=str(executable), executable_sha256=sha256(executable), fixture=str(fixture),
                      fixture_sha256=original_hash)
        tiny = cached_tiny(models)
        local_models = run / "models"
        local_models.mkdir()
        shutil.copytree(models / "nemotron-3", local_models / "nemotron-3")
        (local_models / "asr-prepared.json").write_text(json.dumps({"asr_model": "tiny", "asr_path": str(tiny)}), encoding="utf-8")
        config = AppConfig(transcription_provider="local", model_size="tiny", language="en", compute_type="int8",
                           cpu_threads=4, diarization_engine="nemotron", diarization_backend="cpu",
                           diarization_model_dir=str(local_models))
        config_path = run / "config.json"
        config.save(config_path)
        report["cached_tiny_path"] = str(tiny)
        inputs = run / "inputs"
        inputs.mkdir()
        wav, video = inputs / "public-fixture-18s.wav", inputs / "public-fixture-18s.mkv"
        duration = fixture_audio(fixture, wav)
        fixture_video(wav, video)
        store = RecordingStore(run / "library")
        audio_hashes = []
        for kind, source in (("audio_import", wav), ("video_import", video)):
            source_hash = sha256(source)
            row = store.create("Public fixture installed validation", "import", source_path=str(source), status="processing")
            summary = run_worker(executable, store, row["id"], config_path, store.directory(row["id"]))
            summary.update(validate_result(store, row["id"], duration))
            if sha256(source) != source_hash:
                raise AssertionError("Import modified its input file.")
            summary.update(name=kind, status="passed", input_unchanged=True)
            report["checks"].append(summary)
            audio_hashes.append(summary["normalized_audio_sha256"])
        if len(set(audio_hashes)) != 1:
            raise AssertionError("Lossless video import does not match audio import sample for sample.")
        report["checks"].append({"name": "lossless_video_audio_equivalence", "status": "passed"})
        bad = inputs / "invalid-media.bin"
        bad.write_bytes(b"PACE invalid-media validation fixture\n")
        row = store.create("Invalid media validation", "import", source_path=str(bad), status="processing")
        summary = run_worker(executable, store, row["id"], config_path, store.directory(row["id"]))
        if store.get(row["id"])["status"] != "failed" or not store.get(row["id"]).get("error"):
            raise AssertionError("Invalid media did not fail explicitly and recoverably.")
        summary.update(name="invalid_import", status="passed", expected_recording_status="failed")
        report["checks"].append(summary)
        # Retry the same failed row with valid media; existing library metadata survives.
        store.update(row["id"], source_path=str(wav), status="processing")
        summary = run_worker(executable, store, row["id"], config_path, store.directory(row["id"]))
        summary.update(validate_result(store, row["id"], duration))
        summary.update(name="retry_failed_import", status="passed")
        report["checks"].append(summary)
        row = store.create("Precancelled validation", "import", source_path=str(wav), status="processing")
        (store.directory(row["id"]) / "cancel.request").touch()
        summary = run_worker(executable, store, row["id"], config_path, store.directory(row["id"]))
        if store.get(row["id"])["status"] != "cancelled" or (store.directory(row["id"]) / "result.json").exists():
            raise AssertionError("Precancelled worker produced a completed result.")
        summary.update(name="pre_cancelled_worker", status="passed", expected_recording_status="cancelled")
        report["checks"].append(summary)
        report["status"] = "passed"
    except Exception as exc:
        report.update(status="failed", error_type=type(exc).__name__, error=str(exc))
    finally:
        if original_hash is not None:
            unchanged = sha256(args.fixture.resolve()) == original_hash
            report["fixture_unchanged"] = unchanged
            if not unchanged:
                report.update(status="failed", error="Original public fixture was modified.")
        report["finished_utc"] = datetime.now(timezone.utc).isoformat()
        report_path = run / "report.json"
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        print(json.dumps({"status": report["status"], "report": str(report_path)}, ensure_ascii=False))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
