"""Detached update worker. Never imports the tray, audio or GUI modules."""
from __future__ import annotations

import argparse
import ctypes
import json
import logging
import os
import shutil
import subprocess
import time
from ctypes import wintypes
from pathlib import Path

from whisper_dictation.updater import (
    UpdateError, _parse_version, hidden_process_options, update_cache_root, validate_installer,
)
from whisper_dictation.update_integration import snapshot_integration, restore_integration

_logger = logging.getLogger("whisper_dictation.update_helper")


def wait_for_process_exit(pid: int, timeout: float = 90.0) -> None:
    """Wait on the exact process handle without terminating the original app."""
    if os.name != "nt":
        raise UpdateError("A aplicação de atualizações é suportada somente no Windows.")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
    if not handle:
        if ctypes.get_last_error() == 87:
            return
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        result = kernel.WaitForSingleObject(handle, int(timeout * 1000))
        if result == 0x102:
            raise UpdateError("PACE não encerrou a tempo; a atualização foi adiada.")
        if result != 0:
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel.CloseHandle(handle)


def _read_plan(path: Path) -> dict:
    cache = update_cache_root().resolve()
    path = path.resolve()
    if cache not in path.parents or path.name != "plan.json":
        raise UpdateError("Plano de atualização fora do cache do PACE.")
    plan = json.loads(path.read_text(encoding="utf-8"))
    _parse_version(plan["version"])
    target = Path(plan["target"]).resolve()
    installer = Path(plan["installer"]).resolve()
    receipt = Path(plan["receipt"]).resolve()
    if target.parent == target or cache == target or target in cache.parents or cache in target.parents:
        raise UpdateError("Pastas de atualização/instalação inválidas.")
    if not (target / "Pace.exe").is_file() or (target / "Pace.exe").is_symlink():
        raise UpdateError("Executável instalado do PACE não encontrado.")
    if installer.parent != cache / plan["version"] or installer.name != "Pace_Installer.exe":
        raise UpdateError("Pacote fora do cache versionado.")
    if receipt.parent != path.parent or receipt.name != "confirmed.json":
        raise UpdateError("Confirmação fora do diretório de execução.")
    if int(plan["parent_pid"]) <= 0 or int(plan["parent_pid"]) == os.getpid():
        raise UpdateError("PID de origem inválido.")
    if not plan.get("token"):
        raise UpdateError("Token de confirmação ausente.")
    return plan


def _snapshot(target: Path, backup: Path) -> None:
    backup.mkdir()
    shutil.copy2(target / "Pace.exe", backup / "Pace.exe")
    internal = target / "_internal"
    if internal.exists():
        if internal.is_symlink() or getattr(internal, "is_junction", lambda: False)():
            raise UpdateError("Runtime externo não pode ser substituído por este atualizador.")
        shutil.copytree(internal, backup / "_internal")


def _restore(target: Path, backup: Path) -> None:
    """Replace only explicitly named application binaries, never user data."""
    for name in ("Pace.exe", "_internal"):
        current = target / name
        saved = backup / name
        if current.is_symlink() or getattr(current, "is_junction", lambda: False)():
            raise UpdateError("Rollback interrompido: binário aponta para um caminho externo.")
        if current.is_dir():
            shutil.rmtree(current)
        elif current.exists():
            current.unlink()
        if saved.is_dir():
            shutil.copytree(saved, current)
        elif saved.exists():
            shutil.copy2(saved, current)


def _launch(target: Path, plan: dict | None = None) -> subprocess.Popen:
    environment = os.environ.copy()
    environment.pop("PACE_UPDATE_CONFIRMATION", None)
    environment.pop("PACE_UPDATE_TOKEN", None)
    environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    if plan:
        environment["PACE_UPDATE_CONFIRMATION"] = plan["receipt"]
        environment["PACE_UPDATE_TOKEN"] = plan["token"]
    return subprocess.Popen([str(target / "Pace.exe")], cwd=target, env=environment, **hidden_process_options())


