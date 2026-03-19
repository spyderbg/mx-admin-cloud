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
    "translate_with_fallback",
]


def build_translators(
    configs: Sequence[TranslatorConfig],
    base_dir: Path,
) -> list[TTSTranslate]:
    translators = [
        build_translator(cfg, base_dir)
        for cfg in configs
        if cfg.translate.get("active")
    ]
    translators.sort(key=lambda t: t.priority())
    return translators


async def translate_with_fallback(
    translators: Sequence[TTSTranslate],
    text: str,
    input_lang_code: str,
    target_lang_code: str,
    category: str | None = None,
) -> str:
    """Try each translator in priority order; return original text as last resort."""
    for translator in translators:
        try:
            result = await translator.translate_text(
                text, input_lang_code, target_lang_code, category,
            )
            if result is not None:
                return result
        except Exception:
            continue
    return text
