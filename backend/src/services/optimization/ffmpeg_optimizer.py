"""FFmpeg-based audio optimizer."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from .base import AudioOptimizerBase


class FfmpegAudioOptimizer(AudioOptimizerBase):  # pylint: disable=too-few-public-methods
    """Optimize audio files by shelling out to ffmpeg."""

    async def optimize(self, input_path: Path, output_path: Path) -> Path:
        """Run ffmpeg with configured parameters and return output path."""
        ffmpeg_path = shutil.which("ffmpeg")
        if not ffmpeg_path:
            raise RuntimeError("ffmpeg not found in PATH")

        if not input_path.exists():
            raise FileNotFoundError(f"Input file not found: {input_path}")

        config = self.config
        sample_rate = int(config.get("sample_rate", 22050))
        mono: bool = bool(config.get("mono", True))
        vbr_quality = int(config.get("vbr_quality", 9))

        command: list[str] = [ffmpeg_path, "-y", "-i", str(input_path)]
        if mono:
            command.extend(["-ac", "1"])
        command.extend(
            ["-ar", str(sample_rate), "-q:a", str(vbr_quality), str(output_path)]
        )

        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()
        if process.returncode != 0:
            raise RuntimeError(
                f"ffmpeg exited with code {process.returncode}: "
                f"{stderr.decode('utf-8', errors='replace').strip()}"
            )

        return output_path
