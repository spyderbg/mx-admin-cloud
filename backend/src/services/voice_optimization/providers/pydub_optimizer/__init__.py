"""Pydub-based audio optimizer (provider package).

Importing this package registers ``PydubAudioOptimizer`` with ``AudioOptimizerFactory``
(provider.py calls register() at import time).
"""

from src.services.voice_optimization.providers.pydub_optimizer.provider import PydubAudioOptimizer

__all__ = ["PydubAudioOptimizer"]
