"""Unit tests for ``TextToSpeechService`` provider selection and generation."""

# pylint: disable=missing-function-docstring,too-few-public-methods

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from src.core.models import GeneratedAudio
from src.services.provider_strategy import AllProvidersFailedError, ProviderSelectionStrategy
from src.services.text_to_speech.models import TTSProvider, TTSRequest
from src.services.text_to_speech.service import (
    NoActiveTTSProvidersError,
    TTSProviderNotFoundError,
    TextToSpeechService,
)


class _StubTTSProvider(TTSProvider):
    """Deterministic provider for service tests."""

    def __init__(self, base_dir: Path, name: str, enabled: bool = True, outcome: str = "ok") -> None:
        super().__init__(
            name=name,
            service_name="text_to_speech",
            provider_name=name,
            provider_category="",
            enabled=enabled,
            priority=1,
            config={"generate": {"enabled": enabled}},
            config_path=base_dir / f"{name}.json",
            base_dir=base_dir,
        )
        self.calls: int = 0
        self.outcome = outcome  # "ok", "fail" (unsuccessful result) or "raise"

    async def generate(self, request: TTSRequest, client: Any) -> GeneratedAudio:
        del client
        self.calls += 1
        if self.outcome == "raise":
            raise RuntimeError(f"{self.name} is down")
        return GeneratedAudio(
            provider=self.name,
            request_id=request.request_id,
            language_code=request.language_code,
            language_name=request.language_name,
            expected_text=request.expected_text,
            filename_text=request.filename_text,
            output_format=request.output_format,
            raw_path=self.base_dir / f"{request.request_id}.{request.output_format}",
            raw_extension=request.output_format,
            latency_seconds=0.1,
            success=self.outcome == "ok",
            error=None if self.outcome == "ok" else f"{self.name} could not synthesize",
        )


def _make_service(tmp_path: Path, providers: list[TTSProvider]) -> TextToSpeechService:
    service = TextToSpeechService(
        service_name="text_to_speech",
        package_name="tests",
        service_dir=tmp_path,
        base_dir=tmp_path,
    )
    service.replace_providers(providers)
    return service


def _make_request() -> TTSRequest:
    return TTSRequest(
        request_id="req-1",
        text="apple",
        language_code="en",
        language_name="English",
        filename_text="apple",
        expected_text="apple",
        source_file="unit-test",
        source_type="inline",
        row_index=0,
        output_format="mp3",
    )


@pytest.mark.asyncio
async def test_generate_raises_when_no_active_providers(tmp_path: Path) -> None:
    service = _make_service(tmp_path, [])

    with pytest.raises(NoActiveTTSProvidersError):
        await service.generate(_make_request(), object())


@pytest.mark.asyncio
async def test_generate_raises_when_provider_name_missing(tmp_path: Path) -> None:
    provider = _StubTTSProvider(tmp_path, "openai")
    service = _make_service(tmp_path, [provider])

    with pytest.raises(TTSProviderNotFoundError):
        await service.generate(_make_request(), object(), provider_name="elevenlabs")


@pytest.mark.asyncio
async def test_generate_uses_requested_provider_name(tmp_path: Path) -> None:
    first = _StubTTSProvider(tmp_path, "openai")
    second = _StubTTSProvider(tmp_path, "elevenlabs")
    service = _make_service(tmp_path, [first, second])

    result = await service.generate(_make_request(), object(), provider_name="elevenlabs")

    assert result.provider == "elevenlabs"
    assert first.calls == 0
    assert second.calls == 1


@pytest.mark.asyncio
async def test_generate_uses_first_active_provider_by_default(tmp_path: Path) -> None:
    first = _StubTTSProvider(tmp_path, "speaktor")
    second = _StubTTSProvider(tmp_path, "openai")
    service = _make_service(tmp_path, [first, second])

    result = await service.generate(_make_request(), object())

    assert result.provider == "speaktor"
    assert first.calls == 1
    assert second.calls == 0


def test_text_to_speech_supports_only_first_success(tmp_path: Path) -> None:
    service = _make_service(tmp_path, [])

    assert service.provider_strategy is ProviderSelectionStrategy.FIRST_SUCCESS
    with pytest.raises(ValueError, match="does not support"):
        service.set_provider_strategy(ProviderSelectionStrategy.ALL_PROVIDERS)


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["raise", "fail"])
async def test_generate_falls_back_to_the_next_provider(tmp_path: Path, outcome: str) -> None:
    broken = _StubTTSProvider(tmp_path, "speaktor", outcome=outcome)
    working = _StubTTSProvider(tmp_path, "openai")
    unused = _StubTTSProvider(tmp_path, "elevenlabs")
    service = _make_service(tmp_path, [broken, working, unused])

    result = await service.generate(_make_request(), object())

    assert result.provider == "openai"
    assert result.success
    assert (broken.calls, working.calls, unused.calls) == (1, 1, 0)


@pytest.mark.asyncio
async def test_generate_reports_every_provider_error_when_all_fail(tmp_path: Path) -> None:
    service = _make_service(tmp_path, [
        _StubTTSProvider(tmp_path, "speaktor", outcome="raise"),
        _StubTTSProvider(tmp_path, "openai", outcome="fail"),
    ])

    with pytest.raises(AllProvidersFailedError) as excinfo:
        await service.generate(_make_request(), object())

    assert excinfo.value.errors == ["speaktor: speaktor is down", "openai: openai could not synthesize"]


@pytest.mark.asyncio
async def test_generate_with_provider_name_returns_its_failure_without_fallback(tmp_path: Path) -> None:
    named = _StubTTSProvider(tmp_path, "speaktor", outcome="fail")
    other = _StubTTSProvider(tmp_path, "openai")
    service = _make_service(tmp_path, [named, other])

    result = await service.generate(_make_request(), object(), provider_name="speaktor")

    assert not result.success
    assert other.calls == 0
