"""JSON-backed evaluation history and aggregate score storage."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..models import EvaluationResult, GeneratedAudio, ProviderAggregate
from .score_store import ScoreStore


class JsonScoreStore(ScoreStore):
    def __init__(self, storage_dir: Path) -> None:
        self.storage_dir = storage_dir
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.history_path = self.storage_dir / "evaluations.json"
        self.aggregate_path = self.storage_dir / "provider_aggregates.json"

    async def save(self, generated_audio: GeneratedAudio, evaluation_result: EvaluationResult) -> ProviderAggregate:
        history = self._load_json(self.history_path, [])
        aggregates = self._load_json(self.aggregate_path, {})

        history.append(
            {
                "provider": generated_audio.provider,
                "request_id": generated_audio.request_id,
                "language_code": generated_audio.language_code,
                "expected_text": generated_audio.expected_text,
                "transcription": evaluation_result.transcription,
                "pronunciation_accuracy": evaluation_result.pronunciation_accuracy,
                "evaluation_latency_seconds": evaluation_result.latency_seconds,
                "raw_path": str(generated_audio.raw_path),
                "normalized_path": str(generated_audio.normalized_path) if generated_audio.normalized_path else None,
                "success": evaluation_result.success,
                "error": evaluation_result.error,
            }
        )

        provider_bucket = aggregates.get(
            generated_audio.provider,
            {"evaluation_count": 0, "total_score": 0.0, "average_score": 0.0},
        )
        provider_bucket["evaluation_count"] += 1
        provider_bucket["total_score"] = round(provider_bucket["total_score"] + evaluation_result.pronunciation_accuracy, 4)
        provider_bucket["average_score"] = round(provider_bucket["total_score"] / provider_bucket["evaluation_count"], 4)
        aggregates[generated_audio.provider] = provider_bucket

        self.history_path.write_text(json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")
        self.aggregate_path.write_text(json.dumps(aggregates, indent=2, ensure_ascii=False), encoding="utf-8")

        return ProviderAggregate(
            provider=generated_audio.provider,
            evaluation_count=provider_bucket["evaluation_count"],
            total_score=provider_bucket["total_score"],
            average_score=provider_bucket["average_score"],
        )

    @staticmethod
    def _load_json(path: Path, default: Any) -> Any:
        if not path.exists():
            return default
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
