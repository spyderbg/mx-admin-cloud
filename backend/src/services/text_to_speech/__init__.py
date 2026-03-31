"""TTS subsystem: base class, factory, and service orchestrator."""

from __future__ import annotations

from pathlib import Path

from src.services.acceptance import ServiceAcceptCriteria
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

    @classmethod
    def supports(cls, provider_name: str) -> bool:
        """Return whether a provider builder is registered for *provider_name*."""
        return provider_name.strip().lower() in cls._builders


# Import providers AFTER ProviderFactory is defined so that provider modules
# can safely call `from .. import ProviderFactory` inside their register() methods.
import src.services.text_to_speech.providers  # noqa: F401, E402

from src.services.text_to_speech.service import TextToSpeechService  # noqa: E402

SERVICE_CLASS = TextToSpeechService
SERVICE_NAME = "text_to_speech"


def _validate_tts_provider(provider: ServiceProvider) -> None:
    if not ProviderFactory.supports(provider.name):
        raise ValueError(
            f"Unsupported TTS provider: '{provider.name}' (file='{provider.config_path}')"
        )


def _build_tts_provider(provider: ServiceProvider, base_dir: Path) -> TTSProvider:
    return ProviderFactory.create(provider, base_dir)


SERVICE_ACCEPT_CRITERIA = ServiceAcceptCriteria(
    service_name=SERVICE_NAME,
    config_subdir="text_to_speech",
    validate_provider=_validate_tts_provider,
    build_provider=_build_tts_provider,
)
