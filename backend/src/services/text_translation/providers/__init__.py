"""Translation provider implementations."""

from src.services.text_translation.providers.openai_translate import OpenAITTSTranslate
from src.services.text_translation.providers.static_translate import StaticTTSTranslate

__all__ = ["OpenAITTSTranslate", "StaticTTSTranslate"]
