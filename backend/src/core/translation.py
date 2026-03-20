"""Translation service: delegates to multi-provider services.translation.

Keeps the ``translate_word()`` signature so ``content_service.py`` needs no changes.
"""

import asyncio
from pathlib import Path

from src.core.config import settings
from src.services.config_loader import load_translator_configs
from src.services.translation import build_translators, translate_with_fallback


def translate_word(text: str, target_lang_code: str, category: str | None = None) -> str:
    """Translate a single English word to the target language using configured translators."""
    if not text or not text.strip():
        return text.strip()

    base_dir = Path(".")
    configs = load_translator_configs(base_dir=base_dir, config_dir=Path(settings.CONFIG_DIR))
    translators = build_translators(configs, base_dir)

    if not translators:
        raise ValueError("No active translators configured.")

    result = asyncio.run(
        translate_with_fallback(
            translators,
            text=text.strip(),
            input_lang_code="en",
            target_lang_code=target_lang_code,
            category=category,
        )
    )
    return result or text.strip()
