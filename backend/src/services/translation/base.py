"""Base translator abstraction and translator factory."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from pathlib import Path

from ..languages import LANG_MAP
from ..models import TranslatorConfig


class TTSTranslate(ABC):
    def __init__(self, config: TranslatorConfig, base_dir: Path) -> None:
        self.config = config
        self.base_dir = base_dir

    @property
    def name(self) -> str:
        return self.config.name

    def is_active(self) -> bool:
        return bool(self.config.translate.get("active"))

    def priority(self) -> int:
        return int(self.config.translate.get("priority", 999))

    def api_key_env_name(self) -> str:
        return f"{self.config.name.upper()}_API_KEY"

    def resolve_api_key(self) -> str:
        return os.getenv(self.api_key_env_name(), "")

    def language_name(self, lang_code: str) -> str:
        return LANG_MAP.get(lang_code.lower(), lang_code)

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
    name = config.name.lower()
    if name == "openai":
        from .openai_translate import OpenAITTSTranslate

        return OpenAITTSTranslate(config, base_dir)
    if name == "static":
        from .static_translate import StaticTTSTranslate

        return StaticTTSTranslate(config, base_dir)
    raise ValueError(f"Unsupported translator config: {config.name}")
