import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from whisper_dictation import update_integration as integration
from whisper_dictation.updater import UpdateError


class MemoryRegistry:
    def __init__(self, value=None):
        self.value = copy.deepcopy(value)

    def read(self):
        return copy.deepcopy(self.value)

    def restore(self, value):
        self.value = copy.deepcopy(value)


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.target = self.root / "application"
        self.target.mkdir()
        # Includes a redirected Desktop and a custom nested Start Menu group.
        self.folders = {"programs": self.root / "programs", "desktop": self.root / "OneDrive" / "Desktop",
                        "startup": self.root / "startup"}
        for folder in self.folders.values():
            folder.mkdir(parents=True)
        self.registry = MemoryRegistry({"values": [["DisplayVersion", "1.4.0", 1, False],
            ["Inno Setup: Icon Group", "Tools\\PACE", 1, False]], "children": {}})
        self.backup = self.root / "backup"
        # A failure here makes every accidental access to the host visible.
        for name in ("known_folders", "WindowsRegistry"):
            mock = patch.object(integration, name, side_effect=AssertionError("Real host access from a test"))
            mock.start()
            self.addCleanup(mock.stop)

    def write(self, root, name, payload):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return path

    def snapshot(self):
        integration.snapshot_integration(self.target, self.backup, folders=self.folders, registry=self.registry)

    def restore(self):
        integration.restore_integration(self.target, self.backup, folders=self.folders, registry=self.registry)

    def test_failed_upgrade_restores_custom_group_legacy_links_registry_and_uninstallers(self):
        original = self.registry.read()
        links = [self.write(self.folders["programs"], "Tools/PACE/PACE.lnk", b"old --settings"),
                 self.write(self.folders["desktop"], "PACE.lnk", b"old main"),
                 self.write(self.folders["desktop"], "Whisper Dictation.lnk", b"legacy desktop"),
                 self.write(self.folders["startup"], "Whisper Dictation.lnk", b"legacy startup")]
        uninstaller = self.write(self.target, "unins000.exe", b"old uninstaller")
        self.write(self.target, "unins000.dat", b"old uninstall data")
        unrelated = self.write(self.folders["desktop"], "Another app.lnk", b"untouched")
        user_file = self.write(self.target, "config.json", b"user configuration")
        saved = {path: path.read_bytes() for path in links + [uninstaller, self.target / "unins000.dat"]}
        self.snapshot()
        for path in links:
            path.unlink()
        self.write(self.folders["programs"], "Tools/PACE/PACE.lnk", b"new --recordings")
        introduced = self.write(self.folders["programs"], "PACE/PACE — Gravações.lnk", b"new recordings")
        new_uninstaller = self.write(self.target, "unins001.msg", b"new translation")
        uninstaller.write_bytes(b"new uninstaller")
        self.registry.value = {"values": [["DisplayVersion", "1.5.0", 1, False]], "children": {}}
        self.restore()
        self.assertEqual(self.registry.value, original)
        for path, payload in saved.items():
            self.assertEqual(path.read_bytes(), payload)
        self.assertFalse(introduced.exists())
        self.assertFalse(new_uninstaller.exists())
        self.assertEqual(unrelated.read_bytes(), b"untouched")
        self.assertEqual(user_file.read_bytes(), b"user configuration")

    def test_absent_registration_and_links_are_removed_after_failed_new_install(self):
        self.registry.value = None
        self.snapshot()
        introduced = self.write(self.folders["desktop"], "PACE.lnk", b"new")
        self.registry.value = {"values": [], "children": {}}
        self.restore()
        self.assertFalse(introduced.exists())
        self.assertIsNone(self.registry.value)

    def test_registry_read_failure_aborts_snapshot(self):
        with patch.object(self.registry, "read", side_effect=PermissionError("denied")):
            with self.assertRaises(PermissionError):
                self.snapshot()
        self.assertFalse(self.backup.exists())

    def test_copy_failure_never_publishes_complete_manifest(self):
        self.write(self.target, "unins000.dat", b"old")
        with patch.object(integration.shutil, "copy2", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.snapshot()
        self.assertFalse((self.backup / "integration.json").exists())

    def test_group_cannot_escape_known_programs_folder(self):
        self.registry.value["values"][1][1] = "..\\external"
        with self.assertRaises(UpdateError):
            self.snapshot()
        self.assertFalse(self.backup.exists())

    def test_missing_backup_is_detected_before_modifying_live_links_or_registry(self):
        live = self.write(self.folders["desktop"], "PACE.lnk", b"old")
        self.snapshot()
        for path in self.backup.glob("*.bin"):
            path.unlink()
        live.write_bytes(b"new")
        original = self.registry.read()
        with self.assertRaises(UpdateError):
            self.restore()
        self.assertEqual(live.read_bytes(), b"new")
        self.assertEqual(self.registry.value, original)

    def test_manifest_cannot_restore_arbitrary_user_file(self):
        self.snapshot()
        path = self.backup / "integration.json"
        manifest = json.loads(path.read_text())
        manifest["files"][0]["name"] = "config.json"
        path.write_text(json.dumps(manifest))
        with self.assertRaises(UpdateError):
            self.restore()

    def test_registry_restore_failure_keeps_backup_and_restores_links_first(self):
        link = self.write(self.folders["desktop"], "PACE.lnk", b"old")
        self.snapshot()
        link.write_bytes(b"new")
        with patch.object(self.registry, "restore", side_effect=PermissionError("registry denied")):
            with self.assertRaisesRegex(UpdateError, "registro da instalação"):
                self.restore()
        self.assertEqual(link.read_bytes(), b"old")
        self.assertTrue((self.backup / "integration.json").is_file())

    def test_symlink_shortcut_cannot_overwrite_an_external_file(self):
        victim = self.write(self.root, "external.txt", b"keep")
        link = self.folders["desktop"] / "PACE.lnk"
        try:
            link.symlink_to(victim)
        except OSError:
            self.skipTest("Creating symlinks is unavailable for this Windows user")
        with self.assertRaises(UpdateError):
            self.snapshot()
        self.assertEqual(victim.read_bytes(), b"keep")

    def test_junction_inside_start_menu_is_rejected_before_snapshot(self):
        group = self.folders["programs"] / "Tools"
        group.mkdir()
        original = Path.lstat
        def attributes(path, *args, **kwargs):
            value = original(path, *args, **kwargs)
            if path == group:
                return SimpleNamespace(st_mode=value.st_mode, st_file_attributes=0x400)
            return value
        with patch.object(Path, "lstat", attributes):
            with self.assertRaisesRegex(UpdateError, "link/junction"):
                self.snapshot()
        self.assertFalse((self.backup / "integration.json").exists())

    def test_new_reparse_point_during_rollback_is_rejected_before_live_changes(self):
        link = self.write(self.folders["desktop"], "PACE.lnk", b"old")
        self.snapshot()
        link.write_bytes(b"new")
        original = Path.lstat
        def attributes(path, *args, **kwargs):
            value = original(path, *args, **kwargs)
            if path == link:
                return SimpleNamespace(st_mode=value.st_mode, st_file_attributes=0x400)
            return value
        with patch.object(Path, "lstat", attributes):
            with self.assertRaisesRegex(UpdateError, "link/junction"):
                self.restore()
        self.assertEqual(link.read_bytes(), b"new")


class RegistryCodecTests(unittest.TestCase):
    def test_binary_multistring_and_integer_types_roundtrip_through_json_without_host_access(self):
        winreg = MagicMock()
        winreg.KEY_READ, winreg.KEY_WRITE = 1, 2
        winreg.QueryInfoKey.return_value = (0, 3, 0)
        values = [("binary", b"\x00\xff", 3), ("multi", ["first", "second"], 7), ("number", 17, 4)]
        winreg.EnumValue.side_effect = values
        with patch.dict("sys.modules", {"winreg": winreg}):
            registry = integration.WindowsRegistry()
            snapshot = json.loads(json.dumps(registry.read()))
            registry.restore(snapshot)
        self.assertEqual([call.args[1:] for call in winreg.SetValueEx.call_args_list],
                         [(name, 0, kind, value) for name, value, kind in values])

    def test_invalid_registry_backup_does_not_delete_original_key(self):
        winreg = MagicMock()
        with patch.dict("sys.modules", {"winreg": winreg}):
            with self.assertRaises(UpdateError):
                integration.WindowsRegistry().restore({"unexpected": []})
        winreg.DeleteKey.assert_not_called()


if __name__ == "__main__":
    unittest.main()
