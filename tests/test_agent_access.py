"""Agent access (pace-cli, MCP server, agent connections) and its update safety."""
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import main
from whisper_dictation import agent_jobs, agents, cli, mcp_server, processes, update_helper, updater
from whisper_dictation.meeting_processing import apply_job_options
from whisper_dictation.agent_jobs import Context, JobError
from whisper_dictation.agent_skill import MARKER
from whisper_dictation.meeting_service import MeetingService
from whisper_dictation.settings import AppConfig
from whisper_dictation.transcript_format import agent_text, speaker_turns

ROOT = Path(__file__).resolve().parents[1]
RESULT = {"text": "Oi. Tudo bem? Sim.", "language": "pt", "duration": 9.0, "transcription_warning": "",
          "segments": [{"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00", "text": "Oi."},
                       {"start": 1.2, "end": 2.0, "speaker": "SPEAKER_00", "text": "Tudo bem?"},
                       {"start": 3.0, "end": 4.0, "speaker": "SPEAKER_01", "text": "Sim."},
                       {"start": 5.0, "end": 6.0, "speaker": None, "text": "hum"}]}


class FormatTests(unittest.TestCase):
    def test_turns_join_same_speaker_and_keep_unknown(self):
        turns = speaker_turns(RESULT, {"SPEAKER_01": "Ana"})
        self.assertEqual([(t["speaker"], t["text"]) for t in turns],
                         [("SPEAKER_00", "Oi. Tudo bem?"), ("SPEAKER_01", "Sim."), (None, "hum")])
        self.assertEqual(turns[1]["label"], "Ana")
        self.assertEqual(agent_text(RESULT).splitlines()[0], "[00:00:00] SPEAKER_00: Oi. Tudo bem?")
        self.assertIn("DESCONHECIDO: hum", agent_text(RESULT))

    def test_playback_still_exports_the_same_text(self):
        from whisper_dictation import playback
        self.assertIs(playback.transcript_text, __import__("whisper_dictation.transcript_format", fromlist=["x"]).transcript_text)


class JobsBase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.install = self.root / "Pace"
        self.install.mkdir()
        AppConfig(meeting_storage_dir=str(self.root / "library"), transcription_provider="groq").save(self.install / "config.json")
        self.context = Context(self.install)
        self.audio = self.root / "áudio do WhatsApp.ogg"
        self.audio.write_bytes(b"fake opus data")
        self.spawned = []
        spawn = patch.object(agent_jobs, "_spawn_worker", side_effect=lambda c, s, i: self.spawned.append(i) or 4242)
        spawn.start()
        self.addCleanup(spawn.stop)
        key = patch.object(agent_jobs, "key_configured", return_value=True)
        self.key = key.start()
        self.addCleanup(key.stop)
        alive = patch.object(agent_jobs, "record_worker_alive", return_value=True)
        self.alive = alive.start()
        self.addCleanup(alive.stop)

    def finish(self, recording_id, result=RESULT, status="ready"):
        store = self.context.store()
        path = store.directory(recording_id) / "result.json"
        path.write_text(json.dumps(result), encoding="utf-8")
        return store.update(recording_id, status=status, result_path=str(path), stage="Concluído", progress=1.0)


