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

_ENCODING = "utf-8"
_logger = logging.getLogger("whisper_dictation.ipc")


class SingleInstance:
    def __init__(self, port: int = DEFAULT_PORT) -> None:
        self.port = port
        self._server: socket.socket | None = None
        self._handlers: dict[str, Callable[[], None]] = {}
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

    def start_listener(self) -> None:
        if self._server is None:
            raise RuntimeError("try_acquire() precisa ser chamado (com sucesso) antes.")
        self._thread = threading.Thread(target=self._serve, name="ControlChannel", daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        assert self._server is not None
        while not self._stop.is_set():
            try:
                conn, _ = self._server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with conn:
                try:
                    command = conn.recv(1024).decode(_ENCODING).strip()
                except OSError:
                    continue
                if command == PING:
                    try:
                        conn.sendall(PONG.encode(_ENCODING))
                    except OSError:
                        pass
                    continue
                handler = self._handlers.get(command)
                if handler is None:
                    _logger.debug("Comando de controle desconhecido: %r", command)
                    continue
                try:
                    handler()
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
