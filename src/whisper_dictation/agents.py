"""Register PACE with AI agents installed for this Windows user.

Only agents that are present are touched. Every file is backed up before it is
changed, written atomically, and edited minimally: PACE adds or removes its own
``pace`` server entry and its own skill folder, nothing else. What was added is
recorded in ``PaceData/agents/state.json`` so disconnect and uninstall remove
exactly that, and a PACE update can refresh paths and the skill text.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from whisper_dictation.agent_skill import MARKER, SKILL_NAME, render as render_skill

SERVER_NAME = "pace"
LABELS = {"claude-code": "Claude Code", "claude-desktop": "Claude Desktop", "codex": "Codex", "cursor": "Cursor"}
_BACKUPS_PER_FILE = 5


class Environment:
    """Locations, injectable so tests never touch the real profile."""

    def __init__(self, home: Path | None = None, appdata: Path | None = None, localappdata: Path | None = None,
                 data: Path | None = None, claude_cli: str | None | bool = None, codex_home: Path | None = None):
        self.home = Path(home or Path.home())
        self.appdata = Path(appdata or os.environ.get("APPDATA") or self.home / "AppData" / "Roaming")
        self.localappdata = Path(localappdata or os.environ.get("LOCALAPPDATA") or self.home / "AppData" / "Local")
        self.data = Path(data or self.localappdata / "PaceData") / "agents"
        self._claude_cli = claude_cli
        codex = codex_home or (os.environ.get("CODEX_HOME") if home is None else None)
        self.codex_home = Path(codex) if codex else self.home / ".codex"

    def claude_cli(self) -> str | None:
        """The `claude` command, when installed; False in tests disables it."""
        if self._claude_cli is False:
            return None
        if isinstance(self._claude_cli, str):
            return self._claude_cli
        found = shutil.which("claude")
        # A .cmd shim (npm install) would re-parse the JSON argument through cmd.exe.
        if found and Path(found).suffix.lower() == ".exe":
            return found
        candidate = self.home / ".local" / "bin" / "claude.exe"
        return str(candidate) if candidate.is_file() else None

    def desktop_configs(self) -> list[Path]:
        folders = [self.appdata / "Claude"]
        packages = self.localappdata / "Packages"
        if packages.is_dir():
            folders += sorted(packages.glob("Claude_*/LocalCache/Roaming/Claude"))
        return [folder / "claude_desktop_config.json" for folder in folders if folder.is_dir()]


def cli_path(project_root: Path) -> Path:
    return Path(project_root).resolve() / "pace-cli.exe"


def server_entry(cli: Path) -> dict:
    return {"command": str(cli), "args": ["mcp"]}


def _ours(entry) -> bool:
    """Only entries pointing at a pace-cli.exe are PACE's to replace or remove."""
    return isinstance(entry, dict) and Path(str(entry.get("command", ""))).name.lower() == "pace-cli.exe"


# ---------------------------------------------------------------- file helpers
def _backup(path: Path, env: Environment, target: str) -> None:
    if not path.exists():
        return
    folder = env.data / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"{target}-{path.name}"
    shutil.copy2(path, folder / f"{stem}-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}")
    for old in sorted(folder.glob(f"{stem}-*"))[:-_BACKUPS_PER_FILE]:
        old.unlink(missing_ok=True)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".pace-tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _read_json(path: Path) -> dict:
    if not path.exists() or not path.read_text(encoding="utf-8-sig").strip():
        return {}
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} não contém um objeto JSON.")
    return data


def _json_set_server(path: Path, env: Environment, target: str, entry: dict | None) -> bool:
    """Add/replace (entry) or remove (None) mcpServers.pace. True if the file changed."""
    data = _read_json(path)
    servers = data.get("mcpServers")
    if servers is not None and not isinstance(servers, dict):
        raise ValueError(f"mcpServers inválido em {path}.")
    servers = dict(servers or {})
    current = servers.get(SERVER_NAME)
    if current is not None and not _ours(current):
        raise RuntimeError(f"Já existe um servidor MCP chamado '{SERVER_NAME}' em {path} que não é do PACE; não foi alterado.")
    if entry is None:
        if current is None:
            return False
        servers.pop(SERVER_NAME)
    else:
        if current == entry:
            return False
        servers[SERVER_NAME] = entry
    data["mcpServers"] = servers
    _backup(path, env, target)
    _atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return True


_TOML_HEADER = re.compile(r"^\s*\[\[?\s*([^\]]+?)\s*\]\]?\s*(?:#.*)?$")


