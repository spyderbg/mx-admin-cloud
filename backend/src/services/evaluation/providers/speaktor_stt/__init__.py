"""Speaktor (Transkriptor) speech-to-text provider (provider package).

Importing this package registers ``SpeaktorSTTProvider`` with ``SpeechToTextProviderFactory``
(provider.py calls register() at import time).
"""

from src.services.evaluation.providers.speaktor_stt.provider import SpeaktorSTTProvider

__all__ = ["SpeaktorSTTProvider"]
