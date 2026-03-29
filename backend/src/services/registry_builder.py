"""Factory that builds and reloads a ServiceRegistry from config files.

Separated from ``core.registry`` so that ``core/`` never imports ``services/``.

Adding a new service type requires:
1. Creating its module with a ``ServicePluginRegistry.register(...)`` call.
2. Adding one ``import src.services.<new_module>`` line below to trigger registration.
"""

from __future__ import annotations

from src.core.config import Settings
from src.core.registry import ServiceRegistry

# These imports trigger each package's __init__.py, which calls
# ServicePluginRegistry.register() for that service type.
import src.services.text_to_speech   # noqa: F401
import src.services.text_translation  # noqa: F401
import src.services.optimization      # noqa: F401

from src.services.plugin_registry import ServicePluginRegistry


async def build_registry(settings: Settings) -> ServiceRegistry:
    """Create and populate a registry from config files on disk."""
    registry = ServiceRegistry()
    await _load_into(registry, settings)
    return registry


async def reload_registry(registry: ServiceRegistry, settings: Settings) -> None:
    """Re-parse configs and replace instances in-place (thread-safe)."""
    async with registry._lock:  # pylint: disable=protected-access
        await _load_into(registry, settings)


async def _load_into(registry: ServiceRegistry, settings: Settings) -> None:
    """Iterate registered service plugins and populate the registry."""
    for plugin in ServicePluginRegistry.all_plugins():
        configs = plugin.load_configs(settings)
        instances = plugin.build_instances(configs, settings)
        registry.set_service(plugin.registry_attr, instances)
