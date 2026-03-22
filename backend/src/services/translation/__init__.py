"""Translation subsystem: base class, implementations, and helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from ..models import TranslatorConfig
from .base import TTSTranslate, build_translator

__all__ = [
    "TTSTranslate",
    "build_translator",
    "build_translators",
    "translate_with_all_active",
]


def build_translators(
    configs: Sequence[TranslatorConfig],
    base_dir: Path,
) -> list[TTSTranslate]:
    return [build_translator(cfg, base_dir) for cfg in configs if cfg.active]


async def translate_with_all_active(
    translators: Sequence[TTSTranslate],
    text: str,
    input_lang_code: str | None = None,
    target_lang_code: str | None = None,
    category: str | None = None,
) -> str:
    """Run all active translators and return the last successful translation."""
    current_text = text
    for translator in translators:
        resolved_input = input_lang_code or translator.default_input_language()
        resolved_target = target_lang_code or translator.default_target_language()
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
        except Exception:
            continue
    return current_text
