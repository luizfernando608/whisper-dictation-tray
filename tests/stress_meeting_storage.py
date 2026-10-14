"""Opt-in two-hour synthetic persisted audio stress; never opens audio devices."""
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import tempfile
import time

import numpy as np

from whisper_dictation.meeting_audio import recover_recording


class Counters(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [(name, ctypes.c_size_t) for name in ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]


def peak_memory():
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    kernel = ctypes.windll.kernel32
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    query = ctypes.windll.psapi.GetProcessMemoryInfo
    query.argtypes = (wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD)
    query.restype = wintypes.BOOL
    if not query(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        raise ctypes.WinError()
    return counters.PeakWorkingSetSize


if __name__ == "__main__":
    duration, rate, size = 7200, 16000, 4096
    frames = duration * rate
    baseline = peak_memory()
    start = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="pace-synthetic-stress-") as folder:
        root = Path(folder)
        (root / "capture.json").write_text(json.dumps(dict(duration=duration, sources={name: dict(rate=rate) for name in ("microphone", "computer")})))
        for source, frequency in (("microphone", 400), ("computer", 800)):
            signal = (.3 * np.sin(np.arange(size) * 2 * np.pi * frequency / rate)).astype("<f4").tobytes()
            with (root / (source + ".pcm")).open("wb") as raw, (root / (source + ".jsonl")).open("w") as journal:
                for first in range(0, frames, size):
                    count = min(size, frames-first)
                    raw.write(signal[:count*4])
                    journal.write(json.dumps(dict(offset=first*4, frames=count, start=first/rate)) + "\n")
        audio = recover_recording(root)
        expected = frames * 2
        assert expected <= audio.stat().st_size <= expected + 512
        growth = peak_memory() - baseline
        assert growth < 96 * 1024 * 1024, f"Unexpected working-set growth {growth}"
        print(json.dumps(dict(duration_seconds=duration, source_count=2, output_bytes=audio.stat().st_size, baseline_peak_bytes=baseline, peak_bytes=peak_memory(), peak_growth_bytes=growth, elapsed_seconds=round(time.perf_counter()-start, 2))))
