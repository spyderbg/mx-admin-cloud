"""Placeholder rendering for config-driven provider payloads.

Unlike a plain ``str.format`` pass, a string that consists of exactly one
placeholder is replaced by the **typed** context value. ComfyUI graphs need real
numbers for ``seed``/``steps``/``cfg``, and JSON configs can only carry those
placeholders as strings.
"""

from __future__ import annotations

import re
from typing import Any

_EXACT_PLACEHOLDER_RE = re.compile(r"^\{([A-Za-z_][A-Za-z0-9_]*)\}$")


class _KeepMissing(dict):
    """Format mapping that leaves unknown placeholders untouched."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def render_template(value: Any, context: dict[str, Any]) -> Any:
    """Recursively render *value*, substituting ``{name}`` from *context*.

    A string that is exactly one known placeholder keeps the context value's
    type; every other string is formatted textually. Malformed braces are left
    as-is so an exported workflow can never break rendering.
    """
    if isinstance(value, dict):
        return {key: render_template(item, context) for key, item in value.items()}
    if isinstance(value, list):
        return [render_template(item, context) for item in value]
    if not isinstance(value, str):
        return value

    exact = _EXACT_PLACEHOLDER_RE.match(value)
    if exact is not None and exact.group(1) in context:
        return context[exact.group(1)]

    try:
        return value.format_map(_KeepMissing(context))
    except (ValueError, IndexError, KeyError):
        return value
