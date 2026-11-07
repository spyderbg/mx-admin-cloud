"""Offline text-to-speech provider backed by the pyttsx3 library (provider package).

Importing this package registers ``Pyttsx3TTSProvider`` with ``TextToSpeechProviderFactory``
(provider.py calls register() at import time).
"""

from src.services.text_to_speech.providers.pyttsx3_tts.provider import Pyttsx3TTSProvider

__all__ = ["Pyttsx3TTSProvider"]