def _toml_without_pace(text: str) -> tuple[str, bool]:
    lines, kept, block, skipping = text.splitlines(keepends=True), [], [], False
    for line in lines:
        match = _TOML_HEADER.match(line)
        if match:
            name = match.group(1).replace('"', "").replace("'", "")
            skipping = name == f"mcp_servers.{SERVER_NAME}" or name.startswith(f"mcp_servers.{SERVER_NAME}.")
        (block if skipping else kept).append(line)
    if block and "pace-cli.exe" not in "".join(block).lower():
        raise RuntimeError(f"Já existe [mcp_servers.{SERVER_NAME}] no config do Codex que não é do PACE; não foi alterado.")
    return "".join(kept), bool(block)


def _codex_set_server(path: Path, env: Environment, cli: Path | None) -> bool:
    import tomllib
    original = path.read_text(encoding="utf-8-sig") if path.exists() else ""
    if original.strip():
        tomllib.loads(original)  # never edit a file Codex itself could not read
    text, removed = _toml_without_pace(original)
    if removed:
        text = text.rstrip("\n") + "\n" if text.strip() else ""
    if cli is not None:
        block = (f"[mcp_servers.{SERVER_NAME}]\n"
                 f"command = {json.dumps(str(cli))}\n"
                 'args = ["mcp"]\n'
                 "startup_timeout_sec = 30\n"
                 "tool_timeout_sec = 660\n")
        text = (text.rstrip("\n") + "\n\n" if text.strip() else "") + block
    if text == original:
        return False
    tomllib.loads(text)
    _backup(path, env, "codex")
    _atomic_write(path, text)
    return True


def _write_skill(folder: Path, cli: Path, version: str) -> bool:
    path = folder / SKILL_NAME / "SKILL.md"
    content = render_skill(str(cli), version)
    if path.exists():
        current = path.read_text(encoding="utf-8")
        if MARKER not in current:
            raise RuntimeError(f"{path} existe e não foi criado pelo PACE; não foi alterado.")
        if current == content:
            return False
    _atomic_write(path, content)
    return True


def _remove_skill(folder: Path) -> bool:
    path = folder / SKILL_NAME / "SKILL.md"
    if not path.exists() or MARKER not in path.read_text(encoding="utf-8"):
        return False
    path.unlink()
    try:
        path.parent.rmdir()
    except OSError:
        pass
    return True


# ---------------------------------------------------------------- targets
def detect(env: Environment) -> dict[str, bool]:
    return {
        "claude-code": (env.home / ".claude").is_dir() or (env.home / ".claude.json").is_file(),
        "claude-desktop": bool(env.desktop_configs()),
        "codex": env.codex_home.is_dir(),
        "cursor": (env.home / ".cursor").is_dir(),
    }


def _claude_code(env: Environment, cli: Path | None, version: str) -> dict:
    """Prefer the official `claude mcp` command; fall back to editing ~/.claude.json."""
    changed, method = False, "arquivo"
    command = env.claude_cli()
    try:
        registered = (_read_json(env.home / ".claude.json").get("mcpServers") or {}).get(SERVER_NAME)
    except (OSError, ValueError):
        registered = None
    if registered is not None and not _ours(registered):
        raise RuntimeError(f"Já existe um servidor MCP '{SERVER_NAME}' no Claude Code que não é do PACE; não foi alterado.")
    if command and cli is None and registered is None:
        command = None  # nothing of ours to remove through the CLI
    if command:
        options = {"capture_output": True, "encoding": "utf-8", "errors": "replace", "timeout": 60,
                   "stdin": subprocess.DEVNULL}
        if os.name == "nt":
            options["creationflags"] = subprocess.CREATE_NO_WINDOW
        try:
            subprocess.run([command, "mcp", "remove", "--scope", "user", SERVER_NAME], **options)
            if cli is not None:
                done = subprocess.run([command, "mcp", "add-json", "--scope", "user", SERVER_NAME,
                                       json.dumps({"type": "stdio", **server_entry(cli)})], **options)
                if done.returncode != 0:
                    raise RuntimeError(done.stderr.strip() or done.stdout.strip())
            changed, method = True, "claude mcp"
        except (OSError, ValueError, subprocess.SubprocessError, RuntimeError):
            command = None  # fall back to editing ~/.claude.json directly
    if not command:
        changed = _json_set_server(env.home / ".claude.json", env, "claude-code",
                                   {"type": "stdio", **server_entry(cli)} if cli is not None else None)
    skills = env.home / ".claude" / "skills"
    skill_changed = _write_skill(skills, cli, version) if cli is not None else _remove_skill(skills)
    return {"changed": changed or skill_changed, "method": method,
            "files": [str(env.home / ".claude.json"), str(skills / SKILL_NAME / "SKILL.md")]}


