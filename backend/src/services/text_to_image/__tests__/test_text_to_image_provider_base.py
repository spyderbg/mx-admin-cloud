"""Unit tests for the shared provider template method (write + cost + logging)."""

# pylint: disable=missing-function-docstring,protected-access,too-few-public-methods
# pylint: disable=redefined-outer-name,import-outside-toplevel

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from src.services.logging.schemas import LogProviderRequest
from src.services.text_to_image import models as image_models
from src.services.text_to_image.governor import (
    NonRetryableError,
    RequestGovernor,
    RetryableError,
    TrafficPolicy,
)
from src.services.text_to_image.models import ImagePayload, ImageRequest, TextToImageProvider

_PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6300010000050001"
)


class _ScriptedProvider(TextToImageProvider):
    """Provider whose attempts are scripted by the test."""

    def __init__(self, tmp_path: Path, script: list[Any], config: dict | None = None) -> None:
        super().__init__(
            name="scripted",
            service_name="text_to_image",
            provider_name="scripted",
            provider_category="",
            enabled=True,
            priority=1,
            config=config or {
                "generate": {"model": "test-model", "size": "1024x1024", "quality": "low"},
                "pricing": {"test-model": {"1024x1024|low": 0.005}},
            },
            config_path=tmp_path / "scripted.json",
            base_dir=tmp_path,
        )
        self._script = list(script)
        self.attempt_count = 0

    async def fetch_image(self, request: ImageRequest, client: Any) -> ImagePayload:
        del request, client
        self.attempt_count += 1
        step = self._script.pop(0) if self._script else self._script_default()
        if isinstance(step, BaseException):
            raise step
        return step

    @staticmethod
    def _script_default() -> ImagePayload:
        return ImagePayload(data=b"IMAGE", mime_type="image/png")


class _LogRecorder:
    """Async stand-in for ``log_provider_request``."""

    def __init__(self) -> None:
        self.records: list[LogProviderRequest] = []

    async def __call__(self, request: LogProviderRequest) -> None:
        self.records.append(request)


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> _LogRecorder:
    rec = _LogRecorder()
    monkeypatch.setattr(image_models, "log_provider_request", rec)
    return rec


@pytest.fixture(autouse=True)
def _isolate_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.core.config import settings

    monkeypatch.setattr(settings, "OUTPUT_DIR", str(tmp_path / "output"))


def _request(**overrides: Any) -> ImageRequest:
    payload: dict[str, Any] = {
        "request_id": "img-1",
        "prompt": "a friendly cat",
        "filename_text": "friendly cat",
    }
    payload.update(overrides)
    return ImageRequest(**payload)


@pytest.mark.asyncio
async def test_success_writes_file_and_logs(tmp_path: Path, recorder: _LogRecorder) -> None:
    provider = _ScriptedProvider(tmp_path, [ImagePayload(data=_PNG_1X1, mime_type="image/png")])

    result = await provider.generate(_request(), object())

    assert result.success is True
    assert result.error is None
    assert result.raw_extension == "png"
    assert result.raw_path.exists()
    assert result.raw_path.name == "friendly_cat.png"
    assert result.raw_path.read_bytes() == _PNG_1X1
    assert result.model == "test-model"
    assert result.cost_estimate == 0.005
    assert result.attempts == 1
    # Dimensions come from the PNG header, not the configured size.
    assert (result.width, result.height) == (1, 1)

    assert len(recorder.records) == 1
    record = recorder.records[0]
    assert record.provider == "scripted"
    assert record.request_kind == "image"
    assert record.status == "success"
    assert record.size == "1024x1024"
    assert record.quality == "low"
    assert record.cost_estimate == 0.005
    assert record.input_data == "a friendly cat"
    assert record.error is None


@pytest.mark.asyncio
async def test_failure_is_reported_in_band_and_logged(tmp_path: Path, recorder: _LogRecorder) -> None:
    provider = _ScriptedProvider(tmp_path, [NonRetryableError("OPENAI_API_KEY is not set.")])

    result = await provider.generate(_request(), object())

    assert result.success is False
    assert result.error == "OPENAI_API_KEY is not set."
    assert result.cost_estimate == 0.0
    assert not result.raw_path.exists()

    assert len(recorder.records) == 1
    record = recorder.records[0]
    assert record.status == "error"
    assert record.error == "OPENAI_API_KEY is not set."
    assert record.attempts == 1


@pytest.mark.asyncio
async def test_unexpected_provider_exception_never_escapes(tmp_path: Path, recorder: _LogRecorder) -> None:
    provider = _ScriptedProvider(tmp_path, [KeyError("predictions")])

    result = await provider.generate(_request(), object())

    assert result.success is False
    assert "KeyError" in result.error
    assert recorder.records[0].status == "error"


