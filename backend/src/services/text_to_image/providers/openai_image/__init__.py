"""OpenAI Images provider backed by the official SDK (provider package).

Importing this package registers ``OpenAIImageProvider`` with ``TextToImageProviderFactory``
(provider.py calls register() at import time).
"""

from src.services.text_to_image.providers.openai_image.provider import OpenAIImageProvider

__all__ = ["OpenAIImageProvider"]