def _apply(target: str, env: Environment, cli: Path | None, version: str) -> dict:
    if target == "claude-code":
        return _claude_code(env, cli, version)
    if target == "claude-desktop":
        files = env.desktop_configs()
        changed = False
        for path in files:
            changed = _json_set_server(path, env, target, server_entry(cli) if cli is not None else None) or changed
        return {"changed": changed, "files": [str(path) for path in files],
                "note": "Reinicie o Claude Desktop para ver o PACE." if changed else ""}
    if target == "codex":
        config = env.codex_home / "config.toml"
        changed = _codex_set_server(config, env, cli)
        skills = env.codex_home / "skills"
        skill_changed = _write_skill(skills, cli, version) if cli is not None else _remove_skill(skills)
        return {"changed": changed or skill_changed, "files": [str(config), str(skills / SKILL_NAME / "SKILL.md")]}
    if target == "cursor":
        path = env.home / ".cursor" / "mcp.json"
        return {"changed": _json_set_server(path, env, target, server_entry(cli) if cli is not None else None),
                "files": [str(path)]}
    raise ValueError(f"Agente desconhecido: {target}. Use: {', '.join(LABELS)}.")


# ---------------------------------------------------------------- state
def _state_path(env: Environment) -> Path:
    return env.data / "state.json"


def _load_state(env: Environment) -> dict:
    try:
        state = json.loads(_state_path(env).read_text(encoding="utf-8"))
        return state if isinstance(state, dict) and isinstance(state.get("targets"), dict) else {"targets": {}}
    except (OSError, ValueError):
        return {"targets": {}}


def _save_state(env: Environment, state: dict) -> None:
    _atomic_write(_state_path(env), json.dumps(state, ensure_ascii=False, indent=2) + "\n")


def _targets(requested, available) -> list[str]:
    if not requested:
        return list(available)
    unknown = [name for name in requested if name not in LABELS]
    if unknown:
        raise ValueError(f"Agente desconhecido: {', '.join(unknown)}. Use: {', '.join(LABELS)}.")
    return list(dict.fromkeys(requested))


# ---------------------------------------------------------------- public API
def status(project_root: Path, env: Environment | None = None) -> dict:
    env = env or Environment()
    found, state = detect(env), _load_state(env)
    return {name: {"label": LABELS[name], "installed": found[name], "connected": name in state["targets"],
                   "connected_at": state["targets"].get(name, {}).get("connected_at")} for name in LABELS}


def connect(project_root: Path, targets=None, env: Environment | None = None, version: str | None = None) -> dict:
    from whisper_dictation._version import __version__
    env, version = env or Environment(), version or __version__
    cli = cli_path(project_root)
    found, state = detect(env), _load_state(env)
    report = {}
    for name in _targets(targets, [name for name, present in found.items() if present]):
        if not found.get(name):
            report[name] = {"label": LABELS[name], "ok": False, "error": f"{LABELS[name]} não encontrado neste usuário."}
            continue
        try:
            outcome = _apply(name, env, cli, version)
            state["targets"][name] = {"connected_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "cli": str(cli),
                                      "version": version, "files": outcome.get("files", [])}
            report[name] = {"label": LABELS[name], "ok": True, **outcome}
        except Exception as exc:
            report[name] = {"label": LABELS[name], "ok": False, "error": f"{type(exc).__name__}: {exc}"}
    _save_state(env, state)
    return report


def disconnect(project_root: Path, targets=None, env: Environment | None = None) -> dict:
    env = env or Environment()
    state = _load_state(env)
    report = {}
    for name in _targets(targets, list(state["targets"]) or [n for n, present in detect(env).items() if present]):
        try:
            outcome = _apply(name, env, None, "")
            state["targets"].pop(name, None)
            report[name] = {"label": LABELS[name], "ok": True, **outcome}
        except Exception as exc:
            report[name] = {"label": LABELS[name], "ok": False, "error": f"{type(exc).__name__}: {exc}"}
    _save_state(env, state)
    return report


def refresh(project_root: Path, env: Environment | None = None, version: str | None = None) -> dict:
    """After an update or a move: rewrite paths/skill for agents already connected."""
    from whisper_dictation._version import __version__
    env, version = env or Environment(), version or __version__
    state = _load_state(env)
    stale = [name for name, info in state["targets"].items()
             if info.get("version") != version or info.get("cli") != str(cli_path(project_root))]
    return connect(project_root, stale, env, version) if stale else {}


def describe(report: dict) -> str:
    if not report:
        return "Nenhum agente de IA encontrado (Claude Code, Claude Desktop, Codex ou Cursor)."
    lines = []
    for name, item in report.items():
        if "installed" in item:
            state = "conectado" if item["connected"] else ("instalado, não conectado" if item["installed"] else "não instalado")
            lines.append(f"{item['label']}: {state}")
        elif item.get("ok"):
            note = f" — {item['note']}" if item.get("note") else ""
            lines.append(f"{item['label']}: ok{' (sem mudanças)' if not item.get('changed') else ''}{note}")
        else:
            lines.append(f"{item['label']}: erro — {item.get('error')}")
    return "\n".join(lines)
