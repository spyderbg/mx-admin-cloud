"""Language helpers shared across loaders, configs, and outputs."""

from __future__ import annotations

import re

LANG_MAP = {
    "en": "English",
    "de": "German",
    "fr": "French",
    "it": "Italian",
    "es": "Spanish",
    "pl": "Polish",
    "uk": "Ukrainian",
    "ro": "Romanian",
    "nl": "Dutch",
    "bg": "Bulgarian",
}

LANG_NAME_TO_CODE = {value.lower(): key for key, value in LANG_MAP.items()}

LANG_TO_BCP47: dict[str, str] = {
    "en": "en-US",
    "de": "de-DE",
    "fr": "fr-FR",
    "it": "it-IT",
    "es": "es-ES",
    "pl": "pl-PL",
    "uk": "uk-UA",
    "ro": "ro-RO",
    "nl": "nl-NL",
    "bg": "bg-BG",
}


def to_bcp47(code: str) -> str:
    """Convert a language code to a canonical BCP-47 locale tag.

    Surrounding whitespace is ignored and both ``-`` and ``_`` are accepted
    as separators. A bare known two-letter code maps to its default locale
    from ``LANG_TO_BCP47`` (``"bg"`` -> ``"bg-BG"``). Any other well-formed
    tag is returned in the canonical letter case of RFC 5646 section 2.1.1
    with the same subtags in the same order (``"zh_hant_tw"`` ->
    ``"zh-Hant-TW"``). A malformed value is returned trimmed and otherwise
    unchanged. Never raises for a ``str`` argument.
    """
    stripped = code.strip()
    subtags = re.split(r"[-_]", stripped)
    well_formed = (
        bool(stripped)
        and all(re.fullmatch(r"[A-Za-z0-9]{1,8}", subtag) for subtag in subtags)
        and re.fullmatch(r"[A-Za-z]{2,3}", subtags[0]) is not None
    )
    if not well_formed:
        return stripped

    primary = subtags[0].lower()
    if len(subtags) == 1 and len(primary) == 2 and primary in LANG_TO_BCP47:
        return LANG_TO_BCP47[primary]

    canonical = [primary]
    after_singleton = False
    for subtag in subtags[1:]:
        if len(subtag) == 1:
            after_singleton = True
        if after_singleton:
            canonical.append(subtag.lower())
        elif re.fullmatch(r"[A-Za-z]{2}", subtag):
            canonical.append(subtag.upper())
        elif re.fullmatch(r"[A-Za-z]{4}", subtag):
            canonical.append(subtag[0].upper() + subtag[1:].lower())
        else:
            canonical.append(subtag.lower())
    return "-".join(canonical)


def normalize_language_code(language: str | None, default: str = "en") -> str:
    """Return a normalized ISO-like lower-case language code."""
    if not language:
        return default.lower()

    value = language.strip().lower()
    if not value:
        return default.lower()

    if value in LANG_MAP:
        return value

    if value in LANG_NAME_TO_CODE:
        return LANG_NAME_TO_CODE[value]

    return value


def language_name(language: str | None, default: str = "en") -> str:
    """Return a friendly language name for a language code or name."""
    code = normalize_language_code(language, default=default)
    return LANG_MAP.get(code, code.upper())
