"""Speaktor (Transkriptor) TTS provider (provider package).

Importing this package registers ``SpeaktorTTSProvider`` with ``TextToSpeechProviderFactory``
(provider.py calls register() at import time).
"""

from src.services.text_to_speech.providers.speaktor_tts.provider import SpeaktorTTSProvider

__all__ = ["SpeaktorTTSProvider"]
