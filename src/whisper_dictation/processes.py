"""Minimal Windows process inspection without third-party dependencies.

Used to tell whether a transcription job started by an agent is still running
and, during updates, to find processes still executing binaries from the
installation folder. Every function degrades to "unknown" outside Windows.
"""
from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes
from pathlib import Path

_STILL_ACTIVE = 259
_QUERY_LIMITED = 0x1000
_TERMINATE = 0x0001
_SYNCHRONIZE = 0x00100000
_SNAPPROCESS = 0x00000002


class _ProcessEntry(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
                ("szExeFile", wintypes.WCHAR * 260)]


def _kernel():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetExitCodeProcess.restype = wintypes.BOOL
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                  ctypes.POINTER(wintypes.DWORD)]
    kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.TerminateProcess.restype = wintypes.BOOL
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.GetProcessTimes.restype = wintypes.BOOL
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry)]
    kernel.Process32FirstW.restype = wintypes.BOOL
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry)]
    kernel.Process32NextW.restype = wintypes.BOOL
    return kernel


def process_image(pid: int | None) -> Path | None:
    """Executable of a running process, or None when it exited or is inaccessible."""
    if os.name != "nt" or not isinstance(pid, int) or pid <= 0:
        return None
    kernel = _kernel()
    handle = kernel.OpenProcess(_QUERY_LIMITED, False, pid)
    if not handle:
        return None
    try:
        code = wintypes.DWORD()
        if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != _STILL_ACTIVE:
            return None
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return None
        return Path(buffer.value)
    finally:
        kernel.CloseHandle(handle)


def is_running(pid: int | None, image_name: str | None = None) -> bool:
    """True if ``pid`` is alive and, when given, runs an executable with that name.

    The name check guards against Windows reusing the PID of a finished worker.
    """
    image = process_image(pid)
    if image is None:
        return False
    return image_name is None or image.name.lower() == image_name.lower()


def processes_in(directory: Path, exclude: set[int] | None = None) -> list[tuple[int, Path]]:
    """Processes whose executable lives inside ``directory`` (recursively)."""
    if os.name != "nt":
        return []
    exclude = set(exclude or ()) | {os.getpid()}
    root = Path(os.path.normcase(os.path.abspath(directory)))
    kernel = _kernel()
    snapshot = kernel.CreateToolhelp32Snapshot(_SNAPPROCESS, 0)
    if not snapshot or snapshot == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    found = []
    try:
        entry = _ProcessEntry()
        entry.dwSize = ctypes.sizeof(_ProcessEntry)
        more = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            pid = int(entry.th32ProcessID)
            if pid not in exclude:
                image = process_image(pid)
                if image is not None:
                    normalized = Path(os.path.normcase(os.path.abspath(image)))
                    if root in normalized.parents:
                        found.append((pid, image))
            more = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    return found


def terminate(pid: int, timeout: float = 10.0) -> bool:
    """Terminate one process and wait for it; True when it is gone."""
    if os.name != "nt":
        return False
    kernel = _kernel()
    handle = kernel.OpenProcess(_TERMINATE | _SYNCHRONIZE | _QUERY_LIMITED, False, pid)
    if not handle:
        return process_image(pid) is None
    try:
        kernel.TerminateProcess(handle, 1)
        return kernel.WaitForSingleObject(handle, int(timeout * 1000)) == 0
    finally:
        kernel.CloseHandle(handle)


def creation_time(pid: int | None) -> float | None:
    """When a running process started (Unix seconds), or None if unknown/exited."""
    if os.name != "nt" or not isinstance(pid, int) or pid <= 0:
        return None
    kernel = _kernel()
    handle = kernel.OpenProcess(_QUERY_LIMITED, False, pid)
    if not handle:
        return None
    try:
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(item) for item in times)):
            return None
        ticks = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
        return (ticks - 116444736000000000) / 10_000_000
    finally:
        kernel.CloseHandle(handle)


# Processes that may own a recording job: the worker itself (Pace.exe), the tray
# while it starts a worker, or pace-cli while it claims/resumes a job.
OWNER_IMAGES = frozenset({"pace.exe", "pace-cli.exe"})


def owner(pid: int | None = None) -> dict:
    """Store fields identifying the process that owns a job (default: this one)."""
    pid = os.getpid() if pid is None else pid
    return {"worker_pid": pid, "worker_started": creation_time(pid)}


def worker_alive(pid, started: float | None = None) -> bool:
    """A recording worker (the tray's or an agent's) is still running.

    Packaged owners are ``Pace.exe``/``pace-cli.exe``; from source any live PID
    counts. ``started`` (recorded with the PID) rejects a PID that Windows
    reused for an unrelated process after a restart.
    """
    image = process_image(pid)
    if image is None:
        return False
    if getattr(sys, "frozen", False) and image.name.lower() not in OWNER_IMAGES:
        return False
    if started is not None:
        actual = creation_time(pid)
        if actual is not None and abs(actual - float(started)) > 2.0:
            return False
    return True


def record_worker_alive(record: dict) -> bool:
    return worker_alive(record.get("worker_pid"), record.get("worker_started"))
