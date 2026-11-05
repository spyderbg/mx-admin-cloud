"""Normalize raw provider output to a common WAV format for evaluation."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from src.core.config import settings
from src.services.text_to_speech.models import GeneratedAudio


def _build_output_stem_from_result(generated_audio: GeneratedAudio) -> str:
    """Build the output stem from a generated audio result."""
    request_stub = type(
        "RequestStub",
        (),
        {
            "filename_text": generated_audio.filename_text,
            "language_code": generated_audio.language_code,
            "text": generated_audio.expected_text,
            "request_id": generated_audio.request_id,
        },
    )()
    return settings.build_output_stem(request_stub)


async def normalize_audio(_base_dir: Path, generated_audio: GeneratedAudio) -> GeneratedAudio:
    """Normalize raw provider output to a common WAV format for evaluation."""
    ffmpeg_path = shutil.which("ffmpeg")
    if not ffmpeg_path:
        generated_audio.normalization_error = "ffmpeg not found in PATH."
        return generated_audio

    output_dir = settings.normalized_output_dir(generated_audio.language_code, generated_audio.provider)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{_build_output_stem_from_result(generated_audio)}.wav"

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
