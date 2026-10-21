"""Ownership tests for opt-in helpers; never inspect or stop host processes."""
import ast
import copy
import inspect
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch

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

    def test_both_cleanup_scan_failures_still_restore_host_and_reopen_preview(self):
        # Execute the actual main finally block with every host-facing action
        # replaced. No app, IPC, registry, filesystem or process call is real.
        source = ast.parse(inspect.getsource(validation.main)).body[0]
        final = next(node.finalbody for node in source.body if isinstance(node, ast.Try) and node.finalbody)
        module = ast.fix_missing_locations(ast.Module(body=copy.deepcopy(final), type_ignores=[]))
        events = []
        stop = Mock(side_effect=lambda target: events.append("stop-target"))
        erase = Mock(side_effect=lambda: events.append("erase-registration"))
        run = Mock(side_effect=lambda *a, **k: events.append("restore-registration"))
        copy_link = Mock(side_effect=lambda *a: events.append("restore-link"))
        start = Mock(side_effect=lambda *a: events.append("reopen-preview"))
        record = Mock()
        output = MagicMock()
        scanner = Mock(side_effect=OSError("synthetic enumeration failure"))
        report = {"passed": True}
        scope = {**validation.__dict__, "args": SimpleNamespace(phase="restart-ui"), "report": report,
                 "helper": None, "stop_tree": Mock(), "cleanup_errors": [],
                 "environment": {"LOCALAPPDATA": "synthetic-appdata"}, "ui_owned": {},
                 "observe_restart_helpers": scanner, "targets": [Path("synthetic-target")], "stop": stop,
                 "erase_registration": erase, "exported": True, "registry": Path("synthetic-backup.reg"),
                 "subprocess": SimpleNamespace(run=run), "shutil": SimpleNamespace(copy2=copy_link),
                 "saved_links": [{"path": "synthetic.lnk", "existed": True, "saved": "backup.lnk"}],
                 "original_hashes": {"config.json": "unchanged"}, "original_audio": {"audio.wav": "unchanged"},
                 "digest": Mock(return_value="unchanged"), "was_running": True, "preview": Path("synthetic-preview"),
                 "start": start, "os": SimpleNamespace(environ={}), "record": record, "root": output}
        with self.assertRaises(AssertionError):
            exec(compile(module, "<actual harness finally, isolated actions>", "exec"), scope)
        self.assertEqual(scanner.call_count, 2)
        self.assertEqual(events, ["stop-target", "erase-registration", "restore-registration", "restore-link", "reopen-preview"])
        self.assertFalse(report["passed"])
        fields = record.call_args.kwargs
        self.assertEqual(len(fields["cleanup_errors"]), 2)
        self.assertTrue(fields["preview_reopened"])
        output.__truediv__.return_value.write_text.assert_called_once()


if __name__ == "__main__":
    unittest.main()
