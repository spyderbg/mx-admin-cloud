"""Normalize raw provider output to a common WAV format for evaluation."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from ..models import GeneratedAudio
from ..output_paths import build_output_stem_from_result, normalized_output_dir


async def normalize_audio(base_dir: Path, generated_audio: GeneratedAudio) -> GeneratedAudio:
    ffmpeg_path = shutil.which("ffmpeg")
    if not ffmpeg_path:
        generated_audio.normalization_error = "ffmpeg not found in PATH."
        return generated_audio

    output_dir = normalized_output_dir(base_dir, generated_audio.language_code, generated_audio.provider)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{build_output_stem_from_result(generated_audio)}.wav"

    process = await asyncio.create_subprocess_exec(
        ffmpeg_path,
        "-y",
        "-i",
        str(generated_audio.raw_path),
        "-ac",
        "1",
        "-ar",
        "22050",
        str(output_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    if process.returncode != 0:
        generated_audio.normalization_error = stderr.decode("utf-8", errors="replace").strip()
        return generated_audio

    generated_audio.normalized_path = output_path
    return generated_audio
