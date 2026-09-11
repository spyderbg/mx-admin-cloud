"""Image generation subsystem: base class, factory, and service orchestrator."""

from __future__ import annotations

from pathlib import Path

from src.services.acceptance import ServiceAcceptCriteria
from src.services.models import ServiceProvider
from src.services.text_to_image.models import TextToImageProvider
from src.services.text_to_image.service import (
    TextToImageProviderFactory,
    TextToImageService
)

# Import providers AFTER TextToImageProviderFactory is imported so provider
# modules can register safely without triggering circular package initialization.
import src.services.text_to_image.providers  # noqa: F401, E402 # pylint: disable=wrong-import-position

__all__ = [
    "TextToImageProvider",
    "TextToImageProviderFactory",
    "TextToImageService",
]


def _validate_image_provider(service_provider: ServiceProvider) -> None:
    provider_name = service_provider.provider
    if not TextToImageProviderFactory.supports(provider_name):
        raise ValueError(
            f"Unsupported image provider: '{provider_name}' (file='{service_provider.config_path}')"
        )


def _build_image_provider(provider: ServiceProvider, base_dir: Path) -> TextToImageProvider:
    return TextToImageProviderFactory.create(provider, base_dir)


SERVICE_CLASS = TextToImageService  # pylint: disable=invalid-name
SERVICE_NAME = "text_to_image"
SERVICE_ACCEPT_CRITERIA = ServiceAcceptCriteria(
    service_name=SERVICE_NAME,
    config_subdir="text_to_image",
    validate_provider=_validate_image_provider,
    build_provider=_build_image_provider,
)
