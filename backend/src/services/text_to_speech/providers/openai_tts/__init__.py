"""OpenAI text-to-speech provider (provider package).

Importing this package registers ``OpenAITTSProvider`` with ``TextToSpeechProviderFactory``
(provider.py calls register() at import time).
"""

from src.services.text_to_speech.providers.openai_tts.provider import OpenAITTSProvider

__all__ = ["OpenAITTSProvider"]
