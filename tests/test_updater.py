import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from whisper_dictation import update_helper, updater


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.local = patch.dict(os.environ, {"LOCALAPPDATA": str(self.root / "local")})
        self.local.start()
        self.addCleanup(self.local.stop)
        # These tests simulate program binaries only. Never consult or change
        # the actual user's shortcuts/registry from the helper test fixtures.
        for name in ("snapshot_integration", "restore_integration"):
            mocked = patch.object(update_helper, name)
            setattr(self, name, mocked.start())
            self.addCleanup(mocked.stop)
        self.payload = b"a complete fake installer"
        self.version = "9.0.0"
        self.update = updater.UpdateInfo(self.version, "v9.0.0",
            f"https://github.com/{updater.REPO}/releases/download/v9.0.0/Pace_Installer.exe",
            sha256=hashlib.sha256(self.payload).hexdigest(), size=len(self.payload))

    def release(self):
        return {"tag_name": self.update.tag, "assets": [{"name": "Pace_Installer.exe",
            "browser_download_url": self.update.installer_url,
            "digest": "sha256:" + self.update.sha256, "size": self.update.size}]}

    def test_network_failure_is_not_reported_as_current_version(self):
        with patch.object(updater.urllib.request, "urlopen", side_effect=OSError("offline")):
            with self.assertRaises(updater.UpdateError):
                updater.check_for_update()

    def test_exact_asset_and_required_digest(self):
        data = self.release()
        data["assets"].insert(0, {"name": "unrelated_installer.exe", "browser_download_url": "https://evil.invalid"})
        with patch.object(updater.urllib.request, "urlopen", return_value=io.BytesIO(json.dumps(data).encode())):
            self.assertEqual(updater.check_for_update(), self.update)
        del data["assets"][1]["digest"]
        with patch.object(updater.urllib.request, "urlopen", return_value=io.BytesIO(json.dumps(data).encode())):
            with self.assertRaises(updater.UpdateError):
                updater.check_for_update()

    def test_current_version_requires_successful_valid_response(self):
        with patch.object(updater.urllib.request, "urlopen", return_value=io.BytesIO(json.dumps({"tag_name": "v" + updater.current_version()}).encode())):
            self.assertIsNone(updater.check_for_update())
        with patch.object(updater.urllib.request, "urlopen", return_value=io.BytesIO(b"{}")):
            with self.assertRaises(updater.UpdateError):
                updater.check_for_update()

    def test_partial_download_never_becomes_ready_and_can_retry(self):
        with patch.object(updater.urllib.request, "urlopen", return_value=io.BytesIO(self.payload[:-1])):
            with self.assertRaises(updater.UpdateError):
                updater.prepare_update(self.update)
        directory = updater.update_cache_root() / self.version
        self.assertEqual(list(directory.iterdir()), [])
        with patch.object(updater.urllib.request, "urlopen", return_value=io.BytesIO(self.payload)):
            destination = Path(updater.prepare_update(self.update))
        self.assertEqual(destination.read_bytes(), self.payload)
        with patch.object(updater.urllib.request, "urlopen") as network:
            self.assertEqual(updater.prepare_update(self.update), str(destination))
            network.assert_not_called()

    def test_hash_mismatch_blocks_preparation(self):
        with patch.object(updater.urllib.request, "urlopen", return_value=io.BytesIO(b"x" * self.update.size)):
            with self.assertRaisesRegex(updater.UpdateError, "SHA256"):
                updater.prepare_update(self.update)

    def test_corrupt_cache_is_validated_again_before_use(self):
        directory = updater.update_cache_root() / self.version
        directory.mkdir(parents=True)
        (directory / "Pace_Installer.exe").write_bytes(b"x" * self.update.size)
        with patch.object(updater.urllib.request, "urlopen", return_value=io.BytesIO(self.payload)) as network:
            self.assertEqual(Path(updater.prepare_update(self.update)).read_bytes(), self.payload)
            network.assert_called_once()

    def test_untrusted_url_and_path_version_rejected(self):
        for version, url in [(self.version, "http://github.com/fake"), ("../../target", self.update.installer_url)]:
            update = updater.UpdateInfo(version, "v" + version, url, sha256=self.update.sha256, size=self.update.size)
            with self.assertRaises(updater.UpdateError):
                updater.prepare_update(update)

    def make_plan(self):
        target = self.root / "installed"
        target.mkdir()
        (target / "Pace.exe").write_bytes(b"old binary")
        (target / "_internal").mkdir()
        (target / "_internal" / "runtime.dll").write_bytes(b"old runtime")
        for name in ("config.json", "models", "recordings"):
            (target / name).write_bytes(b"persistent data")
        installer = updater.update_cache_root() / self.version / "Pace_Installer.exe"
        installer.parent.mkdir(parents=True)
        installer.write_bytes(self.payload)
        staging = updater.update_cache_root() / "runs" / "fake-run"
        staging.mkdir(parents=True)
        plan = {"parent_pid": 99999, "target": str(target), "installer": str(installer),
            "version": self.version, "sha256": self.update.sha256, "size": self.update.size,
            "token": "fake-token", "receipt": str(staging / "confirmed.json")}
        path = staging / "plan.json"
        path.write_text(json.dumps(plan), encoding="utf-8")
        return target, path, plan

    def test_helper_waits_before_install_and_uses_correct_target_silently(self):
        target, path, plan = self.make_plan()
        events = []
        def install(command, **options):
            events.append("install")
            self.assertIn("/VERYSILENT", command)
            self.assertIn("/SUPPRESSMSGBOXES", command)
            self.assertIn("/NORESTART", command)
            self.assertIn("/NOCLOSEAPPLICATIONS", command)
            self.assertIn(f"/DIR={target}", command)
            return MagicMock(returncode=0)
        process = MagicMock(pid=88888)
        with patch.object(update_helper, "wait_for_process_exit", side_effect=lambda pid: events.append("exit")), \
             patch.object(update_helper.subprocess, "run", side_effect=install), \
             patch.object(update_helper, "_launch", return_value=process), \
             patch.object(update_helper, "_wait_confirmation", return_value=True):
            self.assertEqual(update_helper.apply_update(path), 0)
        self.assertEqual(events, ["exit", "install"])
        self.assertEqual(json.loads((path.parent / "result.json").read_text())["status"], "updated")
        self.snapshot_integration.assert_called_once()
        self.restore_integration.assert_not_called()

    def test_failed_installer_restores_only_program_binaries(self):
        target, path, plan = self.make_plan()
        def fail(command, **options):
            (target / "Pace.exe").write_bytes(b"bad binary")
            (target / "_internal" / "runtime.dll").write_bytes(b"bad runtime")
            return MagicMock(returncode=5)
        with patch.object(update_helper, "wait_for_process_exit"), \
             patch.object(update_helper.subprocess, "run", side_effect=fail), \
             patch.object(update_helper, "_launch") as launch:
            self.assertEqual(update_helper.apply_update(path), 1)
            launch.assert_called_once_with(target)
        self.assertEqual((target / "Pace.exe").read_bytes(), b"old binary")
        self.assertEqual((target / "_internal" / "runtime.dll").read_bytes(), b"old runtime")
        for name in ("config.json", "models", "recordings"):
            self.assertEqual((target / name).read_bytes(), b"persistent data")

    def test_failed_confirmation_rolls_back_and_stops_only_launched_process(self):
        target, path, plan = self.make_plan()
        process = MagicMock(pid=88888)
        process.poll.return_value = None
        with patch.object(update_helper, "wait_for_process_exit"), \
             patch.object(update_helper.subprocess, "run", return_value=MagicMock(returncode=0)), \
             patch.object(update_helper, "_launch", return_value=process), \
             patch.object(update_helper, "_wait_confirmation", return_value=False):
            self.assertEqual(update_helper.apply_update(path), 1)
        process.terminate.assert_called_once()
        process.wait.assert_called_once_with(timeout=15)
        self.assertEqual((target / "Pace.exe").read_bytes(), b"old binary")

    def test_unsafe_or_corrupt_plan_cannot_run_installer(self):
        target, path, plan = self.make_plan()
        (Path(plan["installer"])).write_bytes(b"corrupt")
        with patch.object(update_helper.subprocess, "run") as install, patch.object(update_helper, "wait_for_process_exit") as wait:
            self.assertEqual(update_helper.apply_update(path), 1)
            install.assert_not_called()
            wait.assert_not_called()

    def test_parent_exit_timeout_does_not_touch_program_files(self):
        target, path, plan = self.make_plan()
        with patch.object(update_helper, "wait_for_process_exit", side_effect=updater.UpdateError("still running")), \
             patch.object(update_helper.subprocess, "run") as install:
            self.assertEqual(update_helper.apply_update(path), 1)
            install.assert_not_called()
        self.assertEqual((target / "Pace.exe").read_bytes(), b"old binary")
        self.assertFalse((path.parent / "backup").exists())

    def test_receipt_requires_expected_pid_version_target_and_token(self):
        target, path, plan = self.make_plan()
        receipt = Path(plan["receipt"])
        process = MagicMock(pid=88888)
        process.poll.return_value = None
        payload = {"version": plan["version"], "token": plan["token"], "pid": process.pid, "target": str(target)}
        receipt.write_text(json.dumps(payload))
        self.assertTrue(update_helper._wait_confirmation(process, plan, timeout=0.5))
        payload["version"] = "1.0.0"
        receipt.write_text(json.dumps(payload))
        self.assertFalse(update_helper._wait_confirmation(process, plan, timeout=0.5))

    def test_confirmation_writes_version_and_consumes_environment(self):
        target, path, plan = self.make_plan()
        with patch.dict(os.environ, {"PACE_UPDATE_CONFIRMATION": plan["receipt"], "PACE_UPDATE_TOKEN": plan["token"]}):
            self.assertTrue(updater.confirm_running_version(target))
            self.assertNotIn("PACE_UPDATE_CONFIRMATION", os.environ)
        payload = json.loads(Path(plan["receipt"]).read_text())
        self.assertEqual(payload["version"], updater.current_version())
        self.assertEqual(payload["pid"], os.getpid())

    def test_source_mode_stages_helper_without_closing_process(self):
        target, path, plan = self.make_plan()
        with patch.object(updater.subprocess, "Popen", return_value=MagicMock(pid=88888)) as launch:
            self.assertEqual(updater.restart_to_update(self.update, target), 88888)
        command = launch.call_args.args[0]
        self.assertIn("--apply-update", command)
        self.assertIn("--update-plan", command)
        generated_plan = Path(command[-1])
        self.assertNotIn(target, generated_plan.parents)
        self.assertEqual(json.loads(generated_plan.read_text())["parent_pid"], os.getpid())

    def test_frozen_helper_copies_runtime_outside_installation_without_user_data(self):
        target, path, plan = self.make_plan()
        with patch.object(updater.sys, "frozen", True, create=True), \
             patch.object(updater.sys, "executable", str(target / "Pace.exe")), \
             patch.object(updater.subprocess, "Popen", return_value=MagicMock(pid=88888)) as launch:
            self.assertEqual(updater.restart_to_update(self.update, target), 88888)
        helper = Path(launch.call_args.args[0][0])
        self.assertEqual(helper.name, "PaceUpdateHelper.exe")
        self.assertNotIn(target, helper.parents)
        self.assertEqual(helper.read_bytes(), b"old binary")
        self.assertEqual((helper.parent / "_internal" / "runtime.dll").read_bytes(), b"old runtime")
        for name in ("config.json", "models", "recordings"):
            self.assertFalse((helper.parent / name).exists())

    def test_snapshot_failure_reopens_original_without_running_installer(self):
        target, path, plan = self.make_plan()
        with patch.object(update_helper, "wait_for_process_exit"), \
             patch.object(update_helper, "_snapshot", side_effect=OSError("disk full")), \
             patch.object(update_helper.subprocess, "run") as install, \
             patch.object(update_helper, "_launch") as launch:
            self.assertEqual(update_helper.apply_update(path), 1)
            install.assert_not_called()
            launch.assert_called_once_with(target)

    def test_integration_snapshot_failure_aborts_before_installation(self):
        target, path, plan = self.make_plan()
        self.snapshot_integration.side_effect = OSError("shortcut backup denied")
        with patch.object(update_helper, "wait_for_process_exit"), \
             patch.object(update_helper.subprocess, "run") as install, \
             patch.object(update_helper, "_launch") as launch:
            self.assertEqual(update_helper.apply_update(path), 1)
        install.assert_not_called()
        self.restore_integration.assert_not_called()
        launch.assert_called_once_with(target)
        self.assertEqual((target / "Pace.exe").read_bytes(), b"old binary")

    def test_integration_is_restored_on_failure_but_not_after_success(self):
        target, path, plan = self.make_plan()
        with patch.object(update_helper, "wait_for_process_exit"), \
             patch.object(update_helper.subprocess, "run", return_value=MagicMock(returncode=5)), \
             patch.object(update_helper, "_launch"):
            self.assertEqual(update_helper.apply_update(path), 1)
        self.snapshot_integration.assert_called_once_with(target, path.parent / "backup" / "integration")
        self.restore_integration.assert_called_once_with(target, path.parent / "backup" / "integration")

    def test_integration_rollback_error_is_reported_and_backup_is_retained(self):
        target, path, plan = self.make_plan()
        self.restore_integration.side_effect = OSError("registry denied")
        with patch.object(update_helper, "wait_for_process_exit"), \
             patch.object(update_helper.subprocess, "run", return_value=MagicMock(returncode=5)), \
             patch.object(update_helper, "_launch") as launch:
            self.assertEqual(update_helper.apply_update(path), 1)
        outcome = json.loads((path.parent / "result.json").read_text())
        self.assertIn("registry denied", outcome["rollback_error"])
        self.assertTrue((path.parent / "backup" / "Pace.exe").is_file())
        launch.assert_called_once_with(target)

    def test_integration_and_relaunch_failures_are_both_reported(self):
        target, path, plan = self.make_plan()
        self.restore_integration.side_effect = OSError("registry denied")
        with patch.object(update_helper, "wait_for_process_exit"), \
             patch.object(update_helper.subprocess, "run", return_value=MagicMock(returncode=5)), \
             patch.object(update_helper, "_launch", side_effect=OSError("launch denied")):
            self.assertEqual(update_helper.apply_update(path), 1)
        outcome = json.loads((path.parent / "result.json").read_text())
        self.assertIn("registry denied", outcome["rollback_error"])
        self.assertIn("launch denied", outcome["rollback_error"])
        self.assertEqual(outcome["status"], "failed")

    def test_binary_restore_failure_does_not_launch_incomplete_application(self):
        target, path, plan = self.make_plan()
        with patch.object(update_helper, "wait_for_process_exit"), \
             patch.object(update_helper.subprocess, "run", return_value=MagicMock(returncode=5)), \
             patch.object(update_helper, "_restore", side_effect=OSError("runtime locked")), \
             patch.object(update_helper, "_launch") as launch:
            self.assertEqual(update_helper.apply_update(path), 1)
        launch.assert_not_called()
        self.restore_integration.assert_not_called()
        outcome = json.loads((path.parent / "result.json").read_text())
        self.assertIn("runtime locked", outcome["rollback_error"])

    def test_receipt_outside_cache_is_not_written(self):
        invalid = self.root / "confirmed.json"
        with patch.dict(os.environ, {"PACE_UPDATE_CONFIRMATION": str(invalid), "PACE_UPDATE_TOKEN": "token"}):
            self.assertFalse(updater.confirm_running_version(self.root))
        self.assertFalse(invalid.exists())

    def test_plan_with_absolute_version_cannot_escape_cache(self):
        target, path, plan = self.make_plan()
        plan["version"] = str(self.root / "arbitrary")
        path.write_text(json.dumps(plan))
        with self.assertRaises(updater.UpdateError):
            update_helper._read_plan(path)

    def test_cache_cleanup_keeps_latest_backup_and_three_failed_logs(self):
        runs = updater.update_cache_root() / "runs"
        successes, failures = [], []
        for number in range(8):
            run = runs / f"{number:032x}"
            run.mkdir(parents=True)
            status = "updated" if number < 3 else "failed"
            (successes if status == "updated" else failures).append(run)
            (run / "result.json").write_text(json.dumps({"status": status}))
            os.utime(run / "result.json", ns=(number + 1, number + 1))
            (run / "helper").mkdir()
            (run / "helper" / "PaceUpdateHelper.exe").write_bytes(b"runtime copy")
            (run / "backup").mkdir()
            (run / "backup" / "Pace.exe").write_bytes(b"recoverable backup")
            (run / "update.log").write_text("diagnostic")
        target = self.root / "installed"
        target.mkdir()
        (target / "Pace.exe").write_bytes(b"app")
        (target / "config.json").write_bytes(b"user configuration")
        updater.cleanup_update_cache()
        self.assertEqual([run for run in successes if run.exists()], [successes[-1]])
        self.assertEqual([run for run in failures if run.exists()], failures[-3:])
        self.assertTrue((successes[-1] / "backup" / "Pace.exe").exists())
        for run in [successes[-1]] + failures[-3:]:
            self.assertFalse((run / "helper").exists())
            self.assertTrue((run / "update.log").exists())
        self.assertEqual((target / "Pace.exe").read_bytes(), b"app")
        self.assertEqual((target / "config.json").read_bytes(), b"user configuration")
        self.assertFalse(updater._remove_cached_path(target, updater.update_cache_root()))

    def test_cleanup_leaves_current_helper_and_pending_runs_untouched(self):
        runs = updater.update_cache_root() / "runs"
        current, pending = runs / ("a" * 32), runs / ("b" * 32)
        for run in (current, pending):
            (run / "helper").mkdir(parents=True)
            (run / "helper" / "PaceUpdateHelper.exe").write_bytes(b"in use")
        (current / "result.json").write_text(json.dumps({"status": "updated"}))
        updater.cleanup_update_cache(exclude_run=current)
        self.assertTrue((current / "helper" / "PaceUpdateHelper.exe").exists())
        self.assertTrue((pending / "helper" / "PaceUpdateHelper.exe").exists())

    def test_cleanup_permission_failure_does_not_prevent_normal_startup(self):
        (updater.update_cache_root() / "runs").mkdir(parents=True)
        with patch.object(Path, "iterdir", side_effect=PermissionError("cache locked")):
            self.assertFalse(updater.confirm_running_version(self.root))


if __name__ == "__main__":
    unittest.main()
