"""Back up only the Windows integration owned by the PACE installer.

The registry and known folders are injectable so tests never touch the host.
"""
from __future__ import annotations

import base64
import ctypes
import json
import os
from pathlib import Path, PureWindowsPath
import re
import shutil

from whisper_dictation.updater import UpdateError

UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Pace_is1"
_UNINSTALLER = re.compile(r"unins\d{3}\.(?:exe|dat|msg)", re.IGNORECASE)
_PACE_LINKS = ("PACE.lnk", "PACE — Configurações.lnk", "PACE — Gravações.lnk")
_LEGACY_LINKS = ("Whisper Dictation.lnk", "Whisper Dictation — Configurações.lnk")


def known_folders() -> dict[str, Path]:
    """Use current Windows folder locations, including redirected Desktop."""
    if os.name != "nt":
        raise UpdateError("Integração de atualização disponível somente no Windows.")
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    function = shell.SHGetFolderPathW
    function.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_wchar_p]
    function.restype = ctypes.c_long
    folders = {}
    for name, identifier in (("programs", 2), ("desktop", 0x10), ("startup", 7)):
        buffer = ctypes.create_unicode_buffer(32768)
        if function(None, identifier, None, 0, buffer) != 0 or not buffer.value:
            raise UpdateError(f"Não foi possível consultar a pasta do Windows: {name}.")
        folders[name] = Path(buffer.value).resolve()
    return folders


class WindowsRegistry:
    """Read/replace the one HKCU uninstall key, preserving value types/subkeys."""

    def read(self):
        import winreg

        def read_key(key):
            children, count, _ = winreg.QueryInfoKey(key)
            values = []
            for index in range(count):
                name, value, kind = winreg.EnumValue(key, index)
                binary = isinstance(value, bytes)
                values.append([name, base64.b64encode(value).decode("ascii") if binary else value, kind, binary])
            result = {"values": values, "children": {}}
            for index in range(children):
                name = winreg.EnumKey(key, index)
                with winreg.OpenKey(key, name, 0, winreg.KEY_READ) as child:
                    result["children"][name] = read_key(child)
            return result

        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY, 0, winreg.KEY_READ)
        except FileNotFoundError:
            return None
        with key:
            return read_key(key)

    def restore(self, snapshot):
        import winreg
        _validate_registry(snapshot)

        def delete(path):
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_READ | winreg.KEY_WRITE) as key:
                    names = [winreg.EnumKey(key, index) for index in range(winreg.QueryInfoKey(key)[0])]
                for name in names:
                    delete(path + "\\" + name)
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
            except FileNotFoundError:
                pass

        def write(path, tree):
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_WRITE) as key:
                for name, value, kind, binary in tree["values"]:
                    winreg.SetValueEx(key, name, 0, kind, base64.b64decode(value, validate=True) if binary else value)
            for name, child in tree["children"].items():
                if not name or "\\" in name or "/" in name:
                    raise UpdateError("Subchave inválida no backup da instalação.")
                write(path + "\\" + name, child)

        delete(UNINSTALL_KEY)
        if snapshot is not None:
            write(UNINSTALL_KEY, snapshot)


def _validate_registry(tree):
    if tree is None:
        return
    if not isinstance(tree, dict) or set(tree) != {"values", "children"} or not isinstance(tree["values"], list) or not isinstance(tree["children"], dict):
        raise UpdateError("Backup do registro da instalação inválido.")
    for item in tree["values"]:
        if not isinstance(item, list) or len(item) != 4:
            raise UpdateError("Valor de registro inválido no backup.")
        name, value, kind, binary = item
        if not isinstance(name, str) or not isinstance(kind, int) or not isinstance(binary, bool):
            raise UpdateError("Tipo de registro inválido no backup.")
        if binary:
            base64.b64decode(value, validate=True)
    for name, child in tree["children"].items():
        if not isinstance(name, str) or not name or "\\" in name or "/" in name or child is None:
            raise UpdateError("Subchave inválida no backup da instalação.")
        _validate_registry(child)


def _checked_file(root: Path, relative: str) -> Path:
    """Refuse every reparse point below an explicitly selected boundary."""
    if root.is_symlink() or (root.exists() and getattr(root.lstat(), "st_file_attributes", 0) & 0x400):
        raise UpdateError("Pasta de integração/backup não pode ser um link/junction.")
    root = root.resolve()
    path = root / relative
    if not path.is_relative_to(root) or any(part == ".." for part in Path(relative).parts):
        raise UpdateError("Caminho externo no backup de integração.")
    current = root
    for part in path.relative_to(root).parts:
        current = current / part
        try:
            attributes = current.lstat()
        except FileNotFoundError:
            continue
        if current.is_symlink() or getattr(attributes, "st_file_attributes", 0) & 0x400:
            raise UpdateError(f"Integração aponta para um link/junction: {current.name}.")
    if path.exists() and not path.is_file():
        raise UpdateError(f"Era esperado um arquivo de integração: {path.name}.")
    return path


