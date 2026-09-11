"""Unit tests for ``TextToImageService`` provider selection and generation."""

# pylint: disable=missing-function-docstring,too-few-public-methods,import-outside-toplevel

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from src.services.text_to_image.models import ImagePayload, ImageRequest, TextToImageProvider
from src.services.text_to_image.service import (
    ImageProviderNotFoundError,
    NoActiveImageProvidersError,
    TextToImageService,
)


class _StubImageProvider(TextToImageProvider):
    """Deterministic provider for service tests."""

    def __init__(self, base_dir: Path, name: str, enabled: bool = True) -> None:
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

    async def fetch_image(self, request: ImageRequest, client: Any) -> ImagePayload:
        del request, client
        self.calls += 1
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
