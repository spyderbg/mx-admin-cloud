"""Static TTS provider: returns an item's existing English voice, or a silent placeholder (provider package).

Importing this package registers ``StaticTTSProvider`` with ``TextToSpeechProviderFactory``
(provider.py calls register() at import time).
"""

from src.services.text_to_speech.providers.static_tts.provider import StaticTTSProvider

__all__ = ["StaticTTSProvider"]
