"""OpenAI Images provider using raw HTTP instead of the SDK (provider package).

Importing this package registers ``OpenAIHttpImageProvider`` with ``TextToImageProviderFactory``
(provider.py calls register() at import time).
"""

from src.services.text_to_image.providers.openai_http_image.provider import OpenAIHttpImageProvider

__all__ = ["OpenAIHttpImageProvider"]
