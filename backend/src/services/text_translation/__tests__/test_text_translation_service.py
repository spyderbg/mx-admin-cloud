"""Unit tests for ``text_translation`` schemas and related pure logic (no HTTP, no registry).

Small surface area keeps runs fast and context small for tooling.
"""

# pylint: disable=duplicate-code,missing-function-docstring

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from src.services.text_translation.input_loader import load_words
from src.services.models import LangCodeText as ServiceLangCodeText
from src.services.provider_strategy import ProviderSelectionStrategy
from src.services.text_translation.models import (
    TextTranslationServiceProvider,
    TranslateResult,
    TranslateTextRequest,
    TranslationSourceWord,
)
from src.services.text_translation.schemas import (
    LangCodeText,
    TranslateFileResponse,
    TranslateRequest,
    TranslateResponse,
    TranslationFileSourceWord,
)
from src.services.text_translation.service import TextTranslationService


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


# ---------------------------------------------------------------------------
# TextTranslationService.translate_text: FIRST_SUCCESS dispatch
# ---------------------------------------------------------------------------

class _StubTranslator(TextTranslationServiceProvider):
    """Translator for one language pair that answers, declines (None) or raises."""

    def __init__(self, name: str, pair: tuple[str, str] = ("en", "fr"), outcome: str = "ok") -> None:
        super().__init__(
            name=name,
            service_name="text_translation",
            provider_name="stub",
            provider_category="",
            enabled=True,
            priority=1,
            config={"input_language": pair[0], "output_language": pair[1]},
            config_path=Path(f"{name}.json"),
            base_dir=Path("."),
        )
        self.pair = pair
        self.outcome = outcome  # "ok", "none" (can't handle it) or "raise"
        self.requests: list[TranslateTextRequest] = []

    def supports(self, input_lang_code: str, target_lang_code: str) -> bool:
        return (input_lang_code, target_lang_code) == self.pair

    async def translate_text(self, request: TranslateTextRequest) -> str | None:
        self.requests.append(request)
        if self.outcome == "raise":
            raise RuntimeError(f"{self.name} is down")
        if self.outcome == "none":
            return None
        return f"{request.text}@{self.name}"


def _translation_service(*translators: _StubTranslator) -> TextTranslationService:
    service = TextTranslationService(
        service_name="text_translation", package_name="tests", service_dir=Path("."), base_dir=Path("."),
    )
    service.replace_providers(list(translators))
    return service


async def _translate(service: TextTranslationService, text: str = "apple", target: str = "fr"):
    return await service.translate_text(text, input_lang_code="en", target_lang_code=target)


def test_text_translation_supports_only_first_success() -> None:
    service = _translation_service()

    assert service.provider_strategy is ProviderSelectionStrategy.FIRST_SUCCESS
    with pytest.raises(ValueError, match="does not support"):
        service.set_provider_strategy(ProviderSelectionStrategy.ALL_PROVIDERS)


@pytest.mark.asyncio
async def test_translate_text_returns_the_first_translation_without_chaining() -> None:
    first = _StubTranslator("static")
    second = _StubTranslator("openai_en_fr")
    service = _translation_service(first, second)

    result = await _translate(service)

    assert result.success
    assert result.lang_text == ServiceLangCodeText(lang_code="fr", text="apple@static")
    assert result.errors == []
    # The second translator is never fed the first one's output.
    assert not second.requests


@pytest.mark.asyncio
async def test_translate_text_skips_translators_for_other_pairs() -> None:
    spanish = _StubTranslator("openai_en_es", pair=("en", "es"))
    french = _StubTranslator("openai_en_fr")
    service = _translation_service(spanish, french)

    result = await _translate(service)

    assert result.lang_text == ServiceLangCodeText(lang_code="fr", text="apple@openai_en_fr")
    assert not spanish.requests


@pytest.mark.asyncio
async def test_translate_text_falls_back_past_declining_and_failing_translators() -> None:
    declining = _StubTranslator("static", outcome="none")
    failing = _StubTranslator("openai_a", outcome="raise")
    working = _StubTranslator("openai_b")
    service = _translation_service(declining, failing, working)

    result = await _translate(service)

    assert result.success
    assert result.lang_text == ServiceLangCodeText(lang_code="fr", text="apple@openai_b")
    # A translator that declines is not an error; one that raises is reported.
    assert result.errors == ["openai_a: openai_a is down"]
    assert working.requests[0].text == "apple"


@pytest.mark.asyncio
async def test_translate_text_without_any_translation_echoes_the_input() -> None:
    service = _translation_service(
        _StubTranslator("static", outcome="none"),
        _StubTranslator("openai", outcome="raise"),
    )

    result = await _translate(service)

    assert not result.success
    assert result.lang_text == ServiceLangCodeText(lang_code="en", text="apple")
    assert result.errors == ["openai: openai is down"]


@pytest.mark.asyncio
async def test_translate_text_without_a_translator_for_the_pair() -> None:
    service = _translation_service(_StubTranslator("openai_en_es", pair=("en", "es")))

    result = await _translate(service)

    assert result == TranslateResult(success=False, lang_text=None, errors=[])
