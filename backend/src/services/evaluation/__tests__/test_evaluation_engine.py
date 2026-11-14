"""EvaluationEngine: STT provider selection through SpeechToTextProviderFactory, and scoring."""

# pylint: disable=missing-function-docstring

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from src.services.evaluation import EvaluationEngine
from src.services.evaluation.models import STTProvider
from src.services.evaluation.service import SpeechToTextProviderFactory
from src.services.text_to_speech import GeneratedAudio


class _EchoSTT(STTProvider):  # pylint: disable=too-few-public-methods
    """Test STT backend: returns the text stored for each file and records every call."""

    texts: dict[str, str] = {}
    calls: list[tuple[str, str, str, float, Any]] = []

    async def transcribe(
            self, audio_path: Path, language: str, eval_cfg: dict[str, Any], api_key: str, client: Any
    ) -> str:
        if audio_path.name == "broken.wav":
            raise RuntimeError("STT backend failed")
        _EchoSTT.calls.append((audio_path.name, language, api_key, self.timeout_seconds, client))
        return _EchoSTT.texts.get(audio_path.name, "")


@pytest.fixture(name="echo", autouse=True)
def fixture_echo(monkeypatch: pytest.MonkeyPatch) -> type[_EchoSTT]:
    """Register the test backend as "echo" (a new id: no engine code names it) for one test."""
    builders = dict(SpeechToTextProviderFactory._builders)  # pylint: disable=protected-access
    monkeypatch.setattr(SpeechToTextProviderFactory, "_builders", builders)
    SpeechToTextProviderFactory.register("echo", _EchoSTT)
    monkeypatch.setattr(_EchoSTT, "texts", {})
    monkeypatch.setattr(_EchoSTT, "calls", [])
    monkeypatch.setenv("ECHO_API_KEY", "echo-key")
    return _EchoSTT


def _audio(tmp_path: Path, name: str, *, provider: str = "echo", expected: str = "hello world") -> GeneratedAudio:
    path = tmp_path / f"{name}.wav"
    path.write_bytes(b"RIFF")
    return GeneratedAudio(
        provider=provider, request_id=f"req-{name}", language_code="bg", language_name="Bulgarian",
        expected_text=expected, filename_text=name, output_format="mp3", raw_path=path, raw_extension="mp3",
        latency_seconds=0.1, success=True, normalized_path=path,
    )


async def test_engine_transcribes_with_the_registered_provider(tmp_path: Path, echo: type[_EchoSTT]) -> None:
    echo.texts["clip.wav"] = " Hello World "
    client = object()

    result = await EvaluationEngine(timeout_seconds=7).evaluate(
        _audio(tmp_path, "clip"), {"stt_provider": "Echo"}, client
    )

    assert result.success and result.error is None
    assert (result.transcription, result.char_accuracy, result.word_accuracy) == ("Hello World", 100.0, 100.0)
    assert echo.calls == [("clip.wav", "bg", "echo-key", 7, client)]


async def test_configured_language_overrides_the_audio_language(tmp_path: Path, echo: type[_EchoSTT]) -> None:
    await EvaluationEngine().evaluate(_audio(tmp_path, "clip"), {"stt_provider": "echo", "language": "en"}, None)
    assert echo.calls[0][1] == "en"


async def test_stt_provider_defaults_to_provider_name_then_audio_provider(
        tmp_path: Path, echo: type[_EchoSTT]
) -> None:
    engine = EvaluationEngine()
    assert (await engine.evaluate(_audio(tmp_path, "a", provider="other"), {}, None, provider_name="ECHO")).success
    assert (await engine.evaluate(_audio(tmp_path, "b", provider="Echo"), {}, None)).success
    assert [call[0] for call in echo.calls] == ["a.wav", "b.wav"]


async def test_unregistered_stt_provider_fails_after_the_api_key_check(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, echo: type[_EchoSTT]
) -> None:
    engine = EvaluationEngine()
    monkeypatch.delenv("NOPE_API_KEY", raising=False)
    skipped = await engine.evaluate(_audio(tmp_path, "a"), {"stt_provider": "nope"}, None)
    assert (skipped.success, skipped.error) == (False, "Evaluation skipped: NOPE_API_KEY is not set.")

    monkeypatch.setenv("NOPE_API_KEY", "k")
    unsupported = await engine.evaluate(_audio(tmp_path, "a"), {"stt_provider": "nope"}, None)
    assert (unsupported.success, unsupported.error) == (False, "Unsupported stt_provider: nope")
    assert unsupported.transcription == "" and not echo.calls


async def test_provider_errors_propagate(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="STT backend failed"):
        await EvaluationEngine().evaluate(_audio(tmp_path, "broken"), {"stt_provider": "echo"}, None)


async def test_compare_scores_b_against_a_with_one_provider(tmp_path: Path, echo: type[_EchoSTT]) -> None:
    echo.texts.update({"a.wav": "the cat", "b.wav": "the hat"})

    result = await EvaluationEngine().compare(
        _audio(tmp_path, "a"), _audio(tmp_path, "b"), {"stt_provider": "echo"}, None
    )

    assert (result.provider, result.request_id) == ("echo", "req-b")
    assert (result.expected_text, result.transcription, result.levenshtein_distance) == ("the cat", "the hat", 1)
    assert [call[0] for call in echo.calls] == ["a.wav", "b.wav"]


async def test_compare_with_an_unregistered_provider_transcribes_nothing(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, echo: type[_EchoSTT]
) -> None:
    monkeypatch.setenv("NOPE_API_KEY", "k")
    result = await EvaluationEngine().compare(
        _audio(tmp_path, "a"), _audio(tmp_path, "b"), {"stt_provider": "nope"}, None
    )
    assert (result.success, result.error, result.request_id) == (False, "Unsupported stt_provider: nope", "req-b")
    assert not echo.calls
