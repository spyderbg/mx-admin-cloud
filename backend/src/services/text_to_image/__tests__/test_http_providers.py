"""Tests for the HTTP-based image providers (OpenAI and Google GenAI).

The providers receive their HTTP client by injection, so these tests pass a
recording client that returns real ``httpx.Response`` objects — no patching of
``httpx`` or of any SDK.
"""

# pylint: disable=missing-function-docstring,too-few-public-methods,redefined-outer-name
# pylint: disable=import-outside-toplevel

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from src.services.text_to_image.governor import NonRetryableError
from src.services.text_to_image.models import ImageRequest
from src.services.text_to_image.providers.genai_http_image import GenAIHttpImageProvider
from src.services.text_to_image.providers.openai_http_image import OpenAIHttpImageProvider

_IMAGE_BYTES = b"\x89PNG\r\n\x1a\nfake"
_ENCODED = base64.b64encode(_IMAGE_BYTES).decode("ascii")


class _RecordingClient:
    """Async HTTP client stand-in that replays canned responses."""

    def __init__(self, responses: list[httpx.Response]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        self.calls.append({"method": "POST", "url": url, **kwargs})
        return self._next("POST", url)

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        self.calls.append({"method": "GET", "url": url, **kwargs})
        return self._next("GET", url)

    def _next(self, method: str, url: str) -> httpx.Response:
        response = self._responses.pop(0)
        response.request = httpx.Request(method, url)
        return response


def _json_response(payload: dict[str, Any], status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload)


def _request(**overrides: Any) -> ImageRequest:
    payload: dict[str, Any] = {
        "request_id": "img-1",
        "prompt": "a friendly cat",
        "filename_text": "cat",
    }
    payload.update(overrides)
    return ImageRequest(**payload)


def _make_openai(tmp_path: Path, generate: dict[str, Any]) -> OpenAIHttpImageProvider:
    return OpenAIHttpImageProvider(
        name="openai_http",
        service_name="text_to_image",
        provider_name="openai_http",
        provider_category="",
        enabled=True,
        priority=1,
        config={"generate": generate},
        config_path=tmp_path / "openai_http.json",
        base_dir=tmp_path,
    )


def _make_genai(tmp_path: Path, generate: dict[str, Any]) -> GenAIHttpImageProvider:
    return GenAIHttpImageProvider(
        name="genai_http",
        service_name="text_to_image",
        provider_name="genai_http",
        provider_category="",
        enabled=True,
        priority=1,
        config={"generate": generate},
        config_path=tmp_path / "genai_http.json",
        base_dir=tmp_path,
    )


# ---------------------------------------------------------------------------
# OpenAI over HTTP
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_openai_http_builds_gpt_image_payload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    provider = _make_openai(tmp_path, {"model": "gpt-image-1-mini", "size": "1024x1024", "quality": "low"})
    client = _RecordingClient([_json_response({"data": [{"b64_json": _ENCODED}]})])

    payload = await provider.fetch_image(_request(), client)

    assert payload.data == _IMAGE_BYTES
    assert payload.mime_type == "image/png"
    call = client.calls[0]
    assert call["url"] == "https://api.openai.com/v1/images/generations"
    assert call["headers"]["Authorization"] == "Bearer sk-test"
    assert call["json"] == {
        "model": "gpt-image-1-mini",
        "prompt": "a friendly cat",
        "n": 1,
        "size": "1024x1024",
        "quality": "low",
        "output_format": "png",
    }


@pytest.mark.asyncio
async def test_openai_http_dalle_uses_response_format(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    provider = _make_openai(tmp_path, {"model": "dall-e-3", "size": "1024x1024"})
    client = _RecordingClient([_json_response({"data": [{"b64_json": _ENCODED}]})])

    await provider.fetch_image(_request(), client)

    body = client.calls[0]["json"]
    assert body["response_format"] == "b64_json"
    assert "output_format" not in body


@pytest.mark.asyncio
async def test_openai_http_missing_key_is_non_retryable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    provider = _make_openai(tmp_path, {"model": "gpt-image-1-mini"})

    with pytest.raises(NonRetryableError, match="OPENAI_API_KEY"):
        await provider.fetch_image(_request(), _RecordingClient([]))


@pytest.mark.asyncio
async def test_openai_http_error_status_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    provider = _make_openai(tmp_path, {"model": "gpt-image-1-mini"})
    client = _RecordingClient([_json_response({"error": {"message": "rate limited"}}, status=429)])

    with pytest.raises(httpx.HTTPStatusError):
        await provider.fetch_image(_request(), client)


@pytest.mark.asyncio
async def test_openai_http_empty_data_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    provider = _make_openai(tmp_path, {"model": "gpt-image-1-mini"})
    client = _RecordingClient([_json_response({"data": []})])

    with pytest.raises(NonRetryableError, match="no image data"):
        await provider.fetch_image(_request(), client)


@pytest.mark.asyncio
async def test_openai_http_prompt_prefix_reaches_payload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    provider = _make_openai(
        tmp_path,
        {"model": "gpt-image-1-mini", "prompt_prefix": "Simple, friendly icon for children:"},
    )
    client = _RecordingClient([_json_response({"data": [{"b64_json": _ENCODED}]})])

    await provider.fetch_image(_request(), client)

    assert client.calls[0]["json"]["prompt"] == "Simple, friendly icon for children: a friendly cat"


# ---------------------------------------------------------------------------
# Google GenAI over HTTP
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_genai_http_imagen_payload_and_parsing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "gk-test")
    provider = _make_genai(
        tmp_path,
        {"api": "imagen", "model": "imagen-4.0-generate-001", "size": "1024x1024", "image_size": "1K"},
    )
    client = _RecordingClient([
        _json_response({"predictions": [{"bytesBase64Encoded": _ENCODED, "mimeType": "image/png"}]}),
    ])

    payload = await provider.fetch_image(_request(), client)

    assert payload.data == _IMAGE_BYTES
    call = client.calls[0]
    assert call["url"].endswith("/imagen-4.0-generate-001:predict")
    assert call["headers"]["x-goog-api-key"] == "gk-test"
    assert call["json"]["instances"] == [{"prompt": "a friendly cat"}]
    params = call["json"]["parameters"]
    assert params["sampleCount"] == 1
    assert params["aspectRatio"] == "1:1"
    assert params["imageSize"] == "1K"
    assert params["outputOptions"] == {"mimeType": "image/png"}


@pytest.mark.asyncio
async def test_genai_http_imagen_seed_disables_watermark(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "gk-test")
    provider = _make_genai(tmp_path, {"api": "imagen", "model": "imagen-4.0-generate-001"})
    client = _RecordingClient([_json_response({"predictions": [{"bytesBase64Encoded": _ENCODED}]})])

    await provider.fetch_image(_request(seed=7), client)

    params = client.calls[0]["json"]["parameters"]
    assert params["seed"] == 7
    assert params["addWatermark"] is False


@pytest.mark.asyncio
async def test_genai_http_gemini_payload_and_parsing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "gk-test")
    provider = _make_genai(tmp_path, {"api": "gemini", "model": "gemini-2.5-flash-image", "size": "1024x768"})
    client = _RecordingClient([
        _json_response({
            "candidates": [{
                "content": {
                    "parts": [
                        {"text": "here is your image"},
                        {"inlineData": {"data": _ENCODED, "mimeType": "image/jpeg"}},
                    ],
                },
            }],
        }),
    ])

    payload = await provider.fetch_image(_request(), client)

    assert payload.data == _IMAGE_BYTES
    assert payload.mime_type == "image/jpeg"
    call = client.calls[0]
    assert call["url"].endswith("/gemini-2.5-flash-image:generateContent")
    body = call["json"]
    assert body["contents"] == [{"parts": [{"text": "a friendly cat"}]}]
    assert body["generationConfig"]["responseModalities"] == ["IMAGE"]
    assert body["generationConfig"]["imageConfig"]["aspectRatio"] == "4:3"


