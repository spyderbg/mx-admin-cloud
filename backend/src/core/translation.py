"""Translation service: delegates to multi-provider services.translation.

Keeps the ``translate_word()`` signature so ``content_service.py`` needs no changes.
"""

import asyncio

from src.core.config import settings
from src.services.config_loader import load_translator_configs
from src.services.translation import build_translators, translate_with_all_active


def translate_word(
    text: str,
    target_lang_code: str | None = None,
    category: str | None = None,
    input_lang_code: str | None = None,
) -> str:
    """Translate text using all active translators with optional language overrides."""
    if not text or not text.strip():
        return text.strip()

    base_dir = settings.BASE_DIR
    configs = load_translator_configs(base_dir=base_dir, config_dir=base_dir / settings.CONFIG_DIR)
    translators = build_translators(configs, base_dir)

    if not translators:
        raise ValueError("No active translators configured.")

    result = asyncio.run(
        translate_with_all_active(
            translators,
            text=text.strip(),
            input_lang_code=input_lang_code,
            target_lang_code=target_lang_code,
            category=category,
        )
    )
    return result or text.strip()
