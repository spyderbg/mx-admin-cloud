"""Generic service-config loader.

Reads every ``*.json`` file in a given directory, delegates parsing to a
config dataclass's ``from_dict(data, config_path)`` classmethod, and returns
the successfully parsed configs.  Invalid files are silently skipped (the
``from_dict`` method returns ``None`` to signal a bad payload).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TypeVar

T = TypeVar("T")


def load_service_configs(config_dir: Path, config_class: type[T]) -> list[T]:
    """Read all JSON files in *config_dir* and build typed config objects.

    Each JSON file is expected to contain a top-level dict.  The class's
    ``from_dict(data, config_path)`` classmethod performs validation and
    construction; files that yield ``None`` are skipped.
    """
    if not config_dir.exists():
        return []

    configs: list[T] = []
    for config_path in sorted(config_dir.glob("*.json")):
        with config_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            continue
        cfg = config_class.from_dict(payload, config_path)  # type: ignore[attr-defined]
        if cfg is not None:
            configs.append(cfg)
    return configs
