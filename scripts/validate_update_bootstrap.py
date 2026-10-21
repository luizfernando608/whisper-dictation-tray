"""Opt-in: exercise real copied frozen helpers without applying an update.

The parent is the source venv with frozen/executable flags simulated. The copied
helper is a real PACE EXE with its actual DLLs. No GUI, IPC, registry changes,
installer execution, or application shutdown are requested. Keep that distinction
in reports; this complements the separate installed upgrade/rollback tests.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys
from unittest.mock import patch

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from whisper_dictation import updater
from whisper_dictation.child_job import ChildJob
from validate_windows_installation import digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", required=True, type=Path)
    parser.add_argument("--installer", required=True, type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    executable, installer, output = args.executable.resolve(), args.installer.resolve(), args.output.resolve()
    assert os.name == "nt" and executable.name == "Pace.exe" and executable.is_file() and installer.is_file()
    assert not output.exists() and not executable.is_relative_to(output) and not output.is_relative_to(executable.parent)
    output.mkdir(parents=True)
    before = digest(executable)
    report = {"status": "running", "source_parent_frozen_flags_simulated": True,
              "real_frozen_helper": True, "installer_executed": False, "host_integration_modified": False,
              "installer_cache_seeded": True, "checks": []}
    cache_environment = {"LOCALAPPDATA": str(output / "appdata")}
    owned_job = ChildJob()
    real_popen = updater.subprocess.Popen

    def launch_owned(*arguments, **options):
        process = real_popen(*arguments, **options)
        try:
            owned_job.add(process.pid)
        except Exception:
            if process.poll() is not None:
                return process  # A very short-lived fixture needs no job cleanup.
            process.terminate()
            process.wait(timeout=5)
            raise
        return process

    update = updater.UpdateInfo(args.version, "v" + args.version,
        f"https://github.com/{updater.REPO}/releases/download/v{args.version}/Pace_Installer.exe",
        sha256=digest(installer), size=installer.stat().st_size)
    try:
        with patch.dict(os.environ, cache_environment):
            cache = updater.update_cache_root()
            destination = cache / args.version / "Pace_Installer.exe"
            destination.parent.mkdir(parents=True)
            shutil.copy2(installer, destination)
            helper = None
            try:
                # Also verify reset of an inherited frozen context in the child.
                with patch.dict(os.environ, {"_PYI_ARCHIVE_FILE": "invalid-inherited-parent",
                                             "_PYI_APPLICATION_HOME_DIR": "invalid-inherited-runtime"}), \
                     patch.object(sys, "frozen", True, create=True), \
                     patch.object(sys, "executable", str(executable)), \
                     patch.object(updater.subprocess, "Popen", side_effect=launch_owned):
                    pid = updater.restart_to_update(update, executable.parent)
                helper = psutil.Process(pid)
                ready = next((cache / "runs").glob("*/ready.json"))
                payload = json.loads(ready.read_text(encoding="utf-8"))
                assert helper.is_running() and payload["helper_pid"] == pid and payload["parent_pid"] == os.getpid()
                assert Path(helper.exe()).resolve() == (ready.parent / "helper" / "PaceUpdateHelper.exe").resolve()
                assert not (ready.parent / "backup").exists() and not (ready.parent / "installer.log").exists()
                report["checks"].append({"name": "real_frozen_helper_ready_before_parent_exit", "passed": True,
                                         "ready": str(ready), "inherited_frozen_context_reset": True})
            finally:
                if helper is not None and helper.is_running():
                    helper.terminate()
                    helper.wait(timeout=15)
            # A real valid Windows EXE which exits on unsupported helper flags
            # tests failure after Popen succeeds, rather than a mocked launch.
            fixture = output / "early-exit-fixture"
            fixture.mkdir()
            (fixture / "_internal").mkdir()
            shutil.copy2(Path(os.environ["SystemRoot"]) / "System32" / "where.exe", fixture / "Pace.exe")
            canary = fixture / "config.json"
            canary.write_bytes(b'{"validation_canary":true}')
            with patch.object(sys, "frozen", True, create=True), patch.object(sys, "executable", str(fixture / "Pace.exe")), \
                 patch.object(updater.subprocess, "Popen", side_effect=launch_owned):
                try:
                    updater.restart_to_update(update, fixture)
                except updater.UpdateError as exc:
                    report["checks"].append({"name": "early_helper_exit_keeps_parent_available", "passed": True,
                                             "error": str(exc), "fixture": "Windows where.exe, not PACE"})
                else:
                    raise AssertionError("A helper without readiness was accepted.")
            assert canary.read_bytes() == b'{"validation_canary":true}'
        assert digest(executable) == before
        report.update(status="passed", target_executable_unchanged=True, parent_pid=os.getpid())
    except Exception as exc:
        report.update(status="failed", error_type=type(exc).__name__, error=str(exc))
    finally:
        owned_job.close()  # Kill owned helpers before parent exit can release their install wait.
        (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps({"status": report["status"], "report": str(output / "report.json")}))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
