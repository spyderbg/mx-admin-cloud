"""Translation subsystem: base class, factory, implementations, and helpers."""

from src.services.translation.models import TextTranslate
from src.services.translation.service import (
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
from src.services.translation import providers as _providers  # noqa: F401
