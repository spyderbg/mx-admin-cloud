"""Internal evaluator-configuration endpoints.

Exposes STT evaluator configs derived from generator/provider configs in ``backend/config/generators/``.
All endpoints require a JWT with role ``internal`` or ``admin``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends  # pylint: disable=import-error

from src.api.dependencies import require_internal
from src.core.config import settings

router = APIRouter(prefix="/internal/voice", tags=["internal-evaluators"])


@router.get(
    "/evaluators",
    summary="List STT evaluator configurations",
    description=(
        "Return all configured STT evaluators (derived from generator/provider configs) "
        "with their activation state and config path."
    ),
)
async def list_evaluators(
    _user: dict = Depends(require_internal),
) -> list[dict[str, Any]]:
    """Return configured evaluators (from generators/providers) with activation states."""
    from src.core.config_loader import load_provider_configs  # pylint: disable=import-outside-toplevel
    configs = load_provider_configs(
        base_dir=settings.BASE_DIR,
        config_dir=settings.config_path,
    )
    return [
        {
            "name": c.name,
            "active": c.evaluate.get("active", False),
            "config_path": str(c.config_path),
        }
        for c in configs
    ]
