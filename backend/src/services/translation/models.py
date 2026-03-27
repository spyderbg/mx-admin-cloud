"""Translation service configuration dataclass and base class."""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.core.languages import LANG_MAP
from src.services.base import ServiceProvider


@dataclass(slots=True)
class TranslatorConfig:
    category: str
    name: str
    enabled: bool
    priority: int
    config: dict[str, Any]
    config_path: Path
    version: str = field(default="1.0")


class TextTranslate(ServiceProvider):
    """Common interface for all translator implementations."""

    def __init__(
        self,
        cfg: TranslatorConfig,
        base_dir: Path,
    ) -> None:
        super().__init__(cfg, base_dir)

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

    def default_output_language(self) -> str | None:
        """Return the default output language."""
        value = self.config.get("output_language")
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
