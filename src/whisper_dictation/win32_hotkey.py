from __future__ import annotations

import ctypes
import logging
import threading
from ctypes import wintypes

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008

VK_CODE_MAP = {
    "tab": 0x09,
    "enter": 0x0D,
    "return": 0x0D,
    "pause": 0x13,
    "capslock": 0x14,
    "esc": 0x1B,
    "escape": 0x1B,
    "space": 0x20,
    "pageup": 0x21,
    "pagedown": 0x22,
    "end": 0x23,
    "home": 0x24,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "insert": 0x2D,
    "delete": 0x2E,
    "backspace": 0x08,
}

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32


def parse_hotkey(hotkey: str) -> tuple[int, int]:
    tokens = [token.strip().lower() for token in hotkey.split("+") if token.strip()]
    if len(tokens) < 2:
        raise ValueError("O atalho precisa ter ao menos um modificador e uma tecla final.")

    modifiers = 0
    for token in tokens[:-1]:
        if token in {"ctrl", "control"}:
            modifiers |= MOD_CONTROL
        elif token == "alt":
            modifiers |= MOD_ALT
        elif token == "shift":
            modifiers |= MOD_SHIFT
        elif token in {"win", "windows"}:
            modifiers |= MOD_WIN
        else:
            raise ValueError(f"Modificador não suportado: {token}")

    key_token = tokens[-1]
    virtual_key = _resolve_virtual_key(key_token)
    return modifiers, virtual_key


def _resolve_virtual_key(token: str) -> int:
    if token in VK_CODE_MAP:
        return VK_CODE_MAP[token]

    if len(token) == 1 and token.isalpha():
        return ord(token.upper())

    if len(token) == 1 and token.isdigit():
        return ord(token)

    if token.startswith("f") and token[1:].isdigit():
        index = int(token[1:])
        if 1 <= index <= 24:
            return 0x6F + index

    raise ValueError(f"Tecla não suportada: {token}")


class GlobalHotkeyManager:
    def __init__(self, hotkey: str, callback, logger: logging.Logger) -> None:
        self.hotkey = hotkey
        self.callback = callback
        self.logger = logger
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._start_ready = threading.Event()
        self._start_error: Exception | None = None
        self._hotkey_id = 1
        self._lock = threading.RLock()

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return

            self._start_ready.clear()
            self._start_error = None
            self._thread = threading.Thread(
                target=self._message_loop,
                name="HotkeyLoop",
                daemon=True,
            )
            self._thread.start()

        if not self._start_ready.wait(timeout=5):
            raise RuntimeError("Timeout ao registrar o atalho global.")
        if self._start_error is not None:
            raise self._start_error

    def stop(self) -> None:
        with self._lock:
            thread = self._thread
            thread_id = self._thread_id
            self._thread = None
            self._thread_id = None

        if thread_id:
            user32.PostThreadMessageW(thread_id, WM_QUIT, 0, 0)
        if thread:
            thread.join(timeout=3)

    def _message_loop(self) -> None:
        self._thread_id = kernel32.GetCurrentThreadId()
        try:
            modifiers, virtual_key = parse_hotkey(self.hotkey)
            if not user32.RegisterHotKey(None, self._hotkey_id, modifiers, virtual_key):
                raise ctypes.WinError()
            self.logger.info("Global hotkey registered: %s", self.hotkey)
        except Exception as exc:
            self._start_error = exc
            self._start_ready.set()
            self.logger.exception("Failed to register global hotkey")
            return

        self._start_ready.set()
        msg = wintypes.MSG()
        try:
            while True:
                result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if result in (0, -1):
                    break
                if msg.message == WM_HOTKEY and msg.wParam == self._hotkey_id:
                    try:
                        self.callback()
                    except Exception:
                        self.logger.exception("Hotkey callback crashed")
        finally:
            user32.UnregisterHotKey(None, self._hotkey_id)
            self.logger.info("Global hotkey unregistered: %s", self.hotkey)