class JobTests(JobsBase):
    def test_start_creates_agent_job_with_snapshot_and_detached_worker(self):
        record, reused = agent_jobs.start(self.context, str(self.audio), speakers=2, language="PT")
        self.assertFalse(reused)
        self.assertEqual(record["status"], "processing")
        self.assertEqual(record["origin"], "import")
        self.assertEqual(record["requested_by"], "agent")
        self.assertEqual(record["worker_pid"], 4242)
        self.assertEqual(record["num_speakers"], 2)
        self.assertEqual(record["source_sha256"], hashlib.sha256(b"fake opus data").hexdigest())
        snapshot = AppConfig.load(self.context.store().directory(record["id"]) / "worker_config.json")
        self.assertEqual(snapshot.language, "pt")
        self.assertEqual(self.spawned, [record["id"]])

    def test_same_file_and_options_reuse_ready_or_running_job(self):
        first, _ = agent_jobs.start(self.context, self.audio)
        again, reused = agent_jobs.start(self.context, self.audio)
        self.assertTrue(reused)
        self.assertEqual(again["id"], first["id"])
        self.finish(first["id"])
        ready, reused = agent_jobs.start(self.context, self.audio)
        self.assertTrue(reused)
        self.assertEqual(ready["status"], "ready")
        other, reused = agent_jobs.start(self.context, self.audio, speakers=3)
        self.assertFalse(reused)
        forced, reused = agent_jobs.start(self.context, self.audio, force=True)
        self.assertFalse(reused)
        self.assertEqual(len(self.spawned), 3)

    def test_invalid_input_is_rejected_before_any_job(self):
        cases = [((self.root / "missing.mp3",), {}, "file_not_found", 2),
                 ((self.audio,), {"speakers": 0.5}, None, 2),
                 ((self.audio,), {"speakers": 99}, "invalid_argument", 2),
                 ((self.audio,), {"min_speakers": 4, "max_speakers": 2}, "invalid_argument", 2)]
        empty = self.root / "empty.wav"
        empty.write_bytes(b"")
        cases.append(((empty,), {}, "empty_file", 2))
        for args, kwargs, code, exit_code in cases:
            with self.subTest(args=args, kwargs=kwargs), self.assertRaises(JobError) as caught:
                agent_jobs.start(self.context, *args, **kwargs)
            self.assertEqual(caught.exception.exit_code, exit_code)
            if code:
                self.assertEqual(caught.exception.code, code)
        self.assertEqual(self.context.store().list(), [])

    def test_missing_key_explains_how_to_fix_and_local_mode_skips_it(self):
        self.key.return_value = False
        with self.assertRaises(JobError) as caught:
            agent_jobs.start(self.context, self.audio)
        self.assertEqual(caught.exception.code, "missing_api_key")
        self.assertIn("--local", caught.exception.hint)
        self.key.side_effect = lambda config, provider: provider == "local"
        record, _ = agent_jobs.start(self.context, self.audio, local=True)
        self.assertEqual(record["job_options"]["provider"], "local")

    def test_dead_worker_is_resumed_then_fails_with_log_after_limit(self):
        record, _ = agent_jobs.start(self.context, self.audio)
        self.alive.return_value = False
        (self.context.store().directory(record["id"]) / "worker.log").write_text("Traceback\nRuntimeError: sem memória\n", encoding="utf-8")
        for attempt in range(agent_jobs.MAX_RESUMES):
            resumed = agent_jobs.ensure_running(self.context, record["id"])
            self.assertEqual(resumed["status"], "processing")
            self.assertEqual(resumed["resume_count"], attempt + 1)
        failed = agent_jobs.ensure_running(self.context, record["id"])
        self.assertEqual(failed["status"], "failed")
        self.assertIn("sem memória", failed["error"])
        self.assertEqual(len(self.spawned), 1 + agent_jobs.MAX_RESUMES)

    def test_queued_job_is_left_to_a_running_tray(self):
        record, _ = agent_jobs.start(self.context, self.audio)
        self.context.store().update(record["id"], status="queued")
        with patch("whisper_dictation.single_instance.probe", return_value=True):
            self.assertEqual(agent_jobs.ensure_running(self.context, record["id"])["status"], "queued")
        with patch("whisper_dictation.single_instance.probe", return_value=False):
            self.assertEqual(agent_jobs.ensure_running(self.context, record["id"])["status"], "processing")

    def test_wait_returns_on_timeout_without_cancelling(self):
        record, _ = agent_jobs.start(self.context, self.audio)
        seen = []
        current = agent_jobs.wait(self.context, record["id"], 0.2, seen.append, interval=0.05)
        self.assertEqual(current["status"], "processing")
        self.assertFalse((self.context.store().directory(record["id"]) / "cancel.request").exists())
        self.assertTrue(seen)
        self.finish(record["id"])
        self.assertEqual(agent_jobs.wait(self.context, record["id"], 5, interval=0.05)["status"], "ready")

    def test_cancel_requests_running_worker_and_marks_orphan(self):
        record, _ = agent_jobs.start(self.context, self.audio)
        self.assertEqual(agent_jobs.cancel(self.context, record["id"])["status"], "processing")
        self.assertTrue((self.context.store().directory(record["id"]) / "cancel.request").exists())
        self.alive.return_value = False
        self.assertEqual(agent_jobs.cancel(self.context, record["id"])["status"], "cancelled")

    def test_result_errors_have_stable_exit_codes(self):
        record, _ = agent_jobs.start(self.context, self.audio)
        with self.assertRaises(JobError) as caught:
            agent_jobs.result(self.context, record["id"])
        self.assertEqual(caught.exception.exit_code, 4)
        self.context.store().update(record["id"], status="failed", error="boom")
        with self.assertRaises(JobError) as caught:
            agent_jobs.result(self.context, record["id"])
        self.assertEqual(caught.exception.exit_code, 3)
        with self.assertRaises(JobError) as caught:
            agent_jobs.get(self.context, "nao-existe")
        self.assertEqual(caught.exception.code, "not_found")

    def test_payload_uses_names_given_in_the_app_and_never_keys(self):
        record, _ = agent_jobs.start(self.context, self.audio)
        self.context.store().update(record["id"], speaker_names={"SPEAKER_00": "Luiz"})
        record, data = agent_jobs.result(self.context, self.finish(record["id"])["id"])
        view = agent_jobs.payload(record, data)
        self.assertEqual(view["speakers"], [{"id": "SPEAKER_00", "name": "Luiz"}, {"id": "SPEAKER_01", "name": None}])
        self.assertEqual(view["turns"][0]["label"], "Luiz")
        self.assertNotIn("key", json.dumps(view).lower().replace("speaker", ""))


