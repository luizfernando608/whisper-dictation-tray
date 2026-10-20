"""Opt-in real Windows install/upgrade test; restores host registration and links.

Runs real installers, briefly stops PACE, and writes private evidence to --output.
Never run automatically from the unit suite. Requires Windows and the source venv.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid
import winreg

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from whisper_dictation.recording_store import RecordingStore
from whisper_dictation.single_instance import CAPABILITIES, OPEN_RECORDINGS, OPEN_SETTINGS, QUIT, probe, request, send_command
from whisper_dictation.updater import hidden_process_options

UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Pace_is1"


def digest(path):
    hasher = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def runtime_manifest(target):
    return {str(path.relative_to(target)): digest(path) for path in (target / "_internal").rglob("*") if path.is_file()}


def wait_for(check, timeout=60, description="condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            value = check()
            if value:
                return value
        except (OSError, RuntimeError, KeyError, ValueError):
            pass
        time.sleep(.25)
    raise TimeoutError(description)


def known_folder(identifier):
    path = ctypes.create_unicode_buffer(32768)
    assert ctypes.windll.shell32.SHGetFolderPathW(None, identifier, None, 0, path) == 0
    return Path(path.value)


def processes(target):
    result = []
    for process in psutil.process_iter(["exe"]):
        try:
            if process.info["exe"] and Path(process.info["exe"]).resolve() == (target / "Pace.exe").resolve():
                result.append(process)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return result


def windows(target):
    pids = {process.pid for process in processes(target)}
    found = []
    user = ctypes.windll.user32
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    @callback_type
    def visit(hwnd, _):
        pid = wintypes.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and user.IsWindowVisible(hwnd):
            title = ctypes.create_unicode_buffer(1024)
            user.GetWindowTextW(hwnd, title, len(title))
            if title.value:
                found.append((hwnd, title.value))
        return True
    user.EnumWindows(visit, 0)
    return found


def stop(target):
    workers = {"--process-recording", "--nemotron-runtime", "--diarization-runtime", "--prepare-models"}
    owners = [process for process in processes(target) if not workers.intersection(process.cmdline())]
    if not owners:
        return
    for hwnd, _ in windows(target):
        post = ctypes.windll.user32.PostMessageW
        post.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        post(hwnd, 0x0010, 0, 0)  # own WM_CLOSE
    if probe():
        # IPC is shared by every installation. Only close the known owner.
        owners_of_port = {connection.pid for connection in psutil.net_connections(kind="tcp")
                          if connection.status == psutil.CONN_LISTEN and connection.laddr.port == 49517}
        assert owners_of_port.intersection(process.pid for process in owners), "IPC belongs to another installation"
        send_command(QUIT)
    wait_for(lambda: all(not process.is_running() for process in owners), description=f"Close original PIDs in {target}")


def stop_tree(process):
    if process is None or process.poll() is not None:
        return
    parent = psutil.Process(process.pid)
    owned = parent.children(recursive=True) + [parent]
    for child in owned:
        try:
            child.terminate()
        except psutil.NoSuchProcess:
            pass
    _, remaining = psutil.wait_procs(owned, timeout=15)
    for child in remaining:
        child.kill()
    psutil.wait_procs(remaining, timeout=15)


def start(target, environment, legacy=False):
    subprocess.Popen([str(target / "Pace.exe"), *([] if legacy else ["--recordings"])],
                     cwd=target, env=environment, **hidden_process_options())
    wait_for(probe, description="tray IPC")
    if legacy:
        return
    state = request(CAPABILITIES, timeout=5)
    assert Path(state["project_root"]).resolve() == target.resolve(), state
    assert state["version"] == "1.5.0", state
    wait_for(lambda: any("Gravações" in title for _, title in windows(target)), description="recordings window")


def erase_registration():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
            assert winreg.QueryInfoKey(key)[0] == 0, "Unexpected nested installation registry keys"
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
    except FileNotFoundError:
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installer", required=True, type=Path)
    parser.add_argument("--old-installer", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--preview", required=True, type=Path)
    parser.add_argument("--phase", choices=("install", "rollback"), default="install")
    parser.add_argument("--helper-source", type=Path, help="Previously clean-installed candidate used by the rollback test")
    args = parser.parse_args()
    root, preview = args.output.resolve(), args.preview.resolve()
    root.mkdir(parents=True, exist_ok=True)
    assert root != preview and not root.is_relative_to(preview)
    state_file = root / f"host-{args.phase}.json"
    assert not state_file.exists(), "Evidence already exists; choose a fresh output"
    environment = os.environ.copy()
    # The app's ordinary LOCALAPPDATA is short. Do not manufacture a nested
    # update cache inside an already long installation-test directory.
    environment["LOCALAPPDATA"] = str(Path.home() / "Documents" / ("pace-validation-data-" + uuid.uuid4().hex[:8]))
    environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    report = {"phase": args.phase, "installer_sha256": digest(args.installer), "isolated_localappdata": environment["LOCALAPPDATA"], "checks": []}
    def record(name, **fields):
        report["checks"].append({"name": name, **fields})
        (root / f"report-{args.phase}.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(name, json.dumps(fields), flush=True)
    programs, desktop, startup = known_folder(2), known_folder(0x10), known_folder(7)
    paths = [programs / "PACE" / name for name in ("PACE.lnk", "PACE — Configurações.lnk", "PACE — Gravações.lnk")]
    paths += [desktop / name for name in ("PACE.lnk", "PACE — Configurações.lnk", "PACE — Gravações.lnk", "Whisper Dictation.lnk", "Whisper Dictation — Configurações.lnk")]
    paths += [startup / "PACE.lnk", startup / "Whisper Dictation.lnk"]
    backups = root / f"host-backup-{args.phase}"
    backups.mkdir()
    saved_links = []
    for index, path in enumerate(paths):
        saved = backups / f"shortcut-{index}.lnk"
        existed = path.exists()
        if existed:
            shutil.copy2(path, saved)
        saved_links.append({"path": str(path), "existed": existed, "saved": str(saved)})
    registry = backups / "Pace_is1.reg"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY):
            registration_exists = True
    except FileNotFoundError:
        registration_exists = False
    exported = False
    if registration_exists:
        exported = subprocess.run(["reg.exe", "export", "HKCU\\" + UNINSTALL_KEY, str(registry), "/y"], capture_output=True).returncode == 0
        assert exported and registry.is_file(), "Existing installation registration must have a valid backup"
    preview_config = preview / "config.json"
    config = json.loads(preview_config.read_text(encoding="utf-8-sig"))
    runtime_path = Path(config["meeting_storage_dir"]) / "runtime.json"
    runtime = json.loads(runtime_path.read_text()) if runtime_path.exists() else {}
    assert not runtime.get("active") and not runtime.get("finalizing"), "Preserve active user capture"
    original_hashes = {str(path): digest(path) for path in [preview_config, preview / ".env"] if path.exists()}
    original_audio = {str(path): digest(path) for path in Path(config["meeting_storage_dir"]).rglob("*.wav")}
    was_running = bool(processes(preview))
    state_file.write_text(json.dumps({"registry_exported": exported, "registry": str(registry), "links": saved_links,
                                      "preview": str(preview), "was_running": was_running}, indent=2), encoding="utf-8")
    targets = []
    helper = None
    def install(installer, target, log):
        target = target.resolve()
        assert target.is_relative_to(root) and target != root, "Installation must stay under validation output"
        process = subprocess.Popen([str(installer.resolve()), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-",
                                 "/NOCLOSEAPPLICATIONS", f"/DIR={target}", f"/LOG={log}"],
                                cwd=root, **hidden_process_options())
        try:
            code = process.wait(timeout=600)
        finally:
            stop_tree(process)
        assert code == 0, f"Installer exit: {code}"
        record("real_installer_completed", target=str(target), exit_code=code)
    try:
        stop(preview)
        erase_registration()
        if args.phase == "install":
            clean = root / "clean"
            targets.append(clean)
            assert not clean.exists()
            install(args.installer, clean, root / "clean-install.log")
            assert not any((clean / "_internal" / "onnx" / "backend" / "test").rglob("*.*"))
            assert not any((clean / "_internal" / "torch" / "include").rglob("*.*"))
            start(clean, environment)
            assert json.loads((clean / "config.json").read_text())["max_record_seconds"] == 0
            assert request(CAPABILITIES)["meetings"]
            send_command(OPEN_SETTINGS)
            wait_for(lambda: len(windows(clean)) >= 2, description="settings window")
            record("clean_install_ui_and_settings", titles=[title for _, title in windows(clean)], default_limit=0)
            stop(clean)
        target = root / ("upgrade" if args.phase == "install" else "rollback")
        targets.append(target)
        assert not target.exists()
        install(args.old_installer, target, root / f"old-{args.phase}-install.log")
        start(target, environment, legacy=True)
        old_hash = digest(target / "Pace.exe")
        old_runtime = runtime_manifest(target)
        old_uninstallers = {path.name: digest(path) for path in target.glob("unins*.*") if path.is_file()}
        old_links = {str(path): digest(path) if path.is_file() else None for path in paths}
        configuration = json.loads((target / "config.json").read_text())
        configuration.update(meeting_storage_dir=str(target / "recordings"), transcription_provider="local", model_size="tiny")
        (target / "config.json").write_text(json.dumps(configuration, ensure_ascii=False), encoding="utf-8")
        (target / ".env").write_text("PACE_VALIDATION_CANARY=preserve-this-value\n", encoding="utf-8")
        (target / "models").mkdir()
        (target / "models" / "keep.txt").write_text("model preservation canary")
        store = RecordingStore(target / "recordings")
        row = store.create("Preserved recording", "import")
        (store.directory(row["id"]) / "keep.txt").write_text("recording preservation canary")
        store.update(row["id"], status="ready", duration=12, speaker_names={"SPEAKER_00": "Validation speaker"})
        preserved = {str(path.relative_to(target)): digest(path) for path in (
            target / "config.json", target / ".env", target / "models" / "keep.txt", store.directory(row["id"]) / "keep.txt")}
        version = "1.5.0" if args.phase == "install" else "9.9.9"
        cache = Path(environment["LOCALAPPDATA"]) / "PaceData" / "updates"
        installer = cache / version / "Pace_Installer.exe"
        installer.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(args.installer, installer)
        staging = cache / "runs" / uuid.uuid4().hex
        helper_root = staging / "helper"
        helper_root.mkdir(parents=True)
        helper_source = clean if args.phase == "install" else args.helper_source.resolve()
        shutil.copy2(helper_source / "Pace.exe", helper_root / "PaceUpdateHelper.exe")
        shutil.copytree(helper_source / "_internal", helper_root / "_internal")
        owner = next(process for process in processes(target) if process.ppid() not in {p.pid for p in processes(target)})
        plan = {"parent_pid": owner.pid, "target": str(target), "installer": str(installer), "version": version,
                "sha256": digest(installer), "size": installer.stat().st_size, "token": uuid.uuid4().hex,
                "receipt": str(staging / "confirmed.json")}
        plan_file = staging / "plan.json"
        plan_file.write_text(json.dumps(plan), encoding="utf-8")
        with (staging / "helper-process.log").open("wb") as log:
            helper = subprocess.Popen([str(helper_root / "PaceUpdateHelper.exe"), "--apply-update", "--update-plan", str(plan_file)],
                                      cwd=helper_root, env=environment, stdout=log, stderr=log, **hidden_process_options())
        wait_for(lambda: (staging / "update.log").exists(), description="detached helper start")
        time.sleep(1)
        assert digest(target / "Pace.exe") == old_hash and helper.poll() is None
        record("helper_waits_for_real_old_parent", old_binary_sha256=old_hash)
        stop(target)
        wait_for(lambda: (staging / "result.json").exists(), timeout=600, description="helper update result")
        outcome = json.loads((staging / "result.json").read_text())
        expected = "updated" if args.phase == "install" else "failed"
        assert outcome["status"] == expected, outcome
        assert helper.wait(timeout=30) == (0 if args.phase == "install" else 1)
        assert not outcome.get("rollback_error"), outcome
        for name, checksum in preserved.items():
            assert digest(target / name) == checksum, f"Persistent data changed: {name}"
        assert RecordingStore(target / "recordings").get(row["id"])["speaker_names"] == {"SPEAKER_00": "Validation speaker"}
        if args.phase == "install":
            state = request(CAPABILITIES)
            assert state["version"] == "1.5.0" and Path(state["project_root"]).resolve() == target
            send_command(OPEN_RECORDINGS)
            wait_for(lambda: any("Gravações" in title for _, title in windows(target)), description="upgraded library")
            record("real_upgrade_confirmed", receipt_version=json.loads((staging / "confirmed.json").read_text())["version"],
                   persistent_hashes_match=True, result=outcome, effective_limit=configuration["max_record_seconds"])
        else:
            assert "confirmou inicialização" in outcome.get("error", ""), "Expected confirmation failure, not a different installation error"
            assert digest(target / "Pace.exe") == old_hash
            assert runtime_manifest(target) == old_runtime, "Rollback must restore every runtime file"
            assert probe()
            record("confirmation_failure_restores_old_exe_and_runtime", runtime_files_verified=len(old_runtime), persistent_hashes_match=True, result=outcome)
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
                registered_version = winreg.QueryValueEx(key, "DisplayVersion")[0]
            record("rollback_registration_version", version=registered_version)
            assert registered_version == "1.4.0", "Rollback left new installer registration over old binaries"
            assert {path.name: digest(path) for path in target.glob("unins*.*") if path.is_file()} == old_uninstallers
            assert {str(path): digest(path) if path.is_file() else None for path in paths} == old_links
            record("rollback_integration_restored", shortcuts=True, uninstallers=True, registry=True)
        report["passed"] = True
    finally:
        cleanup_errors = []
        try:
            stop_tree(helper)
        except Exception as exc:
            cleanup_errors.append(str(exc))
        for target in targets:
            try:
                stop(target)
            except Exception as exc:
                cleanup_errors.append(str(exc))
        try:
            erase_registration()
            if exported:
                subprocess.run(["reg.exe", "import", str(registry)], check=True, capture_output=True)
        except Exception as exc:
            cleanup_errors.append(str(exc))
        for item in saved_links:
            path = Path(item["path"])
            try:
                if item["existed"]:
                    shutil.copy2(item["saved"], path)
                elif path.exists():
                    path.unlink()
            except Exception as exc:
                cleanup_errors.append(str(exc))
        for path, checksum in {**original_hashes, **original_audio}.items():
            assert digest(path) == checksum, f"User data changed: {path}"
        record("host_restored", user_config_and_audio_hashes_match=True, original_audio_files=len(original_audio), cleanup_errors=cleanup_errors)
        if was_running:
            start(preview, os.environ.copy())
        report["passed"] = bool(report.get("passed") and not cleanup_errors)
        (root / f"report-{args.phase}.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        assert not cleanup_errors, cleanup_errors
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
