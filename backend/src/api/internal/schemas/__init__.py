"""Pydantic schemas for internal API routes.

These models define the internal HTTP contract and are used for OpenAPI output.
"""

from src.api.internal.schemas.evaluation import EvaluateRequest
from src.api.internal.schemas.jobs import GenerateJobResponse, JobStatusResponse
from src.api.internal.schemas.service_config import ApplyConfigRequest, ApplyConfigResponse
from src.api.internal.schemas.text_to_speech import GenerateRequest, GenerateWordRequest, TtsGenerateResult
from src.api.internal.schemas.text_translation import (
    LangCodeText,
    TranslateFileResponse,
    TranslateRequest,
    TranslateResponse,
)
from src.api.internal.schemas.voice_optimization import OptimizeRequest

__all__ = [
    "ApplyConfigRequest",
    "ApplyConfigResponse",
    "EvaluateRequest",
    "GenerateJobResponse",
    "GenerateRequest",
    "GenerateWordRequest",
    "JobStatusResponse",
    "LangCodeText",
    "OptimizeRequest",
    "TranslateFileResponse",
    "TranslateRequest",
    "TranslateResponse",
    "TtsGenerateResult",
]
