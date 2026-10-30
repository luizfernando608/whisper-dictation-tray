"""Validated release downloads and a helper outside the running installation."""
from __future__ import annotations

import hashlib
import errno
import http.client
import json
import logging
import os
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from whisper_dictation._version import __version__

REPO = "luizfernando608/whisper-dictation-tray"
_API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
_INSTALLER_NAME = "Pace_Installer.exe"
_logger = logging.getLogger("whisper_dictation.updater")
_download_lock = threading.Lock()


class UpdateError(RuntimeError):
    """Release check, preparation or application failed."""


class _InstallerIntegrityError(UpdateError):
    """An official download may be retried, but its current bytes cannot run."""


@dataclass(frozen=True)
class UpdateInfo:
    version: str
    tag: str
    installer_url: str
    notes: str = ""
    sha256: str = ""
    size: int = 0


def current_version() -> str:
    return __version__


def update_cache_root() -> Path:
    local = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
    return local / "PaceData" / "updates"


def _filesystem_path(path: Path | str) -> str:
    """Use only for file I/O after checking logical containment/reparse points.

    Extended Win32 paths do not require changing LongPathsEnabled. Keep these
    aliases out of plans, receipts, subprocess arguments and containment checks.
    """
    absolute = os.path.abspath(path)
    if os.name != "nt" or absolute.startswith("\\\\?\\"):
        return absolute
    if absolute.startswith("\\\\"):
        return "\\\\?\\UNC\\" + absolute[2:]
    return "\\\\?\\" + absolute


def _logical_path(path: Path | str) -> Path:
    value = os.fspath(path)
    if os.name == "nt":
        if value.lower().startswith("\\\\?\\unc\\"):
            value = "\\\\" + value[8:]
        elif value.startswith("\\\\?\\"):
            value = value[4:]
    return Path(value)


def _remove_cached_path(path: Path, cache: Path) -> bool:
    """Prune only resolved cache descendants, refusing links into other folders."""
    cache, path = cache.resolve(), Path(path)
    if path == cache or not path.resolve().is_relative_to(cache):
        return False
    if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
        return False
    try:
        if path.is_dir():
            def fail_scan(error):
                raise error
            for directory, children, files in os.walk(_filesystem_path(path), followlinks=False, onerror=fail_scan):
                for name in children + files:
                    child = Path(directory) / name
                    logical = _logical_path(child.resolve())
                    if child.is_symlink() or getattr(child, "is_junction", lambda: False)() or not logical.is_relative_to(cache):
                        return False
            shutil.rmtree(_filesystem_path(path))
        elif path.exists():
            path.unlink()
        return True
    except OSError:
        _logger.debug("Cache ainda em uso ou indisponível: %s", path, exc_info=True)
        return False


def cleanup_update_cache(exclude_run: Path | None = None) -> None:
    """Keep the latest successful backup and three failures; prune on later startup.

    Pending runs are left alone because their detached installer may still be
    active. A currently confirming helper is also excluded, including its DLLs.
    """
    cache = update_cache_root().resolve()
    runs = cache / "runs"
    if not runs.is_dir() or runs.is_symlink() or getattr(runs, "is_junction", lambda: False)():
        return
    successes, failures = [], []
    try:
        previous_runs = list(runs.iterdir())
    except OSError:
        _logger.debug("Não foi possível consultar o cache de atualizações.", exc_info=True)
        return
    for run in previous_runs:
        if not re.fullmatch(r"[0-9a-f]{32}", run.name) or not run.is_dir():
            continue
        if run.is_symlink() or getattr(run, "is_junction", lambda: False)() or not run.resolve().is_relative_to(cache):
            continue
        if exclude_run is not None and run.resolve() == Path(exclude_run).resolve():
            continue
        result = run / "result.json"
        if not result.is_file() or result.is_symlink() or not result.resolve().is_relative_to(cache):
            continue
        try:
            outcome = json.loads(result.read_text(encoding="utf-8"))
            status = outcome.get("status")
            if status not in {"updated", "failed"}:
                continue
            (successes if status == "updated" else failures).append((result.stat().st_mtime_ns, run))
            _remove_cached_path(run / "helper", cache)
        except (OSError, ValueError, TypeError, AttributeError):
            continue
    for items, keep in ((successes, 1), (failures, 3)):
        for _, run in sorted(items, reverse=True)[keep:]:
            _remove_cached_path(run, cache)


