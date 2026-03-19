"""Helpers for stable output directory and filename creation."""

from __future__ import annotations

import re
from pathlib import Path

from .models import GeneratedAudio, TTSRequest

SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def slugify_filename(value: str, fallback: str) -> str:
    text = value.strip().replace(" ", "_")
    text = SAFE_NAME_RE.sub("_", text)
    text = re.sub(r"_+", "_", text).strip("._")
    return text or fallback


def raw_output_dir(base_dir: Path, language_code: str, provider: str) -> Path:
    return base_dir / f"tts_audio_{language_code}" / provider


def normalized_output_dir(base_dir: Path, language_code: str, provider: str) -> Path:
    return base_dir / "results" / "normalized" / language_code / provider


def reports_dir(base_dir: Path) -> Path:
    return base_dir / "results" / "reports"


def logs_dir(base_dir: Path) -> Path:
    return base_dir / "results" / "logs"


def requests_log_path(base_dir: Path) -> Path:
    return base_dir / "results" / "requests.log"


def scores_dir(base_dir: Path) -> Path:
    return base_dir / "results" / "scores"


def build_output_stem(request: TTSRequest) -> str:
    base = request.filename_text if request.language_code != "en" and request.filename_text else request.text
    safe_text = slugify_filename(base, fallback=request.request_id)
    return f"{safe_text}_{request.language_code}"


def build_output_stem_from_result(generated_audio: GeneratedAudio) -> str:
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
    return build_output_stem(request_stub)
