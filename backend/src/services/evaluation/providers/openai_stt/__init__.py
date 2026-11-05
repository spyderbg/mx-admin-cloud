"""OpenAI speech-to-text provider (provider package).

Importing this package registers ``OpenAISTTProvider`` with ``SpeechToTextProviderFactory``
(provider.py calls register() at import time).
"""

from src.services.evaluation.providers.openai_stt.provider import OpenAISTTProvider

__all__ = ["OpenAISTTProvider"]
