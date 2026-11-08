"""Google GenAI image provider backed by the ``google-genai`` SDK (provider package).

Importing this package registers ``GenAIImageProvider`` with ``TextToImageProviderFactory``
(provider.py calls register() at import time).
"""

from src.services.text_to_image.providers.genai_image.provider import GenAIImageProvider

__all__ = ["GenAIImageProvider"]
