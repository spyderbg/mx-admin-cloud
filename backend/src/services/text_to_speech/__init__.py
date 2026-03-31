"""TTS subsystem: base class, factory, and service orchestrator."""

from __future__ import annotations

from pathlib import Path

from src.services.models import ServiceProvider
from src.services.text_to_speech.base import TTSProvider

__all__ = [
    "TTSProvider",
    "ProviderFactory",
    "SERVICE_CLASS",
    "TextToSpeechService",
]


class ProviderFactory:
    """Registry that maps provider names to concrete TTS provider classes."""

    _builders: dict[str, type[TTSProvider]] = {}

    @classmethod
    def register(
        cls,
        provider_name: str,
        builder: type[TTSProvider],
    ) -> None:
        """Register a concrete TTS provider class under *provider_name*."""
        cls._builders[provider_name.strip().lower()] = builder

    @classmethod
    def create(cls, provider: ServiceProvider, base_dir: Path) -> TTSProvider:
        """Instantiate the concrete TTS provider for *provider*.

        Raises :exc:`ValueError` when no builder is registered for
        ``provider.name``.
        """
        key = provider.name.strip().lower()
        builder = cls._builders.get(key)
        if builder is None:
            raise ValueError(
                f"Unsupported TTS provider: '{provider.name}' "
                f"(file='{provider.config_path}')"
            )
        return builder(
            service_type=provider.service_type,
            name=provider.name,
            enabled=provider.enabled,
            priority=provider.priority,
            config=provider.config,
            config_path=provider.config_path,
            base_dir=base_dir,
        )


# Import providers AFTER ProviderFactory is defined so that provider modules
# can safely call `from .. import ProviderFactory` inside their register() methods.
import src.services.text_to_speech.providers  # noqa: F401, E402

from src.services.text_to_speech.service import TextToSpeechService  # noqa: E402

SERVICE_CLASS = TextToSpeechService
