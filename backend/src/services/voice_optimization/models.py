"""Audio optimizer configuration dataclass."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

OPTIMIZER_DEFAULT_CONFIG: dict[str, object] = {
    "sample_rate": 22050,
    "mono": True,
    "vbr_quality": 9,
}


@dataclass(slots=True)
class OptimizerConfig:
    """Configuration for an audio optimizer."""
    service_type: str   # "service" field in JSON  (was: category)
    name: str
    enabled: bool
    priority: int
    config: dict[str, Any]
    config_path: Path

    @classmethod
    def from_dict(cls, data: dict[str, Any], config_path: Path) -> OptimizerConfig | None:
        """Parse a raw JSON config dict into an ``OptimizerConfig``.

        Merges ``OPTIMIZER_DEFAULT_CONFIG`` under user-supplied values so
        callers always get ``sample_rate``, ``mono``, and ``vbr_quality``.
        Returns ``None`` when the payload is structurally invalid.
        """
        from src.core.utils import boolish  # pylint: disable=import-outside-toplevel

        service = data.get("service_provider", {})
        if not isinstance(service, dict):
            return None

        service_type = str(service.get("service", "")).strip().lower()
        name = str(service.get("provider") or config_path.stem).strip()
        if not name or not service_type:
            return None

        active = boolish(service.get("enabled", False))
        priority = int(service.get("priority", 999))
        raw_config = service.get("config", {})
        if raw_config is None:
            raw_config = {}
        if isinstance(raw_config, dict):
            merged_config: dict[str, Any] = {**OPTIMIZER_DEFAULT_CONFIG, **raw_config}
        else:
            merged_config = raw_config

        return cls(
            service_type=service_type,
            name=name,
            enabled=active,
            priority=priority,
            config=merged_config,
            config_path=config_path,
        )