@pytest.mark.asyncio
async def test_retry_then_success_counts_attempts(tmp_path: Path, recorder: _LogRecorder) -> None:
    provider = _ScriptedProvider(
        tmp_path,
        [RetryableError("429 slow down"), ImagePayload(data=_PNG_1X1, mime_type="image/png")],
    )
    slept: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    provider._governor = RequestGovernor(
        TrafficPolicy(max_retries=2, backoff_base_seconds=1.0),
        sleep=_fake_sleep,
        rng=lambda: 0.0,
    )

    result = await provider.generate(_request(), object())

    assert result.success is True
    assert result.attempts == 2
    assert provider.attempt_count == 2
    assert slept == [1.0]
    assert recorder.records[0].attempts == 2


@pytest.mark.asyncio
async def test_exhausted_retries_report_total_attempts(tmp_path: Path, recorder: _LogRecorder) -> None:
    provider = _ScriptedProvider(tmp_path, [RetryableError("boom")] * 3)

    async def _fake_sleep(_seconds: float) -> None:
        return None

    provider._governor = RequestGovernor(
        TrafficPolicy(max_retries=2, backoff_base_seconds=0.0),
        sleep=_fake_sleep,
    )

    result = await provider.generate(_request(), object())

    assert result.success is False
    assert result.attempts == 3
    assert recorder.records[0].attempts == 3


@pytest.mark.asyncio
async def test_jpeg_response_changes_extension(tmp_path: Path, recorder: _LogRecorder) -> None:
    del recorder
    provider = _ScriptedProvider(tmp_path, [ImagePayload(data=b"JPEGBYTES", mime_type="image/jpeg")])

    result = await provider.generate(_request(), object())

    assert result.raw_extension == "jpg"
    assert result.raw_path.name == "friendly_cat.jpg"
    assert result.raw_path.exists()
    # Falls back to the configured size when the bytes carry no readable header.
    assert (result.width, result.height) == (1024, 1024)


@pytest.mark.asyncio
async def test_empty_payload_is_a_failure(tmp_path: Path, recorder: _LogRecorder) -> None:
    provider = _ScriptedProvider(tmp_path, [ImagePayload(data=b"", mime_type="image/png")])

    result = await provider.generate(_request(), object())

    assert result.success is False
    assert "empty image" in result.error.lower()
    assert recorder.records[0].status == "error"


@pytest.mark.asyncio
async def test_logging_failure_does_not_break_generation(
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _boom(_request: LogProviderRequest) -> None:
        raise RuntimeError("log service down")

    monkeypatch.setattr(image_models, "log_provider_request", _boom)
    provider = _ScriptedProvider(tmp_path, [ImagePayload(data=_PNG_1X1, mime_type="image/png")])

    result = await provider.generate(_request(), object())

    assert result.success is True


@pytest.mark.asyncio
async def test_prompt_prefix_and_suffix_are_applied(tmp_path: Path, recorder: _LogRecorder) -> None:
    provider = _ScriptedProvider(
        tmp_path,
        [ImagePayload(data=_PNG_1X1, mime_type="image/png")],
        config={
            "generate": {
                "model": "test-model",
                "prompt_prefix": "Simple, friendly icon for children:",
                "prompt_suffix": "flat vector style",
            },
        },
    )

    await provider.generate(_request(), object())

    assert recorder.records[0].input_data == (
        "Simple, friendly icon for children: a friendly cat flat vector style"
    )


@pytest.mark.asyncio
async def test_unpriced_model_logs_null_cost(tmp_path: Path, recorder: _LogRecorder) -> None:
    provider = _ScriptedProvider(
        tmp_path,
        [ImagePayload(data=_PNG_1X1, mime_type="image/png")],
        config={"generate": {"model": "brand-new-model"}},
    )

    result = await provider.generate(_request(), object())

    assert result.cost_estimate is None
    assert recorder.records[0].cost_estimate is None


@pytest.mark.asyncio
async def test_provider_name_is_sanitised_for_the_output_path(
        tmp_path: Path,
        recorder: _LogRecorder,
) -> None:
    """Runtime-applied configs carry names like '<runtime-upload>'."""
    del recorder
    provider = _ScriptedProvider(tmp_path, [ImagePayload(data=_PNG_1X1, mime_type="image/png")])
    provider.name = "<runtime-upload>"

    result = await provider.generate(_request(), object())

    assert result.success is True
    assert result.raw_path.parent.name == "runtime-upload"
    assert result.raw_path.exists()
