"""
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, ClassVar

from src.services.models import Service


class ServiceRegistry:
    """Registry for service packages and pre-built runtime instances."""

    _registered_services: ClassVar[list[Service]] = []

    def __init__(self) -> None:
        self._instances: dict[str, list[Any]] = {}
        self._lock: asyncio.Lock = asyncio.Lock()

    # ── class-level: service-package bookkeeping ──────────────────────

    @classmethod
    def register(cls, service: Service) -> None:
        """Register a service package descriptor."""
        cls._registered_services.append(service)

    @classmethod
    def find(cls, name: str) -> Service | None:
        """Retrieve a service package descriptor by name."""
        for service in cls._registered_services:
            if service.name == name:
                return service
        return None

    @classmethod
    def all_services(cls) -> list[Service]:
        """Return a copy of all registered service package descriptors."""
        return cls._registered_services.copy()

    @classmethod
    def dump(cls) -> None:
        """Print detailed information about all registered service packages."""
        services = cls._registered_services
        print(f"\n{'=' * 80}")
        print(f"ServiceRegistry - Total Services: {len(services)}")
        print(f"{'=' * 80}\n")

        if not services:
            print("No services registered.")
            return

        for idx, service in enumerate(services, start=1):
            print(f"[{idx}] Service: {service.name}")
            print(f"    Type: {type(service).__name__}")

            try:
                service_dict = {
                    key: value for key, value in service.__dict__.items()
                    if not key.startswith('_') and not callable(value)
                }
                if service_dict:
                    print("    Attributes:")
                    for key, value in service_dict.items():
                        value_str = str(value)
                        if len(value_str) > 100:
                            value_str = value_str[:97] + "..."
                        print(f"      - {key}: {value_str}")
            except Exception as exc:  # pylint: disable=broad-exception-caught
                print(f"    [Could not serialize attributes: {exc}]")

            print()

        print(f"{'=' * 80}\n")

    # ── instance-level: runtime service instances (lock-protected) ────

    def get(self, attr: str) -> list[Any]:
        """Return the list of live instances for *attr* (e.g. ``"providers"``)."""
        return self._instances.get(attr, [])

    def set_service(self, attr: str, instances: list[Any]) -> None:
        """Store *instances* under *attr* — called by ``registry_builder``."""
        self._instances[attr] = instances


import importlib  # noqa: E402  pylint: disable=wrong-import-position
import pkgutil    # noqa: E402  pylint: disable=wrong-import-position
import src.services as _services_pkg  # noqa: E402  pylint: disable=wrong-import-position

for _mod_info in pkgutil.iter_modules(_services_pkg.__path__, _services_pkg.__name__ + "."):
    if not _mod_info.ispkg:
        continue
    _mod = importlib.import_module(_mod_info.name)
    ServiceRegistry.register(Service(name=_mod.__name__, service_dir=Path(_mod.__file__).parent))
