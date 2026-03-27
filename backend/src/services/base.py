"""Shared base classes for all service implementations.

Defines the root ``Service`` contract (enforces ``register``) and
``ServiceProvider`` which supplies the common runtime behaviour shared by
TTS providers, translators, and optimizers.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class Service(ABC):
    """Root contract for all service implementations.

    Every concrete service class must implement :meth:`register` so it can
    self-register with its factory at import time.
    """

    @classmethod
    @abstractmethod
    def register(cls) -> None:
        """Register this implementation with its factory."""


class ServiceProvider(Service):
    """Common runtime base for provider, translator, and optimizer instances.

    Holds the config object and base directory; exposes the name, active-state,
    and API-key helpers that every service type needs.

    Subclasses (``TTSProvider``, ``TextTranslate``, ``AudioOptimizerBase``) are
    expected to narrow the type of *cfg* to their own config dataclass.
    """

    def __init__(self, cfg: Any, base_dir: Path) -> None:
        self.cfg = cfg
        self.config: dict[str, Any] = cfg.config
        self.base_dir = base_dir

    @property
    def name(self) -> str:
        """Logical service name from the config."""
        return self.cfg.name  # type: ignore[no-any-return]

    def is_active(self) -> bool:
        """Return whether this service instance should run."""
        return bool(self.cfg.enabled)

    def api_key_env_name(self) -> str:
        """Return the expected environment-variable name for this service's API key."""
        return f"{self.cfg.name.upper()}_API_KEY"

    def resolve_api_key(self) -> str:
        """Read and return the service API key from the environment."""
        return os.getenv(self.api_key_env_name(), "")
