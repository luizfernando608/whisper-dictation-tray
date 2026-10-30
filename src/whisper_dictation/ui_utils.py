"""Close a Tk interpreter without leaving its scheduled callbacks behind."""
from __future__ import annotations

import tkinter as tk
import os


def window_work_area(root):
    """Physical monitor coordinates, excluding the Windows taskbar."""
    root.update_idletasks()
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        class MonitorInfo(ctypes.Structure):
            _fields_ = [("size", wintypes.DWORD), ("monitor", wintypes.RECT),
                        ("work", wintypes.RECT), ("flags", wintypes.DWORD)]
        api = ctypes.windll.user32
        api.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        api.MonitorFromWindow.restype = wintypes.HANDLE
        api.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
        api.GetMonitorInfoW.restype = wintypes.BOOL
        info = MonitorInfo()
        info.size = ctypes.sizeof(info)
        monitor = api.MonitorFromWindow(root.winfo_id(), 2)
        if monitor and api.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return info.work.left, info.work.top, info.work.right, info.work.bottom
    return 0, 0, root.winfo_screenwidth(), max(1, root.winfo_screenheight() - 48)


def fit_window(root, preferred=(1120, 720), minimum=(850, 590)):
    """Fit CTk logical dimensions and physical position to the current monitor."""
    left, top, right, bottom = window_work_area(root)
    scaling = max(0.1, root._get_window_scaling())
    # Leave room for the native title bar and resize borders at the selected DPI.
    available_width = max(1, int((right - left) / scaling) - 16)
    available_height = max(1, int((bottom - top) / scaling) - 48)
    width, height = min(preferred[0], available_width), min(preferred[1], available_height)
    root.minsize(min(minimum[0], available_width), min(minimum[1], available_height))
    x = left + max(0, (right - left - round(width * scaling)) // 2)
    y = top + max(0, (bottom - top - round((height + 48) * scaling)) // 2)
    # CTk scales dimensions, but keeps position offsets in physical pixels.
    root.geometry(f"{width}x{height}+{x}+{y}")


def close_window(root):
    try:
        for token in root.tk.splitlist(root.tk.call("after", "info")):
            # Widgets such as CTkTextbox register timers on themselves. Calling
            # root.after_cancel also deletes their Tcl command from the wrong
            # owner; its later destroy would try to delete the command twice.
            root.tk.call("after", "cancel", token)
    except tk.TclError:
        pass
    root.destroy()
