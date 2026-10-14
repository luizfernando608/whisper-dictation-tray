import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from whisper_dictation.settings import AppConfig
from whisper_dictation.settings_commit import commit_settings
from whisper_dictation.single_instance import CAPABILITIES, SAVE_SETTINGS, SingleInstance, request


class MemoryVault:
    def __init__(self):
        self.values = {("PACE", "groq"): "previous", ("WhisperDictationTray", "groq"): "legacy"}
        self.fail_once = None

    def get_password(self, service, provider):
        return self.values.get((service, provider))

    def set_password(self, service, provider, value):
        if provider == self.fail_once:
            self.fail_once = None
            raise OSError("Synthetic vault failure")
        self.values[service, provider] = value

    def delete_password(self, service, provider):
        if (service, provider) not in self.values:
            raise KeyError(provider)
        del self.values[service, provider]


class SettingsCommitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "config.json"
        AppConfig(max_record_seconds=120).save(self.path)
        self.original_config = self.path.read_bytes()
        self.vault = MemoryVault()
        self.original_keys = dict(self.vault.values)
        self.mock_vault = patch("whisper_dictation.secrets_store._keyring", return_value=self.vault)
        self.mock_vault.start()

    def tearDown(self):
        self.mock_vault.stop()
        self.temp.cleanup()

    def test_validation_rejection_changes_neither_configuration_nor_credentials(self):
        with self.assertRaises(ValueError):
            commit_settings(self.path, AppConfig(hotkey="Ctrl+Shift+R"), {"huggingface": "synthetic"})
        self.assertEqual(self.vault.values, self.original_keys)
        self.assertEqual(self.path.read_bytes(), self.original_config)

    def test_configuration_write_failure_restores_exact_vault_entries(self):
        with patch.object(AppConfig, "save", side_effect=OSError("Synthetic disk failure")):
            with self.assertRaises(RuntimeError):
                commit_settings(self.path, AppConfig(), {"groq": "new", "huggingface": "synthetic"})
        self.assertEqual(self.vault.values, self.original_keys)
        self.assertEqual(self.path.read_bytes(), self.original_config)

    def test_partial_credential_failure_restores_prior_and_legacy_keys(self):
        self.vault.fail_once = "huggingface"
        with self.assertRaises(RuntimeError):
            commit_settings(self.path, AppConfig(), {"groq": "new", "huggingface": "synthetic"})
        self.assertEqual(self.vault.values, self.original_keys)
        self.assertEqual(self.path.read_bytes(), self.original_config)

    def test_success_saves_configuration_and_keys_without_plaintext_key_files(self):
        commit_settings(self.path, AppConfig(), {"groq": "synthetic-secret", "huggingface": "synthetic-hf"})
        self.assertEqual(AppConfig.load(self.path).max_record_seconds, 0)
        self.assertEqual(self.vault.get_password("PACE", "groq"), "synthetic-secret")
        self.assertNotIn("synthetic", self.path.read_text(encoding="utf-8"))
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_loopback_request_roundtrip_handles_unicode_and_fragment_size(self):
        server = SingleInstance(port=0)
        self.assertTrue(server.try_acquire())
        port = server._server.getsockname()[1]
        received = []
        server.register(CAPABILITIES, lambda: {"save_settings": True})
        server.register_request(SAVE_SETTINGS, lambda payload: received.append(payload) or {"ok": True})
        server.start_listener()
        try:
            self.assertTrue(request(CAPABILITIES, port=port)["save_settings"])
            payload = {"command": SAVE_SETTINGS, "config": {"language": "pt"}, "keys": {"huggingface": "á" * 3000}}
            self.assertTrue(request(payload, port=port)["ok"])
            self.assertEqual(received, [payload])
        finally:
            server.stop()

    def test_failing_json_handler_returns_error_without_killing_listener(self):
        import sqlite3
        server = SingleInstance(port=0)
        self.assertTrue(server.try_acquire())
        port = server._server.getsockname()[1]
        def fail(payload):
            raise sqlite3.OperationalError("synthetic disk full")
        server.register_request("fail", fail)
        server.register(CAPABILITIES, lambda: {"save_settings": True})
        server.start_listener()
        try:
            with self.assertLogs("whisper_dictation.ipc", level="ERROR"):
                result = request({"command": "fail"}, port=port)
            self.assertFalse(result["ok"])
            self.assertTrue(server._thread.is_alive())
            self.assertTrue(request(CAPABILITIES, port=port)["save_settings"])
        finally:
            server.stop()


if __name__ == "__main__":
    unittest.main()