@pytest.mark.asyncio
async def test_genai_http_unknown_api_is_non_retryable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "gk-test")
    provider = _make_genai(tmp_path, {"api": "stable-diffusion"})

    with pytest.raises(NonRetryableError, match="config.generate.api"):
        await provider.fetch_image(_request(), _RecordingClient([]))


@pytest.mark.asyncio
async def test_genai_http_missing_key_is_non_retryable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    provider = _make_genai(tmp_path, {"api": "imagen"})

    with pytest.raises(NonRetryableError, match="GEMINI_API_KEY"):
        await provider.fetch_image(_request(), _RecordingClient([]))


@pytest.mark.asyncio
async def test_genai_http_filtered_prompt_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "gk-test")
    provider = _make_genai(tmp_path, {"api": "imagen"})
    client = _RecordingClient([_json_response({"predictions": []})])

    with pytest.raises(NonRetryableError, match="no images"):
        await provider.fetch_image(_request(), client)


@pytest.mark.asyncio
async def test_genai_http_gemini_without_image_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "gk-test")
    provider = _make_genai(tmp_path, {"api": "gemini"})
    client = _RecordingClient([
        _json_response({"candidates": [{"content": {"parts": [{"text": "I cannot draw that"}]}}]}),
    ])

    with pytest.raises(NonRetryableError, match="no inline image data"):
        await provider.fetch_image(_request(), client)


def test_shipped_configs_match_registered_providers() -> None:
    """Every shipped config names a provider the factory knows."""
    from src.services.text_to_image.service import TextToImageProviderFactory

    config_dir = Path(__file__).resolve().parents[4] / "config" / "text_to_image"
    names = set()
    for path in sorted(config_dir.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        provider = payload["service_provider"]["provider"]
        assert TextToImageProviderFactory.supports(provider), f"{path.name}: unknown provider {provider}"
        names.add(provider)
    assert names == {"openai", "openai_http", "genai", "genai_http", "comfyui"}
