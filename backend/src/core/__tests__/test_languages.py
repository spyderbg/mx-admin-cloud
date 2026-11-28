"""Unit tests for the BCP-47 canonicalization in src.core.languages."""

from __future__ import annotations

import pytest

from src.core.languages import LANG_TO_BCP47, to_bcp47


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("en_US", "en-US"),
        (" de-de ", "de-DE"),
        ("zh-hant-tw", "zh-Hant-TW"),
        (" XX ", "xx"),
        ("bg", "bg-BG"),
        (" EN ", "en-US"),
        ("en_us", "en-US"),
        ("sr_latn_rs", "sr-Latn-RS"),
        ("es-419", "es-419"),
        ("", ""),
        ("en--us", "en--us"),
        ("english", "english"),
    ],
)
def test_issue_examples(code: str, expected: str) -> None:
    """Every example from the Issue table and rules."""
    assert to_bcp47(code) == expected


@pytest.mark.parametrize("short_code", sorted(LANG_TO_BCP47))
def test_bare_known_code_maps_through_table_in_any_case(short_code: str) -> None:
    """A bare known two-letter code maps through LANG_TO_BCP47."""
    expected = LANG_TO_BCP47[short_code]
    assert to_bcp47(short_code) == expected
    assert to_bcp47(short_code.upper()) == expected
    assert to_bcp47(f"  {short_code.title()}\t") == expected


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("XX", "xx"),
        ("ZZZ", "zzz"),
        ("Qaa", "qaa"),
    ],
)
def test_unknown_bare_code_is_lowercased(code: str, expected: str) -> None:
    """An unknown bare primary subtag is lowercased."""
    assert to_bcp47(code) == expected


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("EN-us", "en-US"),
        ("EN_GB", "en-GB"),
        ("zh_HANT", "zh-Hant"),
        ("SR-LATN", "sr-Latn"),
        ("ES_419", "es-419"),
        ("de-1996", "de-1996"),
        ("SL-ROZAJ", "sl-rozaj"),
        ("QQQ-Abc", "qqq-abc"),
    ],
)
def test_canonical_case_before_singleton(code: str, expected: str) -> None:
    """Before a singleton: region upper, script title, others lower."""
    assert to_bcp47(code) == expected


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("en-ca-X-CA", "en-CA-x-ca"),
        ("EN-X-PRIV-AB", "en-x-priv-ab"),
        ("de-DE-U-CO-PHONEBK", "de-DE-u-co-phonebk"),
        ("sr_latn_RS_x_LATN", "sr-Latn-RS-x-latn"),
    ],
)
def test_subtags_from_singleton_onward_are_lowercase(code: str, expected: str) -> None:
    """The singleton and every later subtag are lowercase."""
    assert to_bcp47(code) == expected


def test_mixed_separators_are_normalized_to_hyphen() -> None:
    """Both separators are accepted and joined with '-'."""
    assert to_bcp47("zh_hant-tw") == "zh-Hant-TW"


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        # empty subtags from double, leading or trailing separators
        ("en__us", "en__us"),
        ("en-_us", "en-_us"),
        ("-en-us", "-en-us"),
        ("_en", "_en"),
        ("en-us-", "en-us-"),
        (" en_ ", "en_"),
        ("-", "-"),
        # subtag longer than 8 characters
        ("en-abcdefghi", "en-abcdefghi"),
        (" de-DE-toolongsub ", "de-DE-toolongsub"),
        # non-ASCII characters are kept unchanged
        (" en-ÜS ", "en-ÜS"),
        ("dé", "dé"),
        ("zh-ḥant", "zh-ḥant"),
        ("en-١٢٣", "en-١٢٣"),
        # bad first subtag: contains a digit, wrong length
        ("e1-US", "e1-US"),
        ("12-us", "12-us"),
        ("e", "e"),
        ("x-Priv", "x-Priv"),
        ("abcd-US", "abcd-US"),
        # other characters
        ("en US", "en US"),
        ("en.US", "en.US"),
    ],
)
def test_malformed_input_is_trimmed_and_otherwise_unchanged(
    code: str, expected: str
) -> None:
    """Malformed input comes back trimmed and otherwise unchanged."""
    assert to_bcp47(code) == expected


@pytest.mark.parametrize("code", ["   ", "\t", "\n \r"])
def test_whitespace_only_input_returns_empty_string(code: str) -> None:
    """Whitespace-only input trims to the empty string."""
    assert to_bcp47(code) == ""


@pytest.mark.parametrize(
    "code",
    ["", " ", "--", "__", "a" * 100, "en-" + "x" * 50, "\x00", "\U0001f600", "en-" + chr(0x200B)],
)
def test_never_raises_and_returns_str(code: str) -> None:
    """Odd inputs never raise and always return a str."""
    assert isinstance(to_bcp47(code), str)
