"""Google GenAI image provider using raw HTTP instead of the SDK (provider package).

Importing this package registers ``GenAIHttpImageProvider`` with ``TextToImageProviderFactory``
(provider.py calls register() at import time).
"""

from src.services.text_to_image.providers.genai_http_image.provider import GenAIHttpImageProvider

__all__ = ["GenAIHttpImageProvider"]
