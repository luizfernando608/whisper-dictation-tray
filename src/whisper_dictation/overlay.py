from __future__ import annotations

import math
import queue
import threading
import tkinter as tk
from typing import Literal

_State = Literal["hidden", "recording", "transcribing"]

_CHROMA = "#010203"   # key colour made transparent (unique enough to avoid clashes)
_BG = "#1c1c1e"
_RED = "#ef4444"
_GREEN = "#34d399"
_WHITE = "#ffffff"
_GREY = "#9ca3af"

_W = 300
_H = 68
_R = 34           # corner radius
_PULSE_MS = 40    # animation tick interval


def _rounded_rect_points(x0: int, y0: int, x1: int, y1: int, r: int, steps: int = 12) -> list[float]:
    """Return polygon point list for a rounded rectangle."""
    pts: list[float] = []
    for cx, cy, start_deg in (
        (x1 - r, y0 + r, -90),
        (x1 - r, y1 - r,   0),
        (x0 + r, y1 - r,  90),
        (x0 + r, y0 + r, 180),
    ):
        for i in range(steps + 1):
            a = math.radians(start_deg + i * 90 / steps)
            pts.append(cx + r * math.cos(a))
            pts.append(cy + r * math.sin(a))
    return pts


class RecordingOverlay:
    """Pill-shaped floating indicator in the Win+H style.

    Runs its own tkinter event loop in a daemon thread so it never blocks
    the main application.  State changes are pushed via a queue and consumed
    inside tkinter's ``after`` scheduler.
    """

    def __init__(self) -> None:
        self._q: queue.Queue[_State] = queue.Queue()
        self._thread = threading.Thread(target=self._run, daemon=True, name="OverlayTk")
        self._thread.start()

    # ------------------------------------------------------------------ #
    # Public API (called from any thread)
    # ------------------------------------------------------------------ #

    def show_recording(self) -> None:
        self._q.put("recording")

    def show_transcribing(self) -> None:
        self._q.put("transcribing")

    def hide(self) -> None:
        self._q.put("hidden")

    # ------------------------------------------------------------------ #
    # tkinter thread — do NOT call tk methods from outside this thread
    # ------------------------------------------------------------------ #

    def _run(self) -> None:
        root = tk.Tk()
        self._root = root

        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.attributes("-transparentcolor", _CHROMA)
        root.configure(bg=_CHROMA)
        root.withdraw()

        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        x = (sw - _W) // 2
        y = sh - _H - 56          # just above the taskbar
        root.geometry(f"{_W}x{_H}+{x}+{y}")

        canvas = tk.Canvas(root, width=_W, height=_H, bg=_CHROMA, highlightthickness=0)
        canvas.pack()
        self._canvas = canvas

        self._state: _State = "hidden"
        self._step = 0

        self._poll()
        root.mainloop()

    def _poll(self) -> None:
        # drain the queue
        try:
            while True:
                new: _State = self._q.get_nowait()
                if new != self._state:
                    self._state = new
                    if new == "hidden":
                        self._root.withdraw()
                    else:
                        self._root.deiconify()
                        self._root.lift()
        except queue.Empty:
            pass

        if self._state != "hidden":
            self._step += 1
            self._redraw()

        self._root.after(_PULSE_MS, self._poll)

    def _redraw(self) -> None:
        c = self._canvas
        c.delete("all")

        # pill background
        pts = _rounded_rect_points(0, 0, _W, _H, _R)
        c.create_polygon(pts, fill=_BG, outline=_BG, smooth=False)

        state = self._state

        # --- microphone icon (left side) ---
        mx, my = 52, _H // 2
        br = 9   # body half-width
        bh = 14  # body half-height above centre
        bb = 5   # body below centre

        # mic body (rounded rect)
        mic_pts = _rounded_rect_points(mx - br, my - bh, mx + br, my + bb, br, steps=8)
        c.create_polygon(mic_pts, fill=_WHITE, outline=_WHITE)

        # stand arm
        arm_r = br + 7
        c.create_arc(mx - arm_r, my - arm_r + bb, mx + arm_r, my + arm_r + bb,
                     start=0, extent=-180, style=tk.ARC, outline=_WHITE, width=2)
        # vertical line
        c.create_line(mx, my + bb + arm_r, mx, my + bb + arm_r + 6, fill=_WHITE, width=2)
        # base line
        c.create_line(mx - 8, my + bb + arm_r + 6, mx + 8, my + bb + arm_r + 6,
                      fill=_WHITE, width=2)

        # --- label ---
        if state == "recording":
            label = "Gravando..."
            dot_color = _RED
        else:
            label = "Transcrevendo..."
            dot_color = _GREEN

        c.create_text(
            _W // 2 + 8, _H // 2,
            text=label,
            fill=_WHITE,
            font=("Segoe UI", 12, "bold"),
            anchor="center",
        )

        # --- pulsing dot (right side) ---
        px, py = _W - 30, _H // 2
        if state == "recording":
            pulse = 6 + 4 * abs(math.sin(self._step * 0.12))
        else:
            # slow breathe for transcribing
            pulse = 5 + 3 * abs(math.sin(self._step * 0.06))

        c.create_oval(
            px - pulse, py - pulse,
            px + pulse, py + pulse,
            fill=dot_color, outline="",
        )
