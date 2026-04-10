"""CRUD service for on-disk service configuration JSON files."""

from __future__ import annotations

import json
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.core.utils import JsonObjectReadError, read_json_object
from src.services.config_file_store.merge import deep_merge, prune_payload
from src.services.service_registry import ServiceRegistryBuilder

_CONFIG_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


class ServiceConfigStoreError(Exception):
    """Base exception for config store errors."""


class UnknownServiceError(ServiceConfigStoreError):
    """Raised when service name is not registered."""


class InvalidConfigNameError(ServiceConfigStoreError):
    """Raised when config name is unsafe or malformed."""


class ConfigConflictError(ServiceConfigStoreError):
    """Raised when adding a config that already exists."""


class ConfigNotFoundError(ServiceConfigStoreError):
    """Raised when a requested config file does not exist."""


class ConfigValidationError(ServiceConfigStoreError):
    """Raised when payload validation fails."""


@dataclass(frozen=True)
class ConfigSummary:
    """Lightweight config metadata used by list operations."""

    config_name: str
    filename: str
    provider: str | None
    enabled: bool | None
    priority: int | None
    valid_json: bool
    error: str | None = None


@dataclass(frozen=True)
class ConfigMutationResult:
    """Result for one config mutation operation."""

    config_name: str
    changed: bool
    error: str | None = None


