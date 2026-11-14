"""FFmpeg-based audio optimizer (provider package).

Importing this package registers ``FfmpegAudioOptimizer`` with ``AudioOptimizerFactory``
(provider.py calls register() at import time).
"""

from src.services.voice_optimization.providers.ffmpeg_optimizer.provider import FfmpegAudioOptimizer

__all__ = ["FfmpegAudioOptimizer"]
