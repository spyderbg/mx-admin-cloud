"""Unit tests for ``text_translation_routes`` schemas (no HTTP, no registry).

Small surface area keeps runs fast and context small for tooling.
"""

# pylint: disable=missing-function-docstring

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.api.internal.text_translation_routes import (
    TranslateFileResponse,
    TranslateRequest,
    TranslateResponse,
)
from src.services.models import LangCodeText


def test_translate_request_accepts_optional_fields() -> None:
    req = TranslateRequest(text="hello", target_lang_code="es")
    assert req.input_lang_code is None
    assert req.category is None

    full = TranslateRequest(
        text="hello",
        input_lang_code="en",
        target_lang_code="fr",
        category="animals",
    )
    assert full.input_lang_code == "en"
    assert full.category == "animals"


def test_translate_request_requires_text_and_target() -> None:
    with pytest.raises(ValidationError):
        TranslateRequest(target_lang_code="es")
    with pytest.raises(ValidationError):
        TranslateRequest(text="hi")


def test_translate_response_serializes_lang_text() -> None:
    resp = TranslateResponse(
        success=True,
        lang_text=LangCodeText(lang_code="ES", text="hola"),
    )
    dumped = resp.model_dump()
    assert dumped == {"success": True, "lang_text": {"lang_code": "es", "text": "hola"}}


def test_translate_file_response_accepts_result_rows() -> None:
    body = TranslateFileResponse(
        results=[
            {"original": "a", "translated": "b", "language": "en"},
        ],
    )
    assert len(body.results) == 1
    assert body.results[0]["translated"] == "b"
