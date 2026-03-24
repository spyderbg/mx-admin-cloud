"""Internal optimizer-configuration endpoints.

Exposes audio post-processing (optimizer) configs loaded from ``backend/config/optimizers/``.
All endpoints require a JWT with role ``internal`` or ``admin``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends  # pylint: disable=import-error

from src.api.dependencies import require_internal
from src.core.config import settings

router = APIRouter(prefix="/internal/voice", tags=["internal-optimizers"])


@router.get(
    "/optimizers",
    summary="List audio optimizer configurations",
    description=(
        "Return all configured audio post-processing optimizers with their service type, "
        "activation state, priority, and config path."
    ),
)
async def list_optimizers(
    _user: dict = Depends(require_internal),
) -> list[dict[str, Any]]:
    """Return configured optimizers with service metadata."""
    from src.core.config_loader import load_optimizer_configs  # pylint: disable=import-outside-toplevel
    configs = load_optimizer_configs(
        base_dir=settings.BASE_DIR,
        config_dir=settings.config_path,
    )
    return [
        {
            "service_type": c.type,
            "name": c.name,
            "active": c.active,
            "priority": c.priority,
            "config_path": str(c.config_path),
        }
        for c in configs
    ]
