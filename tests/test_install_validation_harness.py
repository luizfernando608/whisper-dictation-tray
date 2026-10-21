"""Ownership tests for opt-in helpers; never inspect or stop host processes."""
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import validate_windows_installation as validation


class InstallValidationHarnessTests(unittest.TestCase):
    def test_stop_tree_retains_the_already_checked_process_identity(self):
        class OwnedProcess:
            constructions = 0

            def __init__(self, pid):
                type(self).constructions += 1
                self.pid = pid
                self.terminate = Mock()

            def is_running(self):
                return True

            def children(self, recursive):
                return []

        owner = OwnedProcess(987654)
        with patch.object(validation.psutil, "Process", OwnedProcess), \
             patch.object(validation.psutil, "wait_procs", return_value=([], [])):
            validation.stop_tree(owner)
        self.assertEqual(OwnedProcess.constructions, 1)
        owner.terminate.assert_called_once()

    def test_stop_ignores_a_process_that_disappears_during_classification(self):
        vanished = Mock()
        vanished.cmdline.side_effect = validation.psutil.NoSuchProcess(987654)
        with patch.object(validation, "processes", return_value=[vanished]), \
             patch.object(validation, "probe") as probe:
            validation.stop(Path("isolated-test-target"))
        probe.assert_not_called()


if __name__ == "__main__":
    unittest.main()
