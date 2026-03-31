"""Translation subsystem: service class, factory, and provider implementations."""

from pathlib import Path

import src.services.text_translation.providers  # noqa: F401 — triggers provider auto-import

from src.services.acceptance import ServiceAcceptCriteria
from src.services.models import ServiceProvider
from src.services.text_translation.models import TextTranslationServiceProvider
from src.services.text_translation.service import (
    TextTranslationService,
    TranslatorFactory,
    translate_with_all_active,
)

SERVICE_CLASS = TextTranslationService
SERVICE_NAME = "text_translation"


def _validate_translation_provider(provider: ServiceProvider) -> None:
    if not TranslatorFactory.supports(provider.name):
        raise ValueError(
            f"Unsupported translator: '{provider.name}' (file='{provider.config_path}')"
        )


def _build_translation_provider(
    provider: ServiceProvider,
    base_dir: Path,
) -> TextTranslationServiceProvider:
    return TranslatorFactory.create(provider, base_dir)


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
    "TranslatorFactory",
    "translate_with_all_active",
]

