from __future__ import annotations

import ctypes
from ctypes import wintypes
import math
import os
import queue
import threading
import time
from typing import Literal

from PIL import Image, ImageDraw, ImageFont, ImageFilter
import numpy as np

_State = Literal["hidden", "recording", "transcribing"]

kernel32 = ctypes.windll.kernel32
user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

# Win32 definitions
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = ctypes.c_longlong
WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_longlong, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class SIZE(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [
        ("BlendOp", ctypes.c_ubyte),
        ("BlendFlags", ctypes.c_ubyte),
        ("SourceConstantAlpha", ctypes.c_ubyte),
        ("AlphaFormat", ctypes.c_ubyte),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [
        ("bmiHeader", BITMAPINFOHEADER),
        ("bmiColors", wintypes.DWORD * 3),
    ]


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HICON),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm", wintypes.HICON),
    ]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", POINT),
    ]


def _def_window_proc(hwnd, msg, wparam, lparam):
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


_wndproc_cb = WNDPROC(_def_window_proc)


def _load_best_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "C:/Windows/Fonts/segoeuib.ttf",
        "C:/Windows/Fonts/seguisb.ttf",
        "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
    ]
    for font_path in candidates:
        if os.path.exists(font_path):
            try:
                return ImageFont.truetype(font_path, size)
            except Exception:
                continue
    return ImageFont.load_default()


