"""Load provider and translator configs from JSON files and environment variables."""

from __future__ import annotations

import json
from pathlib import Path

from dotenv import load_dotenv

from .models import OptimizerConfig, ProviderConfig, TranslatorConfig


def _boolish(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def load_provider_configs(base_dir: Path, config_dir: Path | None = None, dotenv_path: Path | None = None) -> list[ProviderConfig]:
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
        if "generate" not in payload or "evaluate" not in payload:
            continue

        generate = payload.get("generate", {})
        evaluate = payload.get("evaluate", {})
        if not isinstance(generate, dict) or not isinstance(evaluate, dict):
            continue

        provider_name = payload.get("provider") or config_path.stem
        generate["active"] = _boolish(generate.get("active"))
        evaluate["active"] = _boolish(evaluate.get("active", True))

        configs.append(
            ProviderConfig(
                name=provider_name,
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
        if "translate" not in payload:
            continue

        translate = payload.get("translate", {})
        if not isinstance(translate, dict):
            continue

        translator_name = payload.get("translator") or config_path.stem
        translate["active"] = _boolish(translate.get("active"))

        configs.append(
            TranslatorConfig(
                name=translator_name,
                translate=translate,
                config_path=config_path,
            )
        )

    configs.sort(key=lambda c: int(c.translate.get("priority", 999)))
    return configs


_OPTIMIZER_DEFAULT_PARAMS: dict[str, object] = {
    "sample_rate": 22050,
    "mono": True,
    "vbr_quality": 9,
}


def load_optimizer_configs(
    base_dir: Path,
    config_dir: Path | None = None,
) -> list[OptimizerConfig]:
    config_root = (config_dir or base_dir / "config") / "optimizers"
    if not config_root.exists():
        return []

    seen_names: set[str] = set()
    configs: list[OptimizerConfig] = []

    for config_file in sorted(config_root.glob("*.json")):
        with config_file.open("r", encoding="utf-8") as handle:
            entry = json.load(handle)

        if not isinstance(entry, dict):
            continue

        name = config_file.stem.strip()
        opt_type = str(entry.get("type", "")).strip().lower()
        if not name or not opt_type:
            continue
        if name in seen_names:
            raise ValueError(
                f"Duplicate optimizer name '{name}' in {config_file}. "
                "Each optimizer must have a unique name."
            )
        seen_names.add(name)

        active = _boolish(entry.get("active", False))
        raw_params = entry.get("params", {})
        if not isinstance(raw_params, dict):
            raw_params = {}
        params = {**_OPTIMIZER_DEFAULT_PARAMS, **raw_params}

        configs.append(
            OptimizerConfig(
                name=name,
                type=opt_type,
                active=active,
                params=params,
                config_path=config_file,
            )
        )

    return configs
