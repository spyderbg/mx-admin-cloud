"""Voice evaluation service base provider class."""

from __future__ import annotations

from abc import abstractmethod

from src.core.languages import LANG_MAP
from src.services.models import ServiceProvider


class EvaluationServiceProvider(ServiceProvider):
    """Base class for translator service providers."""

    @staticmethod
    def language_name(lang_code: str) -> str:
        """Return the display name for a BCP-47 language code."""
        return LANG_MAP.get(lang_code.lower(), lang_code)

    @abstractmethod
    async def evaluate_text(
            self,
            text: str,
            input_lang_code: str,
            target_lang_code: str,
            category: str | None = None,
    ) -> str | None:
        """Translate a single word/phrase.

        Returns the translated string, or "None" when this translator cannot
        handle the request (signals fallback to the next translator).
        """
