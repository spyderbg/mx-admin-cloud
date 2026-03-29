"""Translation subsystem: base class, factory, implementations, and helpers."""

from src.services.text_translation.models import TextTranslate
from src.services.text_translation.service import (
    TranslatorFactory,
    build_translator,
    build_translators,
    translate_with_all_active,
)

__all__ = [
    "TextTranslate",
    "TranslatorFactory",
    "build_translator",
    "build_translators",
    "translate_with_all_active",
]

# Trigger self-registration of all implementations
from src.services.text_translation import providers as _providers  # noqa: F401