class CliTests(JobsBase):
    def run_cli(self, *arguments):
        out, err = io.StringIO(), io.StringIO()
        with patch.object(sys, "stdout", out), patch.object(sys, "stderr", err):
            code = cli.main(list(arguments), self.install)
        return code, out.getvalue(), err.getvalue()

    def test_transcribe_waits_and_prints_agent_text_with_header(self):
        original = agent_jobs.wait
        def finish_then_wait(context, recording_id, *args, **kwargs):
            self.finish(recording_id)
            return original(context, recording_id, *args, **kwargs)
        with patch.object(agent_jobs, "wait", side_effect=finish_then_wait):
            code, out, err = self.run_cli("transcribe", str(self.audio))
        self.assertEqual(code, 0)
        self.assertIn("# PACE · áudio do WhatsApp · id", out)
        self.assertIn("2 falante(s)", out)
        self.assertIn("[00:00:03] SPEAKER_01: Sim.", out)
        self.assertIn("trabalho iniciado", err)

    def test_no_wait_timeout_and_formats(self):
        code, out, _ = self.run_cli("transcribe", str(self.audio), "--no-wait", "--json")
        self.assertEqual(code, 0)
        recording_id = json.loads(out)["id"]
        code, out, _ = self.run_cli("wait", recording_id, "--timeout", "0.1", "-q")
        self.assertEqual(code, 4)
        self.assertIn(f"pace-cli wait {recording_id}", out)
        self.finish(recording_id)
        code, out, _ = self.run_cli("result", recording_id, "--format", "srt")
        self.assertTrue(out.startswith("1\n00:00:00,000 --> 00:00:01,000\nFalante 1: Oi."))
        target = self.root / "saída" / "legenda.json"
        code, out, err = self.run_cli("result", recording_id, "--json", "--output", str(target))
        self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["id"], recording_id)
        code, out, _ = self.run_cli("list", "--json")
        self.assertEqual(json.loads(out)[0]["id"], recording_id)

    def test_errors_go_to_stderr_with_hint_or_json(self):
        code, out, err = self.run_cli("transcribe", str(self.root / "nada.mp3"))
        self.assertEqual((code, out), (2, ""))
        self.assertIn("Dica:", err)
        code, out, err = self.run_cli("status", "nao-existe", "--json")
        self.assertEqual(json.loads(err)["error"]["code"], "not_found")
        self.assertEqual(self.run_cli()[0], 2)

    def test_cli_entrypoint_detection(self):
        self.assertTrue(main.is_cli_invocation(["pace-cli.exe", "doctor"], r"C:\x\pace-cli.exe"))
        self.assertTrue(main.is_cli_invocation(["main.py", "cli", "doctor"], sys.executable))
        self.assertFalse(main.is_cli_invocation(["Pace.exe", "--recordings"], r"C:\x\Pace.exe"))


class McpTests(JobsBase):
    def setUp(self):
        super().setUp()
        self.output = io.BytesIO()
        self.server = mcp_server.Server(self.context, self.output)

    def messages(self):
        return [json.loads(line) for line in self.output.getvalue().splitlines()]

    def call(self, request_id, name, arguments=None, meta=None):
        params = {"name": name, "arguments": arguments or {}}
        if meta:
            params["_meta"] = meta
        self.server.handle_line(json.dumps({"jsonrpc": "2.0", "id": request_id, "method": "tools/call", "params": params}).encode())
        for thread in self.server._threads:
            thread.join(10)
        return next(m for m in self.messages() if m.get("id") == request_id)

    def test_initialize_negotiates_version_and_gives_instructions(self):
        for requested, expected in (("2025-03-26", "2025-03-26"), ("2099-01-01", "2025-06-18")):
            self.output.seek(0); self.output.truncate()
            self.server.handle_line(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                                "params": {"protocolVersion": requested}}).encode())
            result = self.messages()[0]["result"]
            self.assertEqual(result["protocolVersion"], expected)
            self.assertIn("WhatsApp", result["instructions"])
            self.assertEqual(result["serverInfo"]["name"], "pace")

    def test_protocol_errors_notifications_and_batches(self):
        self.server.handle_line(b"{nope")
        self.server.handle_line(b'{"jsonrpc":"2.0","method":"notifications/initialized"}')
        self.server.handle_line(b'[{"jsonrpc":"2.0","id":2,"method":"ping"},{"jsonrpc":"2.0","id":3,"method":"tools/list"}]')
        self.server.handle_line(b'{"jsonrpc":"2.0","id":4,"method":"what"}')
        self.server.handle_line(b'{"jsonrpc":"2.0","id":5}')
        messages = self.messages()
        self.assertEqual(messages[0]["error"]["code"], -32700)
        self.assertEqual(messages[1], {"jsonrpc": "2.0", "id": 2, "result": {}})
        names = [tool["name"] for tool in messages[2]["result"]["tools"]]
        self.assertEqual(names, ["transcribe_audio", "get_transcription", "list_transcriptions", "cancel_transcription", "pace_status"])
        for tool in messages[2]["result"]["tools"]:
            self.assertEqual(tool["inputSchema"]["type"], "object")
            self.assertGreater(len(tool["description"]), 40)
        self.assertEqual(messages[3]["error"]["code"], -32601)
        self.assertEqual(len(messages), 4)

    def test_transcribe_returns_text_or_job_id_and_reports_progress(self):
        response = self.call(10, "transcribe_audio", {"path": str(self.audio), "wait_seconds": 0}, {"progressToken": "t"})
        text = response["result"]["content"][0]["text"]
        self.assertFalse(response["result"]["isError"])
        self.assertIn("Ainda processando", text)
        recording_id = self.context.store().list()[0]["id"]
        self.assertIn(recording_id, text)
        self.assertTrue(any(m.get("method") == "notifications/progress" and m["params"]["progressToken"] == "t"
                            for m in self.messages()))
        self.finish(recording_id)
        text = self.call(11, "get_transcription", {"id": recording_id, "wait_seconds": 1})["result"]["content"][0]["text"]
        self.assertIn("[00:00:00] SPEAKER_00: Oi. Tudo bem?", text)
        self.assertTrue((self.context.store().directory(recording_id) / "transcricao_agente.txt").is_file())

    def test_long_transcripts_are_paged(self):
        record, _ = agent_jobs.start(self.context, self.audio)
        long_result = {**RESULT, "segments": [{"start": float(i), "end": i + .5, "speaker": f"SPEAKER_0{i % 2}",
                                               "text": "palavra " * 40} for i in range(600)]}
        self.finish(record["id"], long_result)
        with patch.object(mcp_server, "MAX_TEXT", 5000):
            first = self.call(20, "get_transcription", {"id": record["id"]})["result"]["content"][0]["text"]
            self.assertIn("offset 5000", first)
            second = self.call(21, "get_transcription", {"id": record["id"], "offset": 5000})["result"]["content"][0]["text"]
        self.assertIn("Parte 5000", second)

    def test_tool_errors_are_reported_without_crashing(self):
        self.assertTrue(self.call(30, "transcribe_audio", {"path": str(self.root / "x.mp3")})["result"]["isError"])
        self.assertTrue(self.call(31, "transcribe_audio", {})["result"]["isError"])
        self.assertEqual(self.call(32, "nope")["error"]["code"], -32602)
        with patch.object(agent_jobs, "doctor", side_effect=ZeroDivisionError("x")):
            response = self.call(33, "pace_status")
        self.assertTrue(response["result"]["isError"])
        self.assertIn("ZeroDivisionError", response["result"]["content"][0]["text"])

    def test_cancelled_request_gets_no_response(self):
        record, _ = agent_jobs.start(self.context, self.audio)
        self.server._cancelled.add(40)
        self.server._call_tool(40, {"name": "get_transcription", "arguments": {"id": record["id"], "wait_seconds": 5}})
        self.assertEqual([m for m in self.messages() if m.get("id") == 40], [])


