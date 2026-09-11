"""Unit tests for ``text_translation`` schemas and related pure logic (no HTTP, no registry).

Small surface area keeps runs fast and context small for tooling.
"""

# pylint: disable=duplicate-code,missing-function-docstring

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from src.core.input_loader import load_words
from src.core.models import TranslationSourceWord
from src.services.text_translation.schemas import (
    LangCodeText,
    TranslateFileResponse,
    TranslateRequest,
    TranslateResponse,
    TranslationFileSourceWord,
)


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
        TranslateRequest(target_lang_code="es")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        TranslateRequest(text="hi")  # type: ignore[call-arg]


def test_translate_response_serializes_lang_text() -> None:
    resp = TranslateResponse(
        success=True,
        lang_text=LangCodeText(lang_code="ES", text="hola"),
    )
    dumped = resp.model_dump()
    assert dumped == {
        "success": True,
        "lang_text": {"lang_code": "es", "text": "hola"},
        "errors": [],
    }


def test_translate_file_response_accepts_result_rows() -> None:
    body = TranslateFileResponse(
        results=[
            {"original": "a", "translated": "b", "language": "en"},
        ],
    )
    assert len(body.results) == 1
    assert body.results[0]["translated"] == "b"


def test_translation_file_source_word_from_core() -> None:
    word = TranslationSourceWord(
        text="hi",
        source_language_code="en",
        filename_text="hi",
        expected_text="hi",
        row_index=1,
        column_index=None,
        source_file="/tmp/x.txt",
        source_type="txt",
    )
    payload = TranslationFileSourceWord.from_core(word)
    assert payload.text == "hi"
    assert payload.source_language_code == "en"


def test_load_words_txt_skips_when_source_equals_target(tmp_path: Path) -> None:
    path = tmp_path / "w.txt"
    path.write_text("one\ntwo\n", encoding="utf-8")
    assert not load_words(path, source_language="en", target_language="en")
