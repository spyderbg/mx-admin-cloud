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


def _validate_translation_provider(provider: ServiceProvider) -> None:
    if not TextTranslationProviderFactory.supports(provider.name):
        raise ValueError(
            f"Unsupported translator: '{provider.name}' (file='{provider.config_path}')"
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
    "SERVICE_CLASS",
    "TextTranslationService",
    "TextTranslationServiceProvider",
    "TextTranslationProviderFactory",
    "translate_with_all_active",
]
