"""Schemas for internal translation endpoints."""

from __future__ import annotations

# Field layout mirrors core.models.TranslationSourceWord for the translate-file HTTP contract.
# pylint: disable=duplicate-code

from pydantic import BaseModel, field_validator

from src.core.languages import normalize_language_code
from src.core.models import TranslationSourceWord


class TranslationFileSourceWord(BaseModel):
    """One text unit loaded for ``/internal/translate-file`` (HTTP contract for the translation flow)."""

    text: str
    source_language_code: str
    filename_text: str
    expected_text: str
    row_index: int
    column_index: int | None = None
    source_file: str
    source_type: str

    @field_validator("source_language_code")
    @classmethod
    def _normalize_source_lang(cls, value: str) -> str:
        return normalize_language_code(value)

    @classmethod
    def from_core(cls, word: TranslationSourceWord) -> TranslationFileSourceWord:
        """Build schema instance from a :class:`TranslationSourceWord` loaded by ``load_words``."""
        return cls(
            text=word.text,
            source_language_code=word.source_language_code,
            filename_text=word.filename_text,
            expected_text=word.expected_text,
            row_index=word.row_index,
            column_index=word.column_index,
            source_file=word.source_file,
            source_type=word.source_type,
        )


class LangCodeText(BaseModel):
    """Serialized language-code/text pair used in translation responses."""

    lang_code: str
    text: str

    @field_validator("lang_code")
    @classmethod
    def _normalize_lang_code(cls, value: str) -> str:
        return normalize_language_code(value)


class TranslateRequest(BaseModel):
    """Request body for the translation endpoint."""

    text: str
    input_lang_code: str | None = None
    target_lang_code: str
    category: str | None = None


class TranslateResponse(BaseModel):
    """Response body for the translation endpoint."""

    success: bool
    lang_text: LangCodeText


class TranslateFileResponse(BaseModel):
    """Response body for the translation file endpoint."""

    results: list[dict[str, str]]
