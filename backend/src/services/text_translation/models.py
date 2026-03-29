"""Translation service configuration dataclass and base class."""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.core.languages import LANG_MAP
from src.services.base import ServiceProvider


@dataclass(slots=True)
class BaseTranslatorProviderConfig:
    """Common fields shared by all translator provider configs."""
    input_language: str = "en"
    output_language: str = "en"


@dataclass(slots=True)
class TranslatorConfig:
    """Service-level metadata for a translator (parsed from the service_provider envelope)."""
    service_type: str       # "service" field in JSON  (was: category)
    name: str               # "provider" field in JSON
    enabled: bool
    priority: int
    config: dict[str, Any]  # raw provider config dict; parsed into provider_config by the factory
    config_path: Path
    provider_config: Any = field(default=None)   # typed per-provider config; set by TranslatorFactory.create()
    version: str = field(default="1.0")


class TextTranslateCriteria:
    """Criteria to evaluate a text translation."""
    def __init__(self, cfg: TranslatorConfig) -> None:
        self.cfg = cfg


class TextTranslate(ServiceProvider):
    """Common interface for all translator implementations."""

    def __init__(
        self,
        cfg: TranslatorConfig,
        base_dir: Path,
    ) -> None:
        super().__init__(cfg, base_dir)
        self.provider_config: BaseTranslatorProviderConfig | None = cfg.provider_config

    @staticmethod
    def language_name(lang_code: str) -> str:
        """Return the name of the language."""
        return LANG_MAP.get(lang_code.lower(), lang_code)

    def default_input_language(self) -> str | None:
        """Return the default input language from the typed provider config (or raw dict fallback)."""
        if self.provider_config is not None:
            return self.provider_config.input_language.strip() or None
        value = self.config.get("input_language")
        if value is None:
            return None
        return str(value).strip() or None

    def default_output_language(self) -> str | None:
        """Return the default output language from the typed provider config (or raw dict fallback)."""
        if self.provider_config is not None:
            return self.provider_config.output_language.strip() or None
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
