"""Base translator abstraction and translator factory."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from pathlib import Path

from ..languages import LANG_MAP
from ..models import TranslatorConfig


class TTSTranslate(ABC):
    def __init__(
        self,
        service: TranslatorConfig,
        config: dict[str, object],
        base_dir: Path,
    ) -> None:
        self.service = service
        self.config = config
        self.base_dir = base_dir

    @property
    def name(self) -> str:
        return self.service.name

    def is_active(self) -> bool:
        return bool(self.service.active)

    def api_key_env_name(self) -> str:
        return f"{self.service.name.upper()}_API_KEY"

    def resolve_api_key(self) -> str:
        return os.getenv(self.api_key_env_name(), "")

    def language_name(self, lang_code: str) -> str:
        return LANG_MAP.get(lang_code.lower(), lang_code)

    def default_input_language(self) -> str | None:
        value = self.config.get("input_language")
        if value is None:
            return None
        return str(value).strip() or None

    def default_target_language(self) -> str | None:
        value = self.config.get("target_language")
        if value is None:
            return None
        return str(value).strip() or None

    @abstractmethod
    async def translate_text(
        self,
        text: str,
        input_lang_code: str,
        target_lang_code: str,
        category: str | None = None,
    ) -> str | None:
        """Translate a single word/phrase.

        Returns the translated string, or ``None`` when this translator
        cannot handle the request (signals fallback to the next translator).
        """


def build_translator(config: TranslatorConfig, base_dir: Path) -> TTSTranslate:
    translator_name = config.name.lower()
    if translator_name == "openai":
        from .openai_translate import OpenAITTSTranslate

        return OpenAITTSTranslate(config, config.config, base_dir)
    if translator_name == "static":
        from .static_translate import StaticTTSTranslate

        return StaticTTSTranslate(config, config.config, base_dir)
    raise ValueError(f"Unsupported translator config name '{config.name}' in '{config.config_path}'.")
