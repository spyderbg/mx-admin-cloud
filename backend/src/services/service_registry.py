""" Service registry for managing service packages and runtime instances. """

from __future__ import annotations

import importlib
import pkgutil
import threading
from pathlib import Path
from types import ModuleType
from typing import Any, ClassVar

import src.services as _services_pkg
from src.services.models import Service


class ServiceRegistry:
    """Runtime registry built from packages that export ``SERVICE_CLASS``."""

    #: Loaded orchestrator instances (set in :meth:`build_runtime`).
    _services: ClassVar[list[Service]] = []
    _services_lock: ClassVar[threading.Lock] = threading.Lock()

    @classmethod
    def build_runtime(cls, base_dir: Path, config_path: Path) -> ServiceRegistry:
        """Instantiate and load each discovered ``SERVICE_CLASS``."""
        inst = cls()
        with cls._services_lock:
            # Discover and instantiate if not already done or if we want to refresh
            discovered = cls._discover()
            built: list[Service] = []
            for svc_cls, name, service_dir in discovered:
                svc = svc_cls(name=name, service_dir=service_dir, base_dir=base_dir)
                svc.load_runtime_config(config_path)
                built.append(svc)
            cls._services = built
        return inst

    @classmethod
    def _discover(cls) -> list[tuple[type[Service], str, Path]]:
        """Scan src.services for packages that export ``SERVICE_CLASS``."""
        specs: list[tuple[type[Service], str, Path]] = []
        for _mod_info in pkgutil.iter_modules(_services_pkg.__path__, _services_pkg.__name__ + "."):
            if not _mod_info.ispkg:
                continue
            try:
                _mod: ModuleType = importlib.import_module(_mod_info.name)
            except ImportError:
                continue

            _svc_cls = getattr(_mod, "SERVICE_CLASS", None)
            if _svc_cls is None:
                continue
            if not isinstance(_svc_cls, type) or not issubclass(_svc_cls, Service):
                continue
            if _svc_cls is Service:
                continue

            specs.append((_svc_cls, _mod.__name__, Path(_mod.__file__).parent))
        return specs

    def get(self, key: str) -> list[Any]:
        """Return the list published by the first orchestrator that handles *key* (via :meth:`Service.registry_get`)."""
        with ServiceRegistry._services_lock:
            instances = list(ServiceRegistry._services)

        for svc in instances:
            chunk = svc.registry_get(key)
            if chunk is not None:
                return list(chunk)

        raise KeyError(f"Unknown registry key: {key!r}")

    def dump(self, base_dir: Path | None = None) -> None:
        """Print detailed information about loaded orchestrator instances."""
        with ServiceRegistry._services_lock:
            services = list(ServiceRegistry._services)

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
                    k: v for k, v in service.__dict__.items()
                    if not k.startswith('_') and not callable(v)
                }
                if service_dict:
                    print("    Attributes:")
                    for k, v in service_dict.items():
                        print(f"      - {k}: {self._format_registry_attr(k, v, base_dir)}")
            except Exception as exc:  # pylint: disable=broad-exception-caught
                print(f"    [Could not serialize attributes: {exc}]")

            print()

        print(f"{'=' * 80}\n")

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