class ServiceConfigFileStore:
    """Manage service config files under `config/<service_subdir>/`."""

    def __init__(self, config_root: Path, registry_builder: ServiceRegistryBuilder) -> None:
        self._config_root = config_root
        self._builder = registry_builder

    def list_configs(self, service_name: str) -> list[ConfigSummary]:
        """Return config files for one service."""
        config_dir = self._config_dir_for_service(service_name)
        if not config_dir.exists():
            return []
        results: list[ConfigSummary] = []
        for config_path in sorted(config_dir.glob("*.json")):
            payload = self._read_json(config_path)
            if payload is None:
                results.append(
                    ConfigSummary(
                        config_name=config_path.stem,
                        filename=config_path.name,
                        provider=None,
                        enabled=None,
                        priority=None,
                        valid_json=False,
                        error="Invalid JSON payload.",
                    )
                )
                continue
            service_provider = payload.get("service_provider", {})
            provider = service_provider.get("provider") if isinstance(service_provider, dict) else None
            enabled = service_provider.get("enabled") if isinstance(service_provider, dict) else None
            priority = service_provider.get("priority") if isinstance(service_provider, dict) else None
            priority_int = int(priority) if isinstance(priority, int) else None
            enabled_bool = bool(enabled) if isinstance(enabled, bool) else None
            results.append(
                ConfigSummary(
                    config_name=config_path.stem,
                    filename=config_path.name,
                    provider=str(provider) if isinstance(provider, str) else None,
                    enabled=enabled_bool,
                    priority=priority_int,
                    valid_json=True,
                )
            )
        return results

    def add_config(self, service_name: str, config_name: str, payload: dict[str, Any]) -> ConfigMutationResult:
        """Create a new config file for a service."""
        target_path = self._config_path(service_name, config_name, create_dir=True)
        if target_path.exists():
            raise ConfigConflictError(f"Configuration '{config_name}' already exists for '{service_name}'.")
        sanitized = prune_payload(payload)
        self._validate(service_name, sanitized, source=target_path.name)
        self._write_json(target_path, sanitized)
        self._builder.load_all_from_disk()
        return ConfigMutationResult(config_name=config_name, changed=True)

    def remove_config(self, service_name: str, config_name: str) -> ConfigMutationResult:
        """Delete a config file for a service."""
        target_path = self._config_path(service_name, config_name)
        if not target_path.exists():
            raise ConfigNotFoundError(f"Configuration '{config_name}' was not found for '{service_name}'.")
        target_path.unlink()
        self._builder.load_all_from_disk()
        return ConfigMutationResult(config_name=config_name, changed=True)

    def patch_config(
            self,
            service_name: str,
            config_name: str,
            patch: dict[str, Any],
    ) -> ConfigMutationResult:
        """Merge patch payload into an existing config file."""
        target_path = self._config_path(service_name, config_name)
        base_payload = self._read_required_json(service_name, config_name, target_path)
        merged_payload = deep_merge(base_payload, patch)
        sanitized = prune_payload(merged_payload)
        self._validate(service_name, sanitized, source=target_path.name)
        self._write_json(target_path, sanitized)
        self._builder.load_all_from_disk()
        return ConfigMutationResult(config_name=config_name, changed=True)

    def set_configurations_enabled(
            self,
            service_name: str,
            config_names: list[str],
            enabled: bool,
    ) -> list[ConfigMutationResult]:
        """Enable or disable selected service configurations."""
        if not config_names:
            return []

        results: list[ConfigMutationResult] = []
        has_changes = False
        for config_name in config_names:
            try:
                target_path = self._config_path(service_name, config_name)
                payload = self._read_required_json(service_name, config_name, target_path)
                service_provider = payload.get("service_provider")
                if not isinstance(service_provider, dict):
                    raise ConfigValidationError("Invalid service_provider envelope.")
                service_provider["enabled"] = enabled
                sanitized = prune_payload(payload)
                self._validate(service_name, sanitized, source=target_path.name)
                self._write_json(target_path, sanitized)
                has_changes = True
                results.append(ConfigMutationResult(config_name=config_name, changed=True))
            except ServiceConfigStoreError as exc:
                results.append(ConfigMutationResult(config_name=config_name, changed=False, error=str(exc)))
        if has_changes:
            self._builder.load_all_from_disk()
        return results

    def set_all_configurations_enabled(self, service_name: str, enabled: bool) -> list[ConfigMutationResult]:
        """Enable or disable all config files for one service."""
        config_dir = self._config_dir_for_service(service_name)
        if not config_dir.exists():
            return []
        names = [path.stem for path in sorted(config_dir.glob("*.json"))]
        return self.set_configurations_enabled(service_name, names, enabled)

    def _validate(self, service_name: str, payload: dict[str, Any], source: str) -> None:
        result = self._builder.validate_config_payload(service_name, payload, source=source)
        if not result.accepted:
            raise ConfigValidationError(result.error or "Config validation failed.")

    def _config_dir_for_service(self, service_name: str) -> Path:
        normalized = service_name.strip().lower()
        if not normalized:
            raise UnknownServiceError("Service name is required.")
        criteria_map = self._builder.get_accept_criteria()
        criteria = criteria_map.get(normalized)
        if criteria is None:
            raise UnknownServiceError(f"Unknown service '{service_name}'.")
        return self._config_root / criteria.config_subdir

    def _config_path(self, service_name: str, config_name: str, create_dir: bool = False) -> Path:
        config_stem = self._validate_config_name(config_name)
        config_dir = self._config_dir_for_service(service_name)
        if create_dir:
            config_dir.mkdir(parents=True, exist_ok=True)
        return config_dir / f"{config_stem}.json"

    @staticmethod
    def _validate_config_name(config_name: str) -> str:
        stem = config_name.strip()
        if stem.endswith(".json"):
            stem = stem[:-5]
        if not stem:
            raise InvalidConfigNameError("Config name is required.")
        if "/" in stem or "\\" in stem or ".." in stem:
            raise InvalidConfigNameError("Config name must not contain path traversal segments.")
        if not _CONFIG_NAME_RE.fullmatch(stem):
            raise InvalidConfigNameError(
                "Config name may contain only letters, numbers, dot, dash, and underscore."
            )
        return stem

    @staticmethod
    def _read_json(config_path: Path) -> dict[str, Any] | None:
        try:
            return read_json_object(config_path)
        except JsonObjectReadError:
            return None

    def _read_required_json(self, service_name: str, config_name: str, config_path: Path) -> dict[str, Any]:
        if not config_path.exists():
            raise ConfigNotFoundError(f"Configuration '{config_name}' was not found for '{service_name}'.")
        payload = self._read_json(config_path)
        if payload is None:
            raise ConfigValidationError(f"Configuration '{config_name}' contains invalid JSON.")
        return payload

    @staticmethod
    def _write_json(config_path: Path, payload: dict[str, Any]) -> None:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=str(config_path.parent),
                delete=False,
        ) as temp_file:
            json.dump(payload, temp_file, indent=2, ensure_ascii=False)
            temp_file.write("\n")
            temp_path = Path(temp_file.name)
        temp_path.replace(config_path)
