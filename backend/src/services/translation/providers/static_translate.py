"""Static CSV-based translator using pre-built content directories."""

from __future__ import annotations

import csv
from pathlib import Path

from src.core.config import settings
from src.services.translation.models import TextTranslate
from src.services.translation.service import TranslatorFactory


class StaticTTSTranslate(TextTranslate):
    """Look up translations from aligned CSV files in a content directory.

    Expected layout::

        content_dir/
          clothes/              <- category
            images_en.csv       <- one word per line
            images_bg.csv       <- same line = same concept
          drinks/
            images_en.csv
            images_bg.csv
    """

    @classmethod
    def register(cls) -> None:
        TranslatorFactory.register("static_translate", cls)

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._lookup: dict[str, dict[str, dict[str, str]]] = {}
        self._load()

    def _load(self) -> None:
        content_dir = self.config.get("content_dir")
        root = Path(content_dir) if content_dir else settings.content_path
        if not root.is_absolute():
            root = self.base_dir / root

        if not root.is_dir():
            fallback_root = settings.content_path
            if not fallback_root.is_dir():
                return
            root = fallback_root

        for category_dir in sorted(root.iterdir()):
            if not category_dir.is_dir():
                continue

            lang_words: dict[str, list[str]] = {}
            for csv_path in sorted(category_dir.glob("images_*.csv")):
                lang_code = csv_path.stem.split("_", 1)[-1].lower()
                with csv_path.open("r", encoding="utf-8", newline="") as fh:
                    reader = csv.reader(fh)
                    lang_words[lang_code] = [
                        row[0].strip() for row in reader if row and row[0].strip()
                    ]

            lang_codes = list(lang_words.keys())
            for src_lang in lang_codes:
                src_dict = self._lookup.setdefault(src_lang, {})
                for idx, word in enumerate(lang_words[src_lang]):
                    target_map = src_dict.setdefault(word.lower(), {})
                    for tgt_lang in lang_codes:
                        if tgt_lang == src_lang:
                            continue
                        tgt_words = lang_words[tgt_lang]
                        if idx < len(tgt_words):
                            target_map[tgt_lang] = tgt_words[idx]

    async def translate_text(
        self,
        text: str,
        input_lang_code: str,
        target_lang_code: str,
        category: str | None = None,
    ) -> str | None:
        src = input_lang_code.lower()
        tgt = target_lang_code.lower()
        word = text.strip().lower()

        targets = self._lookup.get(src, {}).get(word)
        if targets is None:
            return None
        return targets.get(tgt)


StaticTTSTranslate.register()
