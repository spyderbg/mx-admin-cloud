"""JSON merge and prune helpers for service config files."""

from __future__ import annotations

from typing import Any


def deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge *patch* into *base* and return a new dictionary."""
    merged: dict[str, Any] = {}
    for key, value in base.items():
        merged[key] = value
    for key, value in patch.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = deep_merge(existing, value)
        else:
            merged[key] = value
    return merged


def prune_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop empty/default values from a service config payload."""
    pruned = _prune_value(payload)
    if not isinstance(pruned, dict):
        return {}
    service_provider = pruned.get("service_provider")
    if isinstance(service_provider, dict) and service_provider.get("priority") == 999:
        service_provider.pop("priority", None)
    return pruned


def _prune_value(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, child in value.items():
            pruned_child = _prune_value(child)
            if _should_drop(pruned_child):
                continue
            cleaned[key] = pruned_child
        return cleaned
    if isinstance(value, list):
        cleaned_list = [_prune_value(item) for item in value]
        return [item for item in cleaned_list if not _should_drop(item)]
    return value


def _should_drop(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value == ""
    if isinstance(value, (dict, list)):
        return len(value) == 0
    return False
