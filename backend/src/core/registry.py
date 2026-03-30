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
        self._services: dict[str, list[Any]] = {}
        self._lock: asyncio.Lock = asyncio.Lock()

    def get(self, attr: str) -> list[Any]:
        """Return the list of instances for the given service attribute."""
        return self._services.get(attr, [])

    def set_service(self, attr: str, instances: list[Any]) -> None:
        """Generic write path used by registry_builder plugin loop."""
        self._services[attr] = instances
