"""TTS provider implementations."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Sequence

from src.services.text_to_speech.base import TTSProvider
from src.services.text_to_speech.models import ProviderConfig

__all__ = [
    "TTSProvider",
    "ProviderFactory",
    "build_provider",
    "build_active_providers",
]


class ProviderFactory:
    """Factory registry that builds TTS providers from service metadata."""

    _builders: dict[str, Callable[[ProviderConfig, Path], TTSProvider]] = {}

    @classmethod
    def register(
        cls,
        provider_name: str,
        builder: Callable[[ProviderConfig, Path], TTSProvider],
    ) -> None:
        cls._builders[provider_name.strip().lower()] = builder

    @staticmethod
    def _parse_config_map(raw_value: object, label: str, config_path: Path) -> dict[str, Any]:
        if isinstance(raw_value, dict):
            return dict(raw_value)
        if isinstance(raw_value, str):
            text = raw_value.strip()
            if not text:
                return {}
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid JSON string for provider '{label}' config in '{config_path}'"
                ) from exc
            if isinstance(parsed, dict):
                return parsed
        raise ValueError(f"Provider '{label}' config in '{config_path}' must be a JSON object.")

    @staticmethod
    def _boolish(value: object) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    @classmethod
    def create(cls, cfg: ProviderConfig, base_dir: Path) -> TTSProvider:
        cfg.generate = cls._parse_config_map(cfg.generate, "generate", cfg.config_path)
        cfg.evaluate = cls._parse_config_map(cfg.evaluate, "evaluate", cfg.config_path)
        cfg.generate["enabled"] = cls._boolish(cfg.generate.get("enabled", False))
        cfg.evaluate["enabled"] = cls._boolish(cfg.evaluate.get("enabled", True))

        key = cfg.name.strip().lower()
        builder = cls._builders.get(key)
        if builder is None:
            raise ValueError(
                f"Unsupported provider name: '{cfg.name}' "
                f"(file='{cfg.config_path}')"
            )
        return builder(cfg, base_dir)


def build_provider(config: ProviderConfig, base_dir: Path) -> TTSProvider:
    """Build a provider instance from service metadata."""
    return ProviderFactory.create(config, base_dir)


def build_active_providers(
    configs: Sequence[ProviderConfig],
    base_dir: Path,
) -> list[TTSProvider]:
    """Build only provider instances that are marked active."""
    providers = [build_provider(cfg, base_dir) for cfg in configs]
    return [provider for provider in providers if provider.is_active()]


# Trigger self-registration of all implementations
from src.services.text_to_speech.providers.openai_tts import OpenAITTSProvider as _OpenAI  # noqa: E402, F401
from src.services.text_to_speech.providers.elevenlabs_tts import ElevenLabsTTSProvider as _ElevenLabs  # noqa: E402, F401
from src.services.text_to_speech.providers.speaktor_tts import SpeaktorTTSProvider as _Speaktor  # noqa: E402, F401
