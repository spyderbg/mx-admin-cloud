"""Internal optimizer-configuration and optimization endpoints.

Exposes audio post-processing (optimizer) configs loaded from ``backend/config/voice_optimizers/``.
All endpoints require a JWT with role ``internal`` or ``admin``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException  # pylint: disable=import-error
from pydantic import BaseModel  # pylint: disable=import-error

from src.api.dependencies import get_registry, require_internal
from src.core.config import settings
from src.services.service_registry import ServiceRegistry

router = APIRouter(prefix="/internal", tags=["internal-optimizers"])


class OptimizeRequest(BaseModel):
    """Body for a single optimization request."""
    raw_path: str
    output_path: str


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
    from src.services.config_loader import load_service_configs  # pylint: disable=import-outside-toplevel
    from src.services.models import ServiceProvider  # pylint: disable=import-outside-toplevel
    configs = load_service_configs(settings.config_path / "voice_optimizers", ServiceProvider)
    configs.sort(key=lambda c: c.priority)
    return [
        {
            "service_type": c.service_type,
            "name": c.name,
            "enabled": c.enabled,
            "priority": c.priority,
            "config_path": str(c.config_path),
        }
        for c in configs
    ]


@router.post(
    "/optimizers/{optimizer}/optimize",
    summary="Optimize a single audio file",
    description="Run the named optimizer on the given raw audio file.",
)
async def optimize_single(
    optimizer: str,
    body: OptimizeRequest,
    _user: dict = Depends(require_internal),
    registry: ServiceRegistry = Depends(get_registry),
) -> dict[str, Any]:
    """Optimize an audio file using the named optimizer."""
    matched = [opt for opt in registry.get("optimizers") if opt.name == optimizer]
    if not matched:
        raise HTTPException(status_code=404, detail=f"Optimizer '{optimizer}' not found or not active.")
    opt = matched[0]

    raw = Path(body.raw_path)
    out = Path(body.output_path)
    if not raw.exists():
        raise HTTPException(status_code=400, detail=f"Raw file not found: {body.raw_path}")

    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        await opt.optimize(raw, out)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {"success": True, "output_path": str(out)}
