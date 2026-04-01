"""Service registry for discovered services and runtime instances."""

from __future__ import annotations

import importlib
import json
import pkgutil
import threading
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

import src.services as _services_pkg
from src.services.acceptance import ConfigApplyResult, ServiceAcceptCriteria
from src.services.models import Service, ServiceProvider


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


class ServiceRegistryBuilder:
    """Load and apply service providers without embedding config logic in services."""

    def __init__(self, registry: ServiceRegistry, base_dir: Path, config_root: Path) -> None:
        self._registry = registry
        self._base_dir = base_dir
        self._config_root = config_root

    def load_all_from_disk(self) -> list[ConfigApplyResult]:
        """Load all known config directories and replace each service provider set."""
        criteria_map = self._registry.get_accept_criteria()
        built: dict[str, list[Any]] = {service_name: [] for service_name in criteria_map}
        results: list[ConfigApplyResult] = []

        for service_name, criteria in criteria_map.items():
            config_dir = self._config_root / criteria.config_subdir
            if not config_dir.exists():
                self._registry.replace_service_providers(service_name, [])
                continue
            for config_path in sorted(config_dir.glob("*.json")):
                payload = self._read_json_file(config_path)
                if payload is None:
                    results.append(
                        ConfigApplyResult(
                            accepted=False,
                            source=str(config_path),
                            service_name=service_name,
                            error="Invalid JSON payload.",
                        )
                    )
                    continue
                apply_result, materialized = self._materialize_payload(
                    payload=payload,
                    source=config_path,
                    criteria=criteria,
                    expected_service_name=service_name,
                )
                results.append(apply_result)
                if materialized is not None:
                    built[service_name].append(materialized)

        for service_name, providers in built.items():
            providers.sort(key=lambda provider: getattr(provider, "priority", 999))
            self._registry.replace_service_providers(service_name, providers)

        return results

    def apply_runtime_payload(self, payload: dict[str, Any], source: str = "<runtime-upload>") -> ConfigApplyResult:
        """Validate and apply one runtime-submitted config payload in memory."""
        common_provider = ServiceProvider.from_dict(payload, Path(source))
        if common_provider is None:
            return ConfigApplyResult(
                accepted=False,
                source=source,
                error="Invalid service_provider envelope.",
            )

        candidates = [
            (service_name, criteria)
            for service_name, criteria in self._registry.get_accept_criteria().items()
            if criteria.can_accept(common_provider)
        ]
        if not candidates:
            return ConfigApplyResult(
                accepted=False,
                source=source,
                provider_name=common_provider.name,
                error=f"Unsupported service '{common_provider.service_type}'.",
            )
        if len(candidates) > 1:
            return ConfigApplyResult(
                accepted=False,
                source=source,
                provider_name=common_provider.name,
                error=f"Ambiguous service match for '{common_provider.service_type}'.",
            )

        service_name, criteria = candidates[0]
        apply_result, materialized = self._materialize_payload(
            payload=payload,
            source=Path(source),
            criteria=criteria,
            expected_service_name=service_name,
        )
        if materialized is None:
            return apply_result

        current = self._registry.service_providers_snapshot(service_name)
        current = self._replace_provider_by_identity(current, materialized)
        current.sort(key=lambda provider: getattr(provider, "priority", 999))
        self._registry.replace_service_providers(service_name, current)
        return apply_result

    def _materialize_payload(
        self,
        payload: dict[str, Any],
        source: Path,
        criteria: ServiceAcceptCriteria,
        expected_service_name: str,
    ) -> tuple[ConfigApplyResult, Any | None]:
        provider = ServiceProvider.from_dict(payload, source)
        if provider is None:
            return (
                ConfigApplyResult(
                    accepted=False,
                    source=str(source),
                    service_name=expected_service_name,
                    error="Invalid service_provider envelope.",
                ),
                None,
            )
        if not criteria.can_accept(provider):
            return (
                ConfigApplyResult(
                    accepted=False,
                    source=str(source),
                    service_name=expected_service_name,
                    provider_name=provider.name,
                    error=(
                        f"Service mismatch: expected '{criteria.service_name}', "
                        f"got '{provider.service_type}'."
                    ),
                ),
                None,
            )
        try:
            criteria.validate_provider(provider)
            materialized = criteria.build_provider(provider, self._base_dir)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            return (
                ConfigApplyResult(
                    accepted=False,
                    source=str(source),
                    service_name=expected_service_name,
                    provider_name=provider.name,
                    error=str(exc),
                ),
                None,
            )
        return (
            ConfigApplyResult(
                accepted=True,
                source=str(source),
                service_name=expected_service_name,
                provider_name=provider.name,
            ),
            materialized,
        )

    @staticmethod
    def _read_json_file(config_path: Path) -> dict[str, Any] | None:
        try:
            with config_path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        return payload

    @staticmethod
    def _replace_provider_by_identity(providers: list[Any], new_provider: Any) -> list[Any]:
        provider_name = str(getattr(new_provider, "name", "")).strip().lower()
        service_type = str(getattr(new_provider, "service_type", "")).strip().lower()
        updated = [
            provider
            for provider in providers
            if not (
                str(getattr(provider, "name", "")).strip().lower() == provider_name
                and str(getattr(provider, "service_type", "")).strip().lower() == service_type
            )
        ]
        updated.append(new_provider)
        return updated
