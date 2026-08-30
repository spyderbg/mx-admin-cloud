"""Localized string type for multilingual content."""

from __future__ import annotations

from typing import Any

from pydantic import GetCoreSchemaHandler
from pydantic_core import core_schema

from src.core.languages import LANG_MAP


class LocalizedString:
    """A multilingual string with property-style access per language code.

    Language validation is against "LANG_MAP" from "src.core.languages",
    which covers every code listed in "settings.SUPPORTED_LANGUAGES".
    Accessing a code outside that map raises "AttributeError".
    Accessing a valid code that has no stored translation falls back to the
    English (``"en"``) value.

    Serializes to/from a plain "dict[str, str]" so it works transparently
    as a Pydantic field in any request/response schema::

        {"en": "coffee", "fr": "café", "es": "café", "bg": "кафе"}

    Examples:

        ls = LocalizedString({"en": "coffee", "fr": "café"})
        ls.en # "coffee"
        ls.fr # "café"
        ls.es # "coffee" ← falls back to English (es not set)
        ls.jp # AttributeError: 'jp' is not a supported language code
    """

    def __init__(self, data: dict[str, str]) -> None:
        self.__dict__['_data'] = dict(data)

    # ------------------------------------------------------------------
    # Property-style language access
    # ------------------------------------------------------------------

    def __getattr__(self, lang: str) -> str:
        if lang not in LANG_MAP:
            raise AttributeError(
                f"'{lang}' is not a supported language code. "
                f"Supported: {sorted(LANG_MAP)}"
            )
        data: dict[str, str] = self.__dict__['_data']
        return data.get(lang) or data.get('en', '')

    def __setattr__(self, lang: str, value: str) -> None:
        if lang not in LANG_MAP:
            raise AttributeError(
                f"'{lang}' is not a supported language code. "
                f"Supported: {sorted(LANG_MAP)}"
            )
        self.__dict__['_data'][lang] = value

    # ------------------------------------------------------------------
    # Dict-like helpers
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, str]:
        """Return a copy of the underlying language → string mapping."""
        return dict(self.__dict__['_data'])

    def get(self, lang: str, default: str = '') -> str:
        """Return the raw value for *lang* (no English fallback), or *default*."""
        return self.__dict__['_data'].get(lang, default)

    # ------------------------------------------------------------------
    # Pydantic v2 integration
    # ------------------------------------------------------------------

    @classmethod
    def __get_pydantic_core_schema__(
            cls, _source_type: Any, _handler: GetCoreSchemaHandler
    ) -> core_schema.CoreSchema:
        return core_schema.no_info_plain_validator_function(
            cls._validate,
            serialization=core_schema.plain_serializer_function_ser_schema(
                lambda v: v.to_dict(),
                info_arg=False,
            ),
        )

    @classmethod
    def __get_pydantic_json_schema__(
            cls, _core_schema: Any, _handler: Any
    ) -> dict[str, Any]:
        return {
            "type": "object",
            "additionalProperties": {"type": "string"},
            "description": "Language-code → translation map (e.g. {\"en\": \"dog\", \"fr\": \"chien\"})",
        }

    @classmethod
    def _validate(cls, value: Any) -> LocalizedString:
        if isinstance(value, cls):
            return value
        if isinstance(value, dict):
            return cls(value)
        raise ValueError(
            f"Expected dict[str, str] of language codes to strings, "
            f"got {type(value).__name__}"
        )

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return f"LocalizedString({self.__dict__['_data']!r})"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, LocalizedString):
            return self.__dict__['_data'] == other.__dict__['_data']
        return NotImplemented

    def __hash__(self) -> int:
        return hash(frozenset(self.__dict__['_data'].items()))

    def __bool__(self) -> bool:
        return bool(self.__dict__['_data'])
