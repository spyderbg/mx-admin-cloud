"""Tests for the voice optimization normalizer's output file naming."""

# pylint: disable=missing-function-docstring,protected-access

from __future__ import annotations

from pathlib import Path

import pytest

from src.services.text_to_speech import GeneratedAudio
from src.services.voice_optimization import normalizer


def _audio(*, language_code: str, filename_text: str, expected_text: str) -> GeneratedAudio:
    return GeneratedAudio(
        provider="openai_tts",
        request_id="req-7",
        language_code=language_code,
        language_name="",
        expected_text=expected_text,
        filename_text=filename_text,
        output_format="mp3",
        raw_path=Path("raw.mp3"),
        raw_extension="mp3",
        latency_seconds=0.0,
        success=True,
    )


@pytest.mark.parametrize(
    ("language_code", "filename_text", "expected_text", "stem"),
    [
        # Non-English audio is named after its (English) filename text...
        ("fr", "apple", "pomme", "apple_fr"),
        # ...English audio after the spoken text itself.
        ("en", "ignored", "Water Bottle", "Water_Bottle_en"),
        # Nothing usable to slugify falls back to the request id.
        ("en", "", "", "req-7_en"),
    ],
)
def test_normalized_file_stem_is_built_from_the_generated_audio(
    language_code: str, filename_text: str, expected_text: str, stem: str
) -> None:
    audio = _audio(language_code=language_code, filename_text=filename_text, expected_text=expected_text)

    assert normalizer._build_output_stem_from_result(audio) == stem
