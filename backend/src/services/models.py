"""Shared base classes for all service implementations.

Defines the root ``Service`` contract and ``ServiceProvider`` which is the
unified data representation of a config JSON file.  Common ``service_provider``
envelope fields (service_type, name, enabled, priority) are parsed as direct
attributes; the ``config`` dict (from the "config" key in the JSON) is stored
as-is.  Typed per-provider parsing happens inside each provider subclass using
``self.config``.
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

    def __init__(self, name: str, service_dir: Path) -> None:
        self.name = name
        self.service_dir = service_dir


class ServiceProvider:
    """Data representation of one ``service_provider`` config JSON file.

    Attributes populated from the ``service_provider`` JSON envelope:

    * ``service_type`` — value of the ``"service"`` key
    * ``name``         — value of the ``"provider"`` key (falls back to filename stem)
    * ``enabled``      — value of the ``"enabled"`` key
    * ``priority``     — value of the ``"priority"`` key (defaults to 999)
    * ``config``       — raw dict from the ``"config"`` key, stored as-is

    Subclasses (``TTSProvider``, ``TextTranslationServiceProvider``,
    ``AudioOptimizerBase``) parse ``self.config`` into typed fields in their
    own ``__init__``.
    """

    def __init__(
        self,
        service_type: str,
        name: str,
        enabled: bool,
        priority: int,
        config: dict[str, Any],
        config_path: Path,
        base_dir: Path,
    ) -> None:
        self.service_type = service_type
        self.name = name
        self.enabled = enabled
        self.priority = priority
        self.config = config          # raw "config" dict, stored as-is
        self.config_path = config_path
        self.base_dir = base_dir

    @classmethod
    def from_dict(cls, data: dict[str, Any], config_path: Path) -> "ServiceProvider | None":
        """Parse the common ``service_provider`` envelope from a config JSON dict.

        Returns ``None`` on structural failure so the generic loader can skip the
        file silently.  ``service_type`` is optional (empty string when absent) so
        that service-specific formats (e.g. TTS) that omit the ``"service"`` key
        still parse correctly.
        """
        from src.core.utils import boolish  # pylint: disable=import-outside-toplevel

        service = data.get("service_provider", {})
        if not isinstance(service, dict):
            return None

        name = str(service.get("provider") or config_path.stem).strip()
        if not name:
            return None

        service_type = str(service.get("service", "")).strip().lower()
        enabled = boolish(service.get("enabled", False))
        priority = int(service.get("priority", 999))
        raw_config = service.get("config", {})
        if not isinstance(raw_config, dict):
            raw_config = {}

        return cls(
            service_type=service_type,
            name=name,
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
