"""Shared base classes for all service implementations.

Defines the root "Service" contract and "ServiceProvider", which is the
unified data representation of a config JSON file.
Common "service_provider" envelope fields that are parsed as direct attributes.
The "config" dict (from the "config" key in the JSON) is stored as-is.
Typed per-provider parsing happens inside each provider subclass using "self.config".
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


class Service:
    """Root contract for all service implementations.

    Subclasses are business-logic orchestrators that hold providers and expose
    public methods called from routes.
    """

    def __init__(self, service_name: str, package_name: str, service_dir: Path, base_dir: Path) -> None:
        self.name = service_name
        self.class_name = self.__class__.__name__
        self.package_name = package_name
        self.service_dir = service_dir
        self.base_dir = base_dir

    def replace_providers(self, providers: list[Any]) -> None:
        """Replace runtime providers for this service."""
        raise NotImplementedError(
            f"{type(self).__name__} must implement replace_providers()",
        )

    def providers_snapshot(self) -> list[Any]:
        """Return a copy of currently loaded providers."""
        raise NotImplementedError(
            f"{type(self).__name__} must implement providers_snapshot()",
        )


class ServiceProvider:
    """Data representation of one "service_provider" config JSON file.

    Attributes populated from the "service_provider" JSON envelope:

    * "name" — name of the configuration file, without extension
    * "service_name" — value of the ""service"" key
    * "provider_name" — value of the ""provider"" key
    * "enabled" — value of the ""enabled"" key
    * "priority" — value of the ""priority"" key (defaults to 999)
    * "config" — raw dict from the ""config"" key, stored as-is

    """

    def __init__(
        self,
        name: str,
        service_name: str,
        provider_name: str,
        enabled: bool,
        priority: int,
        config: dict[str, Any],
        config_path: Path,
        base_dir: Path,
    ) -> None:
        self.name = name
        self.service = service_name
        self.provider = provider_name
        self.enabled = enabled
        self.priority = priority
        self.config = config          # raw "config" dict, stored as-is
        self.config_path = config_path
        self.base_dir = base_dir

    @classmethod
    def from_dict(cls, data: dict[str, Any], config_path: Path) -> "ServiceProvider | None":
        """Parse the common "service_provider" envelope from a config JSON dict.

        Returns "None" on structural failure so the generic loader can skip the
        file silently. The "service_name" is optional (empty string when absent) so
        that service-specific formats (e.g., TTS) that omit the ""service"" key
        still parse correctly.
        """
        from src.core.utils import boolish  # pylint: disable=import-outside-toplevel

        name = config_path.stem.strip()
        if not name:
            return None

        service = data.get("service_provider", {})
        if not isinstance(service, dict):
            return None

        service_name = str(service.get("service", "")).strip().lower()
        provider_name = str(service.get("provider", "")).strip().lower()
        enabled = boolish(service.get("enabled", False))
        priority = int(service.get("priority", 999))
        raw_config = service.get("config", {})
        if not isinstance(raw_config, dict):
            raw_config = {}

        return cls(
            name=name,
            service_name=service_name,
            provider_name=provider_name,
            enabled=enabled,
            priority=priority,
            config=raw_config,
            config_path=config_path,
            base_dir=config_path.parent,
        )

    def is_active(self) -> bool:
        """Return whether this provider instance should run."""
        return bool(self.enabled)

    def api_key_env_name(self) -> str:
        """Return the expected environment-variable name for this provider's API key."""
        return f"{self.name.upper()}_API_KEY"

    def resolve_api_key(self) -> str:
        """Read and return the provider API key from the environment."""
        return os.getenv(self.api_key_env_name(), "")
