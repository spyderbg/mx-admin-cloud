"""Factory that builds and reloads a ServiceRegistry from config files.

Separated from ``core.registry`` so that ``core/`` never imports ``services/``.
"""

from __future__ import annotations

from pathlib import Path

from src.core.registry import ServiceRegistry
from src.services.optimization import build_active_optimizers
from src.services.optimization.config_loader import load_optimizer_configs
from src.services.text_to_speech import build_provider
from src.services.text_to_speech.config_loader import load_provider_configs
from src.services.text_translation import build_translators
from src.services.text_translation.config_loader import load_translator_configs


async def build_registry(
    base_dir: Path,
    config_dir: Path,
    output_dir: Path,
) -> ServiceRegistry:
    """Create and populate a registry from config files on disk."""
    registry = ServiceRegistry()
    await _load_into(registry, base_dir, config_dir, output_dir)
    return registry


async def reload_registry(
    registry: ServiceRegistry,
    base_dir: Path,
    config_dir: Path,
    output_dir: Path,
) -> None:
    """Re-parse configs and replace instances in-place (thread-safe)."""
    async with registry._lock:  # pylint: disable=protected-access
        await _load_into(registry, base_dir, config_dir, output_dir)


async def _load_into(
    registry: ServiceRegistry,
    base_dir: Path,
    config_dir: Path,
    output_dir: Path,
) -> None:
    """Parse all config files and populate registry instances."""
    provider_configs = load_provider_configs(base_dir=base_dir, config_dir=config_dir)
    providers: list[tuple] = []
    for config in provider_configs:
        provider = build_provider(config, output_dir)
        if provider.is_active():
            providers.append((provider, config))

    translator_configs = load_translator_configs(base_dir=base_dir, config_dir=config_dir)
    translators = build_translators(translator_configs, base_dir)

    optimizer_configs = load_optimizer_configs(base_dir=base_dir, config_dir=config_dir)
    optimizers = build_active_optimizers(optimizer_configs, output_dir)

    registry.providers = providers
    registry.translators = translators
    registry.optimizers = optimizers
