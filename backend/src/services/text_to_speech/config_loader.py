"""Load TTS provider configs from JSON files in config/text_to_speech/."""

from __future__ import annotations

import json
from pathlib import Path

from dotenv import load_dotenv

from src.services.text_to_speech.models import ProviderConfig


def _boolish(value: object) -> bool:
    """Coerce a config value to bool; accepts True/False, None, and common string truths."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def load_provider_configs(
        base_dir: Path,
        config_dir: Path | None = None,
        dotenv_path: Path | None = None,
) -> list[ProviderConfig]:
    """Load provider configs from JSON files and environment variables."""

    if dotenv_path:
        load_dotenv(dotenv_path=dotenv_path)
    else:
        load_dotenv()

    actual_config_dir = (config_dir or base_dir / "config") / "text_to_speech"
    configs: list[ProviderConfig] = []

    if not actual_config_dir.exists():
        return configs

    for config_path in sorted(actual_config_dir.glob("*.json")):
        with config_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)

        if not isinstance(payload, dict):
            continue

        service = payload.get("service_provider", {})
        if not isinstance(service, dict):
            continue

        name = str(service.get("provider") or config_path.stem).strip()
        if not name:
            continue

        version = str(service.get("version", "1.0")).strip()
        active = _boolish(service.get("enabled", False))
        raw_config = service.get("config", {})
        if raw_config is None:
            raw_config = {}

        generate = raw_config.get("generate", {})
        evaluate = raw_config.get("evaluate", {})
        if not isinstance(generate, dict) or not isinstance(evaluate, dict):
            continue
        generate["enabled"] = _boolish(generate.get("enabled", active))
        evaluate["enabled"] = _boolish(evaluate.get("enabled", True))

        configs.append(
            ProviderConfig(
                name=name,
                generate=generate,
                evaluate=evaluate,
                config_path=config_path,
                version=version,
            )
        )

    return configs
