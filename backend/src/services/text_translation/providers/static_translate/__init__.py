"""Static CSV-based translator using pre-built content directories (provider package).

Importing this package registers ``StaticTextTranslate`` with ``TextTranslationProviderFactory``
(provider.py calls register() at import time).
"""

from src.services.text_translation.providers.static_translate.provider import StaticTextTranslate

__all__ = ["StaticTextTranslate"]
