"""Unit tests for ``TextToImageService`` provider selection and generation."""

# pylint: disable=missing-function-docstring,too-few-public-methods,import-outside-toplevel

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from src.services.provider_strategy import AllProvidersFailedError, ProviderSelectionStrategy
from src.services.text_to_image.governor import NonRetryableError
from src.services.text_to_image.models import ImagePayload, ImageRequest, TextToImageProvider
from src.services.text_to_image.service import (
    ImageProviderNotFoundError,
    NoActiveImageProvidersError,
    TextToImageService,
)


class _StubImageProvider(TextToImageProvider):
    """Deterministic provider for service tests."""

    def __init__(self, base_dir: Path, name: str, enabled: bool = True, failing: bool = False) -> None:
        super().__init__(
            name=name,
            service_name="text_to_image",
            provider_name=name,
            provider_category="",
            enabled=enabled,
            priority=1,
            config={"generate": {"model": "stub-model", "size": "64x64"}},
            config_path=base_dir / f"{name}.json",
            base_dir=base_dir,
        )
        self.calls: int = 0
        self.failing = failing

    async def fetch_image(self, request: ImageRequest, client: Any) -> ImagePayload:
        del request, client
        self.calls += 1
        if self.failing:
            # Not retryable, so the governor reports it at once as an unsuccessful result.
            raise NonRetryableError(f"{self.name} refused the prompt")
        return ImagePayload(data=b"IMAGE", mime_type="image/png")


def _make_service(tmp_path: Path, providers: list[TextToImageProvider]) -> TextToImageService:
    service = TextToImageService(
        service_name="text_to_image",
        package_name="tests",
        service_dir=tmp_path,
        base_dir=tmp_path,
    )
    service.replace_providers(providers)
    return service


def _make_request() -> ImageRequest:
    return ImageRequest(
        request_id="img-1",
        prompt="a friendly cat",
        filename_text="cat",
    )


@pytest.fixture(autouse=True)
def _isolate_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.core.config import settings

    monkeypatch.setattr(settings, "OUTPUT_DIR", str(tmp_path / "output"))


@pytest.mark.asyncio
async def test_generate_raises_when_no_active_providers(tmp_path: Path) -> None:
    service = _make_service(tmp_path, [])

    with pytest.raises(NoActiveImageProvidersError):
        await service.generate(_make_request(), object())


@pytest.mark.asyncio
async def test_generate_raises_when_provider_name_missing(tmp_path: Path) -> None:
    provider = _StubImageProvider(tmp_path, "openai")
    service = _make_service(tmp_path, [provider])

    with pytest.raises(ImageProviderNotFoundError):
        await service.generate(_make_request(), object(), provider_name="comfyui")


@pytest.mark.asyncio
async def test_generate_uses_requested_provider_name(tmp_path: Path) -> None:
    first = _StubImageProvider(tmp_path, "openai")
    second = _StubImageProvider(tmp_path, "comfyui")
    service = _make_service(tmp_path, [first, second])

    result = await service.generate(_make_request(), object(), provider_name="comfyui")

    assert result.provider == "comfyui"
    assert first.calls == 0
    assert second.calls == 1


@pytest.mark.asyncio
async def test_generate_uses_first_active_provider_by_default(tmp_path: Path) -> None:
    first = _StubImageProvider(tmp_path, "openai")
    second = _StubImageProvider(tmp_path, "genai")
    service = _make_service(tmp_path, [first, second])

    result = await service.generate(_make_request(), object())

    assert result.provider == "openai"
    assert first.calls == 1
    assert second.calls == 0


@pytest.mark.asyncio
async def test_disabled_providers_are_skipped(tmp_path: Path) -> None:
    disabled = _StubImageProvider(tmp_path, "openai", enabled=False)
    active = _StubImageProvider(tmp_path, "genai")
    service = _make_service(tmp_path, [disabled, active])

    result = await service.generate(_make_request(), object())

    assert result.provider == "genai"
    assert disabled.calls == 0


def test_text_to_image_supports_only_first_success(tmp_path: Path) -> None:
    service = _make_service(tmp_path, [])

    assert service.provider_strategy is ProviderSelectionStrategy.FIRST_SUCCESS
    with pytest.raises(ValueError, match="does not support"):
        service.set_provider_strategy(ProviderSelectionStrategy.ALL_PROVIDERS)


@pytest.mark.asyncio
async def test_generate_falls_back_to_the_next_provider_when_one_fails(tmp_path: Path) -> None:
    broken = _StubImageProvider(tmp_path, "openai", failing=True)
    working = _StubImageProvider(tmp_path, "genai")
    unused = _StubImageProvider(tmp_path, "comfyui")
    service = _make_service(tmp_path, [broken, working, unused])

    result = await service.generate(_make_request(), object())

    assert result.success
    assert result.provider == "genai"
    assert (broken.calls, working.calls, unused.calls) == (1, 1, 0)


@pytest.mark.asyncio
async def test_generate_reports_every_provider_error_when_all_fail(tmp_path: Path) -> None:
    service = _make_service(tmp_path, [
        _StubImageProvider(tmp_path, "openai", failing=True),
        _StubImageProvider(tmp_path, "genai", failing=True),
    ])

    with pytest.raises(AllProvidersFailedError) as excinfo:
        await service.generate(_make_request(), object())

    assert [error.split(":", 1)[0] for error in excinfo.value.errors] == ["openai", "genai"]
    assert "refused the prompt" in str(excinfo.value)


@pytest.mark.asyncio
async def test_generate_with_provider_name_returns_its_failure_without_fallback(tmp_path: Path) -> None:
    named = _StubImageProvider(tmp_path, "openai", failing=True)
    other = _StubImageProvider(tmp_path, "genai")
    service = _make_service(tmp_path, [named, other])

    result = await service.generate(_make_request(), object(), provider_name="openai")

    assert not result.success
    assert other.calls == 0
