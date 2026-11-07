"""ElevenLabs speech-to-text provider (provider package).

Importing this package registers ``ElevenLabsSTTProvider`` with ``SpeechToTextProviderFactory``
(provider.py calls register() at import time).
"""

from src.services.evaluation.providers.elevenlabs_stt.provider import ElevenLabsSTTProvider

__all__ = ["ElevenLabsSTTProvider"]
