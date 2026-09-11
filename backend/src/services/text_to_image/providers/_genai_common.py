"""Shared helpers for the Google GenAI image providers.

Both the SDK and the raw-HTTP provider support the same two Google image APIs,
selected by ``config.generate.api``:

* ``imagen``  — dedicated image models (``imagen-4.0-*``)
* ``gemini``  — image output from a Gemini model (``gemini-2.5-flash-image``)
"""

from __future__ import annotations

from typing import Any

from src.services.text_to_image.governor import NonRetryableError
from src.services.text_to_image.models import ImageRequest, TextToImageProvider, size_to_aspect_ratio

API_IMAGEN = "imagen"
API_GEMINI = "gemini"
_SUPPORTED_APIS = (API_IMAGEN, API_GEMINI)

DEFAULT_MIME_TYPE = "image/png"


def resolve_api(provider: TextToImageProvider) -> str:
    """Return the configured Google API family, validating the value."""
    api = str(provider.generate_config.get("api", API_IMAGEN) or API_IMAGEN).strip().lower()
    if api not in _SUPPORTED_APIS:
        raise NonRetryableError(
            f"config.generate.api must be one of {_SUPPORTED_APIS}, got '{api}'.",
        )
    return api


def resolve_aspect_ratio(provider: TextToImageProvider, request: ImageRequest) -> str:
    """Return the aspect ratio, from config or derived from the requested size."""
    configured = provider.generate_config.get("aspect_ratio")
    if configured:
        return str(configured).strip()
    return size_to_aspect_ratio(provider.resolve_size(request))


def resolve_output_mime(provider: TextToImageProvider, request: ImageRequest) -> str:
    """Return the requested output MIME type."""
    configured = provider.generate_config.get("output_mime_type")
    if configured:
        return str(configured).strip()
    output_format = (request.output_format or "png").lstrip(".").lower()
    if output_format == "jpg":
        output_format = "jpeg"
    return f"image/{output_format}" if output_format in {"png", "jpeg", "webp"} else DEFAULT_MIME_TYPE


def optional_str(value: Any) -> str | None:
    """Return a trimmed string, or ``None`` when empty."""
    text = str(value).strip() if value is not None else ""
    return text or None
