"""Audio optimizer configuration dataclass."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class OptimizerConfig:
    category: str
    name: str
    enabled: bool
    priority: int
    config: dict[str, Any]
    config_path: Path
    version: str = field(default="1.0")
