"""Opt-in Windows hardware capture validation against an isolated PACE install.

Temporarily closes an idle preview, exercises real microphone/loopback capture
through the installed app's IPC, and restores the preview in finally. No registry
or shortcut changes, cloud APIs, model downloads, or transcription text output.
Do not run automatically in the unit suite: this opens real audio devices.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import logging
import math
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import time
import uuid
import wave

import numpy as np
import psutil
import sounddevice as sd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from validate_installed_pipeline import cached_tiny, fixture_audio, sha256, validate_result
from validate_windows_installation import processes, start, stop, wait_for
from whisper_dictation.audio import AudioRecorder
from whisper_dictation.recording_store import BUSY_STATUSES, RecordingStore
from whisper_dictation.settings import AppConfig
from whisper_dictation.single_instance import CAPABILITIES, MEETING_PAUSE, MEETING_TOGGLE, probe, request, send_command


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}


def assert_preview_idle(preview: Path) -> None:
    config = read_json(preview / "config.json")
    if not config:
        raise RuntimeError("Preview config must exist to verify the user's recording library.")
    root = Path(config.get("meeting_storage_dir") or Path(os.environ["LOCALAPPDATA"]) / "PaceData" / "recordings")
    runtime = read_json(root / "runtime.json")
    if runtime.get("active") or runtime.get("finalizing"):
        raise RuntimeError("Preview has an active/finalizing meeting; test refused.")
    database = root / "library.sqlite3"
    if database.is_file():
        with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as connection:
            for (raw,) in connection.execute("SELECT record FROM recordings"):
                if json.loads(raw).get("status") in BUSY_STATUSES:
                    raise RuntimeError("Preview has queued/active recording work; test refused.")
    if probe():
        state = request(CAPABILITIES, timeout=5)
        if Path(state["project_root"]).resolve() != preview:
            raise RuntimeError("IPC is owned by a different installation; test refused.")
    if processes(preview):
        log = preview / "logs" / "whisper_dictation.log"
        if not log.is_file():
            raise RuntimeError("Cannot confirm preview dictation idle without its log; test refused.")
        pending = False
        # Only inspect lifecycle markers; never expose log or transcription text.
        with log.open(encoding="utf-8", errors="replace") as stream:
            for line in stream:
                if "Starting PACE v" in line or "Application stopped" in line:
                    pending = False
                elif "Recording started" in line:
                    pending = True
                elif any(marker in line for marker in (
                    "Recording discarded because it was too short", "Transcription pipeline finished",
                    "Unhandled transcription failure")):
                    pending = False
        if pending:
            raise RuntimeError("Preview dictation has an unfinished start marker; test refused.")


def microphone_smoke(folder: Path) -> dict:
    recorder = AudioRecorder(16000, None, logging.getLogger("pace.capture-validation"))
    try:
        recorder.start()
        time.sleep(2)
        audio = recorder.stop()
    finally:
        recorder.abort()
    if audio.sample_rate != 16000 or not 1.5 <= audio.duration_seconds <= 3.5:
        raise AssertionError("AudioRecorder did not return the expected two-second 16 kHz capture.")
    if not len(audio.samples) or not np.isfinite(audio.samples).all() or not np.any(audio.samples):
        raise AssertionError("AudioRecorder returned empty, invalid or digitally silent input.")
    pcm = np.clip(audio.samples * 32768, -32768, 32767).astype("<i2")
    path = folder / "microphone-smoke.wav"
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(pcm.tobytes())
    return {"name": "audio_recorder_microphone_smoke", "status": "passed", "scope": "source AudioRecorder and real microphone, no transcription",
            "duration_seconds": audio.duration_seconds, "samples": len(audio.samples),
            "rms": float(np.sqrt(np.mean(audio.samples.astype(np.float64) ** 2))), "evidence": str(path)}


class QuietPlayback:
    def __init__(self, path: Path):
        with wave.open(str(path), "rb") as source:
            self.samples = np.frombuffer(source.readframes(source.getnframes()), "<i2").astype(np.float32) / 32768
        peak = float(np.max(np.abs(self.samples)))
        if not peak:
            raise ValueError("Public playback fixture is digitally silent.")
        # Limit digital peak, leaving the user's Windows master volume untouched.
        self.samples *= min(1.0, .06 / peak)
        self.position = 0
        self.stream = None

    def begin(self):
        self.close()
        self.position = 0
        self.stream = sd.OutputStream(samplerate=16000, channels=1, dtype="float32", callback=self._callback)
        self.stream.start()

    def _callback(self, output, frames, _time, _status):
        output.fill(0)
        chunk = self.samples[self.position:self.position + frames]
        output[:len(chunk), 0] = chunk
        self.position += len(chunk)
        if len(chunk) < frames:
            raise sd.CallbackStop()

    def close(self):
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None


def wav_metrics(path: Path) -> dict:
    with wave.open(str(path), "rb") as stream:
        if (stream.getnchannels(), stream.getsampwidth(), stream.getframerate()) != (1, 2, 16000):
            raise AssertionError("Capture source is not mono PCM16 at 16 kHz.")
        frames = stream.getnframes()
        nonzero, total, peak, square = 0, 0, 0, 0.0
        while raw := stream.readframes(16000):
            values = np.frombuffer(raw, "<i2").astype(np.float64)
            nonzero += int(np.count_nonzero(values))
            total += len(values)
            peak = max(peak, int(np.max(np.abs(values))))
            square += float(np.dot(values, values))
    if frames <= 0 or total != frames:
        raise AssertionError("Capture WAV has inconsistent or empty sample data.")
    return {"frames": frames, "duration_seconds": frames / 16000, "nonzero_samples": nonzero,
            "peak_pcm16": peak, "rms_pcm16": math.sqrt(square / total), "sha256": sha256(path)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("executable", "preview", "output", "model-root", "fixture"):
        parser.add_argument("--" + option, required=True, type=Path)
    parser.add_argument("--duration", type=float, default=128)
    args = parser.parse_args()
    if not math.isfinite(args.duration) or args.duration < 120:
        parser.error("--duration must be finite and at least 120 seconds")
    target = args.executable.resolve().parent
    preview = args.preview.resolve()
    if preview.is_file():
        preview = preview.parent
    if args.executable.name.lower() != "pace.exe" or not args.executable.is_file() or target == preview:
        parser.error("Use the isolated installed Pace.exe, distinct from the preview")
    args.output.mkdir(parents=True, exist_ok=True)
    root = args.output.resolve() / (datetime.now(timezone.utc).strftime("capture-%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8])
    root.mkdir()
    report = {"schema": 1, "status": "running", "output": str(root), "checks": [],
              "scope": {"capture": "installed app through IPC and real WASAPI devices", "cloud": False,
                        "quality_assessment": False, "registry_and_shortcuts_modified": False}}
    config_path = target / "config.json"
    old_config = config_path.read_bytes() if config_path.exists() else None
    config_changed, preview_stopped, test_started = False, False, False
    cleanup_errors = []
    playback = None
    original_fixture_hash = None
    preview_hash = sha256(preview / "config.json") if (preview / "config.json").exists() else None
    report_path = root / "report.json"

    def save():
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")

    def progress(stage, **fields):
        print(json.dumps({"stage": stage, **fields}, ensure_ascii=False), flush=True)
        save()

    try:
        if processes(target):
            raise RuntimeError("Isolated installed app is already running; test refused.")
        assert_preview_idle(preview)
        report["checks"].append({"name": "preview_idle", "status": "passed", "dictation_check": "lifecycle log markers"})
        original_fixture_hash = sha256(args.fixture)
        report.update(executable=str(args.executable.resolve()), executable_sha256=sha256(args.executable),
                      fixture_sha256=original_fixture_hash, requested_active_seconds=args.duration)
        local_models = root / "models"
        local_models.mkdir()
        shutil.copytree(args.model_root.resolve() / "nemotron-3", local_models / "nemotron-3")
        tiny = cached_tiny(args.model_root.resolve())
        (local_models / "asr-prepared.json").write_text(json.dumps({"asr_model": "tiny", "asr_path": str(tiny)}), encoding="utf-8")
        public = root / "public-18s.wav"
        fixture_audio(args.fixture.resolve(), public)
        playback = QuietPlayback(public)
        store_root = root / "library"
        config = AppConfig(transcription_provider="local", model_size="tiny", language="en", compute_type="int8", cpu_threads=4,
                           max_record_seconds=0, meeting_storage_dir=str(store_root), meeting_show_overlay=False,
                           diarization_engine="nemotron", diarization_backend="cpu", diarization_model_dir=str(local_models))
        config.save(root / "test-config.json")
        progress("prepared_isolated_assets")
        assert_preview_idle(preview)
        preview_stopped = True
        stop(preview)
        report["checks"].append(microphone_smoke(root))
        progress("microphone_smoke_completed")
        config.save(config_path)
        config_changed = True
        environment = os.environ.copy()
        environment.update(LOCALAPPDATA=str(root / "appdata"), HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                           HF_HUB_DISABLE_TELEMETRY="1", PYINSTALLER_RESET_ENVIRONMENT="1")
        # The app checks GitHub at startup. Block HTTP(S) locally as well as HF;
        # IPC uses a direct loopback socket, unaffected by these proxy settings.
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
            environment[name] = "http://127.0.0.1:1"
        environment.update(NO_PROXY="localhost,127.0.0.1", no_proxy="localhost,127.0.0.1")
        for name in ("PACE_UPDATE_CONFIRMATION", "PACE_UPDATE_TOKEN"):
            environment.pop(name, None)
        test_started = True
        start(target, environment)
        assert Path(request(CAPABILITIES)["project_root"]).resolve() == target
        store = RecordingStore(store_root)
        runtime_path = store_root / "runtime.json"
        if not send_command(MEETING_TOGGLE):
            raise RuntimeError("Could not send start-meeting IPC.")
        state = wait_for(lambda: (s if (s := read_json(runtime_path)).get("active") else None),
                         timeout=30, description="installed capture start")
        identifier = state["recording_id"]
        report["recording_id"] = identifier
        report["library"] = str(store_root)
        playback.begin()
        wall_started = time.monotonic()
        next_progress, did_pause, continued_after_120 = 0.0, False, False
        peak_levels = [0.0, 0.0]
        while True:
            state = read_json(runtime_path)
            if not state.get("active") or state.get("recording_id") != identifier:
                raise AssertionError("Installed meeting stopped before the requested duration.")
            duration = float(state["duration"])
            for index, value in enumerate(state.get("levels", [0, 0])):
                peak_levels[index] = max(peak_levels[index], float(value))
            if duration > 120:
                continued_after_120 = True
            elapsed = time.monotonic() - wall_started
            if elapsed >= next_progress:
                progress("capturing", active_seconds=round(duration, 2), wall_seconds=round(elapsed, 2),
                         peak_levels=peak_levels)
                next_progress = elapsed + 20
            if duration >= 25 and not did_pause:
                if not send_command(MEETING_PAUSE):
                    raise RuntimeError("Could not send pause IPC.")
                paused = wait_for(lambda: (s if (s := read_json(runtime_path)).get("paused") else None), description="pause")
                before = float(paused["duration"])
                time.sleep(4)
                held = read_json(runtime_path)
                delta = abs(float(held["duration"]) - before)
                if not held.get("paused") or not held.get("active") or delta > .1:
                    raise AssertionError("Capture duration advanced while paused.")
                if not send_command(MEETING_PAUSE):
                    raise RuntimeError("Could not send resume IPC.")
                wait_for(lambda: (s if (s := read_json(runtime_path)).get("active") and not s.get("paused") else None), description="resume")
                playback.begin()
                report["checks"].append({"name": "pause_resume", "status": "passed", "pause_seconds": 4, "duration_drift": delta})
                progress("capture_resumed")
                did_pause = True
            if duration >= args.duration:
                break
            if elapsed > args.duration + 60:
                raise TimeoutError("Capture duration stopped advancing.")
            time.sleep(.5)
        playback.close()
        if not continued_after_120 or not did_pause:
            raise AssertionError("Did not observe an active capture after 120 s and a pause/resume.")
        if not send_command(MEETING_TOGGLE):
            raise RuntimeError("Could not send stop-meeting IPC.")
        wait_for(lambda: store.get(identifier)["status"] not in {"recording", "paused", "finalizing"},
                 timeout=60, description="capture finalization")
        folder = store.directory(identifier)
        metrics = {name: wav_metrics(folder / f"{name}.wav") for name in ("audio", "microphone", "computer")}
        if len({value["frames"] for value in metrics.values()}) != 1:
            raise AssertionError("Microphone/computer/mix WAVs are not sample-aligned.")
        for name, level in zip(("microphone", "computer"), peak_levels):
            if metrics[name]["nonzero_samples"] == 0 or metrics[name]["peak_pcm16"] == 0 or level <= 0:
                raise AssertionError(f"No nonzero samples and runtime level confirmed for {name}.")
        capture_duration = metrics["audio"]["duration_seconds"]
        if capture_duration < args.duration - .5:
            raise AssertionError("Saved capture is shorter than the requested active duration.")
        report["checks"].append({"name": "installed_long_dual_capture", "status": "passed", "continued_after_120_seconds": True,
                                 "sources": metrics, "peak_runtime_levels": peak_levels})
        progress("capture_saved_processing_started", duration_seconds=capture_duration)
        processing_deadline, next_progress = time.monotonic() + 1200, 0.0
        while True:
            record = store.get(identifier)
            if record["status"] == "ready":
                break
            if record["status"] in {"failed", "cancelled", "interrupted"}:
                raise AssertionError(f"Installed processing stopped with status {record['status']}; inspect isolated library.")
            now = time.monotonic()
            if now > processing_deadline:
                raise TimeoutError("Installed processing did not finish within 20 minutes.")
            if now >= next_progress:
                progress("processing", recording_status=record["status"], fraction=record.get("progress", 0))
                next_progress = now + 20
            time.sleep(1)
        result = read_json(folder / "result.json")
        if result.get("processing", {}).get("mode") != "separate_sources":
            raise AssertionError("Installed meeting did not use the separate-source processing path.")
        summary = validate_result(store, identifier, capture_duration)
        if any(sha256(folder / f"{name}.wav") != value["sha256"] for name, value in metrics.items()):
            raise AssertionError("Processing changed an original capture WAV.")
        report["checks"].append({"name": "installed_capture_processing", "status": "passed", "mode": "separate_sources",
                                 "original_capture_unchanged": True, **summary})
        report["status"] = "passed"
        progress("validation_passed")
    except Exception as exc:
        report.update(status="failed", error_type=type(exc).__name__, error=str(exc))
    finally:
        if playback:
            try:
                playback.close()
            except Exception as exc:
                cleanup_errors.append(f"playback: {type(exc).__name__}: {exc}")
        if test_started:
            try:
                stop(target)
            except Exception as exc:
                cleanup_errors.append(f"installed app stop: {type(exc).__name__}: {exc}")
                # This target was required to have no processes before our start.
                # A hung test instance must not retain real recording devices.
                owned = processes(target)
                for process in owned:
                    try:
                        process.terminate()
                    except psutil.NoSuchProcess:
                        pass
                _, remaining = psutil.wait_procs(owned, timeout=10)
                for process in remaining:
                    try:
                        process.kill()
                    except psutil.NoSuchProcess:
                        pass
                psutil.wait_procs(remaining, timeout=10)
        if config_changed:
            try:
                if old_config is None:
                    config_path.unlink(missing_ok=True)
                else:
                    config_path.write_bytes(old_config)
                report["test_config_restored"] = (config_path.read_bytes() == old_config) if old_config is not None else not config_path.exists()
            except Exception as exc:
                cleanup_errors.append(f"config restore: {type(exc).__name__}: {exc}")
        if preview_stopped:
            try:
                start(preview, os.environ.copy())
                report["preview_reopened"] = True
            except Exception as exc:
                cleanup_errors.append(f"preview restart: {type(exc).__name__}: {exc}")
        if preview_hash is not None:
            try:
                report["preview_config_unchanged"] = sha256(preview / "config.json") == preview_hash
                if not report["preview_config_unchanged"]:
                    cleanup_errors.append("Preview config changed.")
            except OSError as exc:
                cleanup_errors.append(f"preview config integrity: {type(exc).__name__}")
        if original_fixture_hash is not None:
            try:
                report["public_fixture_unchanged"] = sha256(args.fixture) == original_fixture_hash
                if not report["public_fixture_unchanged"]:
                    cleanup_errors.append("Public input fixture changed.")
            except OSError as exc:
                cleanup_errors.append(f"public fixture integrity: {type(exc).__name__}")
        if cleanup_errors:
            report.update(status="failed", cleanup_errors=cleanup_errors)
        report["finished_utc"] = datetime.now(timezone.utc).isoformat()
        save()
        print(json.dumps({"status": report["status"], "report": str(report_path)}, ensure_ascii=False), flush=True)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
