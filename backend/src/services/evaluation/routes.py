"""Internal evaluator-configuration and evaluation endpoints.

Exposes STT evaluator configs derived from generator/provider configs in ``backend/config/text_to_speech/``.
All endpoints require a JWT with role ``internal`` or ``admin``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, status  # pylint: disable=import-error

from src.api.dependencies import get_registry, require_internal
from src.api.service_resolver import get_typed_service
from src.core.config import settings
from src.services.text_to_speech.models import GeneratedAudio
from src.services import text_to_speech
from src.services.evaluation import EvaluationEngine, JsonScoreStore, MultiScoreStore, SQLiteScoreStore
from src.services.evaluation.schemas import EvaluateRequest
from src.services.service_registry import ServiceRegistry
from src.services.text_to_speech.service import TextToSpeechService

router = APIRouter(prefix="/internal/evaluators", tags=["internal-evaluators"])

EVALUATORS_LIST = "/"
EVALUATE_AUDIO = "/evaluate"


@router.get(
    EVALUATORS_LIST,
    status_code=status.HTTP_200_OK,
    summary="List STT evaluator configurations",
    description=(
            "Return all configured STT evaluators (derived from text to speech generator "
            "and provider configs) with their activation state and config path. "
            "Entries are sorted by priority (ascending)."
    ),
)
async def list_evaluators(
        _user: dict = Depends(require_internal),
        registry: ServiceRegistry = Depends(get_registry),
) -> list[dict[str, Any]]:
    """Return configured evaluators (from generators/providers) with activation states."""
    service = get_typed_service(
        registry=registry,
        service_name=text_to_speech.SERVICE_NAME,
        expected_type=TextToSpeechService,
    )
    configs = service.providers_snapshot()
    configs.sort(key=lambda cfg: cfg.priority)
    return [
        {
            "name": c.name,
            "enabled": c.config.get("evaluate", {}).get("enabled", False),
            "config_path": str(c.config_path),
        }
        for c in configs
    ]


@router.post(
    EVALUATE_AUDIO,
    status_code=status.HTTP_200_OK,
    summary="Evaluate generated audio against expected text",
    description=(
            "Run STT transcription on the generated audio, score it against the expected "
            "text using Levenshtein-based metrics (character, word, and pronunciation "
            "accuracy), persist the scores to the JSON and SQLite stores, and return the "
            "evaluation result including the rolling average score."
    ),
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

    engine = EvaluationEngine(base_dir=output_dir)
    score_root = settings.scores_output_dir()
    store = MultiScoreStore(
        [
            JsonScoreStore(score_root / "json"),
            SQLiteScoreStore(score_root / "sqlite" / "scores.db"),
        ]
    )

    async with httpx.AsyncClient(timeout=180.0) as client:
        evaluation_result = await engine.evaluate(
            generated_audio,
            evaluate_config=body.evaluate_config,
            client=client,
            provider_name=body.provider,
        )

    try:
        aggregate = await store.save(generated_audio, evaluation_result)
        evaluation_result.rolling_average = aggregate.average_score
    except Exception as exc:  # pylint: disable=broad-exception-caught
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
