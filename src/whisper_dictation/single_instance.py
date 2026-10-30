"""Single-instance guard + tiny control channel over a loopback socket.

The first process to start binds a fixed 127.0.0.1 port and becomes the owner
(it runs the tray and listens for commands). Launching the app again — e.g. via
the "Configurações" shortcut with ``--settings`` — fails to bind, so instead it
sends a command to the running owner and exits. This both prevents a duplicate
tray icon and lets an external shortcut drive the already-running instance.

Loopback-only, so it never triggers a Windows firewall prompt, and needs no
third-party dependency.
"""

from __future__ import annotations

import logging
import json
import socket
import threading
from typing import Callable

HOST = "127.0.0.1"
DEFAULT_PORT = 49517
OPEN_SETTINGS = "open-settings"
RELOAD_CONFIG = "reload-config"
COPY_LAST_TRANSCRIPT = "copy-last-transcript"
QUIT = "quit"
PING = "ping"
PONG = "pong"
OPEN_RECORDINGS = "open-recordings"
MEETING_TOGGLE = "meeting-toggle"
MEETING_PAUSE = "meeting-pause"
APPLY_UPDATE = "apply-update"
CAPABILITIES = "capabilities"
SAVE_SETTINGS = "save-settings"
IMPORT_MEDIA = "import-media"

_ENCODING = "utf-8"
_logger = logging.getLogger("whisper_dictation.ipc")


class SingleInstance:
    def __init__(self, port: int = DEFAULT_PORT) -> None:
        self.port = port
        self._server: socket.socket | None = None
        self._handlers: dict[str, Callable[[], None]] = {}
        self._requests: dict[str, Callable[[dict], dict]] = {}
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def try_acquire(self) -> bool:
        """Return True if this is the first instance (port bound successfully)."""
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            server.bind((HOST, self.port))
            server.listen(5)
        except OSError:
            server.close()
            return False
        server.settimeout(0.5)
        self._server = server
        return True

    def register(self, command: str, handler: Callable[[], None]) -> None:
        self._handlers[command] = handler

    def register_request(self, command: str, handler: Callable[[dict], dict]) -> None:
        self._requests[command] = handler

    def start_listener(self) -> None:
        if self._server is None:
            raise RuntimeError("try_acquire() precisa ser chamado (com sucesso) antes.")
        self._thread = threading.Thread(target=self._serve, name="ControlChannel", daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        assert self._server is not None
        server = self._server
        while not self._stop.is_set():
            try:
                conn, _ = server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with conn:
                try:
                    conn.settimeout(3)
                    data = conn.recv(65536)
                    if data.startswith(b"{"):
                        while b"\n" not in data and len(data) < 65536:
                            chunk = conn.recv(min(4096, 65536 - len(data)))
                            if not chunk:
                                break
                            data += chunk
                        payload = json.loads(data.decode(_ENCODING))
                        handler = self._requests.get(payload.get("command"))
                        try:
                            response = handler(payload) if handler else {"ok": False, "error": "Comando indisponível."}
                        except Exception:
                            _logger.exception("Falha ao executar solicitação do canal de controle.")
                            response = {"ok": False, "error": "Não foi possível concluir a solicitação. Consulte os logs do PACE."}
                        conn.sendall(json.dumps(response, ensure_ascii=False).encode(_ENCODING) + b"\n")
                        continue
                    command = data.decode(_ENCODING).strip()
                except (OSError, ValueError, TypeError, AttributeError):
                    continue
                if command == PING:
                    try:
                        conn.sendall(PONG.encode(_ENCODING))
                    except OSError:
                        pass
                    continue
                handler = self._handlers.get(command)
                if handler is None:
                    _logger.debug("Comando de controle desconhecido.")
                    continue
                try:
                    result = handler()
                    if result is not None:
                        conn.sendall(json.dumps(result, ensure_ascii=False).encode(_ENCODING) + b"\n")
                except Exception:
                    _logger.exception("Handler do canal de controle falhou: %s", command)

    def stop(self) -> None:
        self._stop.set()
        if self._server is not None:
            try:
                self._server.close()
            except OSError:
                pass
            self._server = None


def send_command(command: str, port: int = DEFAULT_PORT, timeout: float = 1.0) -> bool:
    """Send a command to the running instance. Returns False if none answered."""
    try:
        with socket.create_connection((HOST, port), timeout=timeout) as conn:
            conn.sendall(command.encode(_ENCODING))
        return True
    except OSError:
        return False


def probe(port: int = DEFAULT_PORT, timeout: float = 1.0) -> bool:
    """Return True only if OUR running instance answers on ``port``.

    Distinguishes "the app is already running" from "some unrelated process
    happens to hold this port", so a port collision doesn't silently prevent
    startup.
    """
    try:
        with socket.create_connection((HOST, port), timeout=timeout) as conn:
            conn.sendall(PING.encode(_ENCODING))
            conn.settimeout(timeout)
            reply = conn.recv(64).decode(_ENCODING).strip()
        return reply == PONG
    except OSError:
        return False


def request(command: str | dict, port: int = DEFAULT_PORT, timeout: float = 30.0) -> dict:
    """Bounded transient IPC; credential values are never written to disk/logs."""
    wire = json.dumps(command, ensure_ascii=False) if isinstance(command, dict) else command
    encoded = wire.encode(_ENCODING) + b"\n"
    if len(encoded) > 65536:
        raise ValueError("Pedido de configurações muito grande.")
    with socket.create_connection((HOST, port), timeout=timeout) as conn:
        conn.settimeout(timeout)
        conn.sendall(encoded)
        data = bytearray()
        while b"\n" not in data and len(data) < 65536:
            chunk = conn.recv(min(4096, 65536 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
    if not data:
        raise RuntimeError("Esta instância do PACE não oferece salvamento seguro. Abra a versão atual.")
    result = json.loads(data.decode(_ENCODING))
    if not isinstance(result, dict):
        raise RuntimeError("Resposta inválida do PACE.")
    return result
