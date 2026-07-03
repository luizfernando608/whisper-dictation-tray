"""Free auto-update via GitHub Releases.

The app is distributed through GitHub Releases (unlimited free hosting). On
startup we ask the GitHub REST API for the latest release, compare its tag with
the embedded ``__version__`` and, when newer, surface an "Atualizar agora"
action that downloads the installer and launches it. Inno Setup
(``CloseApplications=yes``) closes the running app, replaces the files and
relaunches — preserving ``config.json``, logs and the credential vault.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
import urllib.request
from dataclasses import dataclass
from typing import Callable

from whisper_dictation._version import __version__

REPO = "luizfernando608/whisper-dictation-tray"
_API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
_INSTALLER_NAME = "WhisperDictation_Installer.exe"
_FALLBACK_URL = f"https://github.com/{REPO}/releases/latest/download/{_INSTALLER_NAME}"
_DETACHED_PROCESS = 0x00000008

_logger = logging.getLogger("whisper_dictation.updater")


@dataclass(frozen=True)
class UpdateInfo:
    version: str  # normalized, e.g. "1.3.0"
    tag: str  # raw tag, e.g. "v1.3.0"
    installer_url: str
    notes: str = ""


def current_version() -> str:
    return __version__


def _parse_version(text: str) -> tuple[int, ...]:
    cleaned = text.strip().lstrip("vV")
    parts: list[int] = []
    for chunk in cleaned.split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts) or (0,)


def _is_newer(latest: str, current: str) -> bool:
    a, b = _parse_version(latest), _parse_version(current)
    size = max(len(a), len(b))
    a += (0,) * (size - len(a))
    b += (0,) * (size - len(b))
    return a > b


def check_for_update(timeout: float = 10.0) -> UpdateInfo | None:
    """Return an :class:`UpdateInfo` if a newer release exists, else ``None``.

    Never raises: network/offline problems are logged and swallowed so the app
    keeps running normally.
    """
    try:
        request = urllib.request.Request(
            _API_LATEST,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "WhisperDictationTray",
            },
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception:
        _logger.warning("Falha ao verificar atualização no GitHub.", exc_info=True)
        return None

    tag = str(data.get("tag_name") or "").strip()
    if not tag or not _is_newer(tag, __version__):
        return None

    installer_url = _find_installer_asset(data) or _FALLBACK_URL
    return UpdateInfo(
        version=tag.lstrip("vV"),
        tag=tag,
        installer_url=installer_url,
        notes=str(data.get("body") or ""),
    )


def _find_installer_asset(data: dict) -> str | None:
    for asset in data.get("assets", []) or []:
        name = str(asset.get("name") or "").lower()
        if name.endswith(".exe") and "install" in name:
            url = asset.get("browser_download_url")
            if url:
                return str(url)
    return None


def download_installer(
    update: UpdateInfo,
    dest_dir: str | None = None,
    timeout: float = 120.0,
) -> str:
    """Download the installer to a temp file and return its path."""
    dest_dir = dest_dir or tempfile.gettempdir()
    dest = os.path.join(dest_dir, _INSTALLER_NAME)
    request = urllib.request.Request(
        update.installer_url, headers={"User-Agent": "WhisperDictationTray"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response, open(dest, "wb") as out:
        while True:
            chunk = response.read(65536)
            if not chunk:
                break
            out.write(chunk)
    _logger.info("Instalador baixado para %s", dest)
    return dest


def launch_installer(path: str, silent: bool = False) -> None:
    """Launch the installer detached so it survives this process exiting."""
    args = [path]
    if silent:
        args += ["/SILENT"]
    subprocess.Popen(args, close_fds=True, creationflags=_DETACHED_PROCESS)
    _logger.info("Instalador iniciado: %s", path)


def download_and_launch(
    update: UpdateInfo,
    on_status: Callable[[str], None] | None = None,
    silent: bool = False,
) -> None:
    """Convenience: download then launch. Raises on download failure."""
    if on_status:
        on_status("Baixando atualização…")
    path = download_installer(update)
    if on_status:
        on_status("Iniciando instalador…")
    launch_installer(path, silent=silent)
