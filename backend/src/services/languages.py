"""Language helpers shared across loaders, configs, and outputs."""

from __future__ import annotations

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
    """Convert a 2-letter language code to a BCP-47 locale tag.

    Returns the input unchanged if it already contains a region subtag
    (e.g. ``en-US``) or has no known mapping.
    """
    normalized = code.strip().lower()
    if "-" in normalized or "_" in normalized:
        return code
    return LANG_TO_BCP47.get(normalized, code)


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