def _parse_version(text: str) -> tuple[int, ...]:
    cleaned = text.strip().lstrip("vV")
    if not re.fullmatch(r"\d+\.\d+\.\d+", cleaned):
        raise UpdateError(f"Versão de atualização inválida: {text!r}.")
    return tuple(int(part) for part in cleaned.split("."))


def _is_newer(latest: str, current: str) -> bool:
    return _parse_version(latest) > _parse_version(current)


def _find_installer_asset(data: dict) -> str | None:
    for asset in data.get("assets", []) or []:
        if asset.get("name") == _INSTALLER_NAME:
            return asset.get("browser_download_url")
    return None


def _validate_update(update: UpdateInfo) -> None:
    _parse_version(update.version)
    if update.tag.lstrip("vV") != update.version:
        raise UpdateError("A versão do pacote não corresponde à tag publicada.")
    if not re.fullmatch(r"[a-fA-F0-9]{64}", update.sha256):
        raise UpdateError("O release não fornece SHA256 válido para o instalador.")
    if update.size <= 0:
        raise UpdateError("O tamanho publicado do instalador é inválido.")
    url = urlparse(update.installer_url)
    expected = f"/{REPO}/releases/download/{update.tag}/{_INSTALLER_NAME}"
    if url.scheme != "https" or url.netloc != "github.com" or url.path != expected or url.query or url.fragment:
        raise UpdateError("O instalador deve pertencer ao release do repositório oficial do PACE.")


