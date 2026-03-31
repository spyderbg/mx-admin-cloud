"""Service registry for discovered services and runtime instances."""

from __future__ import annotations

import importlib
import pkgutil
import threading
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

import src.services as _services_pkg
from src.services.acceptance import ServiceAcceptCriteria
from src.services.models import Service


@dataclass(frozen=True)
class ServiceDescriptor:
    """Metadata exported by a service package."""

    service_name: str
    package_name: str
    service_dir: Path
    service_class: type[Service]
    accept_criteria: ServiceAcceptCriteria


class ServiceRegistry:
    """Runtime registry built from packages that export service descriptors."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._services: dict[str, Service] = {}
        self._descriptors: dict[str, ServiceDescriptor] = {}

    @classmethod
    def build_runtime(cls, base_dir: Path) -> ServiceRegistry:
        """Instantiate discovered service classes without loading config files."""
        inst = cls()
        descriptors = cls._discover()
        services: dict[str, Service] = {}
        for descriptor in descriptors:
            services[descriptor.service_name] = descriptor.service_class(
                name=descriptor.package_name,
                service_dir=descriptor.service_dir,
                base_dir=base_dir,
            )
        inst._descriptors = {d.service_name: d for d in descriptors}
        inst._services = services
        return inst

    @classmethod
    def _discover(cls) -> list[ServiceDescriptor]:
        """Scan ``src.services`` for packages exporting service metadata."""
        specs: list[ServiceDescriptor] = []
        for _mod_info in pkgutil.iter_modules(
            _services_pkg.__path__,
            _services_pkg.__name__ + ".",
        ):
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

            service_name = str(getattr(_mod, "SERVICE_NAME", "")).strip()
            if not service_name:
                continue

            criteria = getattr(_mod, "SERVICE_ACCEPT_CRITERIA", None)
            if not isinstance(criteria, ServiceAcceptCriteria):
                continue

            specs.append(
                ServiceDescriptor(
                    service_name=service_name,
                    package_name=_mod.__name__,
                    service_dir=Path(_mod.__file__).parent,
                    service_class=_svc_cls,
                    accept_criteria=criteria,
                )
            )
        return specs

    def get_service(self, service_name: str) -> Service:
        """Return registered service instance for *service_name*."""
        with self._lock:
            service = self._services.get(service_name)
        if service is None:
            raise KeyError(f"Unknown service: {service_name!r}")
        return service

    def get_registered_services(self) -> dict[str, Service]:
        """Return a snapshot of all registered service instances."""
        with self._lock:
            return dict(self._services)

    def get_accept_criteria(self) -> dict[str, ServiceAcceptCriteria]:
        """Return acceptance criteria for all discovered services."""
        with self._lock:
            return {
                service_name: descriptor.accept_criteria
                for service_name, descriptor in self._descriptors.items()
            }

    def replace_service_providers(self, service_name: str, providers: list[Any]) -> None:
        """Replace all providers for one registered service."""
        with self._lock:
            service = self._services.get(service_name)
            if service is None:
                raise KeyError(f"Unknown service: {service_name!r}")
            service.replace_providers(providers)

    def service_providers_snapshot(self, service_name: str) -> list[Any]:
        """Return currently loaded providers for one registered service."""
        with self._lock:
            service = self._services.get(service_name)
            if service is None:
                raise KeyError(f"Unknown service: {service_name!r}")
            return service.providers_snapshot()

    def dump(self, base_dir: Path | None = None) -> None:
        """Print detailed information about registered services."""
        with self._lock:
            services = list(self._services.values())

        print(f"\n{'=' * 80}")
        print(f"ServiceRegistry - Total Registered Services: {len(services)}")
        print(f"{'=' * 80}\n")

        if not services:
            print("No services registered.")
            return

        for idx, service in enumerate(services, start=1):
            print(f"[{idx}] Service: {service.name}")
            print(f"    Type: {type(service).__name__}")

            try:
                service_dict = {
                    k: v
                    for k, v in service.__dict__.items()
                    if not k.startswith("_") and not callable(v)
                }
                if service_dict:
                    print("    Attributes:")
                    for key, value in service_dict.items():
                        formatted = self._format_registry_attr(key, value, base_dir)
                        print(f"      - {key}: {formatted}")
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


