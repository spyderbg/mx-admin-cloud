"""Self-hosted Qwen translator using the OpenAI-compatible Chat Completions API (provider package).

Importing this package registers ``QwenTextTranslate`` with ``TextTranslationProviderFactory``
(provider.py calls register() at import time).
"""

from src.services.text_translation.providers.qwen_translate.provider import QwenTextTranslate

__all__ = ["QwenTextTranslate"]
