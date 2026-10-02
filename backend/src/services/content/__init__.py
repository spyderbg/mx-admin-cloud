"""Content subsystem: service class, factory, and provider implementations."""
# pylint: disable=invalid-name

from pathlib import Path

import src.services.content.providers  # noqa: F401 — triggers provider auto-import

from src.services.acceptance import ServiceAcceptCriteria
from src.services.content.models import (
    CategoryConflictError,
    CategoryNotFoundError,
    ContentError,
    ContentProvider,
    ContentProviderUnavailableError,
    InvalidCategoryNameError,
)
from src.services.content.service import ContentProviderFactory, ContentService
from src.services.models import ServiceProvider

__all__ = [
    "ContentService",
    "ContentProvider",
    "ContentProviderFactory",
    "ContentError",
    "ContentProviderUnavailableError",
    "InvalidCategoryNameError",
    "CategoryNotFoundError",
    "CategoryConflictError",
]


def _validate_content_provider(service_provider: ServiceProvider) -> None:
    provider_name = service_provider.provider
    if not ContentProviderFactory.supports(provider_name):
        raise ValueError(
            f"Unsupported content provider: '{provider_name}' "
            f"(file='{service_provider.config_path}')"
        )


def _build_content_provider(provider: ServiceProvider, base_dir: Path) -> ContentProvider:
    return ContentProviderFactory.create(provider, base_dir)


SERVICE_CLASS = ContentService
SERVICE_NAME = "content"
SERVICE_ACCEPT_CRITERIA = ServiceAcceptCriteria(
    service_name=SERVICE_NAME,
    config_subdir="content",
    validate_provider=_validate_content_provider,
    build_provider=_build_content_provider,
    available_providers=ContentProviderFactory.available_names,
)
