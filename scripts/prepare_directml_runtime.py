"""Vendor the official DirectML wheel without installing it into the CPU venv.

Run with the Python interpreter used for the PACE build. The isolated inference
subprocess imports .runtime-directml (dml_runtime in a frozen build) explicitly.
"""
from __future__ import annotations

import argparse
from email.parser import BytesParser
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import urllib.request
import uuid
import zipfile

from packaging.tags import sys_tags
from packaging.utils import parse_wheel_filename

PACKAGE = "onnxruntime-directml"
VERSION = "1.24.4"
PYPI_URL = f"https://pypi.org/pypi/{PACKAGE}/{VERSION}/json"


def compatible_asset(metadata: dict, compatible_tags=None) -> dict:
    if metadata.get("info", {}).get("name") != PACKAGE or metadata["info"].get("version") != VERSION:
        raise RuntimeError("PyPI retornou metadados de outro pacote ou versão.")
    tags = set(sys_tags() if compatible_tags is None else compatible_tags)
    matches = []
    for asset in metadata.get("urls", []):
        if asset.get("packagetype") != "bdist_wheel" or asset.get("yanked"):
            continue
        filename = asset.get("filename", "")
        try:
            name, version, _, wheel_tags = parse_wheel_filename(filename)
        except ValueError:
            continue
        if name != PACKAGE or str(version) != VERSION or not (wheel_tags & tags):
            continue
        if not all(tag.platform == "win_amd64" for tag in wheel_tags):
            continue
        digest = asset.get("digests", {}).get("sha256", "")
        if not re.fullmatch(r"[a-f0-9]{64}", digest) or int(asset.get("size", 0)) <= 0:
            raise RuntimeError("Wheel oficial sem tamanho/SHA256 válido.")
        matches.append(asset)
    if len(matches) != 1:
        raise RuntimeError("Nenhuma wheel DirectML única compatível com este Python/Windows x64.")
    return matches[0]


def verify_wheel(path: Path, asset: dict) -> None:
    if path.name != asset["filename"] or path.stat().st_size != int(asset["size"]):
        raise RuntimeError("Wheel baixada não corresponde ao arquivo oficial selecionado.")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != asset["digests"]["sha256"]:
        raise RuntimeError("SHA256 da wheel DirectML divergiu do publicado pelo PyPI.")


def extract_wheel(path: Path, destination: Path) -> None:
    """Validate every member before extracting any file into a fresh directory."""
    destination = destination.resolve()
    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
        seen = set()
        for member in members:
            name = PurePosixPath(member.filename)
            if name.is_absolute() or ".." in name.parts or "\\" in member.filename or any(":" in part for part in name.parts):
                raise RuntimeError("A wheel contém caminho fora do diretório de runtime.")
            key = str(name).casefold()
            if key in seen or stat.S_ISLNK(member.external_attr >> 16):
                raise RuntimeError("A wheel contém caminho duplicado ou link simbólico.")
            seen.add(key)
            if not (destination / str(name)).resolve().is_relative_to(destination):
                raise RuntimeError("Caminho de extração inválido.")
        metadata_files = [m for m in members if m.filename.endswith(".dist-info/METADATA")]
        if len(metadata_files) != 1:
            raise RuntimeError("Metadados da wheel DirectML ausentes ou ambíguos.")
        metadata = BytesParser().parsebytes(archive.read(metadata_files[0]))
        if metadata.get("Name", "").replace("_", "-").lower() != PACKAGE or metadata.get("Version") != VERSION:
            raise RuntimeError("Metadados internos da wheel não correspondem ao pin DirectML.")
        if "onnxruntime/__init__.py" not in seen or not any(
            name.startswith("onnxruntime/capi/onnxruntime_pybind11_state") and name.endswith(".pyd") for name in seen
        ) or "onnxruntime/capi/directml.dll" not in seen:
            raise RuntimeError("Wheel DirectML sem o pacote, extensão nativa ou DirectML.dll.")
        destination.mkdir(parents=True)
        for member in members:
            target = destination / member.filename
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output)


