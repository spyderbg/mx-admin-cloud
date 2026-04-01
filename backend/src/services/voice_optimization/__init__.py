"""Audio optimization subsystem: base class, factory, and service orchestrator."""

from __future__ import annotations

from pathlib import Path

from src.services.acceptance import ServiceAcceptCriteria
from src.services.models import ServiceProvider
from src.services.voice_optimization.service import AudioOptimizerFactory
from src.services.voice_optimization.base import AudioOptimizerBase
from src.services.voice_optimization.normalizer import normalize_audio

# Import providers AFTER OptimizerFactory is defined so that provider modules
# can safely call `from ... import OptimizerFactory` inside their register() methods.
import src.services.voice_optimization.providers  # noqa: F401, E402  # pylint: disable=wrong-import-position

from src.services.voice_optimization.service import \
    VoiceOptimizationService  # noqa: E402 # pylint: disable=wrong-import-position


def _validate_optimizer_provider(provider: ServiceProvider) -> None:
    if not AudioOptimizerFactory.supports(provider.name):
        raise ValueError(
            f"Unsupported optimizer: '{provider.name}' (file='{provider.config_path}')"
        )


def _build_optimizer_provider(provider: ServiceProvider, base_dir: Path) -> AudioOptimizerBase:
    return AudioOptimizerFactory.create(provider, base_dir)


SERVICE_CLASS = VoiceOptimizationService  # pylint: disable=invalid-name
SERVICE_NAME = "voice_optimization"
SERVICE_ACCEPT_CRITERIA = ServiceAcceptCriteria(
    service_name=SERVICE_NAME,
    config_subdir="voice_optimizers",
    validate_provider=_validate_optimizer_provider,
    build_provider=_build_optimizer_provider,
)

__all__ = [
    "AudioOptimizerBase",
    "normalize_audio",
    "VoiceOptimizationService",
]
