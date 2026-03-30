"""Load TTS provider configs from JSON files in config/text_to_speech/.

Thin wrapper around the generic :func:`~src.services.config_loader.load_service_configs`.
"""

from __future__ import annotations

from pathlib import Path

from src.services.config_loader import load_service_configs
from src.services.text_to_speech.models import ProviderConfig


def load_provider_configs(
        base_dir: Path,
        config_dir: Path | None = None,
        dotenv_path: Path | None = None,  # kept for backward-compat signature
) -> list[ProviderConfig]:
    """Load provider configs from JSON files."""
    actual_config_dir = (config_dir or base_dir / "config") / "text_to_speech"
    return load_service_configs(actual_config_dir, ProviderConfig)
