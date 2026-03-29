"""Load translator configs from JSON files in config/text_translation/."""

from __future__ import annotations

import json
from pathlib import Path

from dotenv import load_dotenv

from src.services.text_translation.models import TranslatorConfig


def _boolish(value: object) -> bool:
    """Coerce a config value to bool; accepts True/False, None, and common string truths."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def load_translator_configs(
        base_dir: Path,
        config_dir: Path | None = None,
        dotenv_path: Path | None = None,
) -> list[TranslatorConfig]:
    """Load translator configs from JSON files and environment variables."""

    if dotenv_path:
        load_dotenv(dotenv_path=dotenv_path)
    else:
        load_dotenv()

    actual_config_dir = (config_dir or base_dir / "config") / "text_translation"
    configs: list[TranslatorConfig] = []

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

        translator_type = str(service.get("service", "")).strip().lower()
        name = str(service.get("provider") or config_path.stem).strip()
        if not translator_type or not name:
            continue

        version = str(service.get("version", "1.0")).strip()
        active = _boolish(service.get("enabled", False))
        priority = int(service.get("priority", 999))
        raw_config = service.get("config", {})
        if not isinstance(raw_config, dict):
            raw_config = {}

        configs.append(
            TranslatorConfig(
                category=translator_type,
                name=name,
                enabled=active,
                priority=priority,
                config=raw_config,
                config_path=config_path,
                version=version,
            )
        )

    configs.sort(key=lambda c: c.priority)
    return configs
