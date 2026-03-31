"""Voice optimization service business logic and orchestration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.services.models import Service, ServiceProvider
from src.services.voice_optimization.base import AudioOptimizerBase


class VoiceOptimizationService(Service):
    """Orchestrator for all audio optimizer providers.

    Loads optimizers from config files and exposes :meth:`optimize` which runs
    all active optimizers in priority order, feeding the output of each into the
    next (pipeline mode).
    """

    def __init__(self, name: str, service_dir: Path, base_dir: Path) -> None:
        super().__init__(name=name, service_dir=service_dir)
        self.base_dir = base_dir
        self._optimizers: list[AudioOptimizerBase] = []

    def load_optimizers(self, config_dir: Path) -> None:
        """Load and build all enabled optimizers from *config_dir*, sorted by priority."""
        from src.services.config_loader import load_service_configs  # pylint: disable=import-outside-toplevel
        from src.services.voice_optimization import OptimizerFactory  # pylint: disable=import-outside-toplevel
        raw = load_service_configs(config_dir, ServiceProvider)
        raw.sort(key=lambda p: p.priority)
        self._optimizers = [
            OptimizerFactory.create(p, self.base_dir)
            for p in raw
            if p.enabled
        ]

    def load_runtime_config(self, config_root: Path) -> None:
        self.load_optimizers(config_root / "voice_optimizers")

    def registry_get(self, key: str) -> list[Any] | None:
        if key == "optimizers":
            return list(self.active_optimizers)
        return None

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
