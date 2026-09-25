"""Pydub-based audio optimizer."""

from __future__ import annotations

import asyncio
from pathlib import Path

from src.services.voice_optimization.models import AudioOptimizerBase


class PydubAudioOptimizer(AudioOptimizerBase):  # pylint: disable=too-few-public-methods
    """Optimize audio files using pydub (AudioSegment)."""

    @classmethod
    def register(cls) -> None:
        """Register this optimizer with the factory."""
        from src.services.voice_optimization import AudioOptimizerFactory
        AudioOptimizerFactory.register("pydub", cls)

    async def optimize(self, input_path: Path, output_path: Path) -> Path:
        """Optimize an input MP3 asynchronously and return output path."""
        if not input_path.exists():
            raise FileNotFoundError(f"Input file not found: {input_path}")

        config = self.config
        sample_rate = int(config.get("sample_rate", 22050))
        mono: bool = bool(config.get("mono", True))
        vbr_quality = int(config.get("vbr_quality", 9))

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            self._optimize_sync,
            input_path,
            output_path,
            sample_rate,
            mono,
            vbr_quality,
        )
        return output_path

    @staticmethod
    def _optimize_sync(
        input_path: Path,
        output_path: Path,
        sample_rate: int,
        mono: bool,
        vbr_quality: int,
    ) -> None:
        """Decode *input_path*, apply channel/rate settings, and export MP3 to *output_path*."""
        # Imported lazily to keep optional dependency initialization local.
        from pydub import AudioSegment  # pylint: disable=import-outside-toplevel

        audio = AudioSegment.from_mp3(str(input_path))
        if mono:
            audio = audio.set_channels(1)
        audio = audio.set_frame_rate(sample_rate)
        audio.export(
            str(output_path),
            format="mp3",
            parameters=["-q:a", str(vbr_quality)],
        )

PydubAudioOptimizer.register()
