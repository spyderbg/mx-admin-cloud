"""Service plugin registry: allows service modules to self-register loaders and builders.

Each service package appends a ``ServicePlugin`` to ``ServicePluginRegistry`` at import
time.  ``registry_builder._load_into`` iterates the list instead of hard-coding per-service
calls, so adding a new service type only requires touching the new module and
``registry_builder`` (one additive import line).
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class ServicePlugin:
    """Descriptor for one service type's config-load + instance-build pipeline.

    Each plugin declares:

    *   **registry_attr** – key in ``ServiceRegistry`` (e.g. ``"providers"``).
    *   **config_dir** – subdirectory name under ``settings.config_path``
        (e.g. ``"text_to_speech"``).
    *   **config_class** – dataclass with a ``from_dict(data, config_path)``
        classmethod that returns an instance or ``None``.
    *   **factory_create** – ``Factory.create(cfg, base_dir)`` callable that
        returns a ready-to-use service instance.
    *   **base_dir_key** – ``Settings`` attribute passed as ``base_dir``
        to the factory (default ``"output_path"``).
    *   **sort_key** – optional attribute name on the config class used to
        sort configs before building (e.g. ``"priority"``).
    *   **service_key** – unique id; auto-derived from ``module_name`` or
        ``config_dir`` when ``None``.
    """

    registry_attr: str
    config_dir: str
    config_class: type
    factory_create: Callable[..., Any]
    base_dir_key: str = "output_path"
    sort_key: str | None = None
    service_key: str | None = None


class ServicePluginRegistry:
    """Ordered collection of ServicePlugin registrations."""

    _plugins: list[ServicePlugin] = []

    @classmethod
    def register(cls, plugin: ServicePlugin, module_name: str | None = None) -> None:
        """Register a plugin. Idempotent: duplicate service_keys are ignored.

        *module_name* (typically ``__name__``) is used to auto-derive
        ``service_key`` when the plugin leaves it as ``None``.
        """
        if plugin.service_key is None:
            derived = module_name.rsplit(".", 1)[-1] if module_name else plugin.config_dir
            plugin = dataclasses.replace(plugin, service_key=derived)
        if plugin.service_key in {p.service_key for p in cls._plugins}:
            return
        cls._plugins.append(plugin)

    @classmethod
    def all_plugins(cls) -> list[ServicePlugin]:
        """Return all registered plugins in registration order."""
        return list(cls._plugins)
