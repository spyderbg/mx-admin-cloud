"""Shared config-parsing utilities.

Service-specific loaders have moved to their respective sub-modules:

- TTS providers  → ``src.services.text_to_speech.config_loader``
- Translators    → ``src.services.translation.config_loader``
- Optimizers     → ``src.services.optimization.config_loader``
"""

from __future__ import annotations


def _boolish(value: object) -> bool:
    """Coerce a config value to bool; accepts True/False, None, and common string truths."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}