class McpStdioTests(unittest.TestCase):
    """Real subprocess: a tool importing native modules must not deadlock on Windows."""

    def test_stdio_round_trip_with_native_import_in_tool_thread(self):
        with tempfile.TemporaryDirectory() as temp:
            install = Path(temp) / "Pace"
            install.mkdir()
            AppConfig(meeting_storage_dir=str(Path(temp) / "library")).save(install / "config.json")
            code = ("import sys; from pathlib import Path; sys.path.insert(0, r'%s');"
                    "from whisper_dictation.cli import main; sys.exit(main(['mcp'], Path(r'%s')))") % (ROOT / "src", install)
            environment = {**os.environ, "LOCALAPPDATA": str(Path(temp) / "local"), "PYTHONIOENCODING": "utf-8"}
            environment.pop("GROQ_API_KEY", None)
            process = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, env=environment)
            try:
                def send(message):
                    process.stdin.write((json.dumps(message) + "\n").encode())
                    process.stdin.flush()
                send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
                self.assertEqual(json.loads(process.stdout.readline())["result"]["serverInfo"]["name"], "pace")
                send({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "pace_status", "arguments": {}}})
                started = time.monotonic()
                reply = json.loads(process.stdout.readline())  # imports numpy in the tool thread
                self.assertLess(time.monotonic() - started, 60)
                self.assertEqual(reply["id"], 2)
                status = json.loads(reply["result"]["content"][0]["text"])
                self.assertIn("api_key_configured", status)
            finally:
                process.stdin.close()
                process.wait(timeout=30)
                process.stdout.close()
                process.stderr.close()


class AgentsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / "Usuária"
        for folder in (".claude", ".codex", ".cursor", "AppData/Roaming/Claude"):
            (self.home / folder).mkdir(parents=True)
        (self.home / ".claude.json").write_text(json.dumps({"numStartups": 9, "mcpServers": {"other": {"command": "x"}}}), encoding="utf-8")
        (self.home / ".codex" / "config.toml").write_text('model = "gpt-5"\n\n[mcp_servers.node]\ncommand = "node"\n', encoding="utf-8")
        self.desktop = self.home / "AppData/Roaming/Claude/claude_desktop_config.json"
        self.desktop.write_text(json.dumps({"preferences": {"theme": "dark"}}), encoding="utf-8")
        self.env = agents.Environment(home=self.home, appdata=self.home / "AppData/Roaming",
                                      localappdata=self.home / "AppData/Local", claude_cli=False)
        self.install = Path(r"C:\Users\Usuária\AppData\Local\Pace")
        self.cli = str(self.install / "pace-cli.exe")

    def test_connect_registers_everywhere_and_preserves_other_settings(self):
        report = agents.connect(self.install, env=self.env, version="1.6.0")
        self.assertTrue(all(item["ok"] and item["changed"] for item in report.values()), report)
        claude = json.loads((self.home / ".claude.json").read_text(encoding="utf-8"))
        self.assertEqual(claude["numStartups"], 9)
        self.assertEqual(claude["mcpServers"]["other"], {"command": "x"})
        self.assertEqual(claude["mcpServers"]["pace"], {"type": "stdio", "command": self.cli, "args": ["mcp"]})
        codex = tomllib.loads((self.home / ".codex/config.toml").read_text(encoding="utf-8"))
        self.assertEqual(codex["model"], "gpt-5")
        self.assertEqual(codex["mcp_servers"]["pace"]["command"], self.cli)
        self.assertEqual(codex["mcp_servers"]["node"]["command"], "node")
        desktop = json.loads(self.desktop.read_text(encoding="utf-8"))
        self.assertEqual(desktop["preferences"], {"theme": "dark"})
        self.assertEqual(desktop["mcpServers"]["pace"]["args"], ["mcp"])
        self.assertEqual(json.loads((self.home / ".cursor/mcp.json").read_text(encoding="utf-8"))["mcpServers"]["pace"]["command"], self.cli)
        for folder in (".claude/skills", ".codex/skills"):
            skill = (self.home / folder / "pace-transcricao" / "SKILL.md").read_text(encoding="utf-8")
            self.assertTrue(skill.startswith("---\nname: pace-transcricao\n"))
            self.assertIn(MARKER, skill)
            self.assertIn(self.cli, skill)
        self.assertTrue(all(item["connected"] for item in agents.status(self.install, self.env).values()))
        again = agents.connect(self.install, env=self.env, version="1.6.0")
        self.assertFalse(any(item["changed"] for item in again.values()))

    def test_disconnect_removes_only_what_pace_added(self):
        agents.connect(self.install, env=self.env, version="1.6.0")
        report = agents.disconnect(self.install, env=self.env)
        self.assertTrue(all(item["ok"] for item in report.values()), report)
        self.assertEqual(json.loads((self.home / ".claude.json").read_text(encoding="utf-8"))["mcpServers"], {"other": {"command": "x"}})
        self.assertEqual((self.home / ".codex/config.toml").read_text(encoding="utf-8"), 'model = "gpt-5"\n\n[mcp_servers.node]\ncommand = "node"\n')
        self.assertNotIn("pace", json.loads(self.desktop.read_text(encoding="utf-8"))["mcpServers"])
        self.assertFalse((self.home / ".claude/skills/pace-transcricao").exists())
        self.assertFalse(any(item["connected"] for item in agents.status(self.install, self.env).values()))

    def test_foreign_entries_files_and_invalid_configs_are_never_overwritten(self):
        (self.home / ".cursor/mcp.json").write_text(json.dumps({"mcpServers": {"pace": {"command": "outro.exe"}}}), encoding="utf-8")
        (self.home / ".codex/config.toml").write_text("model = [quebrado\n", encoding="utf-8")
        own_skill = self.home / ".claude/skills/pace-transcricao/SKILL.md"
        own_skill.parent.mkdir(parents=True)
        own_skill.write_text("minha skill", encoding="utf-8")
        report = agents.connect(self.install, env=self.env, version="1.6.0")
        for name in ("cursor", "codex", "claude-code"):
            self.assertFalse(report[name]["ok"], name)
        self.assertTrue(report["claude-desktop"]["ok"])
        self.assertEqual(json.loads((self.home / ".cursor/mcp.json").read_text(encoding="utf-8"))["mcpServers"]["pace"], {"command": "outro.exe"})
        self.assertEqual((self.home / ".codex/config.toml").read_text(encoding="utf-8"), "model = [quebrado\n")
        self.assertEqual(own_skill.read_text(encoding="utf-8"), "minha skill")
        agents.disconnect(self.install, ["cursor"], env=self.env)
        self.assertIn("pace", json.loads((self.home / ".cursor/mcp.json").read_text(encoding="utf-8"))["mcpServers"])

    def test_only_installed_agents_are_touched_and_backups_are_kept(self):
        env = agents.Environment(home=Path(self.temp.name) / "vazio", appdata=Path(self.temp.name) / "vazio/r",
                                 localappdata=Path(self.temp.name) / "vazio/l", claude_cli=False)
        self.assertEqual(agents.connect(self.install, env=env), {})
        self.assertFalse((Path(self.temp.name) / "vazio" / ".claude.json").exists())
        agents.connect(self.install, env=self.env, version="1.6.0")
        backups = list((self.home / "AppData/Local/PaceData/agents/backups").iterdir())
        self.assertEqual(len(backups), 3)  # .claude.json, config.toml, desktop config (cursor had no file)

    def test_refresh_after_update_rewrites_only_connected_agents(self):
        agents.connect(self.install, ["codex"], env=self.env, version="1.6.0")
        self.assertEqual(agents.refresh(self.install, env=self.env, version="1.6.0"), {})
        report = agents.refresh(self.install, env=self.env, version="1.7.0")
        self.assertEqual(list(report), ["codex"])
        self.assertIn("v1.7.0", (self.home / ".codex/skills/pace-transcricao/SKILL.md").read_text(encoding="utf-8"))
        self.assertNotIn("pace", json.loads((self.home / ".claude.json").read_text(encoding="utf-8"))["mcpServers"])

    def test_claude_cli_is_used_when_available(self):
        calls = []
        env = agents.Environment(home=self.home, appdata=self.home / "AppData/Roaming",
                                 localappdata=self.home / "AppData/Local", claude_cli=r"C:\bin\claude.exe")
        def run(command, **options):
            calls.append(command)
            return MagicMock(returncode=0, stdout="", stderr="")
        with patch.object(agents.subprocess, "run", side_effect=run):
            report = agents.connect(self.install, ["claude-code"], env=env, version="1.6.0")
        self.assertEqual(report["claude-code"]["method"], "claude mcp")
        self.assertEqual(calls[0][1:5], ["mcp", "remove", "--scope", "user"])
        self.assertEqual(calls[1][1:6], ["mcp", "add-json", "--scope", "user", "pace"])
        self.assertEqual(json.loads(calls[1][6]), {"type": "stdio", "command": self.cli, "args": ["mcp"]})


class TrayCoexistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = AppConfig(meeting_storage_dir=str(self.root / "library"))
        self.config.save(self.root / "config.json")
        self.service = MeetingService(self.config, self.root, self.root / "config.json")

    def tearDown(self):
        self.service.shutdown()
        self.temp.cleanup()

    def test_tray_does_not_requeue_job_of_a_live_agent_worker(self):
        alive = self.service.store.create("agente", "import", status="processing", worker_pid=111)
        dead = self.service.store.create("morto", "import", status="diarizing", worker_pid=222)
        with patch("whisper_dictation.meeting_service.record_worker_alive", side_effect=lambda row: row.get("worker_pid") == 111):
            self.service._recover()
            self.assertTrue(self.service.busy)
        self.assertEqual(self.service.store.get(alive["id"])["status"], "processing")
        self.assertEqual(self.service.store.get(dead["id"])["status"], "queued")

    def test_finished_or_dead_agent_jobs_do_not_block_updates(self):
        self.service.store.create("pronto", "import", status="ready", worker_pid=111)
        self.service.store.create("morto", "import", status="processing", worker_pid=222)
        with patch("whisper_dictation.meeting_service.record_worker_alive", return_value=False):
            self.assertFalse(self.service.busy)

    def test_tray_records_its_own_worker_pid(self):
        row = self.service.store.create("fila", "import", status="queued", worker_pid=999)
        process = MagicMock(pid=31337)
        process.poll.return_value = None
        with patch("whisper_dictation.meeting_service.subprocess.Popen", return_value=process), \
             patch("whisper_dictation.meeting_service.ChildJob"):
            self.service._advance_queue()
        self.assertEqual(self.service.store.get(row["id"])["worker_pid"], 31337)
        self.service._worker = None


class UpdateSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        local = patch.dict(os.environ, {"LOCALAPPDATA": str(self.root / "local")})
        local.start()
        self.addCleanup(local.stop)
        for name in ("snapshot_integration", "restore_integration"):
            mocked = patch.object(update_helper, name)
            mocked.start()
            self.addCleanup(mocked.stop)

    def make_plan(self, with_cli=True):
        target = self.root / "installed"
        (target / "_internal").mkdir(parents=True)
        (target / "Pace.exe").write_bytes(b"old binary")
        if with_cli:
            (target / "pace-cli.exe").write_bytes(b"old cli")
        (target / "_internal" / "runtime.dll").write_bytes(b"old runtime")
        (target / "config.json").write_bytes(b"persistent data")
        payload = b"a complete fake installer"
        installer = updater.update_cache_root() / "9.0.0" / "Pace_Installer.exe"
        installer.parent.mkdir(parents=True)
        installer.write_bytes(payload)
        staging = updater.update_cache_root() / "runs" / "fake-run"
        staging.mkdir(parents=True)
        plan = {"parent_pid": 99999, "target": str(target), "installer": str(installer), "version": "9.0.0",
                "sha256": hashlib.sha256(payload).hexdigest(), "size": len(payload), "token": "t",
                "receipt": str(staging / "confirmed.json")}
        path = staging / "plan.json"
        path.write_text(json.dumps(plan), encoding="utf-8")
        return target, path

    def test_agent_processes_are_closed_before_installing(self):
        target, path = self.make_plan()
        events = []
        listing = [[(501, target / "pace-cli.exe")], [], []]
        with patch.object(update_helper, "wait_for_process_exit", side_effect=lambda pid: events.append("exit")), \
             patch.object(update_helper.processes, "processes_in", side_effect=lambda t: events.append("scan") or (listing.pop(0) if listing else [])), \
             patch.object(update_helper.processes, "terminate", side_effect=lambda pid: events.append(f"kill {pid}") or True), \
             patch.object(update_helper.time, "sleep"), \
             patch.object(update_helper.subprocess, "run", side_effect=lambda c, **o: events.append("install") or MagicMock(returncode=0)), \
             patch.object(update_helper, "_launch", return_value=MagicMock(pid=7)), \
             patch.object(update_helper, "_wait_confirmation", return_value=True):
            self.assertEqual(update_helper.apply_update(path), 0)
        self.assertEqual(events[:3], ["exit", "scan", "kill 501"])
        self.assertLess(events.index("kill 501"), events.index("install"))
        self.assertEqual(events[-2:], ["scan", "install"])

    def test_unclosable_process_defers_update_and_reopens_old_app(self):
        target, path = self.make_plan()
        with patch.object(update_helper, "wait_for_process_exit"), \
             patch.object(update_helper.processes, "processes_in", return_value=[(502, target / "Pace.exe")]), \
             patch.object(update_helper.processes, "terminate", return_value=False), \
             patch.object(update_helper.time, "sleep"), \
             patch.object(update_helper.subprocess, "run") as install, \
             patch.object(update_helper, "_launch") as launch:
            self.assertEqual(update_helper.apply_update(path), 1)
        install.assert_not_called()
        launch.assert_called_once_with(target)
        self.assertIn("adiada", json.loads((path.parent / "result.json").read_text(encoding="utf-8"))["error"])

    def test_processes_started_during_install_are_closed_before_restore(self):
        target, path = self.make_plan()
        events = []
        scans = iter([[], [], [(601, target / "pace-cli.exe")], []])
        real_restore = update_helper._restore

        def install(command, **options):
            events.append("install")
            (target / "Pace.exe").write_bytes(b"half new")
            return MagicMock(returncode=5)

        def restore(target_, backup):
            events.append("restore")
            if events.count("restore") == 1:
                raise OSError("runtime locked")
            return real_restore(target_, backup)

        with patch.object(update_helper, "wait_for_process_exit"), \
             patch.object(update_helper.processes, "processes_in", side_effect=lambda t: next(scans, [])), \
             patch.object(update_helper.processes, "terminate", side_effect=lambda pid: events.append(f"kill {pid}") or True), \
             patch.object(update_helper.time, "sleep"), \
             patch.object(update_helper.subprocess, "run", side_effect=install), \
             patch.object(update_helper, "_restore", side_effect=restore), \
             patch.object(update_helper, "_launch") as launch:
            self.assertEqual(update_helper.apply_update(path), 1)
        self.assertEqual(events, ["install", "kill 601", "restore", "restore"])
        self.assertEqual((target / "Pace.exe").read_bytes(), b"old binary")
        launch.assert_called_once_with(target)

    def test_rollback_restores_cli_and_removes_cli_absent_before(self):
        for with_cli in (True, False):
            with self.subTest(with_cli=with_cli):
                if (self.root / "installed").exists():
                    import shutil
                    shutil.rmtree(self.root / "installed")
                    shutil.rmtree(updater.update_cache_root())
                target, path = self.make_plan(with_cli)
                def fail(command, **options):
                    (target / "Pace.exe").write_bytes(b"new binary")
                    (target / "pace-cli.exe").write_bytes(b"new cli")
                    return MagicMock(returncode=5)
                with patch.object(update_helper, "wait_for_process_exit"), \
                     patch.object(update_helper.processes, "processes_in", return_value=[]), \
                     patch.object(update_helper.subprocess, "run", side_effect=fail), \
                     patch.object(update_helper, "_launch"):
                    self.assertEqual(update_helper.apply_update(path), 1)
                self.assertEqual((target / "Pace.exe").read_bytes(), b"old binary")
                if with_cli:
                    self.assertEqual((target / "pace-cli.exe").read_bytes(), b"old cli")
                else:
                    self.assertFalse((target / "pace-cli.exe").exists())
                self.assertEqual((target / "config.json").read_bytes(), b"persistent data")


