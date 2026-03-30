"""Translation subsystem: service class, factory, and provider implementations."""

import src.services.text_translation.providers  # noqa: F401 — triggers provider auto-import

from src.services.text_translation.models import TextTranslationServiceProvider
from src.services.text_translation.service import (
    TextTranslationService,
    TranslatorFactory,
    translate_with_all_active,
)

__all__ = [
    "TextTranslationService",
    "TextTranslationServiceProvider",
    "TranslatorFactory",
    "translate_with_all_active",
]

