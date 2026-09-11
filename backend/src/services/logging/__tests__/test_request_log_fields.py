"""Unit tests for request-log field filtering in ``StructLogProvider``."""

# pylint: disable=missing-function-docstring,protected-access

from __future__ import annotations

from pathlib import Path

from src.services.logging.providers.struct_log import StructLogProvider


def _make_provider(tmp_path: Path, fields: dict[str, bool] | None = None) -> StructLogProvider:
    config: dict = {}
    if fields is not None:
        config["request_log"] = {"fields": fields}
    provider = StructLogProvider(
        name="requests",
        service_name="logging",
        provider_name="struct_log",
        provider_category="requests",
        enabled=True,
        priority=1,
        config=config,
        config_path=tmp_path / "requests.json",
        base_dir=tmp_path,
    )
    provider._apply_request_log_config(config.get("request_log", {}))
    return provider


def test_legacy_tts_payload_is_unchanged(tmp_path: Path) -> None:
    provider = _make_provider(tmp_path)

    payload = provider._filtered_request_log_fields(
        "provider_request",
        {
            "provider": "openai",
            "model": "tts-1-hd",
            "actor": "alloy",
            "voice_id": "",
            "language": "en",
            "input_data": "apple",
            "response_duration": 0.1234567,
        },
    )

    assert payload["provider"] == "openai"
    assert payload["model"] == "tts-1-hd"
    assert payload["input"] == "apple"
    assert payload["response_duration"] == 0.123457
    assert "timestamp" in payload
    # No image-only keys leak into a TTS record.
    assert not {"request_kind", "size", "quality", "cost_estimate", "status", "error", "attempts"} & set(payload)


def test_image_fields_pass_through(tmp_path: Path) -> None:
    provider = _make_provider(tmp_path)

    payload = provider._filtered_request_log_fields(
        "provider_request",
        {
            "provider": "openai",
            "model": "gpt-image-1-mini",
            "language": "en",
            "input_data": "a friendly cat",
            "response_duration": 2.5,
            "request_kind": "image",
            "size": "1024x1024",
            "quality": "low",
            "cost_estimate": 0.0051234567,
            "status": "success",
            "attempts": 2,
        },
    )

    assert payload["request_kind"] == "image"
    assert payload["size"] == "1024x1024"
    assert payload["quality"] == "low"
    assert payload["cost_estimate"] == 0.005123
    assert payload["status"] == "success"
    assert payload["attempts"] == 2
    assert "error" not in payload


def test_disabled_image_field_is_dropped(tmp_path: Path) -> None:
    provider = _make_provider(tmp_path, {"cost_estimate": False})

    payload = provider._filtered_request_log_fields(
        "provider_request",
        {"provider": "openai", "cost_estimate": 0.01, "status": "error", "error": "boom"},
    )

    assert "cost_estimate" not in payload
    assert payload["status"] == "error"
    assert payload["error"] == "boom"
