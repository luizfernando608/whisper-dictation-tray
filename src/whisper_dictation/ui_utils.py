"""Close a Tk interpreter without leaving its scheduled callbacks behind."""
from __future__ import annotations

import tkinter as tk


def close_window(root):
    try:
        for token in root.tk.splitlist(root.tk.call("after", "info")):
            root.after_cancel(token)
    except tk.TclError:
        pass
    root.destroy()
