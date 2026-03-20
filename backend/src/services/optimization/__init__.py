"""Audio optimization subsystem: base class, implementations, and builders."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from ..models import OptimizerConfig
from .base import AudioOptimizerBase

__all__ = [
    "AudioOptimizerBase",
    "build_optimizer",
    "build_active_optimizers",
]


def build_optimizer(config: OptimizerConfig, base_dir: Path) -> AudioOptimizerBase:
    """Instantiate an optimizer implementation from its config type."""
    opt_type = config.type.lower()
    if opt_type == "ffmpeg":
        from .ffmpeg_optimizer import FfmpegAudioOptimizer

        return FfmpegAudioOptimizer(config, base_dir)
    if opt_type == "pydub":
        from .pydub_optimizer import PydubAudioOptimizer

        return PydubAudioOptimizer(config, base_dir)
    raise ValueError(
        f"Unsupported optimizer type: '{config.type}' (name='{config.name}')"
    )


def build_active_optimizers(
    configs: Sequence[OptimizerConfig],
    base_dir: Path,
) -> list[AudioOptimizerBase]:
    """Build only optimizer instances that are marked active."""
    return [build_optimizer(cfg, base_dir) for cfg in configs if cfg.active]
