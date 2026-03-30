"""Load translator configs from JSON files in config/text_translation/.

Thin wrapper around the generic :func:`~src.services.config_loader.load_service_configs`.
"""

from __future__ import annotations

from pathlib import Path

from src.services.config_loader import load_service_configs
from src.services.text_translation.models import TranslatorConfig


def load_translator_configs(
        base_dir: Path,
        config_dir: Path | None = None,
        dotenv_path: Path | None = None,  # kept for backward-compat signature
) -> list[TranslatorConfig]:
    """Load translator configs from JSON files (sorted by priority)."""
    actual_config_dir = (config_dir or base_dir / "config") / "text_translation"
    configs = load_service_configs(actual_config_dir, TranslatorConfig)
    configs.sort(key=lambda c: c.priority)
    return configs
