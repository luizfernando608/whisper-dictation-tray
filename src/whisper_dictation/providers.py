"""Central registry of transcription providers and their models.

Single source of truth reused by the config sanitizer (`settings.py`), the
settings window (`settings_window.py`) and the transcriber (`transcription.py`).
Add a model or provider here and it shows up everywhere.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProviderInfo:
    key: str
    label: str
    needs_api_key: bool
    models: tuple[str, ...]
    default_model: str
    #: Human hint shown in the settings window (e.g. where to get the key).
    key_hint: str = ""


PROVIDERS: dict[str, ProviderInfo] = {
    "groq": ProviderInfo(
        key="groq",
        label="Groq  (nuvem, muito rápido)",
        needs_api_key=True,
        models=(
            "whisper-large-v3",
            "whisper-large-v3-turbo",
        ),
        default_model="whisper-large-v3",
        key_hint="Crie uma chave em console.groq.com/keys",
    ),
    "openai": ProviderInfo(
        key="openai",
        label="OpenAI  (nuvem)",
        needs_api_key=True,
        models=(
            "gpt-transcribe",
            "gpt-4o-transcribe",
            "gpt-4o-mini-transcribe",
            "gpt-4o-transcribe-diarize",
            "whisper-1",
        ),
        default_model="gpt-transcribe",
        key_hint="Crie uma chave em platform.openai.com/api-keys",
    ),
    "gemini": ProviderInfo(
        key="gemini",
        label="Google Gemini  (nuvem)",
        needs_api_key=True,
        models=(
            "gemini-3.8-flash",
            "gemini-3.5-transcribe",
            "gemini-4-argon",
            "gemini-3.6-flash",
            "gemini-3.5-flash-lite",
            "gemini-3.1-flash-lite",
        ),
        default_model="gemini-3.8-flash",
        key_hint="Crie uma chave em aistudio.google.com/apikey",
    ),
    "local": ProviderInfo(
        key="local",
        label="Local  (CPU, 100% offline)",
        needs_api_key=False,
        models=("tiny", "base", "small", "medium", "large-v2", "large-v3"),
        default_model="small",
    ),
}

#: Display / iteration order for the provider dropdown.
PROVIDER_ORDER: tuple[str, ...] = ("groq", "openai", "gemini", "local")

#: Providers that call a cloud API (need a key, share the fallback-to-local path).
CLOUD_PROVIDERS: frozenset[str] = frozenset({"groq", "openai", "gemini"})


def is_valid_provider(provider: str) -> bool:
    return provider in PROVIDERS


def models_for(provider: str) -> tuple[str, ...]:
    info = PROVIDERS.get(provider)
    return info.models if info else ()
