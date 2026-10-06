"""Static TTS provider: returns an item's existing English voice, or a silent placeholder."""

from __future__ import annotations

import csv
import io
import shutil
import time
from pathlib import Path
from typing import Any

from src.core.config import settings
from src.core.models import GeneratedAudio
from src.services.text_to_speech.models import TTSProvider, TTSRequest
from src.services.text_to_speech.service import TextToSpeechProviderFactory

_VOICE_LANGUAGE = "en"
_DUMMY_VOICE_MS = 500


class StaticTTSProvider(TTSProvider):
    """Make sure an item has a voice without calling any external service.

    The word is looked up in the content directory's ``items_<lang>.csv`` files (row-aligned
    with ``items_ids.csv``). When the matching item already has an English voice
    (``<item>/<item>_en.mp3``) that file is returned; otherwise a short silent clip stands in
    for it, so the item is never left without a voice.
    """

    @classmethod
    def register(cls) -> None:
        """Register this provider under the key "static" in the ProviderFactory."""
        TextToSpeechProviderFactory.register("static", cls)

    @staticmethod
    def _read_column(path: Path) -> list[str]:
        """Return the first column of *path*, one entry per row, or [] when it does not exist."""
        if not path.is_file():
            return []
        with path.open("r", encoding="utf-8", newline="") as fh:
            return [row[0].strip() if row else "" for row in csv.reader(fh)]

    @classmethod
    def _find_english_voice(cls, text: str, language_code: str) -> Path | None:
        """Return the existing English voice of the item whose *language_code* word is *text*."""
        # Imported here: the content service depends on this one, not the other way round.
        from src.services.content.models import ContentProvider  # pylint: disable=import-outside-toplevel

        word = text.strip().lower()
        root = settings.content_path
        if not word or not root.is_dir():
            return None

        for category_dir in sorted(p for p in root.iterdir() if p.is_dir()):
            words = cls._read_column(category_dir / f"items_{language_code}.csv")
            ids = cls._read_column(category_dir / "items_ids.csv")
            for index, candidate in enumerate(words):
                if candidate.lower() != word or index >= len(ids) or not ids[index]:
                    continue
                folder = ContentProvider._folder_name_from_item_id(ids[index])  # pylint: disable=protected-access
                item_dir = category_dir / folder
                # The main voice first, then the first numbered variant (<item>_en_01.mp3, ...).
                voices = [item_dir / f"{folder}_{_VOICE_LANGUAGE}.mp3"]
                voices.extend(sorted(item_dir.glob(f"{folder}_{_VOICE_LANGUAGE}_[0-9]*.mp3")))
                for voice in voices:
                    if voice.is_file():
                        return voice
        return None

    @staticmethod
    def _dummy_voice(output_format: str) -> bytes:
        """Return a short silent clip encoded as *output_format*."""
        from pydub import AudioSegment  # pylint: disable=import-outside-toplevel

        buffer = io.BytesIO()
        AudioSegment.silent(duration=_DUMMY_VOICE_MS).export(buffer, format=output_format.lstrip("."))
        return buffer.getvalue()

    async def generate(self, request: TTSRequest, client: Any) -> GeneratedAudio:
        """Return the item's English voice when it has one, else a silent placeholder."""
        output_format = request.output_format
        output_path = self.build_output_path(request, output_format)
        started_at = time.perf_counter()

        existing = self._find_english_voice(request.text, request.language_code)
        if existing is not None:
            shutil.copyfile(existing, output_path)
        else:
            output_path.write_bytes(self._dummy_voice(output_format))

        return GeneratedAudio(
            provider=self.name,
            request_id=request.request_id,
            language_code=request.language_code,
            language_name=request.language_name,
            expected_text=request.expected_text,
            filename_text=request.filename_text,
            output_format=output_format,
            raw_path=output_path,
            raw_extension=output_format,
            latency_seconds=time.perf_counter() - started_at,
            success=True,
        )


StaticTTSProvider.register()
