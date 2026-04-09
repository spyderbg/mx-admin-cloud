"""Schemas for internal translation endpoints."""

from pydantic import BaseModel, field_validator

from src.core.languages import normalize_language_code


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
