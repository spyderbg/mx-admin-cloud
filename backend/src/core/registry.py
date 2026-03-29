"""Service registry: owns all TTS provider, translator, and optimizer instances.

Pure data container — no service imports. Built by ``services.registry_builder``
during FastAPI lifespan and injected via ``Depends(get_registry)``.
"""

from __future__ import annotations

import asyncio
from typing import Any


class ServiceRegistry:
    """Owns pre-built service instances; eliminates per-request config parsing."""

    def __init__(self) -> None:
        self.providers: list[tuple[Any, Any]] = []
        self.translators: list[Any] = []
        self.optimizers: list[Any] = []
        self._lock: asyncio.Lock = asyncio.Lock()

    def provider_configs(self) -> list[Any]:
        """Return only the config objects (without provider instances)."""
        return [cfg for _, cfg in self.providers]

    def provider_names_and_configs(self) -> list[tuple[str, Any]]:
        """Return (provider_name, config) for each active provider."""
        return [(provider.name, cfg) for provider, cfg in self.providers]

    def optimizer_names(self) -> list[str]:
        """Return names of all active optimizers."""
        return [opt.name for opt in self.optimizers]

    def set_service(self, attr: str, instances: list[Any]) -> None:
        """Generic write path used by registry_builder plugin loop."""
        if not hasattr(self, attr):
            raise AttributeError(
                f"ServiceRegistry has no attribute '{attr}'. "
                f"Add it to __init__ before registering a plugin for it."
            )
        setattr(self, attr, instances)
