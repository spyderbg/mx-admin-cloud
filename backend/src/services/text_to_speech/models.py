"""TTS provider configuration dataclass."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class ProviderConfig:
    name: str
    generate: dict[str, Any]
    evaluate: dict[str, Any]
    config_path: Path
    version: str = field(default="1.0")
