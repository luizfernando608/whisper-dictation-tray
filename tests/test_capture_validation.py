"""Synthetic tests for the opt-in validator; never start IPC or audio devices."""
import copy
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import validate_installed_capture as validation


def snapshot(**fields):
    return {"active": True, "paused": False, "finalizing": False, "recording_id": "synthetic",
            "duration": 13.0, "updated_at": 123.0, "levels": [.1, .2], **fields}


class CaptureValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "runtime.json"
        self.report, self.saved = {}, []
        self.reader = validation.CaptureRuntime(
            self.path, "synthetic", self.report, lambda: self.saved.append(copy.deepcopy(self.report)))

    def tearDown(self):
        self.temp.cleanup()

    def write(self, state):
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state), encoding="utf-8")
        os.replace(temporary, self.path)

    def test_transient_read_error_retains_valid_state_and_evidence(self):
        with patch.object(validation, "read_runtime", side_effect=[snapshot(), PermissionError(13, "synthetic lock"), snapshot(duration=14)]):
            self.reader.read()
            self.assertEqual(self.reader.read()["duration"], 14)
        evidence = self.report["capture_runtime"]
        self.assertEqual(evidence["attempts"], 3)
        failure = evidence["read_errors"][0]
        self.assertEqual((failure["error_type"], failure["attempt"], failure["retry_attempt"]), ("PermissionError", 2, 1))
        self.assertIn("at_utc", failure)
        self.assertEqual(self.saved[0]["capture_runtime"]["last_valid"]["state"]["duration"], 13)
        self.assertNotIn("failure", evidence)

    def test_valid_inactive_is_saved_and_never_retried(self):
        inactive = snapshot(active=False, recording_id=None, finalizing=True, duration=0)
        with patch.object(validation, "read_runtime", side_effect=[inactive, snapshot()]) as read:
            with self.assertRaises(AssertionError):
                self.reader.read()
        self.assertEqual(read.call_count, 1)
        self.assertEqual(self.saved[-1]["capture_runtime"]["failure_snapshot"]["state"], inactive)
        self.assertEqual(self.report["capture_runtime"]["read_errors"], [])

    def test_wrong_recording_after_unavailable_snapshot_still_fails_immediately(self):
        wrong = snapshot(recording_id="other-synthetic")
        with patch.object(validation, "read_runtime", side_effect=[PermissionError(13, "synthetic lock"), wrong, snapshot()]) as read:
            with self.assertRaises(AssertionError):
                self.reader.read()
        self.assertEqual(read.call_count, 2)
        self.assertEqual(self.report["capture_runtime"]["failure_snapshot"]["state"], wrong)

    def test_persistent_error_times_out_as_unknown_within_two_seconds(self):
        now = [100.0]
        with patch.object(validation.time, "monotonic", side_effect=lambda: now[0]), \
             patch.object(validation.time, "sleep", side_effect=lambda duration: now.__setitem__(0, now[0] + duration)), \
             patch.object(validation, "read_runtime", side_effect=PermissionError(13, "synthetic lock")):
            with self.assertRaisesRegex(TimeoutError, "state unknown"):
                self.reader.read()
        self.assertLessEqual(now[0], 102.0)
        evidence = self.saved[-1]["capture_runtime"]
        self.assertEqual(evidence["failure"], "runtime_unavailable")
        self.assertGreater(len(evidence["read_errors"]), 1)
        self.assertNotIn("failure_snapshot", evidence)

    def test_invalid_json_and_schema_are_recorded_before_recovery(self):
        for raw, error_type in (("{", "JSONDecodeError"), ("[]", "RuntimeSchemaError"),
                                (json.dumps(snapshot(duration=float("nan"))), "RuntimeSchemaError")):
            with self.subTest(raw=raw):
                self.path.write_text(raw, encoding="utf-8")
                def recover(_seconds):
                    self.write(snapshot())
                with patch.object(validation.time, "sleep", side_effect=recover):
                    self.assertTrue(self.reader.read()["active"])
                self.assertEqual(self.report["capture_runtime"]["read_errors"][-1]["error_type"], error_type)

    def test_schema_rejects_incomplete_or_malformed_capture_telemetry(self):
        missing = snapshot()
        del missing["recording_id"]
        for state in (missing, snapshot(active=1), snapshot(duration=-1), snapshot(updated_at=float("inf")),
                      snapshot(levels=[0]), snapshot(levels=[0, None]), snapshot(recording_id=None)):
            with self.subTest(state=state):
                self.write(state)
                with self.assertRaises(validation.RuntimeSchemaError):
                    validation.read_runtime(self.path)

    def test_pause_wait_does_not_swallow_a_real_stop(self):
        with patch.object(validation, "read_runtime", side_effect=[snapshot(active=False, recording_id=None), snapshot(paused=True)]) as read:
            with self.assertRaises(AssertionError):
                self.reader.wait_paused(True)
        self.assertEqual(read.call_count, 1)

    @unittest.skipUnless(os.name == "nt", "Windows sharing semantics")
    def test_real_windows_exclusive_lock_reproduces_old_false_stop_and_new_recovery(self):
        self.write(snapshot())
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                      ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.CreateFileW(str(self.path), 0x80000000, 0, None, 3, 0x80, None)
        self.assertNotEqual(handle, ctypes.c_void_p(-1).value)
        released = threading.Event()
        def release():
            time.sleep(.25)
            kernel.CloseHandle(handle)
            released.set()
        unlock = threading.Thread(target=release)
        unlock.start()
        try:
            # Original code mistakes an unreadable file for an explicit stop.
            self.assertEqual(validation.read_json(self.path), {})
            self.assertTrue(self.reader.read()["active"])
        finally:
            unlock.join(3)
            if not released.is_set():
                kernel.CloseHandle(handle)
        evidence = self.report["capture_runtime"]
        self.assertTrue(evidence["last_valid"]["state"]["active"])
        self.assertEqual(evidence["read_errors"][0]["error_type"], "PermissionError")
        # CPython's text open may expose CRT errno=13 without winerror=32.
        self.assertEqual(evidence["read_errors"][0]["errno"], 13)
        if "winerror" in evidence["read_errors"][0]:
            self.assertEqual(evidence["read_errors"][0]["winerror"], 32)
        self.assertNotIn("failure", evidence)


if __name__ == "__main__":
    unittest.main()
