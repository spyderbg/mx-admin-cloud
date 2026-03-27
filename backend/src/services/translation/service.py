"""Translation service business logic: factory, builders, and orchestration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Sequence

from src.core.models import TranslatorConfig
from src.services.translation.models import TextTranslate


class TranslatorFactory:
    """Factory registry that builds translators from service metadata."""

    _builders: dict[str, Callable[[TranslatorConfig, Path], TextTranslate]] = {}

    @classmethod
    def register(
        cls,
        translator_name: str,
        builder: Callable[[TranslatorConfig, Path], TextTranslate],
    ) -> None:
        """Register a new translator builder."""
        cls._builders[translator_name.strip().lower()] = builder

    @staticmethod
    def _parse_config(raw_value: object, config_path: Path) -> dict[str, Any]:
        if isinstance(raw_value, dict):
            return dict(raw_value)
        if isinstance(raw_value, str):
            text = raw_value.strip()
            if not text:
                return {}
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid translator config JSON in '{config_path}'"
                ) from exc
            if isinstance(parsed, dict):
                return parsed
        raise ValueError(f"Translator config in '{config_path}' must be a JSON object.")

    @classmethod
    def create(cls, cfg: TranslatorConfig, base_dir: Path) -> TextTranslate:
        """Create a new translator instance."""
        cfg.config = cls._parse_config(cfg.config, cfg.config_path)
        key = cfg.name.strip().lower()
        builder = cls._builders.get(key)
        if builder is None:
            raise ValueError(
                f"Unsupported translator service name: '{cfg.name}' "
                f"(category='{cfg.category}', file='{cfg.config_path}')"
            )
        return builder(cfg, base_dir)


def build_translator(config: TranslatorConfig, base_dir: Path) -> TextTranslate:
    """Build a translator instance from service metadata and config JSON."""
    return TranslatorFactory.create(config, base_dir)


def build_translators(
    configs: Sequence[TranslatorConfig],
    base_dir: Path,
) -> list[TextTranslate]:
    """Build only translator instances that are marked active."""
    return [build_translator(cfg, base_dir) for cfg in configs if cfg.enabled]


async def translate_with_all_active(
    translators: Sequence[TextTranslate],
    text: str,
    input_lang_code: str | None = None,
    target_lang_code: str | None = None,
    category: str | None = None,
) -> str:
    """Run all active translators and return the last successful translation."""
    current_text = text
    for translator in translators:
        resolved_input = input_lang_code or translator.default_input_language()
        resolved_target = target_lang_code or translator.default_output_language()
        if not resolved_input or not resolved_target:
            continue
        try:
            result = await translator.translate_text(
                current_text,
                resolved_input,
                resolved_target,
                category,
            )
            if result is not None:
                current_text = result
        except Exception:  # pylint: disable=broad-exception-caught
            continue
    return current_text
