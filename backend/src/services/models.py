"""Shared base classes for all service implementations.

Defines the root "Service" contract and "ServiceProvider", which is the
unified data representation of a config JSON file.
Common "service_provider" envelope fields that are parsed as direct attributes.
The "config" dict (from the "config" key in the JSON) is stored as-is.
Typed per-provider parsing happens inside each provider subclass using "self.config".
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.core.languages import normalize_language_code
from src.services.schemas import ServiceProviderInfo


@dataclass
class LangCodeText:
    """Pair of text and its normalized language code."""

    lang_code: str
    text: str

    def __post_init__(self) -> None:
        self.lang_code = normalize_language_code(self.lang_code)


class Service(ABC):
    """Root contract for all service implementations.

    Subclasses are business-logic orchestrators that hold providers and expose
    public methods called from routes.
    """

    def __init__(self, service_name: str, package_name: str, service_dir: Path, base_dir: Path) -> None:
        self.name: str = service_name
        self.class_name: str = self.__class__.__name__
        self.package_name: str = package_name
        self.service_dir: Path = service_dir
        self.base_dir: Path = base_dir

    @abstractmethod
    def replace_providers(self, providers: list["ServiceProvider"]) -> None:
        """Replace runtime providers for this service."""
        raise NotImplementedError(
            f"{type(self).__name__} must implement replace_providers()",
        )

    @abstractmethod
    def providers_snapshot(self) -> list["ServiceProvider"]:
        """Return a copy of currently loaded providers."""
        raise NotImplementedError(
            f"{type(self).__name__} must implement providers_snapshot()",
        )

    @abstractmethod
    def get_all_providers(self) -> list["ServiceProvider"]:
        """Return a list of all providers."""
        raise NotImplementedError(
            f"{type(self).__name__} must implement get_all_providers()",
        )

    @abstractmethod
    def get_active_providers(self) -> list["ServiceProvider"]:
        """Return a list of active providers."""
        raise NotImplementedError(
            f"{type(self).__name__} must implement get_active_providers()",
        )

    def get_provider(self, provider_name: str) -> "ServiceProvider | None":
        """Return a provider by name, or None if not found."""
        normalized = provider_name.strip().lower()
        for provider in self.get_active_providers():
            if provider.name.strip().lower() == normalized:
                return provider
        for provider in self.get_all_providers():
            if provider.name.strip().lower() == normalized:
                return provider
        return None


class ServiceProvider:
    """Data representation of one `service_provider` config JSON file.

    Attributes populated from the "service_provider" JSON envelope:

    * `name` - name of the configuration file, without extension
    * `service_name` - value of the `service` key
    * `provider_name` - value of the `provider` key
    * `enabled` - value of the `enabled` key
    * `priority` - value of the `priority` key (defaults to 999)
    * `config` - raw dict from the `config` key, stored as-is

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
        self.config = config  # raw "config" dict, stored as-is
        self.config_path = config_path
        self.base_dir = base_dir

    @classmethod
    def from_dict(cls, data: dict[str, Any], config_path: Path) -> "ServiceProvider | None":
        """Parse the common `service_provider` envelope from a config JSON dict.

        Returns `None` on structural failure so the generic loader can skip the
        file silently. The `service_name` is optional (empty string when absent) so
        that service-specific formats (e.g., TTS) that omit the `service` key
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

    def get_priority(self) -> int:
        """Return the priority of this provider instance."""
        return self.priority

    def api_key_env_name(self) -> str:
        """Return the expected environment-variable name for this provider's API key."""
        return f"{self.name.upper()}_API_KEY"

    def resolve_api_key(self) -> str:
        """Read and return the provider API key from the environment."""
        return os.getenv(self.api_key_env_name(), "")

    def get_info(self) -> ServiceProviderInfo:
        """Return a structured representation of this provider."""
        return ServiceProviderInfo(
            name=self.name,
            service=self.service,
            provider=self.provider,
            enabled=self.enabled,
            priority=self.priority,
        )
