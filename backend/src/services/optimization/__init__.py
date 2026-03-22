"""Audio optimization subsystem: base class, implementations, and builders."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Sequence

from ..models import OptimizerConfig
from .base import AudioOptimizerBase

__all__ = [
    "AudioOptimizerBase",
    "build_optimizer",
    "build_active_optimizers",
]


class OptimizerFactory:
    """Factory registry that builds optimizers from service metadata."""

    _builders: dict[str, Callable[[OptimizerConfig, Path], AudioOptimizerBase]] = {}

    @classmethod
    def register(
        cls,
        optimizer_name: str,
        builder: Callable[[OptimizerConfig, Path], AudioOptimizerBase],
    ) -> None:
        cls._builders[optimizer_name.strip().lower()] = builder

    @classmethod
    def create(cls, service: OptimizerConfig, base_dir: Path) -> AudioOptimizerBase:
        key = service.name.strip().lower()
        builder = cls._builders.get(key)
        if builder is None:
            raise ValueError(
                f"Unsupported optimizer service name: '{service.name}' "
                f"(type='{service.type}', file='{service.config_path}')"
            )
        return builder(service, base_dir)


def _build_ffmpeg_optimizer(service: OptimizerConfig, base_dir: Path) -> AudioOptimizerBase:
    from .ffmpeg_optimizer import FfmpegAudioOptimizer

    return FfmpegAudioOptimizer(service, service.config, base_dir)


def _build_pydub_optimizer(service: OptimizerConfig, base_dir: Path) -> AudioOptimizerBase:
    from .pydub_optimizer import PydubAudioOptimizer

    return PydubAudioOptimizer(service, service.config, base_dir)


OptimizerFactory.register("ffmpeg", _build_ffmpeg_optimizer)
OptimizerFactory.register("pydub", _build_pydub_optimizer)


def build_optimizer(config: OptimizerConfig, base_dir: Path) -> AudioOptimizerBase:
    """Build an optimizer instance from service metadata and config JSON."""
    return OptimizerFactory.create(config, base_dir)


def build_active_optimizers(
    configs: Sequence[OptimizerConfig],
    base_dir: Path,
) -> list[AudioOptimizerBase]:
    """Build only optimizer instances that are marked active."""
    return [build_optimizer(cfg, base_dir) for cfg in configs if cfg.active]
