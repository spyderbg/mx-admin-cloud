"""Text translation service base provider class."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from src.core.languages import LANG_MAP
from src.services.models import LangCodeText, ServiceProvider


@dataclass
class TranslateResult:
    """Result of one translation operation."""

    success: bool
    lang_text: LangCodeText | None = None


@dataclass
class TranslateTextRequest:
    """Parameters for a single translation call."""

    text: str
    input_lang_code: str
    target_lang_code: str
    category: str | None = None


class TextTranslationServiceProvider(ServiceProvider, ABC):
    """Base class for translator service providers.

    Reads "input_language" and "output_language" from "self.config"
    (the raw "config" dict stored by ``ServiceProvider``).  Subclasses implement
    "translate_text" and may parse additional typed fields from "self.config"
    in their own "__init__".
    """

    def default_input_language(self) -> str | None:
        """Return the default input language from the provider config dict."""
        raw_lang = self.config.get("input_language")
        if not isinstance(raw_lang, str):
            return None
        return raw_lang.strip() or None

    def default_output_language(self) -> str | None:
        """Return the default output language from the provider config dict."""
        raw_lang = self.config.get("output_language")
        if not isinstance(raw_lang, str):
            return None
        return raw_lang.strip() or None

    @staticmethod
    def language_name(lang_code: str) -> str:
        """Return the display name for a BCP-47 language code."""
        return LANG_MAP.get(lang_code.lower(), lang_code)

    @abstractmethod
    async def translate_text(self, request: TranslateTextRequest) -> str | None:
        """Translate a single word/phrase.

        Returns the translated string, or "None" when this translator cannot
        handle the request (signals fallback to the next translator).
        """
