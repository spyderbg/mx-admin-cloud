"""Load audio optimizer configs from JSON files in config/optimizers/.

Thin wrapper around the generic :func:`~src.services.config_loader.load_service_configs`.
"""

from __future__ import annotations

from pathlib import Path

from src.services.config_loader import load_service_configs
from src.services.optimization.models import OptimizerConfig


def load_optimizer_configs(
        base_dir: Path,
        config_dir: Path | None = None,
) -> list[OptimizerConfig]:
    """Load optimizer configs from JSON files (sorted by priority)."""
    config_root = (config_dir or base_dir / "config") / "optimizers"
    configs = load_service_configs(config_root, OptimizerConfig)
    configs.sort(key=lambda c: c.priority)
    return configs
