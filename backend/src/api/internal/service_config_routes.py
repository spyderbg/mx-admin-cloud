"""Internal endpoints for in-memory service config registration."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile  # pylint: disable=import-error

from src.api.dependencies import get_registry_builder, require_internal
from src.api.internal.schemas.service_config import ApplyConfigRequest, ApplyConfigResponse
from src.services.service_registry import ServiceRegistryBuilder

router = APIRouter(prefix="/internal/services/configs", tags=["internal-service-configs"])


@router.post("/apply", response_model=ApplyConfigResponse)
async def apply_config_payload(
        body: ApplyConfigRequest,
        _user: dict = Depends(require_internal),
        builder: ServiceRegistryBuilder = Depends(get_registry_builder),
) -> ApplyConfigResponse:
    """Validate and apply one service config payload in memory."""
    result = builder.apply_runtime_payload(body.payload, source="<runtime-upload>")
    if not result.accepted:
        raise HTTPException(status_code=422, detail=result.error or "Config validation failed.")
    return ApplyConfigResponse(
        config_name=result.config_name,
        accepted=result.accepted,
        error=result.error,
    )


@router.post("/upload", response_model=ApplyConfigResponse)
async def upload_config_file(
        file: UploadFile = File(...),
        _user: dict = Depends(require_internal),
        builder: ServiceRegistryBuilder = Depends(get_registry_builder),
) -> ApplyConfigResponse:
    """Validate and apply uploaded config content in memory without persisting."""
    try:
        payload_raw = await file.read()
        payload = json.loads(payload_raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid JSON file: {exc}") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="Uploaded JSON root must be an object.")

    source = f"<runtime-upload:{file.filename or 'unknown.json'}>"
    result = builder.apply_runtime_payload(payload, source=source)
    if not result.accepted:
        raise HTTPException(status_code=422, detail=result.error or "Config validation failed.")
    return ApplyConfigResponse(
        config_name=result.config_name,
        accepted=result.accepted,
        error=result.error,
    )