def _wait_confirmation(process: subprocess.Popen, plan: dict, timeout: float = 60.0) -> bool:
    deadline = time.monotonic() + timeout
    receipt = Path(plan["receipt"])
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        if receipt.exists():
            try:
                data = json.loads(receipt.read_text(encoding="utf-8"))
                return (
                    data.get("version") == plan["version"] and data.get("token") == plan["token"]
                    and data.get("pid") == process.pid
                    and Path(data.get("target", "")).resolve() == Path(plan["target"]).resolve()
                )
            except (OSError, ValueError):
                pass
        time.sleep(0.25)
    return False


def _write_result(staging: Path, result: dict) -> None:
    try:
        (staging / "result.json").write_text(json.dumps(result), encoding="utf-8")
    except OSError:
        # A reporting error must not stop an already-confirmed healthy app.
        _logger.exception("Não foi possível persistir o resultado da atualização.")


def apply_update(plan_path: Path) -> int:
    """Install after graceful exit; restore binaries on installer/start failure."""
    plan = _read_plan(plan_path)
    staging = plan_path.resolve().parent
    handler = logging.FileHandler(staging / "update.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    _logger.addHandler(handler)
    _logger.setLevel(logging.INFO)
    target = Path(plan["target"]).resolve()
    backup = staging / "backup"
    process = None
    installation_started = False
    parent_exited = False
    try:
        validate_installer(Path(plan["installer"]), plan["sha256"], plan["size"])
        wait_for_process_exit(int(plan["parent_pid"]))
        parent_exited = True
        _snapshot(target, backup)
        snapshot_integration(target, backup / "integration")
        Path(plan["receipt"]).unlink(missing_ok=True)
        installation_started = True
        result = subprocess.run(
            [plan["installer"], "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-",
             "/NOCLOSEAPPLICATIONS", f"/DIR={target}", f"/LOG={staging / 'installer.log'}"],
            cwd=staging, **hidden_process_options(),
        )
        if result.returncode != 0:
            raise UpdateError(f"Instalador retornou código {result.returncode}.")
        process = _launch(target, plan)
        if not _wait_confirmation(process, plan):
            raise UpdateError("A nova versão não confirmou inicialização; revertendo binários.")
        _logger.info("Atualização para %s confirmada no PID %s.", plan["version"], process.pid)
        _write_result(staging, {"status": "updated", "version": plan["version"]})
        return 0
    except Exception as exc:
        _logger.exception("Atualização falhou: %s", exc)
        rollback_error = None
        if installation_started:
            try:
                # Only stop the process launched by this helper for validation.
                if process is not None and process.poll() is None:
                    process.terminate()
                    process.wait(timeout=15)
                _restore(target, backup)
            except Exception as restore_exc:
                rollback_error = str(restore_exc)
                _logger.exception("Rollback falhou; backup preservado em %s.", backup)
            else:
                # A locked shortcut/registry key must not prevent reopening the
                # restored application. Keep both errors and the recovery backup.
                errors = []
                try:
                    restore_integration(target, backup / "integration")
                except Exception as integration_exc:
                    errors.append(f"Integração: {integration_exc}")
                    _logger.exception("Integração não restaurada por completo; backup em %s.", backup)
                try:
                    _launch(target)
                except Exception as restart_exc:
                    errors.append(f"Reabertura: {restart_exc}")
                    _logger.exception("Binários restaurados, mas não foi possível reabrir o PACE.")
                rollback_error = "; ".join(errors) or None
                _logger.info("Binários anteriores restaurados; dados preservados.")
        elif parent_exited:
            try:
                _launch(target)
            except Exception as restart_exc:
                rollback_error = str(restart_exc)
                _logger.exception("Não foi possível reabrir o PACE após falha no preparo.")
        _write_result(staging, {"status": "failed", "error": str(exc), "rollback_error": rollback_error})
        return 1
    finally:
        _logger.removeHandler(handler)
        handler.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PACE update helper")
    parser.add_argument("--update-plan", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        return apply_update(args.update_plan)
    except Exception:
        _logger.exception("Não foi possível carregar o plano de atualização.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
