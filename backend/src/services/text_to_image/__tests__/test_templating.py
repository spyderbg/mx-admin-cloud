"""Unit tests for typed placeholder rendering."""

# pylint: disable=missing-function-docstring

from __future__ import annotations

from src.services.text_to_image.providers.comfyui_image.templating import render_template

_CONTEXT = {
    "prompt": "a friendly cat",
    "seed": 12345,
    "cfg": 7.5,
    "steps": 20,
    "flag": True,
    "api_key": "secret",
}


def test_exact_placeholder_keeps_type() -> None:
    assert render_template("{seed}", _CONTEXT) == 12345
    assert isinstance(render_template("{seed}", _CONTEXT), int)
    assert render_template("{cfg}", _CONTEXT) == 7.5
    assert render_template("{flag}", _CONTEXT) is True


def test_embedded_placeholder_becomes_string() -> None:
    assert render_template("Bearer {api_key}", _CONTEXT) == "Bearer secret"
    assert render_template("seed={seed}", _CONTEXT) == "seed=12345"


def test_nested_structures_are_rendered() -> None:
    template = {
        "3": {"inputs": {"seed": "{seed}", "steps": "{steps}", "cfg": "{cfg}", "model": ["4", 0]}},
        "6": {"inputs": {"text": "{prompt}"}},
    }
    rendered = render_template(template, _CONTEXT)
    assert rendered["3"]["inputs"]["seed"] == 12345
    assert rendered["3"]["inputs"]["cfg"] == 7.5
    assert rendered["3"]["inputs"]["model"] == ["4", 0]
    assert rendered["6"]["inputs"]["text"] == "a friendly cat"


def test_unknown_placeholder_is_preserved() -> None:
    assert render_template("{unknown}", _CONTEXT) == "{unknown}"
    assert render_template("x={unknown}", _CONTEXT) == "x={unknown}"


def test_malformed_braces_are_left_alone() -> None:
    assert render_template("{not closed", _CONTEXT) == "{not closed"
    assert render_template("100% {", _CONTEXT) == "100% {"


def test_non_string_values_pass_through() -> None:
    assert render_template(42, _CONTEXT) == 42
    assert render_template(None, _CONTEXT) is None
    assert render_template([1, "{seed}", 2.5], _CONTEXT) == [1, 12345, 2.5]
