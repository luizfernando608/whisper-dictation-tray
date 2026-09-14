"""Secure storage for provider API keys using the Windows Credential Manager.

Keys are stored via `keyring` (Windows Credential Manager / DPAPI), so no
secret is ever written to a file that could be committed. For backwards
compatibility with the previous `.env` workflow, reads fall back to the
provider's environment variable when the vault has no entry.
"""

from __future__ import annotations

import logging
import os

SERVICE_NAME = "PACE"
LEGACY_SERVICE_NAME = "WhisperDictationTray"

# Provider -> legacy environment variable used as a read fallback.
ENV_FALLBACK: dict[str, str] = {
    "groq": "GROQ_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
}

_logger = logging.getLogger("whisper_dictation.secrets")


def _keyring():
    try:
        import keyring

        return keyring
    except Exception:
        _logger.warning(
            "keyring indisponível; usando apenas variáveis de ambiente.", exc_info=True
        )
        return None


def get_api_key(provider: str, env_var: str | None = None) -> str | None:
    """Return the API key for ``provider`` or ``None`` if not configured.

    Looks in the credential vault first, then falls back to ``env_var`` (or the
    provider's default environment variable) so existing `.env` setups keep
    working.
    """
    provider = provider.strip().lower()
    kr = _keyring()
    if kr is not None:
        try:
            value = kr.get_password(SERVICE_NAME, provider)
            if value:
                return value
            # Legacy fallback: check previous WhisperDictationTray vault and migrate
            legacy_value = kr.get_password(LEGACY_SERVICE_NAME, provider)
            if legacy_value:
                try:
                    kr.set_password(SERVICE_NAME, provider, legacy_value)
                except Exception:
                    pass
                return legacy_value
        except Exception:
            _logger.warning(
                "Falha ao ler a chave de %s no keyring.", provider, exc_info=True
            )

    env_name = env_var or ENV_FALLBACK.get(provider)
    if env_name:
        env_value = os.environ.get(env_name)
        if env_value:
            return env_value
    return None


def set_api_key(provider: str, key: str) -> None:
    """Store (or clear, when ``key`` is empty) the API key for ``provider``."""
    provider = provider.strip().lower()
    key = (key or "").strip()
    if not key:
        delete_api_key(provider)
        return

    kr = _keyring()
    if kr is None:
        raise RuntimeError(
            "keyring indisponível; não foi possível salvar a chave com segurança."
        )
    kr.set_password(SERVICE_NAME, provider, key)


def delete_api_key(provider: str) -> None:
    provider = provider.strip().lower()
    kr = _keyring()
    if kr is None:
        return
    for svc in (SERVICE_NAME, LEGACY_SERVICE_NAME):
        try:
            kr.delete_password(svc, provider)
        except Exception:
            pass


def has_api_key(provider: str, env_var: str | None = None) -> bool:
    return bool(get_api_key(provider, env_var))
