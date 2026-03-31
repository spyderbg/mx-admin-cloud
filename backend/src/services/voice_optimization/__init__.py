"""Audio optimization subsystem: base class, factory, and service orchestrator."""

from __future__ import annotations

from pathlib import Path

from src.services.acceptance import ServiceAcceptCriteria
from src.services.models import ServiceProvider
from src.services.voice_optimization.base import AudioOptimizerBase
from src.services.voice_optimization.normalizer import normalize_audio

__all__ = [
    "AudioOptimizerBase",
    "OptimizerFactory",
    "normalize_audio",
    "SERVICE_CLASS",
    "VoiceOptimizationService",
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

    @classmethod
    def supports(cls, optimizer_name: str) -> bool:
        """Return whether an optimizer builder is registered."""
        return optimizer_name.strip().lower() in cls._builders


# Import providers AFTER OptimizerFactory is defined so that provider modules
# can safely call `from .. import OptimizerFactory` inside their register() methods.
import src.services.voice_optimization.providers  # noqa: F401, E402

from src.services.voice_optimization.service import VoiceOptimizationService  # noqa: E402

SERVICE_CLASS = VoiceOptimizationService
SERVICE_NAME = "voice_optimization"


def _validate_optimizer_provider(provider: ServiceProvider) -> None:
    if not OptimizerFactory.supports(provider.name):
        raise ValueError(
            f"Unsupported optimizer: '{provider.name}' (file='{provider.config_path}')"
        )


def _build_optimizer_provider(provider: ServiceProvider, base_dir: Path) -> AudioOptimizerBase:
    return OptimizerFactory.create(provider, base_dir)


SERVICE_ACCEPT_CRITERIA = ServiceAcceptCriteria(
    service_name=SERVICE_NAME,
    config_subdir="voice_optimizers",
    validate_provider=_validate_optimizer_provider,
    build_provider=_build_optimizer_provider,
)