class ReviewFindingTests(JobsBase):
    """Regressions for the independent review of the agent access feature."""

    def test_worker_reapplies_local_only_and_language_from_the_job(self):
        config = AppConfig(transcription_provider="groq", language="auto")
        apply_job_options(config, {"requested_by": "agent", "job_options": {"provider": "local", "language": "pt"}})
        self.assertEqual((config.transcription_provider, config.language), ("local", "pt"))
        untouched = AppConfig(transcription_provider="groq")
        apply_job_options(untouched, {"requested_by": "app", "job_options": {"provider": "local"}})
        self.assertEqual(untouched.transcription_provider, "groq")

    def test_process_recording_applies_job_options_even_with_tray_snapshot(self):
        from whisper_dictation import meeting_processing
        record, _ = agent_jobs.start(self.context, self.audio, local=True)
        store = self.context.store()
        directory = store.directory(record["id"])
        AppConfig(transcription_provider="groq").save(directory / "worker_config.json")  # tray overwrote it
        (directory / "audio.wav").write_bytes(b"x")
        store.update(record["id"], audio_path=str(directory / "audio.wav"))
        seen = {}

        def stop(audio, directory_, config, *args, **kwargs):
            seen["provider"] = config.transcription_provider
            raise meeting_processing.ProcessingCancelled()

        with patch.object(meeting_processing, "transcribe_recording", side_effect=stop):
            meeting_processing.process_recording(store.root, record["id"], directory / "worker_config.json")
        self.assertEqual(seen["provider"], "local")

    def test_transition_compares_the_owner(self):
        store = self.context.store()
        row = store.create("x", "import", status="processing", worker_pid=10)
        self.assertFalse(store.transition(row["id"], "processing", "resuming", expect={"worker_pid": 99}))
        self.assertTrue(store.transition(row["id"], "processing", "resuming", expect={"worker_pid": 10}, worker_pid=11))
        self.assertEqual(store.get(row["id"])["worker_pid"], 11)

    def test_jobs_are_owned_from_creation_and_resume_does_not_steal(self):
        def spawn(context, store, recording_id):
            self.assertEqual(store.get(recording_id)["worker_pid"], os.getpid())
            return 4242

        with patch.object(agent_jobs, "_spawn_worker", side_effect=spawn):
            record, _ = agent_jobs.start(self.context, self.audio)
        self.assertEqual(record["worker_pid"], 4242)
        self.alive.return_value = False
        store = self.context.store()
        real_transition = store.transition

        def claimed_meanwhile(recording_id, *args, **kwargs):
            store.update(recording_id, worker_pid=777)  # the tray claimed it a moment ago
            return real_transition(recording_id, *args, **kwargs)

        with patch.object(agent_jobs.Context, "store", return_value=store), \
             patch.object(store, "transition", side_effect=claimed_meanwhile):
            current = agent_jobs.ensure_running(self.context, record["id"])
        self.assertEqual(current["worker_pid"], 777)
        self.assertEqual(current["status"], "processing")
        self.assertEqual(self.spawned, [])

    def test_stuck_resuming_with_dead_owner_is_resumed(self):
        record, _ = agent_jobs.start(self.context, self.audio)
        self.context.store().update(record["id"], status="resuming", worker_pid=55555)
        self.alive.return_value = False
        self.assertEqual(agent_jobs.ensure_running(self.context, record["id"])["status"], "processing")

    def make_service(self, name):
        root = self.root / name
        root.mkdir()
        config = AppConfig(meeting_storage_dir=str(root / "library"))
        config.save(root / "config.json")
        service = MeetingService(config, root, root / "config.json")
        self.addCleanup(service.shutdown)
        return service

    def test_tray_claims_with_its_own_pid_before_starting_the_worker(self):
        service = self.make_service("tray")
        row = service.store.create("fila", "import", status="queued")
        process = MagicMock(pid=31337)
        process.poll.return_value = None

        def popen(*args, **kwargs):
            self.assertEqual(service.store.get(row["id"])["worker_pid"], os.getpid())
            return process

        with patch("whisper_dictation.meeting_service.subprocess.Popen", side_effect=popen), \
             patch("whisper_dictation.meeting_service.ChildJob"):
            service._advance_queue()
        self.assertEqual(service.store.get(row["id"])["worker_pid"], 31337)
        service._worker = None

    def test_tray_recovery_does_not_overwrite_a_fresh_claim(self):
        service = self.make_service("tray2")
        row = service.store.create("agente", "import", status="processing", worker_pid=1)
        real = service.store.transition

        def claim_first(recording_id, *args, **kwargs):
            service.store.update(recording_id, status="resuming", worker_pid=2)
            return real(recording_id, *args, **kwargs)

        with patch("whisper_dictation.meeting_service.record_worker_alive", return_value=False), \
             patch.object(service.store, "transition", side_effect=claim_first):
            service._recover()
        self.assertEqual(service.store.get(row["id"])["status"], "resuming")

    def test_reused_pid_is_not_a_live_worker(self):
        frozen = patch.object(processes.sys, "frozen", True, create=True)
        with frozen, patch.object(processes, "process_image", return_value=Path(r"C:\Pace\Pace.exe")), \
             patch.object(processes, "creation_time", return_value=2000.0):
            self.assertTrue(processes.worker_alive(5, 1999.5))
            self.assertFalse(processes.worker_alive(5, 1000.0))
            self.assertTrue(processes.worker_alive(5))
        with patch.object(processes.sys, "frozen", True, create=True), \
             patch.object(processes, "process_image", return_value=Path(r"C:\Windows\notepad.exe")):
            self.assertFalse(processes.worker_alive(5))
        with patch.object(processes.sys, "frozen", True, create=True), \
             patch.object(processes, "process_image", return_value=Path(r"C:\Pace\pace-cli.exe")), \
             patch.object(processes, "creation_time", return_value=None):
            self.assertTrue(processes.worker_alive(5, 1.0))
        if os.name == "nt":
            self.assertAlmostEqual(processes.owner()["worker_started"], processes.creation_time(os.getpid()))
            self.assertTrue(processes.record_worker_alive(processes.owner()))

    def test_cancel_refuses_recordings_owned_by_the_tray(self):
        row = self.context.store().create("Reunião", "meeting", status="recording")
        with self.assertRaises(JobError) as caught:
            agent_jobs.cancel(self.context, row["id"])
        self.assertEqual(caught.exception.code, "not_cancellable")
        self.assertEqual(self.context.store().get(row["id"])["status"], "recording")
        self.assertFalse((self.context.store().directory(row["id"]) / "cancel.request").exists())


    def test_rows_owned_by_this_process_are_alive_only_while_it_launches(self):
        record, _ = agent_jobs.start(self.context, self.audio)
        store = self.context.store()
        store.update(record["id"], **processes.owner())  # the last write (child pid) failed
        self.alive.return_value = True  # other owners would count as alive
        resumed = agent_jobs.ensure_running(self.context, record["id"])
        self.assertEqual(resumed["status"], "processing")
        self.assertEqual(resumed["worker_pid"], 4242)
        store.update(record["id"], **processes.owner())
        with agent_jobs._owning(record["id"]):
            self.assertTrue(agent_jobs._alive(store.get(record["id"])))
            with agent_jobs._owning(record["id"]):
                pass  # a second thread finishing must not clear the first one's mark
            self.assertTrue(agent_jobs._alive(store.get(record["id"])))
        self.assertFalse(agent_jobs._alive(store.get(record["id"])))

    def test_tray_rows_with_its_own_pid_do_not_keep_it_busy(self):
        service = self.make_service("tray3")
        row = service.store.create("x", "import", status="processing", **processes.owner())
        self.assertFalse(service.busy)
        service._worker_id = row["id"]
        self.assertTrue(service.busy)
        service._worker_id = None

    def test_tray_does_not_overwrite_a_job_an_agent_took_over(self):
        service = self.make_service("tray4")
        row = service.store.create("x", "import", status="queued")
        process = MagicMock(pid=31337)
        process.poll.return_value = None
        with patch("whisper_dictation.meeting_service.subprocess.Popen", return_value=process), \
             patch("whisper_dictation.meeting_service.ChildJob"):
            service._advance_queue()
        service.store.update(row["id"], status="resuming", worker_pid=999)  # agent resumed after a crash
        process.poll.return_value = 1
        service._advance_queue()
        self.assertEqual(service.store.get(row["id"])["status"], "resuming")
        self.assertIsNone(service._worker)


