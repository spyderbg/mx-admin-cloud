"""Load source text units from CSV, XLSX, and TXT files for translation."""

from __future__ import annotations

import csv
from pathlib import Path

from openpyxl import load_workbook

from src.core.languages import normalize_language_code
from src.services.text_translation.models import TranslationSourceWord


def load_words(
    input_path: Path,
    *,
    source_language: str,
    target_language: str,
) -> list[TranslationSourceWord]:
    """Load text segments that still need translation (source lang ≠ target lang)."""
    suffix = input_path.suffix.lower()
    if suffix == ".txt":
        return _load_from_txt(input_path, source_language=source_language, target_language=target_language)
    if suffix == ".xlsx":
        return _load_from_xlsx(input_path, source_language=source_language, target_language=target_language)
    return _load_from_csv(input_path, source_language=source_language, target_language=target_language)


def _needs_translation(source_lang: str, target_lang: str) -> bool:
    return normalize_language_code(source_lang) != normalize_language_code(target_lang)


def _word(
    *,
    source_file: Path,
    source_type: str,
    row_index: int,
    column_index: int | None,
    text: str,
    language: str,
    target_language: str,
    filename_text: str | None = None,
    expected_text: str | None = None,
) -> TranslationSourceWord | None:
    normalized_code = normalize_language_code(language)
    if not _needs_translation(normalized_code, target_language):
        return None
    cleaned_text = text.strip()
    file_text = (filename_text or cleaned_text).strip()
    return TranslationSourceWord(
        text=cleaned_text,
        source_language_code=normalized_code,
        filename_text=file_text,
        expected_text=(expected_text or cleaned_text).strip(),
        row_index=row_index,
        column_index=column_index,
        source_file=str(source_file),
        source_type=source_type,
    )


def _load_from_txt(
    input_path: Path,
    *,
    source_language: str,
    target_language: str,
) -> list[TranslationSourceWord]:
    words: list[TranslationSourceWord] = []
    with input_path.open("r", encoding="utf-8") as handle:
        for row_index, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            item = _word(
                source_file=input_path,
                source_type="txt",
                row_index=row_index,
                column_index=None,
                text=text,
                language=source_language,
                target_language=target_language,
            )
            if item is not None:
                words.append(item)
    return words


def _load_from_csv(
    input_path: Path,
    *,
    source_language: str,
    target_language: str,
) -> list[TranslationSourceWord]:
    # pylint: disable=too-many-locals
    words: list[TranslationSourceWord] = []
    with input_path.open("r", encoding="utf-8", newline="") as handle:
        sample = handle.read(2048)
        handle.seek(0)
        try:
            has_header = csv.Sniffer().has_header(sample) if sample.strip() else False
        except csv.Error:
            has_header = False
        if has_header:
            reader = csv.DictReader(handle)
            for row_index, row in enumerate(reader, start=2):
                text = (row.get("text") or row.get("word") or row.get("value") or "").strip()
                if not text:
                    continue
                language = row.get("language") or row.get("lang") or source_language
                filename_text = row.get("filename_text") or row.get("filename") or text
                expected = (row.get("expected_text") or text).strip()
                item = _word(
                    source_file=input_path,
                    source_type="csv",
                    row_index=row_index,
                    column_index=None,
                    text=text,
                    language=language,
                    target_language=target_language,
                    filename_text=filename_text,
                    expected_text=expected,
                )
                if item is not None:
                    words.append(item)
            return words

        handle.seek(0)
        reader = csv.reader(handle)
        for row_index, row in enumerate(reader, start=1):
            for column_index, cell in enumerate(row, start=1):
                text = cell.strip()
                if not text:
                    continue
                item = _word(
                    source_file=input_path,
                    source_type="csv",
                    row_index=row_index,
                    column_index=column_index,
                    text=text,
                    language=source_language,
                    target_language=target_language,
                )
                if item is not None:
                    words.append(item)
    return words


def _load_from_xlsx(  # pylint: disable=too-many-locals
    input_path: Path,
    *,
    source_language: str,
    target_language: str,
) -> list[TranslationSourceWord]:
    workbook = load_workbook(filename=input_path, read_only=True, data_only=True)
    sheet = workbook.active

    rows = list(sheet.iter_rows(values_only=True))
    if not rows:
        workbook.close()
        return []

    header_languages = [
        normalize_language_code(str(value).strip(), source_language) if value else source_language
        for value in rows[0]
    ]
    words: list[TranslationSourceWord] = []

    for row_index, row in enumerate(rows[1:], start=2):
        english_text = ""
        for column_index, cell in enumerate(row, start=1):
            if not cell:
                continue
            text = str(cell).strip()
            if not text:
                continue
            if header_languages[column_index - 1] == "en":
                english_text = text

        for column_index, cell in enumerate(row, start=1):
            if not cell:
                continue
            text = str(cell).strip()
            if not text:
                continue
            language = header_languages[column_index - 1]
            filename_text = english_text or text
            item = _word(
                source_file=input_path,
                source_type="xlsx",
                row_index=row_index,
                column_index=column_index,
                text=text,
                language=language,
                target_language=target_language,
                filename_text=filename_text,
            )
            if item is not None:
                words.append(item)

    workbook.close()
    return words
