"""Base optimizer abstraction for audio post-processing."""

from __future__ import annotations

from abc import abstractmethod
from pathlib import Path
from typing import Any

from src.services.models import ServiceProvider

_OPTIMIZER_DEFAULTS: dict[str, Any] = {
    "sample_rate": 22050,
    "mono": True,
    "vbr_quality": 9,
}


class AudioOptimizerBase(ServiceProvider):
    """Common interface for all audio optimizer implementations.

    Merges ``_OPTIMIZER_DEFAULTS`` into ``self.config`` on construction so that
    subclasses can always rely on ``sample_rate``, ``mono``, and ``vbr_quality``
    being present.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # Merge defaults under user values (user config takes precedence)
        self.config = {**_OPTIMIZER_DEFAULTS, **self.config}

    @property
    def optimizer_type(self) -> str:
        """Return optimizer backend type (e.g. ``"ffmpeg"`` or ``"pydub"``)."""
        return self.service_type

    @abstractmethod
    async def optimize(self, input_path: Path, output_path: Path) -> Path:
        """Optimize an audio file and write the result to *output_path*.

        Returns *output_path* on success.
        Raises on failure so the caller can log and continue.
        """