class McpRobustnessTests(JobsBase):
    def test_malformed_params_never_stop_the_server(self):
        output = io.BytesIO()
        server = mcp_server.Server(self.context, output)
        server.run(io.BytesIO(
            b'{"jsonrpc":"2.0","method":"notifications/cancelled","params":[1]}\n'
            b'{"jsonrpc":"2.0","method":"notifications/cancelled","params":{"requestId":{"a":1}}}\n'
            b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":[1]}\n'
            b'{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"pace_status","_meta":[1]}}\n'
            b'{"jsonrpc":"2.0","id":3,"method":"ping"}\n'))
        for thread in server._threads:
            thread.join(30)
        replies = {m["id"]: m for m in map(json.loads, output.getvalue().splitlines()) if "id" in m}
        self.assertEqual(replies[1]["error"]["code"], -32602)
        self.assertIn("result", replies[2])
        self.assertEqual(replies[3]["result"], {})


class InstallerDefinitionTests(unittest.TestCase):
    def test_installer_and_build_ship_and_manage_the_cli(self):
        iss = (ROOT / "installer.iss").read_text(encoding="utf-8")
        self.assertIn("pace-cli.exe", iss.split("CloseApplicationsFilter=")[1].splitlines()[0])
        self.assertIn('Type: files; Name: "{app}\\pace-cli.exe"', iss)
        self.assertIn("agents disconnect --all", iss)
        spec = (ROOT / "Pace.spec").read_text(encoding="utf-8")
        self.assertIn("name='pace-cli', console=True", spec)
        self.assertIn("name='Pace', console=False", spec)
        self.assertIn("Pace.spec", (ROOT / "build.ps1").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
