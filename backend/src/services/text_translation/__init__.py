"""Translation subsystem: service class, factory, and provider implementations."""

from pathlib import Path

import src.services.text_translation.providers  # noqa: F401 — triggers provider auto-import

from src.services.acceptance import ServiceAcceptCriteria
from src.services.models import ServiceProvider
from src.services.text_translation.models import TextTranslationServiceProvider
from src.services.text_translation.service import (
    TextTranslationService,
    TextTranslationProviderFactory,
    translate_with_all_active,
)


def _validate_translation_provider(service_provider: ServiceProvider) -> None:
    provider_name = service_provider.provider
    if not TextTranslationProviderFactory.supports(provider_name):
        raise ValueError(
            f"Unsupported translator: '{provider_name}' (file='{service_provider.config_path}')"
        )


def _build_translation_provider(
        provider: ServiceProvider,
        base_dir: Path,
) -> TextTranslationServiceProvider:
    return TextTranslationProviderFactory.create(provider, base_dir)


SERVICE_CLASS = TextTranslationService
SERVICE_NAME = "text_translation"
SERVICE_ACCEPT_CRITERIA = ServiceAcceptCriteria(
    service_name=SERVICE_NAME,
    config_subdir="text_translation",
    validate_provider=_validate_translation_provider,
    build_provider=_build_translation_provider,
)

__all__ = [
    "TextTranslationService",
    "TextTranslationServiceProvider",
    "TextTranslationProviderFactory",
    "translate_with_all_active",
]
