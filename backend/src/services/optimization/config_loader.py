"""Load audio optimizer configs from JSON files in config/optimizers/."""

from __future__ import annotations

import json
from pathlib import Path

from src.services.optimization.models import OptimizerConfig


def _boolish(value: object) -> bool:
    """Coerce a config value to bool; accepts True/False, None, and common string truths."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


_OPTIMIZER_DEFAULT_CONFIG: dict[str, object] = {
    "sample_rate": 22050,
    "mono": True,
    "vbr_quality": 9,
}


def load_optimizer_configs(
        base_dir: Path,
        config_dir: Path | None = None,
) -> list[OptimizerConfig]:
    """Load optimizer configs from JSON files."""

    config_root = (config_dir or base_dir / "config") / "optimizers"
    if not config_root.exists():
        return []

    configs: list[OptimizerConfig] = []

    for config_file in sorted(config_root.glob("*.json")):
        with config_file.open("r", encoding="utf-8") as handle:
            entry = json.load(handle)

        if not isinstance(entry, dict):
            continue

        service = entry.get("service_provider", {})
        if not isinstance(service, dict):
            continue

        service_type = str(service.get("service", "")).strip().lower()
        name = str(service.get("provider") or config_file.stem).strip()
        if not name or not service_type:
            continue

        version = str(service.get("version", "1.0")).strip()
        active = _boolish(service.get("enabled", False))
        priority = int(service.get("priority", 999))
        raw_config = service.get("config", {})
        if raw_config is None:
            raw_config = {}
        merged_config: object
        if isinstance(raw_config, dict):
            merged_config = {**_OPTIMIZER_DEFAULT_CONFIG, **raw_config}
        else:
            merged_config = raw_config

        configs.append(
            OptimizerConfig(
                service_type=service_type,
                name=name,
                enabled=active,
                priority=priority,
                config=merged_config,
                config_path=config_file,
                version=version,
            )
        )

    configs.sort(key=lambda c: c.priority)
    return configs
