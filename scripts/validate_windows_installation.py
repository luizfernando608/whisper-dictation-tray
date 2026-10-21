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
    owners = []
    for process in processes(target):
        try:
            if not workers.intersection(process.cmdline()):
                owners.append(process)
        except psutil.NoSuchProcess:
            continue
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
    if process is None:
        return
    if isinstance(process, psutil.Process):
        parent = process
        if not parent.is_running():
            return
    else:
        if process.poll() is not None:
            return
        try:
            parent = psutil.Process(process.pid)
        except psutil.NoSuchProcess:
            return
    try:
        owned = parent.children(recursive=True) + [parent]
    except psutil.NoSuchProcess:
        return
    for child in owned:
        try:
            child.terminate()
        except psutil.NoSuchProcess:
            pass
    _, remaining = psutil.wait_procs(owned, timeout=15)
    for child in remaining:
        child.kill()
    psutil.wait_procs(remaining, timeout=15)


def observe_ui_installer(temporary, records, owned, old_pids):
    """Observe only the executable downloaded into this run's isolated TEMP."""
    expected = (temporary / "Pace_Installer.exe").resolve()
    known = {item["pid"] for item in records}
    for process in psutil.process_iter(["exe"]):
        try:
            if not process.info["exe"] or Path(process.info["exe"]).resolve() != expected:
                continue
            if process.pid not in known:
                command = process.cmdline()
                records.append({"pid": process.pid, "parent_pid": process.ppid(), "command": command,
                                "arguments": command[1:], "parent_is_old_pace": process.ppid() in old_pids,
                                "observation": "owned executable path via psutil; no Popen hook"})
                known.add(process.pid)
            for child in [process, *process.children(recursive=True)]:
                # The installer's optional post-install PACE launch is managed
                # gracefully via stop(target), not as installer cleanup.
                if child.pid == process.pid or Path(child.exe()).resolve().is_relative_to(temporary):
                    owned[(child.pid, child.create_time())] = child
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue


def validate_ui_marker(marker, identity):
    """Require the external native-UI driver's attestation for this exact run."""
    assert marker.get("run_id") == identity["run_id"], "UI marker belongs to another run"
    for name in ("target", "temporary"):
        assert Path(marker[name]).resolve() == Path(identity[name]).resolve(), "UI marker path mismatch"
    assert marker.get("version") == identity["version"], "UI marker version mismatch"


