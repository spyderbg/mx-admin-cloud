"""Base optimizer abstraction for audio post-processing."""

from __future__ import annotations

from abc import abstractmethod
from pathlib import Path

from src.services.base import ServiceProvider
from src.services.optimization.models import OptimizerConfig


class AudioOptimizerBase(ServiceProvider):
    """Common interface for all audio optimizer implementations."""

    def __init__(
        self,
        cfg: OptimizerConfig,
        base_dir: Path,
    ) -> None:
        super().__init__(cfg, base_dir)

    @property
    def optimizer_type(self) -> str:
        """Return optimizer backend type (for example, ffmpeg or pydub)."""
        return self.cfg.category

    @abstractmethod
    async def optimize(self, input_path: Path, output_path: Path) -> Path:
        """Optimize an audio file and write the result to *output_path*.

        Returns *output_path* on success.
        Raises on failure so the caller can log and continue.
        """
