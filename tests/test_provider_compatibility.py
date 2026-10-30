from __future__ import annotations

import base64
import io
import json
import logging
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
import numpy as np
from google import genai
from groq import Groq
from openai import OpenAI

from whisper_dictation import transcription as module
from whisper_dictation.audio import CapturedAudio
from whisper_dictation.providers import PROVIDERS, models_for
from whisper_dictation.settings import AppConfig


class ProviderCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.audio = self.root / "sample.wav"
        self.audio.write_bytes(b"RIFFcontract-audio")
        self.config = AppConfig(language="pt")
        self.transcriber = module.WhisperTranscriber(self.config, self.root, logging.getLogger(__name__))
        self.key = patch.object(module.secrets_store, "get_api_key", return_value="contract-test-key")
        self.key.start()
        self.addCleanup(self.key.stop)

    def openai(self, response=None):
        self.requests = []
        def handle(request):
            self.requests.append(request)
            return httpx.Response(200, json=response if response is not None else {"text": "Olá mundo."})
        self.http = httpx.Client(transport=httpx.MockTransport(handle))
        client = OpenAI(api_key="contract-test-key", http_client=self.http, max_retries=0)
        self.transcriber._openai_client = client
        return client

    def gemini(self, response=None):
        self.requests = []
        def handle(request):
            self.requests.append(request)
            body = response if response is not None else {
                "status": "completed", "steps": [{"type": "model_output", "content": [
                    {"type": "text", "text": "Olá mundo."}]}]}
            return httpx.Response(200, json=body)
        self.http = httpx.Client(transport=httpx.MockTransport(handle))
        client = genai.Client(api_key="contract-test-key", http_options={"httpx_client": self.http})
        self.addCleanup(self.http.close)
        self.addCleanup(client.close)
        close = patch.object(client, "close", wraps=client.close)
        self.gemini_close = close.start()
        self.addCleanup(close.stop)
        self.transcriber._get_gemini_client = Mock(return_value=client)
        return client

    def gemini_input(self):
        body = json.loads(self.requests[0].content)
        self.assertEqual([step["type"] for step in body["input"]], ["user_input"])
        return body["input"][0]["content"]

    def test_catalog_defaults_and_saved_custom_ids(self):
        self.assertEqual(AppConfig().openai_model, "gpt-transcribe")
        self.assertEqual(AppConfig().gemini_model, "gemini-3.8-flash")
        for provider in ("openai", "gemini"):
            self.assertEqual(PROVIDERS[provider].default_model, getattr(AppConfig(), provider + "_model"))
        self.assertNotIn("distil-whisper-large-v3-en", models_for("groq"))
        self.assertNotIn("gemini-3.5-flash", models_for("gemini"))
        self.assertNotIn("gemini-2.5-flash", models_for("gemini"))
        self.assertIn("gemini-3.5-transcribe", models_for("gemini"))
        config = AppConfig(**AppConfig._sanitize({"openai_model": "private-transcriber", "gemini_model": "gemini-2.5-flash"}))
        self.assertEqual(config.openai_model, "private-transcriber")
        self.assertEqual(config.gemini_model, "gemini-2.5-flash")

    def test_openai_current_serializes_plural_languages_and_closes(self):
        self.config.openai_model = "gpt-transcribe"
        self.openai()
        self.assertEqual(self.transcriber._transcribe_with_openai(self.audio), "Olá mundo.")
        request = self.requests[0]
        self.assertEqual(request.url.path, "/v1/audio/transcriptions")
        self.assertIn(b'name="languages[]"', request.content)
        self.assertNotIn(b'name="language"', request.content)
        self.assertIn(b'filename="sample.wav"', request.content)
        self.assertTrue(self.http.is_closed)

    def test_openai_legacy_and_custom_preserve_language_contract(self):
        for model in ("gpt-4o-transcribe", "gpt-4o-mini-transcribe", "whisper-1", "private-transcriber"):
            with self.subTest(model=model):
                self.config.openai_model = model
                self.openai()
                self.transcriber._transcribe_with_openai(self.audio)
                self.assertIn(b'name="language"', self.requests[0].content)
                self.assertNotIn(b'name="languages[]"', self.requests[0].content)

    def test_openai_auto_does_not_send_either_language_hint(self):
        self.config.language = "auto"
        self.openai()
        self.transcriber._transcribe_with_openai(self.audio)
        self.assertNotIn(b'name="language"', self.requests[0].content)
        self.assertNotIn(b'name="languages[]"', self.requests[0].content)

    def test_openai_diarize_uses_server_chunking(self):
        self.config.openai_model = "gpt-4o-transcribe-diarize"
        self.openai()
        self.transcriber._transcribe_with_openai(self.audio, 80)
        self.assertIn(b'name="chunking_strategy"', self.requests[0].content)
        self.assertIn(b"auto", self.requests[0].content)

    def test_empty_openai_response_is_failure_and_closes(self):
        self.openai({"text": "  "})
        with self.assertRaisesRegex(RuntimeError, "vazia"):
            self.transcriber._transcribe_with_openai(self.audio)
        self.assertTrue(self.http.is_closed)

    def test_groq_real_sdk_serializes_existing_contract(self):
        requests = []
        def handle(request):
            requests.append(request)
            return httpx.Response(200, json={"text": "Olá mundo."})
        client = Groq(api_key="contract-test-key", http_client=httpx.Client(transport=httpx.MockTransport(handle)))
        self.addCleanup(client.close)
        self.transcriber._groq_client = client
        self.assertEqual(self.transcriber._transcribe_with_groq(self.audio), "Olá mundo.")
        self.assertEqual(requests[0].url.path, "/openai/v1/audio/transcriptions")
        self.assertIn(b'name="language"', requests[0].content)

    def test_empty_groq_response_is_failure(self):
        client = Groq(api_key="contract-test-key", http_client=httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"text": ""}))))
        self.addCleanup(client.close)
        self.transcriber._groq_client = client
        with self.assertRaisesRegex(RuntimeError, "vazia"):
            self.transcriber._transcribe_with_groq(self.audio)

    def test_consecutive_groq_dictations_reuse_client_and_transport(self):
        requests = []
        def handle(request):
            requests.append(request)
            return httpx.Response(200, json={"text": "Olá."})
        http = httpx.Client(transport=httpx.MockTransport(handle))
        client = Groq(api_key="contract-test-key", http_client=http)
        self.addCleanup(client.close)
        self.transcriber._groq_client = client
        with patch("groq.Groq", side_effect=AssertionError("client must be reused")):
            self.assertEqual(self.transcriber._transcribe_with_groq(self.audio), "Olá.")
            self.assertIs(self.transcriber._groq_client, client)
            self.assertFalse(http.is_closed)
            self.assertEqual(self.transcriber._transcribe_with_groq(self.audio), "Olá.")
        self.assertEqual(len(requests), 2)
        self.assertTrue(all(request.url.path == "/openai/v1/audio/transcriptions" for request in requests))
        self.assertIs(self.transcriber._groq_client, client)
        self.assertFalse(http.is_closed)

    def test_gemini_modern_http_body_contains_base64_and_stateless_prompt(self):
        self.config.gemini_model = "gemini-3.8-flash"
        self.gemini()
        self.assertEqual(self.transcriber._transcribe_with_gemini(self.audio), "Olá mundo.")
        self.assertEqual(self.requests[0].url.path, "/v1beta/interactions")
        body = json.loads(self.requests[0].content)
        self.assertFalse(body["store"])
        content = self.gemini_input()
        audio = next(part for part in content if part["type"] == "audio")
        self.assertEqual(base64.b64decode(audio["data"]), self.audio.read_bytes())
        self.assertEqual(audio["mime_type"], "audio/wav")
        self.assertTrue(any(part["type"] == "text" for part in content))
        self.assertNotIn("thinking_level", body.get("generation_config", {}))
        self.gemini_close.assert_called_once()

    def test_gemini_dedicated_transcriber_has_own_contract(self):
        self.config.gemini_model = "gemini-3.5-transcribe"
        self.gemini()
        self.transcriber._transcribe_with_gemini(self.audio)
        body = json.loads(self.requests[0].content)
        self.assertEqual([p["type"] for p in self.gemini_input()], ["audio"])
        self.assertEqual(body["generation_config"], {"transcription_config": {
            "language_codes": ["pt-BR"], "mode": {"type": "verbatim"}}})

    def test_all_suggested_gemini_ids_serialize_with_the_real_sdk(self):
        for model in models_for("gemini"):
            with self.subTest(model=model):
                self.config.gemini_model = model
                self.gemini()
                self.transcriber._transcribe_with_gemini(self.audio)
                self.assertEqual(json.loads(self.requests[0].content)["model"], model)

    def test_dedicated_gemini_auto_and_regional_language_hints(self):
        self.config.gemini_model = "gemini-3.5-transcribe"
        for language, expected in (("auto", []), ("en", ["en-US"]), ("pt-pt", ["pt-pt"])):
            with self.subTest(language=language):
                self.config.language = language
                self.gemini()
                self.transcriber._transcribe_with_gemini(self.audio)
                config = json.loads(self.requests[0].content)["generation_config"]["transcription_config"]
                self.assertEqual(config["language_codes"], expected)

    def test_legacy_custom_gemini_uses_generate_content(self):
        self.config.gemini_model = "custom-gemini-audio"
        self.gemini({"candidates": [{"content": {"parts": [{"text": "Olá."}], "role": "model"}, "finishReason": "STOP"}]})
        self.assertEqual(self.transcriber._transcribe_with_gemini(self.audio), "Olá.")
        self.assertTrue(self.requests[0].url.path.endswith("/models/custom-gemini-audio:generateContent"))

    def test_gemini_empty_failed_and_incomplete_results_are_failures(self):
        for status, text in (("completed", "  "), ("failed", "partial"), ("incomplete", "partial")):
            with self.subTest(status=status, text=text):
                self.config.gemini_model = "gemini-3.8-flash"
                self.gemini({"status": status, "steps": [{"type": "model_output", "content": [{"type": "text", "text": text}]}]})
                with self.assertRaises(RuntimeError):
                    self.transcriber._transcribe_with_gemini(self.audio)
                self.gemini_close.assert_called_once()

    def test_legacy_gemini_rejects_blocked_and_truncated_content(self):
        for reason in ("SAFETY", "MAX_TOKENS"):
            with self.subTest(reason=reason):
                self.config.gemini_model = "gemini-2.5-flash"
                self.gemini({"candidates": [{"content": {"parts": [{"text": "partial"}]}, "finishReason": reason}]})
                with self.assertRaises(RuntimeError):
                    self.transcriber._transcribe_with_gemini(self.audio)

    def test_completed_gemini_with_top_level_errors_rejects_partial_text(self):
        self.config.gemini_model = "gemini-3.8-flash"
        self.gemini({
            "status": "completed",
            "errors": [{"code": "TRANSCRIPTION_FAILED", "message": "provider error"}],
            "steps": [{"type": "model_output", "content": [{"type": "text", "text": "partial transcript"}]}],
        })
        with self.assertRaisesRegex(RuntimeError, "bloqueada ou incompleta"):
            self.transcriber._transcribe_with_gemini(self.audio)
        self.gemini_close.assert_called_once()

    def test_upload_threshold_includes_base64_and_remote_cleanup_on_error(self):
        self.config.gemini_model = "gemini-3.8-flash"
        client = self.gemini({"status": "failed"})
        uploaded = SimpleNamespace(uri="https://example.invalid/files/test", name="files/test", mime_type="audio/wav")
        with patch.object(client.files, "upload", return_value=uploaded) as upload, \
                patch.object(client.files, "delete") as delete, \
                patch.object(module, "_GEMINI_MAX_INLINE_BYTES", len(self.audio.read_bytes()) + 1):
            with self.assertRaises(RuntimeError):
                self.transcriber._transcribe_with_gemini(self.audio)
        upload.assert_called_once()
        delete.assert_called_once_with(name="files/test")
        body = json.loads(self.requests[0].content)
        self.assertIn("uri", next(p for p in self.gemini_input() if p["type"] == "audio"))
        self.gemini_close.assert_called_once()

    def test_upload_is_deleted_when_generation_transport_fails(self):
        self.config.gemini_model = "gemini-3.8-flash"
        client = self.gemini()
        uploaded = SimpleNamespace(uri="https://example.invalid/files/test", name="files/test", mime_type="audio/wav")
        with patch.object(client.files, "upload", return_value=uploaded), \
                patch.object(client.files, "delete") as delete, \
                patch.object(client.interactions, "create", side_effect=RuntimeError("transport failed")), \
                patch.object(module, "_GEMINI_MAX_INLINE_BYTES", 1):
            with self.assertRaises(RuntimeError):
                self.transcriber._transcribe_with_gemini(self.audio)
        delete.assert_called_once_with(name="files/test")
        self.gemini_close.assert_called_once()

    def test_upload_success_cleanup_failure_keeps_text_and_sanitizes_log(self):
        self.config.gemini_model = "gemini-3.8-flash"
        client = self.gemini()
        uploaded = SimpleNamespace(uri="https://example.invalid/files/test", name="files/test", mime_type="audio/wav")
        with patch.object(client.files, "upload", return_value=uploaded), \
                patch.object(client.files, "delete", side_effect=RuntimeError("secret-delete-response")), \
                patch.object(module, "_GEMINI_MAX_INLINE_BYTES", 1):
            with self.assertLogs(__name__, level="WARNING") as logs:
                self.assertEqual(self.transcriber._transcribe_with_gemini(self.audio), "Olá mundo.")
        self.assertNotIn("secret-delete-response", " ".join(logs.output))
        self.gemini_close.assert_called_once()

    def test_real_google_sdk_upload_generation_and_delete_http_contract(self):
        requests = []
        def handle(request):
            requests.append(request)
            if request.url.path == "/upload/v1beta/files":
                return httpx.Response(200, json={}, headers={"x-goog-upload-url": "https://example.invalid/resumable"})
            if request.url.path == "/resumable":
                return httpx.Response(200, json={"file": {"name": "files/test", "uri": "https://example.invalid/files/test", "mimeType": "audio/wav"}},
                                      headers={"x-goog-upload-status": "final"})
            if request.method == "DELETE":
                return httpx.Response(200, json={})
            return httpx.Response(200, json={"status": "completed", "steps": [{"type": "model_output", "content": [
                {"type": "text", "text": "Olá."}]}]})
        http = httpx.Client(transport=httpx.MockTransport(handle))
        self.addCleanup(http.close)
        client = genai.Client(api_key="contract-test-key", http_options={"httpx_client": http})
        self.addCleanup(client.close)
        self.transcriber._get_gemini_client = Mock(return_value=client)
        with patch.object(module, "_GEMINI_MAX_INLINE_BYTES", 1):
            self.assertEqual(self.transcriber._transcribe_with_gemini(self.audio), "Olá.")
        self.assertEqual([r.url.path for r in requests], ["/upload/v1beta/files", "/resumable", "/v1beta/interactions", "/v1beta/files/test"])
        self.assertEqual(requests[1].content, self.audio.read_bytes())
        self.assertEqual(requests[1].headers["x-goog-upload-command"], "upload, finalize")
        self.assertEqual(requests[-1].method, "DELETE")
        body = json.loads(requests[2].content)
        self.assertFalse(body["store"])
        self.assertEqual(body["input"][0]["content"][-1]["uri"], "https://example.invalid/files/test")

    def test_internally_owned_gemini_transport_is_closed(self):
        client = genai.Client(api_key="contract-test-key")
        self.addCleanup(client.close)
        transport = client._api_client._httpx_client
        self.transcriber._get_gemini_client = Mock(return_value=client)
        with patch.object(client.interactions, "create", side_effect=RuntimeError("transport failed")):
            with self.assertRaises(RuntimeError):
                self.transcriber._transcribe_with_gemini(self.audio)
        self.assertTrue(transport.is_closed)

    def test_wav_fallback_chunks_by_actual_pcm_bytes_at_any_sample_rate(self):
        self.config.transcription_provider = "openai"
        samples = np.full(1800, .2, dtype=np.float32)
        audio = CapturedAudio(samples=samples, sample_rate=48000, duration_seconds=len(samples) / 48000)
        payloads = []
        def cloud(provider, path, duration):
            payloads.append(path.read_bytes())
            return "part"
        with patch.object(module, "_CLOUD_MAX_FILE_BYTES", 1044), \
                patch.object(self.transcriber, "_write_temp_mp3", side_effect=RuntimeError("encoder")), \
                patch.object(self.transcriber, "_transcribe_with_cloud", side_effect=cloud):
            result = self.transcriber.transcribe(audio)
        self.assertGreater(len(payloads), 1)
        self.assertTrue(all(len(payload) <= 1044 for payload in payloads))
        actual = []
        for payload in payloads:
            with wave.open(io.BytesIO(payload)) as wav:
                actual.extend(np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16))
        np.testing.assert_array_equal(actual, (samples * 32767).astype(np.int16))
        self.assertEqual(result, " ".join(["part"] * len(payloads)))
        self.assertEqual(list(self.root.glob("dictation_*")), [])

    def test_oversized_wav_after_mp3_failure_is_rechunked(self):
        self.config.transcription_provider = "openai"
        audio = CapturedAudio(samples=np.full(1800, .2, dtype=np.float32), sample_rate=2, duration_seconds=900)
        sizes = []
        def cloud(provider, path, duration):
            sizes.append(path.stat().st_size)
            return "part"
        with patch.object(module, "_CLOUD_MAX_FILE_BYTES", 1044), \
                patch.object(self.transcriber, "_write_temp_mp3", side_effect=RuntimeError("encoder")) as encode, \
                patch.object(self.transcriber, "_transcribe_with_cloud", side_effect=cloud):
            self.transcriber.transcribe(audio)
        encode.assert_called_once()
        self.assertGreater(len(sizes), 1)
        self.assertTrue(all(size <= 1044 for size in sizes))
        self.assertEqual(list(self.root.glob("dictation_*")), [])

    def test_failed_mp3_writer_removes_incomplete_file(self):
        audio = CapturedAudio(samples=np.full(100, .2, dtype=np.float32), sample_rate=16000, duration_seconds=.00625)
        with patch("av.open", side_effect=RuntimeError("encoder unavailable")):
            with self.assertRaises(RuntimeError):
                self.transcriber._write_temp_mp3(audio)
        self.assertEqual(list(self.root.glob("dictation_*")), [])

    def test_cloud_error_falls_back_without_logging_sensitive_exception(self):
        self.config.transcription_provider = "gemini"
        audio = CapturedAudio(samples=np.full(100, .2, dtype=np.float32), sample_rate=16000, duration_seconds=.00625)
        with patch.object(self.transcriber, "_transcribe_with_cloud", side_effect=RuntimeError("secret-api-key-body")), \
                patch.object(self.transcriber, "_transcribe_with_local_model", return_value="local"):
            with self.assertLogs(__name__, level="WARNING") as logs:
                self.assertEqual(self.transcriber.transcribe(audio), "local")
        self.assertNotIn("secret-api-key-body", " ".join(logs.output))
        self.assertEqual(list(self.root.glob("dictation_*")), [])

    def test_local_contract_and_silent_audio_remain_unchanged(self):
        self.config.transcription_provider = "local"
        model = Mock()
        model.transcribe.return_value = (iter([SimpleNamespace(text=" Olá . ")]), SimpleNamespace(language="pt", language_probability=.9))
        self.transcriber._local_model = model
        audio = CapturedAudio(samples=np.full(100, .2, dtype=np.float32), sample_rate=16000, duration_seconds=.00625)
        self.assertEqual(self.transcriber.transcribe(audio), "Olá.")
        self.assertEqual(model.transcribe.call_args.kwargs["language"], "pt")
        self.assertTrue(model.transcribe.call_args.kwargs["vad_filter"])
        self.assertEqual(list(self.root.glob("dictation_*")), [])
        silent = CapturedAudio(samples=np.zeros(100, dtype=np.float32), sample_rate=16000, duration_seconds=.00625)
        self.assertEqual(self.transcriber.transcribe(silent), "")
        self.assertEqual(model.transcribe.call_count, 1)


if __name__ == "__main__":
    unittest.main()
