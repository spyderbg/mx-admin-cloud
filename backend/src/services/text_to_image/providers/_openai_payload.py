"""Shared payload construction for the OpenAI Images API.

Both the SDK-backed and the raw-HTTP OpenAI providers must send identical
parameters, so the request shape lives here rather than being duplicated.
"""

from __future__ import annotations

from typing import Any

from src.services.text_to_image.models import ImageRequest, TextToImageProvider

ENDPOINT = "https://api.openai.com/v1/images/generations"


def build_openai_kwargs(provider: TextToImageProvider, request: ImageRequest) -> dict[str, Any]:
    """Build the Images API parameters for *request*.

    ``gpt-image-*`` models always return base64 and accept ``output_format``;
    the older ``dall-e-*`` models need an explicit ``response_format`` instead.
    """
    model = provider.resolve_model(request) or "gpt-image-1-mini"
    size = provider.resolve_size(request)
    quality = provider.resolve_quality(request)

    kwargs: dict[str, Any] = {
        "model": model,
        "prompt": provider.build_prompt(request),
        "n": 1,
    }
    if size:
        kwargs["size"] = size
    if quality:
        kwargs["quality"] = quality

    if model.startswith("gpt-image"):
        output_format = (request.output_format or "png").lstrip(".").lower()
        if output_format in {"png", "jpeg", "webp"}:
            kwargs["output_format"] = output_format
    else:
        kwargs["response_format"] = "b64_json"

    return kwargs


def mime_for_kwargs(kwargs: dict[str, Any]) -> str:
    """Return the MIME type implied by the request parameters."""
    output_format = str(kwargs.get("output_format", "png")).lower()
    if output_format == "jpg":
        output_format = "jpeg"
    return f"image/{output_format}"
