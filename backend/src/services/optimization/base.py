"""Base optimizer abstraction for audio post-processing."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from ..models import OptimizerConfig


class AudioOptimizerBase(ABC):
    """Common interface for all audio optimizer implementations."""

    def __init__(
        self,
        service: OptimizerConfig,
        config: dict[str, object],
        base_dir: Path,
    ) -> None:
        self.service = service
        self.config = config
        self.base_dir = base_dir

    @property
    def name(self) -> str:
        """Return logical optimizer name from configuration."""
        return self.service.name

    @property
    def optimizer_type(self) -> str:
        """Return optimizer backend type (for example, ffmpeg or pydub)."""
        return self.service.type

    def is_active(self) -> bool:
        """Return whether this optimizer should run."""
        return self.service.active

    @abstractmethod
    async def optimize(self, input_path: Path, output_path: Path) -> Path:
        """Optimize an audio file and write the result to *output_path*.

        Returns *output_path* on success.
        Raises on failure so the caller can log and continue.
        """