def _safe_remove(path: Path, workspace: Path) -> None:
    workspace = workspace.resolve()
    if path.resolve() == workspace or not path.resolve().is_relative_to(workspace):
        raise RuntimeError("Limpeza recusada fora do workspace de preparação.")
    if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
        raise RuntimeError("Limpeza recusada para um diretório externo.")
    if path.is_dir():
        # shutil.rmtree does not traverse Windows junctions (Python >=3.8),
        # but reject links beforehand to keep the cleanup contract explicit.
        for directory, children, files in os.walk(path, followlinks=False):
            for name in children + files:
                child = Path(directory) / name
                if child.is_symlink() or getattr(child, "is_junction", lambda: False)() or not child.resolve().is_relative_to(workspace):
                    raise RuntimeError("Limpeza recusada: cache contém link para outro diretório.")
        shutil.rmtree(path)


def publish_vendor(staging: Path, workspace: Path) -> Path:
    workspace, staging = workspace.resolve(), staging.resolve()
    if not staging.is_relative_to(workspace) or staging == workspace:
        raise RuntimeError("Runtime temporário fora do workspace.")
    destination = workspace / ".runtime-directml"
    if destination.is_symlink() or getattr(destination, "is_junction", lambda: False)():
        raise RuntimeError("Pasta DirectML aponta para um diretório externo.")
    previous = workspace / (".directml-previous-" + uuid.uuid4().hex)
    had_previous = destination.exists()
    if had_previous:
        destination.rename(previous)
    try:
        staging.rename(destination)
    except Exception:
        if had_previous:
            previous.rename(destination)
        raise
    if had_previous:
        _safe_remove(previous, workspace)
    return destination


def prepare_runtime(workspace: Path | None = None) -> Path:
    if sys.platform != "win32" or platform.machine().lower() not in {"amd64", "x86_64"} or struct.calcsize("P") != 8:
        raise RuntimeError("Prepare o runtime DirectML com o Python Windows x64 usado no build.")
    workspace = (workspace or Path(__file__).resolve().parents[1]).resolve()
    with urllib.request.urlopen(urllib.request.Request(PYPI_URL, headers={"User-Agent": "PACE-runtime-build"}), timeout=30) as response:
        asset = compatible_asset(json.loads(response.read()))
    staging_root = Path(tempfile.mkdtemp(prefix=".directml-prepare-", dir=workspace)).resolve()
    if not staging_root.is_relative_to(workspace):
        raise RuntimeError("Diretório temporário não pertence ao workspace.")
    try:
        downloads = staging_root / "downloads"
        downloads.mkdir()
        requirement = staging_root / "requirement.txt"
        requirement.write_text(f"{PACKAGE}=={VERSION} --hash=sha256:{asset['digests']['sha256']}\n", encoding="utf-8")
        subprocess.run([sys.executable, "-m", "pip", "--isolated", "download", "--only-binary=:all:",
                        "--no-deps", "--require-hashes", "--no-cache-dir", "--disable-pip-version-check",
                        "--index-url", "https://pypi.org/simple", "--dest", str(downloads), "-r", str(requirement)],
                       check=True, timeout=180, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        wheels = list(downloads.glob("*.whl"))
        if len(wheels) != 1:
            raise RuntimeError("Download não produziu exatamente uma wheel DirectML.")
        verify_wheel(wheels[0], asset)
        vendor = staging_root / "vendor"
        extract_wheel(wheels[0], vendor)
        (vendor / "pace-runtime.json").write_text(json.dumps({"package": PACKAGE, "version": VERSION,
            "filename": asset["filename"], "sha256": asset["digests"]["sha256"],
            "source": PYPI_URL, "python": list(sys.version_info[:2])}, indent=2), encoding="utf-8")
        destination = publish_vendor(vendor, workspace)
        print(f"DirectML {VERSION} preparado sem instalar no venv: {destination}")
        return destination
    finally:
        _safe_remove(staging_root, workspace)


def main() -> int:
    argparse.ArgumentParser(description=__doc__).parse_args()
    prepare_runtime()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
