"""Load structured requests from CSV, XLSX, and TXT files."""

from __future__ import annotations

import csv
from pathlib import Path

from openpyxl import load_workbook

from src.core.config import settings
from src.core.languages import language_name, normalize_language_code
from src.core.models import TTSRequest


def load_requests(input_path: Path, default_language: str) -> list[TTSRequest]:
    """Load structured requests from CSV, XLSX, and TXT files."""
    suffix = input_path.suffix.lower()
    if suffix == ".txt":
        return _load_from_txt(input_path, default_language)
    if suffix == ".xlsx":
        return _load_from_xlsx(input_path, default_language)
    return _load_from_csv(input_path, default_language)


def _request(
    *,
    source_file: Path,
    source_type: str,
    row_index: int,
    column_index: int | None,
    text: str,
    language: str,
    filename_text: str | None = None,
) -> TTSRequest:
    normalized_code = normalize_language_code(language)
    cleaned_text = text.strip()
    file_text = (filename_text or cleaned_text).strip()
    request_id = (
        f"{source_type}-{row_index}-{column_index or 0}-{normalized_code}-"
        f"{settings.slugify_filename(file_text, 'item')}"
    )
    return TTSRequest(
        request_id=request_id,
        text=cleaned_text,
        language_code=normalized_code,
        language_name=language_name(normalized_code),
        filename_text=file_text,
        expected_text=cleaned_text,
        source_file=str(source_file),
        source_type=source_type,
        row_index=row_index,
        column_index=column_index,
    )


def _load_from_txt(input_path: Path, default_language: str) -> list[TTSRequest]:
    requests: list[TTSRequest] = []
    with input_path.open("r", encoding="utf-8") as handle:
        for row_index, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            requests.append(
                _request(
                    source_file=input_path,
                    source_type="txt",
                    row_index=row_index,
                    column_index=None,
                    text=text,
                    language=default_language,
                )
            )
    return requests


def _load_from_csv(input_path: Path, default_language: str) -> list[TTSRequest]:
    requests: list[TTSRequest] = []
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
                language = row.get("language") or row.get("lang") or default_language
                filename_text = row.get("filename_text") or row.get("filename") or text
                expected = (row.get("expected_text") or text).strip()
                request = _request(
                    source_file=input_path,
                    source_type="csv",
                    row_index=row_index,
                    column_index=None,
                    text=text,
                    language=language,
                    filename_text=filename_text,
                )
                request.expected_text = expected
                requests.append(request)
            return requests

        handle.seek(0)
        reader = csv.reader(handle)
        for row_index, row in enumerate(reader, start=1):
            for column_index, cell in enumerate(row, start=1):
                text = cell.strip()
                if not text:
                    continue
                requests.append(
                    _request(
                        source_file=input_path,
                        source_type="csv",
                        row_index=row_index,
                        column_index=column_index,
                        text=text,
                        language=default_language,
                    )
                )
    return requests


def _load_from_xlsx(input_path: Path, default_language: str) -> list[TTSRequest]:
    workbook = load_workbook(filename=input_path, read_only=True, data_only=True)
    sheet = workbook.active

    rows = list(sheet.iter_rows(values_only=True))
    if not rows:
        return []

    header_languages = [normalize_language_code(str(value).strip(), default_language) if value else default_language for value in rows[0]]
    requests: list[TTSRequest] = []

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
            requests.append(
                _request(
                    source_file=input_path,
                    source_type="xlsx",
                    row_index=row_index,
                    column_index=column_index,
                    text=text,
                    language=language,
                    filename_text=filename_text,
                )
            )

    workbook.close()
    return requests
