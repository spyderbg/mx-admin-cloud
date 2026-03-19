"""Utilities for structured outbound request logging."""

from __future__ import annotations

import importlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..output_paths import requests_log_path as default_requests_log_path

try:
    _structlog = importlib.import_module("structlog")
except Exception:
    _structlog = None

_requests_logger = (
    _structlog.get_logger("services.requests")
    if _structlog is not None
    else logging.getLogger("services.requests")
)

_REQUEST_LOG_REL_PATH = "results/requests.log"
_REQUEST_FIELDS: dict[str, bool] = {
    "provider": True,
    "translator": True,
    "model": True,
    "actor": True,
    "voice_id": True,
    "language": True,
    "input": True,
    "response_duration": True,
    "timestamp": True,
}


def _enabled(field_name: str) -> bool:
    return bool(_REQUEST_FIELDS.get(field_name, True))


def _apply_common_payload_fields(
    *,
    payload: dict[str, Any],
    model: str | None,
    actor: str | None,
    voice_id: str | None,
    language: str | None,
    input: Any | None,
    response_duration: float | None,
) -> None:
    if model is not None and _enabled("model"):
        payload["model"] = model
    if actor is not None and _enabled("actor"):
        payload["actor"] = actor
    if voice_id is not None and _enabled("voice_id"):
        payload["voice_id"] = voice_id
    if language is not None and _enabled("language"):
        payload["language"] = language
    if input is not None and _enabled("input"):
        payload["input"] = input
    if response_duration is not None and _enabled("response_duration"):
        payload["response_duration"] = round(float(response_duration), 6)
    if _enabled("timestamp"):
        payload["timestamp"] = datetime.now(timezone.utc).isoformat()


def _emit_request(event_name: str, payload: dict[str, Any]) -> None:
    if _structlog is not None:
        _requests_logger.info(event_name, **payload)
        return
    _requests_logger.info("%s %s", event_name, json.dumps(payload, ensure_ascii=False))


def configure_request_logger(config: dict[str, Any] | None) -> None:
    """Configure request log behavior from config/logging.json."""
    global _REQUEST_LOG_REL_PATH
    global _REQUEST_FIELDS

    if not isinstance(config, dict):
        return

    path_value = config.get("path")
    if isinstance(path_value, str) and path_value.strip():
        rel_path = Path(path_value.strip())
        if rel_path.is_absolute():
            raise ValueError("request_logger.path must be relative to the output directory.")
        _REQUEST_LOG_REL_PATH = rel_path.as_posix()

    fields = config.get("fields")
    if isinstance(fields, dict):
        for key, value in fields.items():
            if key in _REQUEST_FIELDS:
                _REQUEST_FIELDS[key] = bool(value)


def resolve_requests_log_path(base_dir: Path) -> Path:
    rel_path = Path(_REQUEST_LOG_REL_PATH)
    if rel_path.is_absolute():
        return rel_path
    if _REQUEST_LOG_REL_PATH == "results/requests.log":
        return default_requests_log_path(base_dir)
    return base_dir / rel_path


def ensure_requests_log(base_dir: Path) -> Path:
    log_path = resolve_requests_log_path(base_dir)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.touch(exist_ok=True)
    return log_path


def log_provider_request(
    *,
    base_dir: Path,
    provider: str | None = None,
    model: str | None = None,
    actor: str | None = None,
    voice_id: str | None = None,
    language: str | None = None,
    input: Any | None = None,
    response_duration: float | None = None,
) -> None:
    ensure_requests_log(base_dir)
    payload: dict[str, Any] = {}
    if provider is not None and _enabled("provider"):
        payload["provider"] = provider
    _apply_common_payload_fields(
        payload=payload,
        model=model,
        actor=actor,
        voice_id=voice_id,
        language=language,
        input=input,
        response_duration=response_duration,
    )

    _emit_request("provider_request", payload)


def log_translator_request(
    *,
    base_dir: Path,
    translator: str | None = None,
    model: str | None = None,
    actor: str | None = None,
    voice_id: str | None = None,
    language: str | None = None,
    input: Any | None = None,
    response_duration: float | None = None,
) -> None:
    ensure_requests_log(base_dir)
    payload: dict[str, Any] = {}
    if translator is not None and _enabled("translator"):
        payload["translator"] = translator
    _apply_common_payload_fields(
        payload=payload,
        model=model,
        actor=actor,
        voice_id=voice_id,
        language=language,
        input=input,
        response_duration=response_duration,
    )

    _emit_request("translator_request", payload)
