import hashlib
import importlib.util
import io
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from packaging.tags import Tag

_script = Path(__file__).resolve().parents[1] / "scripts" / "prepare_directml_runtime.py"
_spec = importlib.util.spec_from_file_location("pace_directml_preparation", _script)
module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(module)


class DirectMLPreparationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def wheel(self, extra=None, version="1.24.4"):
        path = self.root / "onnxruntime_directml-1.24.4-cp312-cp312-win_amd64.whl"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("onnxruntime/__init__.py", "pass")
            archive.writestr("onnxruntime/capi/onnxruntime_pybind11_state.pyd", b"fake-extension")
            archive.writestr("onnxruntime/capi/DirectML.dll", b"fake-library")
            archive.writestr("onnxruntime_directml-1.24.4.dist-info/METADATA", f"Name: onnxruntime-directml\nVersion: {version}\n")
            if extra:
                archive.writestr(*extra)
        return path

    def asset(self, path):
        return {"filename": path.name, "packagetype": "bdist_wheel", "size": path.stat().st_size,
                "digests": {"sha256": hashlib.sha256(path.read_bytes()).hexdigest()}, "yanked": False}

    def test_pin_and_python_platform_compatibility_are_enforced(self):
        asset = self.asset(self.wheel())
        metadata = {"info": {"name": module.PACKAGE, "version": module.VERSION}, "urls": [asset]}
        self.assertEqual(module.compatible_asset(metadata, [Tag("cp312", "cp312", "win_amd64")]), asset)
        with self.assertRaises(RuntimeError):
            module.compatible_asset(metadata, [Tag("cp311", "cp311", "win_amd64")])
        metadata["info"]["version"] = "1.25.0"
        with self.assertRaises(RuntimeError):
            module.compatible_asset(metadata, [Tag("cp312", "cp312", "win_amd64")])

    def test_changed_download_rejected_before_extraction(self):
        wheel = self.wheel()
        asset = self.asset(wheel)
        module.verify_wheel(wheel, asset)
        wheel.write_bytes(b"x" * asset["size"])
        with self.assertRaisesRegex(RuntimeError, "SHA256"):
            module.verify_wheel(wheel, asset)

    def test_archive_traversal_absolute_windows_paths_and_symlinks_rejected(self):
        for malicious in ("../outside.txt", "/outside.txt", "C:/outside.txt", "onnxruntime\\..\\outside.txt"):
            with self.subTest(name=malicious):
                wheel = self.wheel((malicious, b"unsafe"))
                target = self.root / "vendor"
                with self.assertRaises(RuntimeError):
                    module.extract_wheel(wheel, target)
                self.assertFalse(target.exists())
        link = zipfile.ZipInfo("onnxruntime/link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        with self.assertRaisesRegex(RuntimeError, "link"):
            module.extract_wheel(self.wheel((link, "../../outside")), self.root / "vendor")

    def test_internal_metadata_pin_rejected_before_writing_runtime(self):
        with self.assertRaisesRegex(RuntimeError, "pin"):
            module.extract_wheel(self.wheel(version="1.20.0"), self.root / "vendor")
        self.assertFalse((self.root / "vendor").exists())

    def test_validated_vendor_is_published_without_touching_python_environment(self):
        wheel = self.wheel()
        staged = self.root / "pending"
        module.extract_wheel(wheel, staged)
        previous = self.root / ".runtime-directml"
        previous.mkdir()
        (previous / "old-file").write_text("previous")
        unrelated = self.root / ".venv"
        unrelated.mkdir()
        (unrelated / "onnxruntime-cpu").write_text("keep")
        destination = module.publish_vendor(staged, self.root)
        self.assertEqual(destination, previous)
        self.assertTrue((destination / "onnxruntime" / "capi" / "DirectML.dll").exists())
        self.assertFalse((destination / "old-file").exists())
        self.assertEqual((unrelated / "onnxruntime-cpu").read_text(), "keep")

    def test_failed_publish_restores_previous_runtime(self):
        previous = self.root / ".runtime-directml"
        previous.mkdir()
        (previous / "old-file").write_text("previous")
        staged = self.root / "pending"
        staged.mkdir()
        original = Path.rename
        def rename(path, target):
            if path == staged:
                raise OSError("file locked")
            return original(path, target)
        with patch.object(Path, "rename", rename):
            with self.assertRaises(OSError):
                module.publish_vendor(staged, self.root)
        self.assertEqual((previous / "old-file").read_text(), "previous")

    def test_cleanup_refuses_workspace_itself_and_external_paths(self):
        with self.assertRaises(RuntimeError):
            module._safe_remove(self.root, self.root)
        outside = self.root.parent / "outside"
        with self.assertRaises(RuntimeError):
            module._safe_remove(outside, self.root)


if __name__ == "__main__":
    unittest.main()
