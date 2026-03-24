"""Service registry: owns all TTS provider, translator, and optimizer instances.

Built once during FastAPI lifespan and injected via Depends(get_registry).
Supports hot-reload when config files change (via watchfiles background task).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from src.core.config_loader import (
    load_optimizer_configs,
    load_provider_configs,
    load_translator_configs,
)
from src.services.models import ProviderConfig
from src.services.optimization import build_active_optimizers
from src.services.providers import build_provider
from src.services.translation import build_translators


class ServiceRegistry:
    """Owns pre-built service instances; eliminates per-request config parsing."""

    def __init__(self) -> None:
        # Each entry is (provider_instance, ProviderConfig) — active providers only
        self.providers: list[tuple] = []
        self.translators: list = []
        self.optimizers: list = []
        self._lock: asyncio.Lock = asyncio.Lock()
        self._base_dir: Path | None = None
        self._config_dir: Path | None = None
        self._output_dir: Path | None = None

    @classmethod
    async def build(
        cls,
        base_dir: Path,
        config_dir: Path,
        output_dir: Path,
    ) -> "ServiceRegistry":
        """Create and populate a registry from config files on disk."""
        reg = cls()
        reg._base_dir = base_dir
        reg._config_dir = config_dir
        reg._output_dir = output_dir
        await reg._load()
        return reg

    async def _load(self) -> None:
        """Parse all config files and (re)build service instances."""
        provider_configs = load_provider_configs(
            base_dir=self._base_dir, config_dir=self._config_dir
        )
        providers: list[tuple] = []
        for config in provider_configs:
            provider = build_provider(config, self._output_dir)
            if provider.is_active():
                providers.append((provider, config))

        translator_configs = load_translator_configs(
            base_dir=self._base_dir, config_dir=self._config_dir
        )
        translators = build_translators(translator_configs, self._base_dir)

        optimizer_configs = load_optimizer_configs(
            base_dir=self._base_dir, config_dir=self._config_dir
        )
        optimizers = build_active_optimizers(optimizer_configs, self._output_dir)

        self.providers = providers
        self.translators = translators
        self.optimizers = optimizers

    async def reload(self) -> None:
        """Re-parse configs and replace instances in-place.

        Acquires the lock so in-flight requests using current instances finish
        before they are replaced.
        """
        async with self._lock:
            await self._load()

    # ------------------------------------------------------------------
    # Convenience accessors (read-only views used by listing endpoints)
    # ------------------------------------------------------------------

    def provider_configs(self) -> list[ProviderConfig]:
        """Return only the ProviderConfig objects (without provider instances)."""
        return [cfg for _, cfg in self.providers]
