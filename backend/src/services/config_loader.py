"""Load provider and translator configs from JSON files and environment variables."""

from __future__ import annotations

import json
from pathlib import Path

from dotenv import load_dotenv

from .models import OptimizerConfig, ProviderConfig, TranslatorConfig


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
        dotenv_path: Path | None = None
) -> list[ProviderConfig]:
    """Load provider configs from JSON files and environment variables."""

    if dotenv_path:
        load_dotenv(dotenv_path=dotenv_path)
    else:
        load_dotenv()

    actual_config_dir = (config_dir or base_dir / "config") / "generators"
    configs: list[ProviderConfig] = []

    if not actual_config_dir.exists():
        return configs

    for config_path in sorted(actual_config_dir.glob("*.json")):
        with config_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)

        if not isinstance(payload, dict):
            continue

        service = payload.get("service", {})
        if not isinstance(service, dict):
            continue

        name = str(service.get("name") or config_path.stem).strip()
        if not name:
            continue

        active = _boolish(service.get("active", False))
        raw_config = service.get("config", {})
        if raw_config is None:
            raw_config = {}

        generate = raw_config.get("generate", {})
        evaluate = raw_config.get("evaluate", {})
        if not isinstance(generate, dict) or not isinstance(evaluate, dict):
            continue
        generate["active"] = _boolish(generate.get("active", active))
        evaluate["active"] = _boolish(evaluate.get("active", True))

        configs.append(
            ProviderConfig(
                name=name,
                generate=generate,
                evaluate=evaluate,
                config_path=config_path,
            )
        )

    return configs


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

    actual_config_dir = (config_dir or base_dir / "config") / "translators"
    configs: list[TranslatorConfig] = []

    if not actual_config_dir.exists():
        return configs

    for config_path in sorted(actual_config_dir.glob("*.json")):
        with config_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)

        if not isinstance(payload, dict):
            continue

        service = payload.get("service", {})
        if not isinstance(service, dict):
            continue

        translator_type = str(service.get("type", "")).strip().lower()
        name = str(service.get("name") or config_path.stem).strip()
        if not translator_type or not name:
            continue

        active = _boolish(service.get("active", False))
        priority = int(service.get("priority", 999))
        raw_config = service.get("config", {})
        if not isinstance(raw_config, dict):
            raw_config = {}

        configs.append(
            TranslatorConfig(
                type=translator_type,
                name=name,
                active=active,
                priority=priority,
                config=raw_config,
                config_path=config_path,
            )
        )

    configs.sort(key=lambda c: c.priority)
    return configs


_OPTIMIZER_DEFAULT_CONFIG: dict[str, object] = {
    "sample_rate": 22050,
    "mono": True,
    "vbr_quality": 9,
}


def load_optimizer_configs(
        base_dir: Path,
        config_dir: Path | None = None,
) -> list[OptimizerConfig]:
    """Load optimizer configs from JSON files and environment variables."""

    config_root = (config_dir or base_dir / "config") / "optimizers"
    if not config_root.exists():
        return []

    configs: list[OptimizerConfig] = []

    for config_file in sorted(config_root.glob("*.json")):
        with config_file.open("r", encoding="utf-8") as handle:
            entry = json.load(handle)

        if not isinstance(entry, dict):
            continue

        service = entry.get("service", {})
        if not isinstance(service, dict):
            continue

        service_type = str(service.get("type", "")).strip().lower()
        name = str(service.get("name") or config_file.stem).strip()
        if not name or not service_type:
            continue

        active = _boolish(service.get("active", False))
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
                type=service_type,
                name=name,
                active=active,
                priority=priority,
                config=merged_config,
                config_path=config_file,
            )
        )

    configs.sort(key=lambda c: c.priority)
    return configs
