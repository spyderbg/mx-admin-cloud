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
    enabled: bool = field(default=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any], config_path: Path) -> ProviderConfig | None:
        """Parse a raw JSON config dict into a ``ProviderConfig``.

        Returns ``None`` when the payload is structurally invalid so the
        generic loader can skip it silently.
        """
        from src.core.utils import boolish  # pylint: disable=import-outside-toplevel

        service = data.get("service_provider", {})
        if not isinstance(service, dict):
            return None

        name = str(service.get("provider") or config_path.stem).strip()
        if not name:
            return None

        active = boolish(service.get("enabled", False))
        raw_config = service.get("config", {})
        if raw_config is None:
            raw_config = {}

        generate = raw_config.get("generate", {})
        evaluate = raw_config.get("evaluate", {})
        if not isinstance(generate, dict) or not isinstance(evaluate, dict):
            return None

        generate["enabled"] = boolish(generate.get("enabled", active))
        evaluate["enabled"] = boolish(evaluate.get("enabled", True))

        return cls(
            name=name,
            generate=generate,
            evaluate=evaluate,
            config_path=config_path,
            enabled=generate["enabled"],
        )
