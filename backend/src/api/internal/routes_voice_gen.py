"""Internal voice-generation API endpoints.

All endpoints require a JWT with role 'internal' or 'admin'
via the ``require_internal`` dependency.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile  # pylint: disable=import-error
from pydantic import BaseModel  # pylint: disable=import-error

from src.api.dependencies import get_job_store, get_registry, require_internal
from src.core.config import settings
from src.core.job_store import JobStore
from src.core.pipeline import run_pipeline
from src.core.registry import ServiceRegistry

router = APIRouter(prefix="/internal/voice", tags=["internal"])

ALLOWED_EXTENSIONS = {".txt", ".csv", ".xlsx"}


class GenerateResponse(BaseModel):
    """Response body for a voice generation job."""
    job_id: str
    status: str
    message: str


class JobStatusResponse(BaseModel):
    """Response body for a voice generation job status."""
    job_id: str
    status: str
    result: dict[str, Any] | None = None
    error: str | None = None


async def _save_upload(upload: UploadFile, job_id: str) -> Path:
    """Persist the uploaded file to OUTPUT_DIR, preserving its extension."""
    suffix = Path(upload.filename or "input.txt").suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{suffix}'. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )
    output_dir = settings.output_path
    output_dir.mkdir(parents=True, exist_ok=True)
    dest = output_dir / f"_input_{job_id}{suffix}"
    content = await upload.read()
    dest.write_bytes(content)
    return dest


async def _run_job(
    job_id: str,
    input_path: Path,
    language: str,
    output_format: str,
    max_concurrency: int,
    registry: ServiceRegistry,
    job_store: JobStore,
) -> None:
    await job_store.update(job_id, status="running")
    try:
        summary = await run_pipeline(
            registry=registry,
            input_path=input_path,
            default_language=language,
            output_format=output_format,
            max_concurrency=max_concurrency,
            output_dir=settings.output_path,
        )
        await job_store.update(job_id, status="completed", result=summary.to_dict())
        input_path.unlink(missing_ok=True)
    except Exception as exc:
        await job_store.update(job_id, status="failed", error=str(exc))


@router.post("/generate", response_model=GenerateResponse)
async def generate(
    file: UploadFile = File(...),
    language: str = Form("en"),
    output_format: str = Form("mp3"),
    max_concurrency: int = Form(5),
    _user: dict = Depends(require_internal),
    registry: ServiceRegistry = Depends(get_registry),
    job_store: JobStore = Depends(get_job_store),
) -> GenerateResponse:
    """Upload an input file and trigger the TTS pipeline in background."""
    job_id = str(uuid.uuid4())
    input_path = await _save_upload(file, job_id)
    await job_store.create(job_id)
    asyncio.create_task(
        _run_job(job_id, input_path, language, output_format, max_concurrency, registry, job_store)
    )
    return GenerateResponse(job_id=job_id, status="pending", message="Pipeline started.")


@router.get("/generate/{job_id}/status", response_model=JobStatusResponse)
async def generate_status(
    job_id: str,
    _user: dict = Depends(require_internal),
    job_store: JobStore = Depends(get_job_store),
) -> JobStatusResponse:
    """Return job status and result when complete."""
    record = await job_store.get(job_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found.")
    return JobStatusResponse(
        job_id=record.job_id,
        status=record.status,
        result=record.result,
        error=record.error,
    )


@router.post("/generate-sync")
async def generate_sync(
    file: UploadFile = File(...),
    language: str = Form("en"),
    output_format: str = Form("mp3"),
    max_concurrency: int = Form(5),
    _user: dict = Depends(require_internal),
    registry: ServiceRegistry = Depends(get_registry),
) -> dict[str, Any]:
    """Upload an input file, run the pipeline synchronously, return the result."""
    job_id = str(uuid.uuid4())
    input_path = await _save_upload(file, job_id)
    try:
        summary = await run_pipeline(
            registry=registry,
            input_path=input_path,
            default_language=language,
            output_format=output_format,
            max_concurrency=max_concurrency,
            output_dir=settings.output_path,
        )
        input_path.unlink(missing_ok=True)
        return summary.to_dict()
    except Exception as exc:
        input_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/generators")
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


@router.get("/translators")
async def list_translators(
    _user: dict = Depends(require_internal),
) -> list[dict[str, Any]]:
    """Return configured translators with service metadata."""
    from src.core.config_loader import load_translator_configs  # pylint: disable=import-outside-toplevel
    configs = load_translator_configs(
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


@router.get("/optimizers")
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


@router.get("/evaluators")
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
