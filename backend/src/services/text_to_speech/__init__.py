"""TTS subsystem: base class, factory, and service orchestrator."""

from __future__ import annotations

from pathlib import Path

from src.services.acceptance import ServiceAcceptCriteria
from src.services.models import ServiceProvider
from src.services.text_to_speech.models import GeneratedAudio, TTSProvider, TTSRequest
from src.services.text_to_speech.service import (
    TextToSpeechProviderFactory,
    TextToSpeechService
)

# Import providers AFTER TextToSpeechProviderFactory is imported so provider
# modules can register safely without triggering circular package initialization.
import src.services.text_to_speech.providers  # noqa: F401, E402 # pylint: disable=wrong-import-position

__all__ = [
    "GeneratedAudio",
    "TTSProvider",
    "TTSRequest",
    "TextToSpeechProviderFactory",
    "TextToSpeechService",
]


def _validate_tts_provider(service_provider: ServiceProvider) -> None:
    provider_name = service_provider.provider
    if not TextToSpeechProviderFactory.supports(provider_name):
        raise ValueError(
            f"Unsupported TTS provider: '{provider_name}' (file='{service_provider.config_path}')"
        )


def _build_tts_provider(provider: ServiceProvider, base_dir: Path) -> TTSProvider:
    return TextToSpeechProviderFactory.create(provider, base_dir)


SERVICE_CLASS = TextToSpeechService  # pylint: disable=invalid-name
SERVICE_NAME = "text_to_speech"
SERVICE_ACCEPT_CRITERIA = ServiceAcceptCriteria(
    service_name=SERVICE_NAME,
    config_subdir="text_to_speech",
    validate_provider=_validate_tts_provider,
    build_provider=_build_tts_provider,
    available_providers=TextToSpeechProviderFactory.available_names,
)
