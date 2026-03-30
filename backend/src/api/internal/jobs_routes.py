"""Internal voice-generation job endpoints.

Manages async and synchronous TTS pipeline execution and job-status polling.
All endpoints require a JWT with role ``internal`` or ``admin``.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile  # pylint: disable=import-error
from pydantic import BaseModel  # pylint: disable=import-error

from src.api.dependencies import get_internal_token, get_job_store, get_registry, require_internal
from src.core.config import settings
from src.core.job_store import JobStore
# from src.core.pipeline import run_pipeline
from src.core.registry import ServiceRegistry

router = APIRouter(prefix="/internal", tags=["internal-jobs"])

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


# async def _run_job(
#     job_id: str,
#     input_path: Path,
#     language: str,
#     output_format: str,
#     max_concurrency: int,
#     registry: ServiceRegistry,
#     job_store: JobStore,
#     internal_token: str,
# ) -> None:
#     await job_store.update(job_id, status="running")
#     try:
#         summary = await run_pipeline(
#             registry=registry,
#             internal_token=internal_token,
#             input_path=input_path,
#             default_language=language,
#             output_format=output_format,
#             max_concurrency=max_concurrency,
#             output_dir=settings.output_path,
#         )
#         await job_store.update(job_id, status="completed", result=summary.to_dict())
#         input_path.unlink(missing_ok=True)
#     except Exception as exc:
#         await job_store.update(job_id, status="failed", error=str(exc))


@router.post(
    "/generate",
    response_model=GenerateResponse,
    summary="Start async TTS pipeline job",
    description=(
        "Upload an input file (TXT, CSV, or XLSX) and start a TTS pipeline job in the background. "
        "Returns a ``job_id`` that can be polled via the status endpoint."
    ),
)
async def generate(
    file: UploadFile = File(...),
    language: str = Form("en"),
    output_format: str = Form("mp3"),
    max_concurrency: int = Form(5),
    _user: dict = Depends(require_internal),
    registry: ServiceRegistry = Depends(get_registry),
    job_store: JobStore = Depends(get_job_store),
    internal_token: str = Depends(get_internal_token),
) -> GenerateResponse:
    """Upload an input file and trigger the TTS pipeline in background."""
    job_id = str(uuid.uuid4())
    input_path = await _save_upload(file, job_id)
    await job_store.create(job_id)
    asyncio.create_task(
        _run_job(job_id, input_path, language, output_format, max_concurrency, registry, job_store, internal_token)
    )
    return GenerateResponse(job_id=job_id, status="pending", message="Pipeline started.")


@router.get(
    "/generate/{job_id}/status",
    response_model=JobStatusResponse,
    summary="Poll TTS pipeline job status",
    description=(
        "Return the current status of a TTS pipeline job. "
        "Status values: ``pending``, ``running``, ``completed``, ``failed``."
    ),
)
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

#
# @router.post(
#     "/generate-sync",
#     summary="Run TTS pipeline synchronously",
#     description=(
#         "Upload an input file and run the TTS pipeline synchronously. "
#         "Blocks until the pipeline completes and returns the result directly. "
#         "Use for small payloads or scripted calls; prefer ``/generate`` for large batches."
#     ),
# )
# async def generate_sync(
#     file: UploadFile = File(...),
#     language: str = Form("en"),
#     output_format: str = Form("mp3"),
#     max_concurrency: int = Form(5),
#     _user: dict = Depends(require_internal),
#     registry: ServiceRegistry = Depends(get_registry),
#     internal_token: str = Depends(get_internal_token),
# ) -> dict[str, Any]:
#     """Upload an input file, run the pipeline synchronously, return the result."""
#     job_id = str(uuid.uuid4())
#     input_path = await _save_upload(file, job_id)
#     try:
#         summary = await run_pipeline(
#             registry=registry,
#             internal_token=internal_token,
#             input_path=input_path,
#             default_language=language,
#             output_format=output_format,
#             max_concurrency=max_concurrency,
#             output_dir=settings.output_path,
#         )
#         input_path.unlink(missing_ok=True)
#         return summary.to_dict()
#     except Exception as exc:
#         input_path.unlink(missing_ok=True)
#         raise HTTPException(status_code=500, detail=str(exc))
