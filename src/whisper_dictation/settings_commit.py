"""Commit validated settings and credential changes, restoring both on failure."""
from __future__ import annotations

from pathlib import Path
import os
import tempfile

from whisper_dictation import secrets_store
from whisper_dictation.settings import AppConfig, validate_hotkeys


def commit_settings(path: Path, config: AppConfig, keys: dict[str, str]) -> None:
    validate_hotkeys(config)
    if not isinstance(keys, dict) or set(keys) - {"groq", "openai", "gemini", "huggingface"}:
        raise ValueError("Credenciais inválidas.")
    if any(not isinstance(value, str) or len(value) > 4096 for value in keys.values()):
        raise ValueError("Credenciais inválidas.")
    vault = secrets_store._keyring() if keys else None
    if keys and vault is None:
        raise RuntimeError("Cofre de credenciais indisponível.")
    original = path.read_bytes() if path.exists() else None
    snapshots = {(service, key): vault.get_password(service, key)
                 for service in (secrets_store.SERVICE_NAME, secrets_store.LEGACY_SERVICE_NAME) for key in keys} if vault else {}
    try:
        for provider, value in keys.items():
            secrets_store.set_api_key(provider, value)
            if value.strip():
                if vault.get_password(secrets_store.SERVICE_NAME, provider) != value.strip():
                    raise RuntimeError("O cofre não confirmou a gravação da credencial.")
            elif any(vault.get_password(service, provider) is not None
                     for service in (secrets_store.SERVICE_NAME, secrets_store.LEGACY_SERVICE_NAME)):
                raise RuntimeError("O cofre não confirmou a exclusão da credencial.")
        config.save(path)
    except Exception as error:
        failures = []
        for (service, provider), value in snapshots.items():
            try:
                if value is None:
                    try:
                        vault.delete_password(service, provider)
                    except Exception:
                        if vault.get_password(service, provider) is not None:
                            raise
                else:
                    vault.set_password(service, provider, value)
            except Exception:
                failures.append(provider)
        if original is not None and (not path.exists() or path.read_bytes() != original):
            try:
                fd, temporary = tempfile.mkstemp(dir=path.parent, prefix="restore-config-", suffix=".tmp")
                try:
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(original)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temporary, path)
                finally:
                    Path(temporary).unlink(missing_ok=True)
            except Exception:
                failures.append("configuração")
        elif original is None and path.exists():
            try:
                path.unlink()
            except OSError:
                failures.append("configuração")
        if failures:
            raise RuntimeError("Falha ao salvar; não foi possível restaurar todas as credenciais. Confira o cofre do Windows.") from error
        raise RuntimeError("Não foi possível salvar. As configurações e credenciais anteriores foram preservadas.") from error
