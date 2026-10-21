import importlib.util
import sys
import types
import unittest
from unittest.mock import Mock, patch

import main
from whisper_dictation._version import __version__


class MainCommandTests(unittest.TestCase):
    def test_update_helper_entrypoint_does_not_load_audio_dependencies(self):
        helper = types.ModuleType("whisper_dictation.update_helper")
        helper.main = Mock(return_value=0)
        spec = importlib.util.spec_from_file_location("pace_isolated_update_entrypoint", main.__file__)
        entrypoint = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"whisper_dictation.audio": None, "whisper_dictation.update_helper": helper}), \
             patch("sys.argv", ["Pace.exe", "--apply-update", "--update-plan", "isolated-plan.json"]):
            spec.loader.exec_module(entrypoint)
            self.assertEqual(entrypoint.main(), 0)
        helper.main.assert_called_once_with(["--update-plan", "isolated-plan.json"])

    def test_old_tray_cannot_silently_swallow_new_recordings_shortcut(self):
        instance = Mock()
        instance.try_acquire.return_value = False
        with patch.object(main, "SingleInstance", return_value=instance), \
             patch.object(main, "probe", return_value=True), \
             patch.object(main, "request", return_value={}), \
             patch.object(main, "send_command") as send, \
             patch("sys.argv", ["main.py", "--recordings"]), \
             patch("tkinter.messagebox.showerror") as error:
            self.assertEqual(main.main(), 1)
        send.assert_not_called()
        error.assert_called_once()

    def test_matching_tray_receives_recordings_shortcut(self):
        instance = Mock()
        instance.try_acquire.return_value = False
        with patch.object(main, "SingleInstance", return_value=instance), \
             patch.object(main, "probe", return_value=True), \
             patch.object(main, "request", return_value={"version": __version__, "project_root": str(main.PROJECT_ROOT)}), \
             patch.object(main, "send_command") as send, \
             patch("sys.argv", ["main.py", "--recordings"]):
            self.assertEqual(main.main(), 0)
        send.assert_called_once_with(main.OPEN_RECORDINGS)


if __name__ == "__main__":
    unittest.main()
