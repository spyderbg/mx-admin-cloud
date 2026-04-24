"""Shared factory base for service-provider registries."""

from __future__ import annotations

from pathlib import Path
from typing import Generic, TypeVar

from src.services.models import ServiceProvider

ProviderT = TypeVar("ProviderT", bound=ServiceProvider)


class ServiceProviderFactory(Generic[ProviderT]):
    """Map provider names to concrete provider classes for one service domain."""

    unsupported_label = "provider"
    _builders: dict[str, type[ProviderT]]

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        cls._builders = {}

    @classmethod
    def register(cls, provider_name: str, builder: type[ProviderT]) -> None:
        """Register a concrete provider class under *provider_name*."""
        cls._builders[provider_name.strip().lower()] = builder

    @classmethod
    def supports(cls, provider_name: str) -> bool:
        """Return whether a provider builder is registered for *provider_name*."""
        return provider_name.strip().lower() in cls._builders

    @classmethod
    def create(cls, service_provider: ServiceProvider, base_dir: Path) -> ProviderT:
        """Instantiate the concrete provider for *provider*."""
        key = service_provider.provider.strip().lower()
        builder = cls._builders.get(key)
        if builder is None:
            raise ValueError(
                f"Unsupported {cls.unsupported_label}: '{key}' "
                f"(file='{service_provider.config_path}')"
            )
        return builder(
            name=service_provider.name,
            service_name=service_provider.service,
            provider_name=service_provider.provider,
            provider_category=service_provider.provider_category,
            enabled=service_provider.enabled,
            priority=service_provider.priority,
            config=service_provider.config,
            config_path=service_provider.config_path,
            base_dir=base_dir,
        )
