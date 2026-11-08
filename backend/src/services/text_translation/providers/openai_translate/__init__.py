"""OpenAI-powered translator using the Chat Completions API (provider package).

Importing this package registers ``OpenAITextTranslate`` with ``TextTranslationProviderFactory``
(provider.py calls register() at import time).
"""

from src.services.text_translation.providers.openai_translate.provider import OpenAITextTranslate

__all__ = ["OpenAITextTranslate"]