def check_for_update(timeout: float = 10.0) -> UpdateInfo | None:
    """None means a valid response with no newer version. Network errors raise."""
    try:
        request = urllib.request.Request(_API_LATEST, headers={
            "Accept": "application/vnd.github+json", "User-Agent": "PACE-App"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
        if not isinstance(data, dict) or not data.get("tag_name"):
            raise UpdateError("O GitHub retornou um release sem versão válida.")
        tag = str(data["tag_name"]).strip()
        if not _is_newer(tag, __version__):
            return None
        matches = [a for a in data.get("assets", []) if a.get("name") == _INSTALLER_NAME]
        if len(matches) != 1:
            raise UpdateError(f"O release precisa conter exatamente um {_INSTALLER_NAME}.")
        asset = matches[0]
        digest = str(asset.get("digest") or "")
        update = UpdateInfo(tag.lstrip("vV"), tag, str(asset.get("browser_download_url") or ""),
            str(data.get("body") or ""), digest[7:] if digest.startswith("sha256:") else "",
            int(asset.get("size") or 0))
        _validate_update(update)
        return update
    except UpdateError:
        raise
    except Exception as exc:
        _logger.warning("Falha ao verificar atualização no GitHub.", exc_info=True)
        raise UpdateError("Não foi possível verificar atualizações. Confira sua conexão e tente novamente.") from exc


def validate_installer(path: Path, sha256: str, size: int) -> None:
    if path.stat().st_size != size:
        raise _InstallerIntegrityError("O instalador está incompleto ou tem tamanho diferente do publicado.")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != sha256.lower():
        raise _InstallerIntegrityError("A verificação SHA256 do instalador falhou; o pacote não será executado.")


def _retryable_download_error(error: Exception) -> bool:
    if isinstance(error, urllib.error.HTTPError):
        return error.code in {408, 425, 429, 500, 502, 503, 504}
    if isinstance(error, urllib.error.URLError):
        return not isinstance(error.reason, ssl.SSLCertVerificationError)
    if isinstance(error, (_InstallerIntegrityError, http.client.IncompleteRead,
                          ConnectionError, TimeoutError, ssl.SSLEOFError)):
        return True
    return isinstance(error, OSError) and error.errno in {
        errno.ECONNRESET, errno.ECONNABORTED, errno.ENETRESET, errno.ENETUNREACH,
        errno.EHOSTUNREACH, errno.ETIMEDOUT,
    }


def _download_once(update: UpdateInfo, destination: Path, timeout: float) -> None:
    descriptor, partial_name = tempfile.mkstemp(prefix="Pace-", suffix=".partial", dir=destination.parent)
    partial = Path(partial_name)
    try:
        request = urllib.request.Request(update.installer_url, headers={"User-Agent": "PACE-App"})
        with os.fdopen(descriptor, "wb") as output:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                received = 0
                while chunk := response.read(65536):
                    received += len(chunk)
                    if received > update.size:
                        raise _InstallerIntegrityError("O download excedeu o tamanho publicado do instalador.")
                    output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        validate_installer(partial, update.sha256, update.size)
        partial.replace(destination)
    finally:
        partial.unlink(missing_ok=True)


def download_installer(update: UpdateInfo, dest_dir: str | None = None, timeout: float = 120.0) -> str:
    """Publish only a complete validated file; never execute a partial download."""
    _validate_update(update)
    directory = Path(dest_dir) if dest_dir else update_cache_root() / update.version
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / _INSTALLER_NAME
    with _download_lock:
        if destination.exists():
            try:
                validate_installer(destination, update.sha256, update.size)
                return str(destination)
            except UpdateError:
                _logger.warning("Pacote em cache inválido; baixando novamente.")
        for attempt in range(3):
            try:
                _download_once(update, destination, timeout)
                break
            except Exception as exc:
                if isinstance(exc, urllib.error.HTTPError):
                    exc.close()
                if attempt < 2 and _retryable_download_error(exc):
                    _logger.warning("Download não validado; repetindo tentativa %s de 3 (%s).", attempt + 2, type(exc).__name__)
                    time.sleep(0.25 * (attempt + 1))
                    continue
                if isinstance(exc, UpdateError):
                    raise
                raise UpdateError("Não foi possível baixar a atualização; o aplicativo continua disponível.") from exc
    return str(destination)


def prepare_update(update: UpdateInfo) -> str:
    """Run in a background worker; does not launch or close any process."""
    return download_installer(update)


def hidden_process_options() -> dict:
    options: dict = {"close_fds": True}
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
        options["startupinfo"] = startup
    return options


def _wait_helper_ready(process: subprocess.Popen, plan: dict, staging: Path, timeout: float = 30.0) -> None:
    """Verify a receipt from this owned helper before allowing the caller to exit."""
    ready = staging / "ready.json"
    expected = {"version": plan["version"], "target": str(Path(plan["target"]).resolve()),
                "parent_pid": plan["parent_pid"], "token": plan["token"], "helper_pid": process.pid}
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise UpdateError("O auxiliar de atualização encerrou antes de confirmar prontidão. O PACE continua aberto.")
            if ready.exists():
                if ready.is_symlink() or ready.resolve().parent != staging.resolve():
                    raise UpdateError("Confirmação de prontidão fora do diretório de atualização.")
                try:
                    receipt = json.loads(ready.read_text(encoding="utf-8"))
                except (OSError, ValueError) as exc:
                    raise UpdateError("O auxiliar retornou uma confirmação de prontidão inválida.") from exc
                if receipt != expected or process.poll() is not None:
                    raise UpdateError("A confirmação de prontidão não corresponde ao auxiliar iniciado.")
                return
            time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
        raise UpdateError("O auxiliar não confirmou prontidão a tempo. O PACE continua aberto.")
    except Exception:
        if process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        raise


def restart_to_update(update: UpdateInfo, project_root: Path) -> int:
    """Stage helper outside installation; caller then closes UI and tray safely."""
    installer = Path(prepare_update(update)).resolve()
    target = Path(project_root).resolve()
    executable = target / "Pace.exe"
    if not executable.is_file():
        raise UpdateError("Reiniciar para atualizar está disponível somente na instalação empacotada do PACE.")
    if getattr(sys, "frozen", False) and Path(sys.executable).resolve() != executable:
        raise UpdateError("A pasta de atualização não corresponde ao aplicativo em execução.")
    cache = update_cache_root().resolve()
    if target.parent == target or cache == target or target in cache.parents or cache in target.parents:
        raise UpdateError("O cache de atualização deve ficar separado da pasta de instalação.")
    staging = cache / "runs" / uuid.uuid4().hex
    staging.mkdir(parents=True)
    plan = {"parent_pid": os.getpid(), "target": str(target), "installer": str(installer),
        "version": update.version, "sha256": update.sha256, "size": update.size,
        "token": uuid.uuid4().hex, "receipt": str(staging / "confirmed.json")}
    plan_path = staging / "plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    if getattr(sys, "frozen", False):
        # Running PyInstaller EXEs and DLLs cannot be overwritten. Copy only
        # runtime binaries, never configuration, models or recordings.
        runtime = staging / "helper"
        runtime.mkdir()
        helper = runtime / "PaceUpdateHelper.exe"
        internal = target / "_internal"
        if internal.is_symlink() or getattr(internal, "is_junction", lambda: False)():
            raise UpdateError("O runtime do PACE deve estar dentro da pasta de instalação.")
        shutil.copy2(_filesystem_path(executable), _filesystem_path(helper))
        shutil.copytree(_filesystem_path(internal), _filesystem_path(runtime / "_internal"))
        command = [str(helper), "--apply-update", "--update-plan", str(plan_path)]
    else:
        main = Path(__file__).resolve().parents[2] / "main.py"
        command = [sys.executable, str(main), "--apply-update", "--update-plan", str(plan_path)]
    environment = os.environ.copy()
    environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    with (staging / "bootstrap.log").open("ab") as bootstrap:
        process = subprocess.Popen(command, cwd=staging, env=environment,
            stdout=bootstrap, stderr=subprocess.STDOUT, **hidden_process_options())
    _wait_helper_ready(process, plan, staging)
    return process.pid


def confirm_running_version(project_root: Path) -> bool:
    """Call after acquiring the instance and successfully initializing the tray."""
    receipt_name = os.environ.pop("PACE_UPDATE_CONFIRMATION", "")
    token = os.environ.pop("PACE_UPDATE_TOKEN", "")
    if not receipt_name or not token:
        cleanup_update_cache()
        return False
    receipt = Path(receipt_name).resolve()
    if update_cache_root().resolve() not in receipt.parents or receipt.name != "confirmed.json":
        _logger.error("Caminho de confirmação de atualização inválido.")
        return False
    payload = {"version": __version__, "pid": os.getpid(),
        "target": str(Path(project_root).resolve()), "token": token}
    partial = receipt.with_suffix(".partial")
    partial.write_text(json.dumps(payload), encoding="utf-8")
    partial.replace(receipt)
    cleanup_update_cache(exclude_run=receipt.parent)
    return True


def launch_installer(path: str, silent: bool = False) -> None:
    """Legacy manual installer API; restart uses the validated helper."""
    args = [path]
    if silent:
        args += ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-"]
    subprocess.Popen(args, **hidden_process_options())


def download_and_launch(update: UpdateInfo, on_status: Callable[[str], None] | None = None, silent: bool = False) -> None:
    if on_status:
        on_status("Baixando atualização…")
    path = prepare_update(update)
    if on_status:
        on_status("Iniciando instalador…")
    launch_installer(path, silent=silent)
