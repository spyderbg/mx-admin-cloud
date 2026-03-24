"""Internal generator-configuration endpoints.

Exposes TTS provider (generator) configs loaded from ``backend/config/generators/``.
All endpoints require a JWT with role ``internal`` or ``admin``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends  # pylint: disable=import-error

from src.api.dependencies import require_internal
from src.core.config import settings

router = APIRouter(prefix="/internal/voice", tags=["internal-generators"])


@router.get(
    "/generators",
    summary="List TTS generator configurations",
    description=(
        "Return all configured TTS providers (generators) with their activation state, "
        "config path, and generation settings. Language lists are omitted from the response."
    ),
)
async def list_generators(
    _user: dict = Depends(require_internal),
) -> list[dict[str, Any]]:
    """Return configured generators with activation states."""
    from src.core.config_loader import load_provider_configs  # pylint: disable=import-outside-toplevel
    configs = load_provider_configs(
        base_dir=settings.BASE_DIR,
        config_dir=settings.config_path,
    )
    return [
        {
            "name": c.name,
            "active": c.generate.get("active", False),
            "config_path": str(c.config_path),
            "generate": {k: v for k, v in c.generate.items() if k != "languages"},
            "evaluate_active": c.evaluate.get("active", False),
        }
        for c in configs
    ]
