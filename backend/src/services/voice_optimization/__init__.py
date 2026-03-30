"""Audio optimization subsystem: base class, factory, and service orchestrator."""

from __future__ import annotations

from pathlib import Path

from src.services.models import ServiceProvider
from src.services.voice_optimization.base import AudioOptimizerBase
from src.services.voice_optimization.normalizer import normalize_audio

__all__ = [
    "AudioOptimizerBase",
    "OptimizerFactory",
    "normalize_audio",
]


class OptimizerFactory:
    """Registry that maps optimizer names to concrete optimizer classes."""

    _builders: dict[str, type[AudioOptimizerBase]] = {}

    @classmethod
    def register(
        cls,
        optimizer_name: str,
        builder: type[AudioOptimizerBase],
    ) -> None:
        """Register a concrete optimizer class under *optimizer_name*."""
        cls._builders[optimizer_name.strip().lower()] = builder

    @classmethod
    def create(cls, provider: ServiceProvider, base_dir: Path) -> AudioOptimizerBase:
        """Instantiate the concrete optimizer for *provider*.

        Raises :exc:`ValueError` when no builder is registered for
        ``provider.name``.
        """
        key = provider.name.strip().lower()
        builder = cls._builders.get(key)
        if builder is None:
            raise ValueError(
                f"Unsupported optimizer: '{provider.name}' "
                f"(file='{provider.config_path}')"
            )
        return builder(
            service_type=provider.service_type,
            name=provider.name,
            enabled=provider.enabled,
            priority=provider.priority,
            config=provider.config,
            config_path=provider.config_path,
            base_dir=base_dir,
        )


# Import providers AFTER OptimizerFactory is defined so that provider modules
# can safely call `from .. import OptimizerFactory` inside their register() methods.
import src.services.voice_optimization.providers  # noqa: F401, E402
