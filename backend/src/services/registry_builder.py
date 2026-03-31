"""External runtime builder for service provider configuration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.services.acceptance import ConfigApplyResult, ServiceAcceptCriteria
from src.services.models import ServiceProvider
from src.services.service_registry import ServiceRegistry


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