def start(target, environment, legacy=False, expected_version=None):
    subprocess.Popen([str(target / "Pace.exe"), *([] if legacy else ["--recordings"])],
                     cwd=target, env=environment, **hidden_process_options())
    wait_for(probe, description="tray IPC")
    if legacy:
        return
    state = request(CAPABILITIES, timeout=5)
    assert Path(state["project_root"]).resolve() == target.resolve(), state
    if expected_version is not None:
        assert state["version"] == expected_version, state
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
    parser.add_argument("--installer", type=Path, help="Candidate installer; not used/seeding downloads in legacy phase")
    parser.add_argument("--old-installer", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--preview", required=True, type=Path)
    parser.add_argument("--phase", choices=("install", "rollback", "legacy", "legacy-ui"), default="install")
    parser.add_argument("--helper-source", type=Path, help="Previously clean-installed candidate used by the rollback test")
    parser.add_argument("--version", default="1.5.0", help="Expected candidate version")
    parser.add_argument("--old-version", default="1.4.0", help="Expected rollback version")
    parser.add_argument("--single-window", action="store_true", help="Verify settings reuse the library window")
    args = parser.parse_args()
    root, preview = args.output.resolve(), args.preview.resolve()
    assert root != preview and not root.is_relative_to(preview) and not preview.is_relative_to(root)
    assert not root.exists(), "Choose an entirely fresh validation output directory"
    assert args.old_installer.is_file() and not args.old_installer.resolve().is_relative_to(root)
    if args.phase not in {"legacy", "legacy-ui"}:
        assert args.installer and args.installer.is_file() and not args.installer.resolve().is_relative_to(root)
    else:
        assert args.old_version == "1.4.0"
        from validate_legacy_update import verify_public_installer
        public_baseline = verify_public_installer(args.old_installer, "1.4.0")
    # The hardware harness also checks queued processing and dictation lifecycle;
    # importing is safe, but no hardware capture is performed here.
    from validate_installed_capture import assert_preview_idle
    assert_preview_idle(preview)
    root.mkdir(parents=True)
    state_file = root / f"host-{args.phase}.json"
    assert not state_file.exists(), "Evidence already exists; choose a fresh output"
    environment = os.environ.copy()
    # The app's ordinary LOCALAPPDATA is short. Do not manufacture a nested
    # update cache inside an already long installation-test directory.
    environment["LOCALAPPDATA"] = str(Path.home() / "Documents" / ("pace-validation-data-" + uuid.uuid4().hex[:8]))
    assert not Path(environment["LOCALAPPDATA"]).exists(), "Isolated app data directory must be fresh"
    environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    if args.phase == "legacy-ui":
        ui_temporary = root / "windows-temp"
        ui_temporary.mkdir()
        # The actual frozen old tray, settings UI and its real installer all
        # inherit this TEMP. The harness process's tempfile cache is untouched.
        environment.update(TEMP=str(ui_temporary), TMP=str(ui_temporary))
    report = {"phase": args.phase, "installer_sha256": digest(args.installer) if args.installer else None,
              "old_installer_sha256": digest(args.old_installer), "isolated_localappdata": environment["LOCALAPPDATA"], "checks": []}
    if args.phase in {"legacy", "legacy-ui"}:
        report["public_baseline"] = public_baseline
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
    ui_owned = {}
    def install(installer, target, log):
        target = target.resolve()
        assert target.is_relative_to(root) and target != root, "Installation must stay under validation output"
        process = subprocess.Popen([str(installer.resolve()), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-",
                                 "/NOCLOSEAPPLICATIONS", f"/DIR={target}", f"/LOG={log}"],
                                cwd=root, env=environment if args.phase == "legacy-ui" else None,
                                **hidden_process_options())
        try:
            code = process.wait(timeout=600)
        finally:
            stop_tree(process)
        assert code == 0, f"Installer exit: {code}"
        record("real_installer_completed", target=str(target), exit_code=code)
    try:
        assert_preview_idle(preview)
        stop(preview)
        erase_registration()
        if args.phase == "install":
            clean = root / "clean"
            targets.append(clean)
            assert not clean.exists()
            install(args.installer, clean, root / "clean-install.log")
            assert not any((clean / "_internal" / "onnx" / "backend" / "test").rglob("*.*"))
            assert not any((clean / "_internal" / "torch" / "include").rglob("*.*"))
            start(clean, environment, expected_version=args.version)
            assert json.loads((clean / "config.json").read_text())["max_record_seconds"] == 0
            assert request(CAPABILITIES)["meetings"]
            library_handles = {hwnd for hwnd, _ in windows(clean)}
            send_command(OPEN_SETTINGS)
            if args.single_window:
                wait_for(lambda: any("Configurações" in title for _, title in windows(clean)), description="settings page")
                assert len(library_handles) == 1 and {hwnd for hwnd, _ in windows(clean)} == library_handles
                record("settings_share_library_window", same_hwnd=True, visible_windows=1)
                send_command(OPEN_RECORDINGS)
                wait_for(lambda: any("Gravações" in title for _, title in windows(clean)), description="return to library")
                assert {hwnd for hwnd, _ in windows(clean)} == library_handles
            else:
                wait_for(lambda: len(windows(clean)) >= 2, description="settings window")
            record("clean_install_ui_and_settings", titles=[title for _, title in windows(clean)], default_limit=0)
            stop(clean)
        target = root / ("upgrade" if args.phase == "install" else args.phase)
        targets.append(target)
        assert not target.exists()
        install(args.old_installer, target, root / f"old-{args.phase}-install.log")
        start(target, environment, legacy=args.old_version == "1.4.0", expected_version=args.old_version)
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
        if args.phase == "legacy-ui":
            from validate_legacy_update import registered_target, verify_public_installer
            assert registered_target() == (target, "1.4.0")
            assert send_command(OPEN_SETTINGS)
            wait_for(lambda: any("Configura" in title for _, title in windows(target)), description="actual old settings UI")
            identity = {"run_id": uuid.uuid4().hex, "target": str(target), "temporary": str(ui_temporary),
                        "version": args.version, "old_version": "1.4.0", "old_executable_sha256": old_hash,
                        "settings_visible": True, "harness_pid": os.getpid()}
            old_pids = {process.pid for process in processes(target)}
            ready = root / "legacy-ui-ready.json"
            partial = ready.with_suffix(".partial")
            partial.write_text(json.dumps(identity, indent=2), encoding="utf-8")
            partial.replace(ready)
            record("legacy_ui_ready_for_native_driver", **identity)
            complete, failed = root / "legacy-ui-complete.json", root / "legacy-ui-failed.json"
            observed = []
            deadline = time.monotonic() + 2400
            while True:
                observe_ui_installer(ui_temporary, observed, ui_owned, old_pids)
                if failed.is_file():
                    marker = json.loads(failed.read_text(encoding="utf-8-sig"))
                    validate_ui_marker(marker, identity)
                    record("legacy_ui_failed", ui_error=marker.get("error", "Native UI driver reported failure"),
                           physical_button_clicked=bool(marker.get("physical_button_clicked")),
                           observed_installer_processes=observed)
                    raise RuntimeError("Native legacy UI driver reported an error; see report")
                if complete.is_file():
                    marker = json.loads(complete.read_text(encoding="utf-8-sig"))
                    validate_ui_marker(marker, identity)
                    assert marker.get("physical_button_clicked") is True, "Native UI button attestation required"
                    break
                if time.monotonic() >= deadline:
                    download = ui_temporary / "Pace_Installer.exe"
                    record("legacy_ui_harness_timeout", timeout_seconds=2400,
                           downloaded_bytes=download.stat().st_size if download.exists() else 0,
                           application_failure_confirmed=False, observed_installer_processes=observed)
                    raise TimeoutError("Native UI validation exceeded the harness wait; this alone is not an app failure")
                time.sleep(.25)
            assert observed, "No owned installer process was observed"
            assert all(item["arguments"] == [] and item["parent_is_old_pace"] for item in observed), "Unexpected interactive legacy launch"
            installer_roots = [process for (pid, _), process in ui_owned.items() if pid in {item["pid"] for item in observed}]
            wait_for(lambda: all(not process.is_running() for process in installer_roots),
                     description="native installer closed after completion attestation")
            downloaded = ui_temporary / "Pace_Installer.exe"
            public_download = verify_public_installer(downloaded, args.version)
            assert registered_target() == (target, args.version)
            # Finishing the native installer may already have opened the app.
            # start() routes to that same installation if it owns the IPC.
            start(target, environment, expected_version=args.version)
            for name, checksum in preserved.items():
                assert digest(target / name) == checksum, f"Persistent data changed: {name}"
            assert RecordingStore(target / "recordings").get(row["id"])["speaker_names"] == {"SPEAKER_00": "Validation speaker"}
            record("actual_legacy_native_ui_upgrade", physical_button_clicked=True,
                   native_ui_attestation=marker, frozen_legacy_bootloader_context=True,
                   source_injection=False, simulated_transport=False, installer_arguments=[],
                   observed_installer_processes=observed, actual_network_download=True,
                   downloaded_bytes=public_download["size"], downloaded_sha256=public_download["sha256"],
                   persistent_hashes_match=True, registry_target_verified=True)
            report["passed"] = True
            return 0
        if args.phase == "legacy":
            temporary = root / "legacy-temp"
            temporary.mkdir()
            legacy_environment = environment.copy()
            legacy_environment.update(TEMP=str(temporary), TMP=str(temporary))
            legacy_plan = root / "legacy-plan.json"
            legacy_plan.write_text(json.dumps({"target": str(target), "temporary": str(temporary),
                                              "version": args.version}), encoding="utf-8")
            with (root / "legacy-child.log").open("wb") as log:
                helper = subprocess.Popen([sys.executable, str(Path(__file__).with_name("validate_legacy_update.py")),
                                           "--child-plan", str(legacy_plan)], cwd=root, env=legacy_environment,
                                          stdout=log, stderr=log, **hidden_process_options())
            launched_path = root / "installer-launched.json"
            try:
                wait_for(lambda: launched_path.exists() or helper.poll() is not None,
                         timeout=2400, description="actual legacy API network download and silent installer launch")
            except TimeoutError:
                partial_download = temporary / "Pace_Installer.exe"
                record("legacy_api_harness_timeout", timeout_seconds=2400,
                       downloaded_bytes=partial_download.stat().st_size if partial_download.exists() else 0,
                       application_failure_confirmed=False, physical_button_clicked=False)
                raise
            if not launched_path.exists():
                raise RuntimeError("Legacy child stopped before launch; inspect legacy-result.json/legacy-child.log")
            # Mirrors the old tray's download_and_launch -> _shutdown_app order.
            # The command remains the old launcher's exact /SILENT command.
            stop(target)
            assert helper.wait(timeout=660) == 0, "Legacy installer failed; inspect legacy-result.json"
            outcome = json.loads((root / "legacy-result.json").read_text(encoding="utf-8"))
            assert outcome["passed"] and outcome["actual_network_download"] and outcome["exact_legacy_launch"]
            for name, checksum in preserved.items():
                assert digest(target / name) == checksum, f"Persistent data changed: {name}"
            assert RecordingStore(target / "recordings").get(row["id"])["speaker_names"] == {"SPEAKER_00": "Validation speaker"}
            start(target, environment, expected_version=args.version)
            record("legacy_api_silent_variant", **outcome, persistent_hashes_match=True,
                   old_app_shutdown="owned IPC immediately after actual legacy launch")
            report["passed"] = True
            return 0
        version = args.version if args.phase == "install" else "9.9.9"
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
            assert state["version"] == args.version and Path(state["project_root"]).resolve() == target
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
            assert registered_version == args.old_version, "Rollback left new installer registration over old binaries"
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
        for process in reversed(list(ui_owned.values())):
            try:
                stop_tree(process)
            except psutil.NoSuchProcess:
                pass
            except Exception as exc:
                cleanup_errors.append(f"Owned UI installer cleanup: {exc}")
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
        preview_reopened = False
        if was_running:
            try:
                start(preview, os.environ.copy())
                preview_reopened = True
            except Exception as exc:
                cleanup_errors.append(f"Preview restart: {exc}")
        report["passed"] = bool(report.get("passed") and not cleanup_errors)
        record("host_restored", user_config_and_audio_hashes_match=True, original_audio_files=len(original_audio),
               preview_reopened=preview_reopened, cleanup_errors=cleanup_errors)
        (root / f"report-{args.phase}.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        assert not cleanup_errors, cleanup_errors
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
