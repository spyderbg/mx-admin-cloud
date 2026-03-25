"""Audio optimization subsystem: base class, implementations, and builders."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Sequence

from src.core.models import OptimizerConfig
from src.services.optimization.base import AudioOptimizerBase

__all__ = [
    "AudioOptimizerBase",
    "OptimizerFactory",
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
        """Register a new optimizer builder."""
        cls._builders[optimizer_name.strip().lower()] = builder

    @staticmethod
    def _parse_config(raw_value: object, config_path: Path) -> dict[str, Any]:
        if isinstance(raw_value, dict):
            return dict(raw_value)
        if isinstance(raw_value, str):
            text = raw_value.strip()
            if not text:
                return {}
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid optimizer config JSON in '{config_path}'"
                ) from exc
            if isinstance(parsed, dict):
                return parsed
        raise ValueError(f"Optimizer config in '{config_path}' must be a JSON object.")

    @classmethod
    def create(cls, cfg: OptimizerConfig, base_dir: Path) -> AudioOptimizerBase:
        """Create a new optimizer instance."""
        cfg.config = cls._parse_config(cfg.config, cfg.config_path)
        key = cfg.name.strip().lower()
        builder = cls._builders.get(key)
        if builder is None:
            raise ValueError(
                f"Unsupported optimizer service name: '{cfg.name}' "
                f"(category='{cfg.category}', file='{cfg.config_path}')"
            )
        return builder(cfg, base_dir)


def build_optimizer(config: OptimizerConfig, base_dir: Path) -> AudioOptimizerBase:
    """Build an optimizer instance from service metadata and config JSON."""
    return OptimizerFactory.create(config, base_dir)


def build_active_optimizers(
    configs: Sequence[OptimizerConfig],
    base_dir: Path,
) -> list[AudioOptimizerBase]:
    """Build only optimizer instances that are marked active."""
    return [build_optimizer(cfg, base_dir) for cfg in configs if cfg.enabled]


# Trigger self-registration of all implementations
from src.services.optimization.ffmpeg_optimizer import FfmpegAudioOptimizer as _Ffmpeg
from src.services.optimization.pydub_optimizer import PydubAudioOptimizer as _Pydub
