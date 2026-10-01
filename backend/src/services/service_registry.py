"""Service registry for discovered services and runtime instances."""

from __future__ import annotations

import importlib
import pkgutil
import threading
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, TypeVar

from rich.console import Console
from rich.markup import escape
from rich.text import Text

import src.services as _services_pkg
from src.core.utils import JsonObjectReadError, read_json_object
from src.services.acceptance import ConfigApplyResult, ServiceAcceptCriteria
from src.services.models import Service, ServiceProvider
from src.services.provider_strategy import STRATEGIES_FILE, InvalidStrategiesFileError, read_strategies

ServiceT = TypeVar("ServiceT", bound=Service)


def _runtime_source_config_name(source: str) -> str:
    """Derive *config_name* from apply_runtime_payload *source* strings."""
    if source.startswith("<runtime-upload:") and source.endswith(">"):
        inner = source[len("<runtime-upload:"): -1].strip() or "unknown.json"
        path = Path(inner)
        return path.stem if path.suffix else path.name
    path = Path(source)
    if path.suffix:
        return path.stem
    return path.name or source


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
                service_name=descriptor.service_name,
                package_name=descriptor.package_name,
                service_dir=descriptor.service_dir,
                base_dir=base_dir,
            )
        inst._descriptors = {d.service_name: d for d in descriptors}
        inst._services = services
        return inst

    @classmethod
    def _discover(cls) -> list[ServiceDescriptor]:
        """Scan "src.services" for packages exporting service metadata."""
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
        """Return a registered service instance for *service_name*."""
        with self._lock:
            service = self._services.get(service_name)
        if service is None:
            raise KeyError(f"Unknown service: {service_name!r}")
        return service

    def get_typed_service(self, service_name: str, expected_type: type[ServiceT]) -> ServiceT:
        """Return *service_name* and validate it is an instance of *expected_type*."""
        service = self.get_service(service_name)
        if not isinstance(service, expected_type):
            raise TypeError(
                f"Service registry mismatch for {service_name}: "
                f"expected {expected_type.__name__}, got {type(service).__name__}."
            )
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
        console = Console()
        with self._lock:
            services = list(self._services.values())

        console.print()
        console.print("=" * 80, style="dim")
        console.print(f"[bold]ServiceRegistry[/bold] - Total Registered Services: [yellow]{len(services)}[/yellow]")
        console.print("=" * 80, style="dim")
        console.print()

        if not services:
            console.print("[yellow]No services registered.[/yellow]")
            return

        for idx, service in enumerate(services, start=1):
            title = Text() \
                .append(f"[{idx}] ", style="cyan") \
                .append("Service: ") \
                .append(escape(service.name), style="bold green")
            console.print(title)
            active_provider_names = self._active_provider_names(service)
            console.print(
                Text.assemble(
                    "    Active providers: ",
                    (escape(str(active_provider_names)), "yellow"),
                )
            )

            try:
                service_dict = {
                    k: v
                    for k, v in service.__dict__.items()
                    if not k.startswith("_") and not callable(v)
                }
                if service_dict:
                    console.print("    Attributes:")
                    for key, value in service_dict.items():
                        formatted = self._format_registry_attr(key, value, base_dir)
                        console.print(Text.assemble("      - ", (escape(key), "cyan"), ": ", (escape(formatted), ""), ))
            except Exception as exc:  # pylint: disable=broad-exception-caught
                err = Text.assemble(
                    "    ",
                    ("Could not serialize attributes: ", "red"),
                    (escape(str(exc)), "red"),
                )
                console.print(err)

            console.print()

        console.print("=" * 80, style="dim")
        console.print()

    @staticmethod
    def _active_provider_names(service: Service) -> list[str]:
        """Return active provider names for dump output."""
        active_names: list[str] = []
        for provider in service.providers_snapshot():
            if provider.is_active():
                provider_name = provider.name
                if provider_name:
                    active_names.append(provider_name)
        return active_names

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

    def get_accept_criteria(self) -> dict[str, ServiceAcceptCriteria]:
        """Return acceptance criteria for all discovered services."""
        return self._registry.get_accept_criteria()

    def load_all_from_disk(self):
        """Load all known config directories and replace each service provider set."""
        criteria_map = self._registry.get_accept_criteria()
        built: dict[str, list[Any]] = {service_name: [] for service_name in criteria_map}
        results: list[ConfigApplyResult] = []

        for service_name, criteria in criteria_map.items():
            config_dir = self._config_root / criteria.config_subdir
            if not config_dir.exists():
                continue
            for config_path in sorted(config_dir.glob("*.json")):
                payload = self._read_json_file(config_path)
                if payload is None:
                    results.append(
                        ConfigApplyResult(
                            config_name=config_path.stem,
                            accepted=False,
                            error="Invalid JSON payload.",
                        )
                    )
                    continue
                apply_result, materialized = self._materialize_payload(
                    payload=payload,
                    source=config_path,
                    criteria=criteria,
                )
                results.append(apply_result)
                if materialized is not None:
                    built[service_name].append(materialized)

        for service_name, criteria in criteria_map.items():
            providers = self._merge_runtime_providers(built[service_name], criteria)
            providers.sort(key=lambda provider: getattr(provider, "priority", 999))
            self._registry.replace_service_providers(service_name, providers)

        self._apply_provider_strategies()

        # log results
        self._log_apply_result(results)

    def get_service(self, service_name: str) -> Service:
        """Return the registered service instance for *service_name* (KeyError if unknown)."""
        return self._registry.get_service(service_name)

    def _apply_provider_strategies(self) -> None:
        """Give every service its stored provider strategy, or its default when none or an unsupported one is stored.

        A broken strategies file never stops the configuration from loading: it is reported
        and every service falls back to its default.
        """
        console = Console()
        try:
            stored = read_strategies(self._config_root)
        except InvalidStrategiesFileError as exc:
            console.print(Text(f"{exc} Using the default provider strategies.", style="red"))
            stored = {}
        for service_name, service in self._registry.get_registered_services().items():
            if not service.SUPPORTED_STRATEGIES:
                continue
            requested = stored.get(service_name)
            strategy = service.resolve_provider_strategy(requested)
            if requested is not None and requested != strategy:
                console.print(Text(
                    f"{STRATEGIES_FILE}: '{service_name}' does not support {requested.value}; "
                    f"using {strategy.value}.",
                    style="red",
                ))
            service.set_provider_strategy(strategy)

    def apply_runtime_payload(self, payload: dict[str, Any], source: str = "<runtime-upload>") -> ConfigApplyResult:
        """Validate and apply one runtime-submitted config payload in memory."""

        apply_result = ConfigApplyResult(_runtime_source_config_name(source))

        # check for common fields
        common_provider = ServiceProvider.from_dict(payload, Path(source))
        if common_provider is None:
            apply_result.error = "Invalid service_provider envelope."
            return apply_result

        candidates = [
            (service_name, criteria)
            for service_name, criteria in self._registry.get_accept_criteria().items()
            if criteria.can_accept(common_provider)
        ]
        if not candidates:
            apply_result.error = f"Unsupported service '{common_provider.service}'."

        if len(candidates) > 1:
            apply_result.error = f"Ambiguous service match for '{common_provider.service}'."

        if apply_result.error:
            return apply_result

        # apply payload to matching service
        service_name, criteria = candidates[0]
        apply_result, materialized = self._materialize_payload(
            payload=payload,
            source=Path(source),
            criteria=criteria,
        )
        if materialized is None:
            return apply_result

        current = self._registry.service_providers_snapshot(service_name)
        current = self._replace_provider_by_identity(current, materialized)
        current.sort(key=lambda provider: getattr(provider, "priority", 999))
        self._registry.replace_service_providers(service_name, current)
        return apply_result

    def validate_config_payload(
            self,
            service_name: str,
            payload: dict[str, Any],
            source: str = "<runtime-validate>",
    ) -> ConfigApplyResult:
        """Validate one payload against one registered service without mutating runtime state."""
        criteria = self._registry.get_accept_criteria().get(service_name)
        if criteria is None:
            return ConfigApplyResult(
                config_name=_runtime_source_config_name(source),
                accepted=False,
                error=f"Unknown service '{service_name}'.",
            )
        apply_result, _ = self._materialize_payload(
            payload=payload,
            source=Path(source),
            criteria=criteria,
        )
        return apply_result

    @staticmethod
    def _log_apply_result(results: list[ConfigApplyResult]):
        console = Console()

        def log(result: ConfigApplyResult):
            txt = Text()
            txt.append(f"{result.config_name}")
            txt.append(f"  accepted:{str(result.accepted).lower()}\n", style="yellow")
            if result.error:
                txt.append(f" error:{result.error}", style="red")
            console.print(txt)

        for result in [r for r in results if r.error]:
            log(result)

        not_accepted_results = [r for r in results if not r.accepted and not r.error]
        if not_accepted_results:
            console.print("\nService config NOT accepted:")
            for result in not_accepted_results:
                log(result)

    def _materialize_payload(
            self,
            payload: dict[str, Any],
            source: Path,
            criteria: ServiceAcceptCriteria,
    ) -> tuple[ConfigApplyResult, Any | None]:

        config_name = _runtime_source_config_name(str(source))
        apply_result = ConfigApplyResult(config_name)
        service_provider = ServiceProvider.from_dict(payload, source)
        if service_provider is None:
            apply_result.error = "Invalid service_provider envelope."
            return apply_result, None
        # "from_dict" names the provider after the path stem, which garbles runtime
        # sources: "<runtime-upload:openai.json>" would become "<runtime-upload:openai".
        service_provider.name = config_name

        if not criteria.can_accept(service_provider):
            apply_result.error = (
                f"Service mismatch: expected '{criteria.service_name}', got "
                f"'{service_provider.service}'."
            )
            return apply_result, None
        try:
            criteria.validate_provider(service_provider)
            materialized = criteria.build_provider(service_provider, self._base_dir)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            apply_result.error = str(exc)
            return apply_result, None

        apply_result.accepted = True
        return apply_result, materialized

    @staticmethod
    def _read_json_file(config_path: Path) -> dict[str, Any] | None:
        try:
            return read_json_object(config_path)
        except JsonObjectReadError:
            return None

    @staticmethod
    def _replace_provider_by_identity(providers: list[Any], new_provider: Any) -> list[Any]:
        """Replace the provider that has *new_provider*'s config name.

        *providers* all belong to one service, whose config names are unique, while
        several configs may share a provider type ("ffmpeg_high", "ffmpeg_low").
        """
        config_name = str(getattr(new_provider, "name", "")).strip().lower()
        updated = [
            provider
            for provider in providers
            if str(getattr(provider, "name", "")).strip().lower() != config_name
        ]
        updated.append(new_provider)
        return updated

    def _merge_runtime_providers(self, providers: list[Any], criteria: ServiceAcceptCriteria) -> list[Any]:
        """Add the service's runtime-wired providers, replacing loaded ones of the same provider type."""
        if criteria.build_runtime_providers is None:
            return providers
        runtime_providers = criteria.build_runtime_providers(self._config_root, self)
        runtime_types = {self._provider_type(provider) for provider in runtime_providers}
        merged = [provider for provider in providers if self._provider_type(provider) not in runtime_types]
        merged.extend(runtime_providers)
        return merged

    @staticmethod
    def _provider_type(provider: Any) -> str:
        return str(getattr(provider, "provider", "")).strip().lower()
