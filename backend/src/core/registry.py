"""Service registry: owns all TTS provider, translator, and optimizer instances.

Pure data container — no service imports. Built by ``services.registry_builder``
during FastAPI lifespan and injected via ``Depends(get_registry)``.
"""

from __future__ import annotations

import asyncio

from src.core.models import ProviderConfig


class ServiceRegistry:
    """Owns pre-built service instances; eliminates per-request config parsing."""

    def __init__(self) -> None:
        self.providers: list[tuple] = []
        self.translators: list = []
        self.optimizers: list = []
        self._lock: asyncio.Lock = asyncio.Lock()

    def provider_configs(self) -> list[ProviderConfig]:
        """Return only the ProviderConfig objects (without provider instances)."""
        return [cfg for _, cfg in self.providers]

    def provider_names_and_configs(self) -> list[tuple[str, ProviderConfig]]:
        """Return (provider_name, ProviderConfig) for each active provider."""
        return [(provider.name, cfg) for provider, cfg in self.providers]

    def optimizer_names(self) -> list[str]:
        """Return names of all active optimizers."""
        return [opt.name for opt in self.optimizers]
