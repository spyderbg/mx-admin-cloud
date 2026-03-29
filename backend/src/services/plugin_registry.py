"""Service plugin registry: allows service modules to self-register loaders and builders.

Each service package appends a ``ServicePlugin`` to ``ServicePluginRegistry`` at import
time.  ``registry_builder._load_into`` iterates the list instead of hard-coding per-service
calls, so adding a new service type only requires touching the new module and
``registry_builder`` (one additive import line).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from src.core.config import Settings


@dataclass(frozen=True)
class ServicePlugin:
    """Descriptor for one service type's config-load + instance-build pipeline."""

    # Matches the "service" field in config JSON (e.g. "text_to_speech")
    service_key: str

    # Attribute name on ServiceRegistry to populate (e.g. "providers")
    registry_attr: str

    # (settings) -> list of config objects
    load_configs: Callable[["Settings"], list[Any]]

    # (configs, settings) -> list of service instances
    build_instances: Callable[[list[Any], "Settings"], list[Any]]


class ServicePluginRegistry:
    """Ordered collection of ServicePlugin registrations."""

    _plugins: list[ServicePlugin] = []

    @classmethod
    def register(cls, plugin: ServicePlugin) -> None:
        """Register a plugin. Idempotent: duplicate service_keys are ignored."""
        if plugin.service_key not in {p.service_key for p in cls._plugins}:
            cls._plugins.append(plugin)

    @classmethod
    def all_plugins(cls) -> list[ServicePlugin]:
        """Return all registered plugins in registration order."""
        return list(cls._plugins)
