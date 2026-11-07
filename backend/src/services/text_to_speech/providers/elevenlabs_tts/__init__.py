"""ElevenLabs text-to-speech provider (provider package).

Importing this package registers ``ElevenLabsTTSProvider`` with ``TextToSpeechProviderFactory``
(provider.py calls register() at import time).
"""

from src.services.text_to_speech.providers.elevenlabs_tts.provider import ElevenLabsTTSProvider

__all__ = ["ElevenLabsTTSProvider"]