class RecordingOverlay:
    """True 32-bit hardware alpha floating capsule in modern PACE aesthetic.

    Uses native Windows UpdateLayeredWindow (ULW_ALPHA) with pre-multiplied alpha.
    Eliminates all chroma-key artifacts, color fringes, and staircase aliasing.
    Includes an ambient gaussian drop shadow and fluid harmonic acoustic wave.
    """

    def __init__(self) -> None:
        self._q: queue.Queue[_State] = queue.Queue()
        self._state: _State = "hidden"
        self._step = 0
        self._visible = False
        self._stop = threading.Event()

        # Physical geometry: capsule 340x60 with 18px soft shadow margins
        self._w = 340
        self._h = 60
        self._pad = 18
        self._total_w = self._w + self._pad * 2
        self._total_h = self._h + self._pad * 2

        self._hwnd = 0
        self._hdc_mem = 0
        self._hbitmap = 0
        self._ppv_bits = ctypes.c_void_p()
        self._hdc_screen = 0

        self._font = _load_best_font(26)  # 2x supersampled
        self._cached_shadow = self._build_shadow_cache()

        self._thread = threading.Thread(target=self._run, daemon=True, name="PaceOverlayThread")
        self._thread.start()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def show_recording(self) -> None:
        self._q.put("recording")

    def show_transcribing(self) -> None:
        self._q.put("transcribing")

    def hide(self) -> None:
        self._q.put("hidden")

    def stop(self) -> None:
        self._stop.set()
        self._q.put("hidden")

    # ---------------------------- internal ----------------------------

    def _build_shadow_cache(self) -> Image.Image:
        scale = 2
        tw, th = self._total_w * scale, self._total_h * scale
        shadow = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
        sdraw = ImageDraw.Draw(shadow)
        pill_box = (
            self._pad * scale,
            self._pad * scale,
            (self._w + self._pad) * scale,
            (self._h + self._pad) * scale,
        )
        r = (self._h // 2) * scale
        sdraw.rounded_rectangle(pill_box, radius=r, fill=(0, 0, 0, 160))
        return shadow.filter(ImageFilter.GaussianBlur(10 * scale))

    def _run(self) -> None:
        # DPI Awareness
        try:
            ctypes.windll.user32.SetProcessDpiAwarenessContext(-4)
        except Exception:
            pass

        hinst = kernel32.GetModuleHandleW(None)
        cls_name = "PACE_NativeLayeredCapsule"

        wndclass = WNDCLASSEXW()
        wndclass.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wndclass.style = 0
        wndclass.lpfnWndProc = _wndproc_cb
        wndclass.cbClsExtra = 0
        wndclass.cbWndExtra = 0
        wndclass.hInstance = hinst
        wndclass.hIcon = 0
        wndclass.hCursor = user32.LoadCursorW(0, 32512)
        wndclass.hbrBackground = 0
        wndclass.lpszMenuName = None
        wndclass.lpszClassName = cls_name
        wndclass.hIconSm = 0

        user32.RegisterClassExW(ctypes.byref(wndclass))

        WS_POPUP = 0x80000000
        WS_EX_LAYERED = 0x00080000
        WS_EX_TOPMOST = 0x00000008
        WS_EX_TOOLWINDOW = 0x00000080
        WS_EX_NOACTIVATE = 0x08000000

        sw = user32.GetSystemMetrics(0)
        sh = user32.GetSystemMetrics(1)
        x = (sw - self._total_w) // 2
        y = int(sh * 0.86)

        hwnd = user32.CreateWindowExW(
            WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
            cls_name,
            "PACE Overlay",
            WS_POPUP,
            x,
            y,
            self._total_w,
            self._total_h,
            0,
            0,
            hinst,
            0,
        )
        self._hwnd = hwnd

        # Persistent memory DC and 32-bit DIB section
        self._hdc_screen = user32.GetDC(0)
        self._hdc_mem = gdi32.CreateCompatibleDC(self._hdc_screen)

        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = self._total_w
        bmi.bmiHeader.biHeight = -self._total_h  # Top-down DIB
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = 0

        self._hbitmap = gdi32.CreateDIBSection(
            self._hdc_screen, ctypes.byref(bmi), 0, ctypes.byref(self._ppv_bits), 0, 0
        )
        gdi32.SelectObject(self._hdc_mem, self._hbitmap)

        msg = MSG()
        pt_dst = POINT(x, y)
        sz = SIZE(self._total_w, self._total_h)
        pt_src = POINT(0, 0)
        blend = BLENDFUNCTION(0, 0, 255, 1)  # AC_SRC_OVER, 0, 255, AC_SRC_ALPHA

        while not self._stop.is_set():
            # Process state queue
            try:
                while True:
                    new_state = self._q.get_nowait()
                    if new_state != self._state:
                        self._state = new_state
                        if new_state == "hidden" and self._visible:
                            user32.ShowWindow(self._hwnd, 0)  # SW_HIDE
                            self._visible = False
            except queue.Empty:
                pass

            # Dispatch Win32 messages
            while user32.PeekMessageW(ctypes.byref(msg), self._hwnd, 0, 0, 1):
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))

            if self._state != "hidden":
                self._step += 1
                raw_bytes = self._render_frame_bytes()
                ctypes.memmove(self._ppv_bits, raw_bytes, len(raw_bytes))

                user32.UpdateLayeredWindow(
                    self._hwnd,
                    self._hdc_screen,
                    ctypes.byref(pt_dst),
                    ctypes.byref(sz),
                    self._hdc_mem,
                    ctypes.byref(pt_src),
                    0,
                    ctypes.byref(blend),
                    2,  # ULW_ALPHA
                )

                if not self._visible:
                    user32.ShowWindow(self._hwnd, 4)  # SW_SHOWNOACTIVATE
                    self._visible = True

            time.sleep(0.033)  # ~30 FPS fluid update

        # Cleanup
        if self._hbitmap:
            gdi32.DeleteObject(self._hbitmap)
        if self._hdc_mem:
            gdi32.DeleteDC(self._hdc_mem)
        if self._hdc_screen:
            user32.ReleaseDC(0, self._hdc_screen)
        if self._hwnd:
            user32.DestroyWindow(self._hwnd)

    def _render_frame_bytes(self) -> bytes:
        scale = 2
        tw, th = self._total_w * scale, self._total_h * scale
        pad = self._pad * scale
        pw, ph = self._w * scale, self._h * scale
        r = (self._h // 2) * scale

        # Start with cached ambient drop shadow
        img = self._cached_shadow.copy()

        # Pill background: deep obsidian with satin glass border
        pill_box = (pad, pad, pad + pw, pad + ph)
        pill = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
        pdraw = ImageDraw.Draw(pill)
        pdraw.rounded_rectangle(
            pill_box,
            radius=r,
            fill=(13, 16, 23, 246),
            outline=(45, 55, 78, 220),
            width=int(1.5 * scale),
        )
        img = Image.alpha_composite(img, pill)
        draw = ImageDraw.Draw(img)

        cy = pad + ph // 2
        state = self._state

        # High-definition color palette
        red_bright = (248, 113, 113, 255)
        red_core = (239, 68, 68, 255)
        red_glow = (239, 68, 68, 55)

        green_bright = (52, 211, 153, 255)
        green_core = (16, 185, 129, 255)
        green_glow = (16, 185, 129, 55)

        slate_bar = (100, 116, 139, 180)

        # Dynamic 7-bar harmonic acoustic waveform
        num_bars = 7
        bar_w = int(3.5 * scale)
        pitch = 6.8 * scale
        start_x = pad + 38 * scale
        base_h = [8, 14, 22, 30, 22, 14, 8]

        if state == "recording":
            active_color = red_core
            accent_color = red_bright
            label_text = "PACE • Gravando"
            heights = [
                (
                    base_h[i]
                    + 5.0 * math.sin(self._step * 0.18 + i * 0.85)
                    + 2.5 * math.cos(self._step * 0.28 + i * 1.4)
                )
                * scale
                for i in range(num_bars)
            ]
        else:
            active_color = green_core
            accent_color = green_bright
            label_text = "PACE • Processando"
            pulse = 4.0 * math.sin(self._step * 0.14)
            heights = [
                (base_h[i] + (pulse if 2 <= i <= 4 else pulse * 0.4)) * scale
                for i in range(num_bars)
            ]

        for i in range(num_bars):
            bx = start_x + (i - (num_bars // 2)) * pitch
            bh = max(5.0 * scale, heights[i])
            is_center = i == num_bars // 2
            col = accent_color if is_center else (active_color if 1 <= i <= 5 else slate_bar)
            rx = bar_w / 2.0
            draw.rounded_rectangle(
                (bx - rx, cy - bh / 2.0, bx + rx, cy + bh / 2.0),
                radius=rx,
                fill=col,
            )

        # Crisp anti-aliased Segoe UI typography
        if self._font:
            bbox = draw.textbbox((0, 0), label_text, font=self._font)
            tw_text = bbox[2] - bbox[0]
            th_text = bbox[3] - bbox[1]
            tx = pad + (pw - tw_text) // 2 + int(12 * scale)
            ty = cy - th_text // 2 - int(1 * scale)
            draw.text((tx, ty), label_text, font=self._font, fill=(255, 255, 255, 245))

        # Breathing luminous orb on the right
        px = pad + pw - 34 * scale
        py = cy
        if state == "recording":
            pulse = (4.5 + 2.0 * math.sin(self._step * 0.16)) * scale
            glow_r = pulse + 5.0 * scale
            draw.ellipse((px - glow_r, py - glow_r, px + glow_r, py + glow_r), fill=red_glow)
            draw.ellipse((px - pulse, py - pulse, px + pulse, py + pulse), fill=red_core)
            hs = pulse * 0.4
            draw.ellipse((px - hs, py - hs, px + hs, py + hs), fill=red_bright)
        else:
            pulse = (4.0 + 1.5 * math.sin(self._step * 0.10)) * scale
            glow_r = pulse + 4.5 * scale
            draw.ellipse((px - glow_r, py - glow_r, px + glow_r, py + glow_r), fill=green_glow)
            draw.ellipse((px - pulse, py - pulse, px + pulse, py + pulse), fill=green_core)
            hs = pulse * 0.4
            draw.ellipse((px - hs, py - hs, px + hs, py + hs), fill=green_bright)

        # Downsample with Lanczos for anti-aliasing
        final = img.resize((self._total_w, self._total_h), Image.Resampling.LANCZOS)

        # Vectorized pre-multiplication for Windows AC_SRC_ALPHA
        arr = np.frombuffer(final.tobytes(), dtype=np.uint8).reshape((self._total_h, self._total_w, 4))
        a = arr[:, :, 3].astype(np.uint16)
        r = ((arr[:, :, 0].astype(np.uint16) * a + 127) >> 8).astype(np.uint8)
        g = ((arr[:, :, 1].astype(np.uint16) * a + 127) >> 8).astype(np.uint8)
        b = ((arr[:, :, 2].astype(np.uint16) * a + 127) >> 8).astype(np.uint8)
        bgra = np.dstack((b, g, r, arr[:, :, 3]))
        return bgra.tobytes()
