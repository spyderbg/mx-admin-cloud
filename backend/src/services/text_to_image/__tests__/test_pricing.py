"""Unit tests for the configured price table."""

# pylint: disable=missing-function-docstring

from __future__ import annotations

from src.services.text_to_image.pricing import PriceTable

_PRICING = {
    "gpt-image-1-mini": {"1024x1024|low": 0.005, "1024x1024": 0.009, "high": 0.036, "default": 0.011},
    "dall-e-3": 0.04,
    "default": 0.02,
}


def test_size_and_quality_key_wins() -> None:
    table = PriceTable.from_config(_PRICING, None)
    assert table.estimate("gpt-image-1-mini", "1024x1024", "low") == 0.005


def test_falls_back_to_size_then_quality_then_default() -> None:
    table = PriceTable.from_config(_PRICING, None)
    assert table.estimate("gpt-image-1-mini", "1024x1024", "medium") == 0.009
    assert table.estimate("gpt-image-1-mini", "512x512", "high") == 0.036
    assert table.estimate("gpt-image-1-mini", "512x512", "medium") == 0.011


def test_scalar_model_price() -> None:
    table = PriceTable.from_config(_PRICING, None)
    assert table.estimate("dall-e-3", "1024x1024", "hd") == 0.04


def test_unknown_model_uses_table_default() -> None:
    table = PriceTable.from_config(_PRICING, None)
    assert table.estimate("some-new-model", "1024x1024", "low") == 0.02


def test_cost_per_image_fallback() -> None:
    table = PriceTable.from_config(None, 0.5)
    assert table.estimate("anything", None, None) == 0.5


def test_unpriced_model_returns_none() -> None:
    table = PriceTable.from_config({"dall-e-3": 0.04}, None)
    assert table.estimate("gpt-image-1", "1024x1024", "low") is None
    assert table.estimate(None, None, None) is None


def test_invalid_entries_are_ignored() -> None:
    table = PriceTable.from_config(
        {"m": {"low": "cheap", "high": 0.2}, "broken": "free", "": 1.0},
        "not-a-number",
    )
    assert table.estimate("m", None, "low") is None
    assert table.estimate("m", None, "high") == 0.2
    assert table.estimate("broken", None, None) is None
    assert table.cost_per_image is None


def test_as_dict_includes_cost_per_image() -> None:
    table = PriceTable.from_config({"m": 0.1}, 0.3)
    assert table.as_dict() == {"m": 0.1, "cost_per_image": 0.3}
