from __future__ import annotations

import ctypes
import logging
import time
from ctypes import wintypes

import pyperclip

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004

VK_CONTROL = 0x11
VK_RETURN = 0x0D
VK_TAB = 0x09
VK_V = 0x56

ULONG_PTR = wintypes.WPARAM
user32 = ctypes.windll.user32


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class INPUTUNION(ctypes.Union):
    _fields_ = [
        ("ki", KEYBDINPUT),
        ("mi", MOUSEINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", wintypes.DWORD),
        ("union", INPUTUNION),
    ]


class TextInserter:
    def __init__(
        self,
        mode: str,
        restore_clipboard: bool,
        typing_interval_ms: int,
        logger: logging.Logger,
    ) -> None:
        self.mode = mode
        self.restore_clipboard = restore_clipboard
        self.typing_interval_seconds = typing_interval_ms / 1000.0
        self.logger = logger

    def insert_text(self, text: str) -> bool:
        if not text.strip():
            return False

        if self.mode == "type":
            self._type_text(text)
            return True

        try:
            self._paste_text(text)
            return True
        except Exception:
            self.logger.exception("Paste failed. Falling back to unicode typing.")
            self._type_text(text)
            return True

    def _paste_text(self, text: str) -> None:
        previous_text = None
        can_restore = False
        if self.restore_clipboard:
            try:
                previous_text = pyperclip.paste()
                can_restore = True
            except pyperclip.PyperclipException:
                self.logger.warning("Clipboard restore disabled because current clipboard could not be read.")

        pyperclip.copy(text)
        time.sleep(0.05)
        _send_key_combo(VK_CONTROL, VK_V)
        time.sleep(0.15)

        if self.restore_clipboard and can_restore and previous_text is not None:
            pyperclip.copy(previous_text)

    def _type_text(self, text: str) -> None:
        for char in text:
            if char == "\n":
                _send_virtual_key(VK_RETURN)
            elif char == "\t":
                _send_virtual_key(VK_TAB)
            else:
                _send_unicode_character(char)

            if self.typing_interval_seconds > 0:
                time.sleep(self.typing_interval_seconds)


def _send_virtual_key(vk_code: int) -> None:
    down = INPUT(type=INPUT_KEYBOARD, union=INPUTUNION(ki=KEYBDINPUT(wVk=vk_code)))
    up = INPUT(
        type=INPUT_KEYBOARD,
        union=INPUTUNION(ki=KEYBDINPUT(wVk=vk_code, dwFlags=KEYEVENTF_KEYUP)),
    )
    _send_inputs([down, up])


def _send_key_combo(modifier_vk: int, key_vk: int) -> None:
    inputs = [
        INPUT(type=INPUT_KEYBOARD, union=INPUTUNION(ki=KEYBDINPUT(wVk=modifier_vk))),
        INPUT(type=INPUT_KEYBOARD, union=INPUTUNION(ki=KEYBDINPUT(wVk=key_vk))),
        INPUT(
            type=INPUT_KEYBOARD,
            union=INPUTUNION(ki=KEYBDINPUT(wVk=key_vk, dwFlags=KEYEVENTF_KEYUP)),
        ),
        INPUT(
            type=INPUT_KEYBOARD,
            union=INPUTUNION(ki=KEYBDINPUT(wVk=modifier_vk, dwFlags=KEYEVENTF_KEYUP)),
        ),
    ]
    _send_inputs(inputs)


def _send_unicode_character(char: str) -> None:
    codepoint = ord(char)
    down = INPUT(
        type=INPUT_KEYBOARD,
        union=INPUTUNION(ki=KEYBDINPUT(wVk=0, wScan=codepoint, dwFlags=KEYEVENTF_UNICODE)),
    )
    up = INPUT(
        type=INPUT_KEYBOARD,
        union=INPUTUNION(
            ki=KEYBDINPUT(
                wVk=0,
                wScan=codepoint,
                dwFlags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP,
            )
        ),
    )
    _send_inputs([down, up])


def _send_inputs(inputs: list[INPUT]) -> None:
    array_type = INPUT * len(inputs)
    sent = user32.SendInput(len(inputs), array_type(*inputs), ctypes.sizeof(INPUT))
    if sent != len(inputs):
        raise ctypes.WinError()

