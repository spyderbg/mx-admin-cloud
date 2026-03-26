"""Translation service models and base classes."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from pathlib import Path

from src.core.languages import LANG_MAP
from src.core.models import TranslatorConfig


class TextTranslate(ABC):
    """Common interface for all translator implementations."""

    def __init__(
        self,
        cfg: TranslatorConfig,
        base_dir: Path,
    ) -> None:
        self.cfg = cfg
        self.config = cfg.config
        self.base_dir = base_dir

    @property
    def name(self) -> str:
        """Return the name of the translator."""
        return self.cfg.name

    def is_active(self) -> bool:
        """Return whether this translator should be used."""
        return bool(self.cfg.enabled)

    def api_key_env_name(self) -> str:
        """Return the environment variable name for the API key."""
        return f"{self.cfg.name.upper()}_API_KEY"

    def resolve_api_key(self) -> str:
        """Resolve the API key from the environment variables."""
        return os.getenv(self.api_key_env_name(), "")

    @staticmethod
    def language_name(lang_code: str) -> str:
        """Return the name of the language."""
        return LANG_MAP.get(lang_code.lower(), lang_code)

    def default_input_language(self) -> str | None:
        """Return the default input language."""
        value = self.config.get("input_language")
        if value is None:
            return None
        return str(value).strip() or None

    def default_target_language(self) -> str | None:
        """Return the default target language."""
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
