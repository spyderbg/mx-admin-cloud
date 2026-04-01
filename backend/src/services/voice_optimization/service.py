"""Voice optimization service business logic and orchestration."""

from __future__ import annotations

from pathlib import Path

from src.services.models import Service
from src.services.provider_factory import ServiceProviderFactory
from src.services.voice_optimization.base import AudioOptimizerBase


class AudioOptimizerFactory(ServiceProviderFactory[AudioOptimizerBase]):
    """Audio-optimizer-specific provider registry."""

    unsupported_label = "optimizer"


class VoiceOptimizationService(Service):
    """Orchestrator for all audio optimizer providers.

    Loads optimizers from config files and exposes :meth:`optimize` which runs
    all active optimizers in priority order, feeding the output of each into the
    next (pipeline mode).
    """

    def __init__(self, name: str, service_dir: Path, base_dir: Path) -> None:
        super().__init__(name=name, service_dir=service_dir, base_dir=base_dir)
        self._optimizers: list[AudioOptimizerBase] = []

    def replace_providers(self, providers: list[object]) -> None:
        """Replace loaded optimizers with *providers*."""
        self._optimizers = [o for o in providers if isinstance(o, AudioOptimizerBase)]

    def providers_snapshot(self) -> list[object]:
        """Return loaded optimizers, including disabled ones."""
        return list(self._optimizers)

    @property
    def active_optimizers(self) -> list[AudioOptimizerBase]:
        """Return optimizers that report themselves as active."""
        return [o for o in self._optimizers if o.is_active()]

    async def optimize(self, input_path: Path, output_path: Path) -> Path:
        """Run all active optimizers in sequence.

        The output of each optimizer is passed as input to the next.
        Returns the final output path.
        """
        current = input_path
        for optimizer in self.active_optimizers:
            current = await optimizer.optimize(current, output_path)
        return current