def _shortcut_paths(registry, folders):
    groups = {"PACE"}
    if registry:
        for name, value, _, binary in registry["values"]:
            if name.lower() == "inno setup: icon group" and value:
                if binary or not isinstance(value, str):
                    raise UpdateError("Grupo do menu Iniciar inválido na instalação anterior.")
                group = PureWindowsPath(value)
                if group.is_absolute() or group.drive or any(part in {"..", "."} for part in group.parts):
                    raise UpdateError("Grupo do menu Iniciar deve ficar dentro da pasta de programas do usuário.")
                groups.add(str(Path(*group.parts)))
    paths = [("programs", str(Path(group) / name)) for group in sorted(groups) for name in _PACE_LINKS]
    paths += [("desktop", name) for name in (*_PACE_LINKS, *_LEGACY_LINKS)]
    paths += [("startup", name) for name in ("PACE.lnk", "Whisper Dictation.lnk")]
    return paths


def snapshot_integration(target: Path, backup: Path, *, folders=None, registry=None) -> None:
    """Fail before installation if any known integration resource cannot be saved."""
    folders = known_folders() if folders is None else folders
    registry = WindowsRegistry() if registry is None else registry
    saved_registry = registry.read()
    _validate_registry(saved_registry)
    paths = _shortcut_paths(saved_registry, folders)
    backup.mkdir()
    files = []
    resources = [(kind, name) for kind, name in paths]
    resources += [("uninstaller", path.name) for path in target.iterdir() if _UNINSTALLER.fullmatch(path.name)]
    for index, (kind, name) in enumerate(resources):
        root = target if kind == "uninstaller" else Path(folders[kind])
        path = _checked_file(root, name)
        item = {"kind": kind, "name": name, "existed": path.exists(), "saved": f"{index:04d}.bin"}
        if item["existed"]:
            shutil.copy2(path, backup / item["saved"])
        files.append(item)
    manifest = {"format": 1, "registry": saved_registry, "files": files}
    (backup / "integration.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")


def restore_integration(target: Path, backup: Path, *, folders=None, registry=None) -> None:
    """Restore only allowlisted links, uninstallers and HKCU registration."""
    folders = known_folders() if folders is None else folders
    registry = WindowsRegistry() if registry is None else registry
    manifest_path = _checked_file(backup, "integration.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("format") != 1:
        raise UpdateError("Formato do backup de integração inválido.")
    _validate_registry(manifest["registry"])
    allowed = set(_shortcut_paths(manifest["registry"], folders))
    seen = set()
    operations = []
    for item in manifest["files"]:
        kind, name = item["kind"], item["name"]
        key = (kind, name)
        if key in seen or (kind == "uninstaller" and not _UNINSTALLER.fullmatch(name)) or (kind != "uninstaller" and key not in allowed):
            raise UpdateError("Recurso inesperado no backup de integração.")
        seen.add(key)
        destination = _checked_file(target if kind == "uninstaller" else Path(folders[kind]), name)
        if not re.fullmatch(r"\d{4}\.bin", item["saved"]):
            raise UpdateError("Arquivo inesperado no backup de integração.")
        source = _checked_file(backup, item["saved"])
        if item["existed"] and not source.is_file():
            raise UpdateError("Backup de integração incompleto; arquivos originais preservados.")
        operations.append((destination, source, item["existed"]))
    if not allowed.issubset(seen):
        raise UpdateError("Lista de atalhos incompleta no backup de integração.")
    # Validate every removal before touching any live integration resource.
    saved_uninstallers = {name.casefold() for kind, name in seen if kind == "uninstaller"}
    extras = [_checked_file(target, path.name) for path in target.iterdir()
              if _UNINSTALLER.fullmatch(path.name) and path.name.casefold() not in saved_uninstallers]
    errors = []
    for destination, source, existed in operations:
        try:
            if existed:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
            else:
                destination.unlink(missing_ok=True)
        except OSError as exc:
            errors.append(f"{destination.name}: {exc}")
    for path in extras:
        try:
            path.unlink()
        except OSError as exc:
            errors.append(f"{path.name}: {exc}")
    try:
        registry.restore(manifest["registry"])
    except Exception as exc:
        errors.append(f"registro da instalação: {exc}")
    if errors:
        raise UpdateError("Rollback da integração incompleto; backup preservado. " + "; ".join(errors))
