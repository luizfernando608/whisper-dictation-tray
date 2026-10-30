"""Child of the opt-in Windows install harness: real legacy download and launch.

Runs the updater bytecode extracted from the public 1.4 installed EXE using the
source venv. This does not claim to reproduce the frozen bootloader or a physical
button click. TEMP/TMP isolation is supplied only to this child process.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import marshal
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import types
import urllib.request

REPO = "luizfernando608/whisper-dictation-tray"


def sha256(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def public_asset(version):
    url = f"https://api.github.com/repos/{REPO}/releases/tags/v{version}"
    request = urllib.request.Request(url, headers={"User-Agent": "PACE-legacy-install-validation",
                                                  "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        release = json.load(response)
    assert release["tag_name"] == "v" + version and not release["draft"] and not release["prerelease"]
    matches = [asset for asset in release["assets"] if asset["name"] == "Pace_Installer.exe"]
    assert len(matches) == 1, "Expected one official installer asset"
    asset = matches[0]
    assert str(asset.get("digest", "")).startswith("sha256:"), "Public artifact digest is required"
    expected_url = f"https://github.com/{REPO}/releases/download/v{version}/Pace_Installer.exe"
    assert asset["browser_download_url"] == expected_url and int(asset["size"]) > 0
    return {"version": version, "url": expected_url, "sha256": asset["digest"][7:], "size": asset["size"]}


def verify_public_installer(path, version):
    """Read-only preflight: the baseline must be the actual public artifact."""
    asset = public_asset(version)
    assert Path(path).stat().st_size == asset["size"] and sha256(path) == asset["sha256"]
    return asset


def registered_target():
    import winreg
    from validate_windows_installation import UNINSTALL_KEY
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
        return (Path(winreg.QueryValueEx(key, "Inno Setup: App Path")[0]).resolve(),
                winreg.QueryValueEx(key, "DisplayVersion")[0])


def child_main(plan_path):
    from PyInstaller.archive.readers import CArchiveReader
    from validate_windows_installation import stop_tree

    plan_path = plan_path.resolve()
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    target, temporary = Path(plan["target"]).resolve(), Path(plan["temporary"]).resolve()
    report_path = plan_path.with_name("legacy-result.json")
    report = {"passed": False, "simulated_transport": False, "physical_button_clicked": False,
              "variant": "legacy API silent variant",
              "frozen_legacy_bootloader_context": False, "execution_context": "source venv; extracted original updater bytecode",
              "extra_installer_switches": [], "legacy_silent_argument": True, "harness_integrity_guard": True}
    launched = []
    original_popen = subprocess.Popen

    def save():
        partial = report_path.with_suffix(".partial")
        partial.write_text(json.dumps(report, indent=2), encoding="utf-8")
        partial.replace(report_path)

    def record_launch(command, **options):
        # The exact old command and process options are passed through unchanged.
        assert command == [str(temporary / "Pace_Installer.exe"), "/SILENT"]
        assert registered_target() == (target, "1.4.0"), "Installer must default to the isolated old installation"
        process = original_popen(command, **options)
        launched.append(process)
        report.update(installer_pid=process.pid, actual_launcher_command=command,
                      actual_creationflags=options.get("creationflags"), actual_close_fds=options.get("close_fds"))
        launch_path = plan_path.with_name("installer-launched.json")
        partial = launch_path.with_suffix(".partial")
        partial.write_text(json.dumps({"pid": process.pid, "command": command}), encoding="utf-8")
        partial.replace(launch_path)
        save()
        return process

    try:
        assert os.name == "nt" and target.is_dir() and temporary.is_dir()
        assert not target.is_relative_to(temporary) and not temporary.is_relative_to(target)
        assert Path(os.environ["TEMP"]).resolve() == temporary and Path(os.environ["TMP"]).resolve() == temporary
        # Reset only the fresh harness child, never the running parent/application.
        tempfile.tempdir = None
        assert Path(tempfile.gettempdir()).resolve() == temporary
        archive = CArchiveReader(str(target / "Pace.exe"))
        pyz = archive.open_embedded_archive(next(name for name in archive.toc if name.endswith(".pyz")))
        code = pyz.extract("whisper_dictation.updater")
        version_code = pyz.extract("whisper_dictation._version")
        version_module = types.ModuleType("pace_legacy_installed_version")
        exec(version_code, version_module.__dict__)
        assert version_module.__version__ == "1.4.0"
        legacy = types.ModuleType("pace_actual_legacy_updater")
        sys.modules[legacy.__name__] = legacy
        exec(code, legacy.__dict__)
        legacy.__version__ = version_module.__version__
        legacy.subprocess = types.SimpleNamespace(Popen=record_launch)
        report.update(legacy_version=legacy.__version__, legacy_executable_sha256=sha256(target / "Pace.exe"),
                      legacy_code_sha256=hashlib.sha256(marshal.dumps(code)).hexdigest(), temporary=str(temporary))
        asset = public_asset(plan["version"])
        update = legacy.check_for_update(timeout=30)
        assert update and update.version == plan["version"] and update.installer_url == asset["url"]
        started = time.monotonic()
        # No destination override, seeded cache, patched HTTP or new downloader.
        downloaded = Path(legacy.download_installer(update)).resolve()
        assert downloaded == temporary / "Pace_Installer.exe"
        checksum = sha256(downloaded)
        assert downloaded.stat().st_size == asset["size"] and checksum == asset["sha256"]
        report.update(actual_network_download=True, downloaded_url=update.installer_url,
                      downloaded_bytes=downloaded.stat().st_size, downloaded_sha256=checksum,
                      download_seconds=round(time.monotonic() - started, 3))
        save()
        legacy.launch_installer(str(downloaded), silent=True)
        assert len(launched) == 1
        code = launched[0].wait(timeout=600)
        report["installer_exit_code"] = code
        assert code == 0, f"Legacy-launched installer returned {code}"
        assert registered_target() == (target, plan["version"]), "Unexpected post-install registration"
        report.update(passed=True, exact_legacy_launch=True, registration_target_verified=True)
        save()
        return 0
    except Exception as exc:
        report.update(error_type=type(exc).__name__, error=str(exc))
        save()
        return 1
    finally:
        for process in launched:
            stop_tree(process)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--child-plan", required=True, type=Path)
    return child_main(parser.parse_args().child_plan)


if __name__ == "__main__":
    raise SystemExit(main())
