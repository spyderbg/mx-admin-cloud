"""Shared config-parsing utilities.

Service-specific loaders have moved to their respective sub-modules:

- TTS providers  → ``src.services.text_to_speech.config_loader``
- Translators    → ``src.services.text_translation.config_loader``
- Optimizers     → ``src.services.optimization.config_loader``

The canonical ``boolish`` helper lives in ``src.core.utils``.
"""

from __future__ import annotations

from src.core.utils import boolish as _boolish  # noqa: F401  — backward compat alias
