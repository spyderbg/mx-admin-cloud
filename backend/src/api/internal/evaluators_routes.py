"""Internal evaluator-configuration and evaluation endpoints.

Exposes STT evaluator configs derived from generator/provider configs in ``backend/config/text_to_speech/``.
All endpoints require a JWT with role ``internal`` or ``admin``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends  # pylint: disable=import-error
from pydantic import BaseModel  # pylint: disable=import-error

from src.api.dependencies import get_registry, require_internal
from src.core.config import settings
from src.core.models import GeneratedAudio
from src.core.registry import ServiceRegistry
from src.services.text_to_speech.models import ProviderConfig
from src.services.evaluation import EvaluationEngine, JsonScoreStore, MultiScoreStore, SQLiteScoreStore

router = APIRouter(prefix="/internal", tags=["internal-evaluators"])


class EvaluateRequest(BaseModel):
    """Body for a single evaluation request."""
    provider: str
    request_id: str
    expected_text: str
    language_code: str
    language_name: str
    filename_text: str
    output_format: str
    raw_path: str
    raw_extension: str
    latency_seconds: float
    normalized_path: str | None = None
    normalization_error: str | None = None
    evaluate_config: dict[str, Any]
    output_dir: str


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
    from src.services.text_to_speech.config_loader import load_provider_configs  # pylint: disable=import-outside-toplevel
    configs = load_provider_configs(
        base_dir=settings.BASE_DIR,
        config_dir=settings.config_path,
    )
    return [
        {
            "name": c.name,
            "enabled": c.evaluate.get("enabled", False),
            "config_path": str(c.config_path),
        }
        for c in configs
    ]


@router.post(
    "/evaluators/evaluate",
    summary="Evaluate generated audio against expected text",
    description="Run STT transcription, score, persist, and return the evaluation result.",
)
async def evaluate(
    body: EvaluateRequest,
    _user: dict = Depends(require_internal),
    _registry: ServiceRegistry = Depends(get_registry),
) -> dict[str, Any]:
    """Evaluate a generated audio file using STT and Levenshtein scoring."""
    import httpx  # pylint: disable=import-outside-toplevel

    output_dir = Path(body.output_dir)

    generated_audio = GeneratedAudio(
        provider=body.provider,
        request_id=body.request_id,
        language_code=body.language_code,
        language_name=body.language_name,
        expected_text=body.expected_text,
        filename_text=body.filename_text,
        output_format=body.output_format,
        raw_path=Path(body.raw_path),
        raw_extension=body.raw_extension,
        latency_seconds=body.latency_seconds,
        success=True,
        normalized_path=Path(body.normalized_path) if body.normalized_path else None,
        normalization_error=body.normalization_error,
    )

    provider_config = ProviderConfig(
        name=body.provider,
        generate={},
        evaluate=body.evaluate_config,
        config_path=Path("api-supplied"),
    )

    engine = EvaluationEngine(base_dir=output_dir)
    score_root = settings.scores_output_dir()
    store = MultiScoreStore(
        [
            JsonScoreStore(score_root / "json"),
            SQLiteScoreStore(score_root / "sqlite" / "scores.db"),
        ]
    )

    async with httpx.AsyncClient(timeout=180.0) as client:
        evaluation_result = await engine.evaluate(generated_audio, provider_config, client)

    try:
        aggregate = await store.save(generated_audio, evaluation_result)
        evaluation_result.rolling_average = aggregate.average_score
    except Exception as exc:
        evaluation_result.error = (evaluation_result.error or "") + f" Score save failed: {exc}"

    return {
        "provider": evaluation_result.provider,
        "request_id": evaluation_result.request_id,
        "expected_text": evaluation_result.expected_text,
        "transcription": evaluation_result.transcription,
        "levenshtein_distance": evaluation_result.levenshtein_distance,
        "char_accuracy": evaluation_result.char_accuracy,
        "word_accuracy": evaluation_result.word_accuracy,
        "pronunciation_accuracy": evaluation_result.pronunciation_accuracy,
        "latency_seconds": evaluation_result.latency_seconds,
        "success": evaluation_result.success,
        "error": evaluation_result.error,
        "rolling_average": evaluation_result.rolling_average,
    }
