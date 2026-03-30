""" Service registry for managing service packages and runtime instances. """

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, ClassVar

from src.services.models import Service


class ServiceRegistry:
    """Registry for service package descriptors."""

    _registered_services: ClassVar[list[Service]] = []
    _lock: ClassVar[threading.Lock] = threading.Lock()

    @classmethod
    def register(cls, service: Service) -> None:
        """Register a service package descriptor (thread-safe)."""
        with cls._lock:
            cls._registered_services.append(service)

    @classmethod
    def find(cls, name: str) -> Service | None:
        """Retrieve a service package descriptor by name (thread-safe)."""
        with cls._lock:
            for service in cls._registered_services:
                if service.name == name:
                    return service
        return None

    @classmethod
    def all_services(cls) -> list[Service]:
        """Return a copy of all registered service package descriptors (thread-safe)."""
        with cls._lock:
            return cls._registered_services.copy()

    @classmethod
    def dump(cls, base_dir: Path | None = None) -> None:
        """Print detailed information about all registered service packages.

        When *base_dir* is set, *service_dir* is printed relative to it; otherwise the
        absolute path is shown.
        """
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
                        print(f"      - {key}: {cls._format_registry_attr(key, value, base_dir)}")
            except Exception as exc:  # pylint: disable=broad-exception-caught
                print(f"    [Could not serialize attributes: {exc}]")

            print()

        print(f"{'=' * 80}\n")

    def get(self, attr: str) -> list[Any]:
        """Return the list of live instances for *attr* (e.g. ``"providers"``)."""
        raise NotImplementedError("ServiceRegistry.get() is not implemented yet; use get_registry() instead.")

    @staticmethod
    def _format_registry_attr(key: str, value: Any, base_dir: Path | None) -> str:
        """String form for dump output; *service_dir* is relative to *base_dir* when possible."""
        if key == "service_dir" and base_dir is not None and isinstance(value, Path):
            try:
                display = str(value.relative_to(base_dir))
            except ValueError:
                display = str(value)
        else:
            display = str(value)
        if len(display) > 100:
            display = display[:97] + "..."
        return display


import importlib  # noqa: E402  pylint: disable=wrong-import-position
import pkgutil  # noqa: E402  pylint: disable=wrong-import-position
import src.services as _services_pkg  # noqa: E402  pylint: disable=wrong-import-position

for _mod_info in pkgutil.iter_modules(_services_pkg.__path__, _services_pkg.__name__ + "."):
    if not _mod_info.ispkg:
        continue
    _mod = importlib.import_module(_mod_info.name)
    ServiceRegistry.register(Service(name=_mod.__name__, service_dir=Path(_mod.__file__).parent))
