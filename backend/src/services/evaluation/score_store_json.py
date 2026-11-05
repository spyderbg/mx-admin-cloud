"""JSON-backed evaluation history and aggregate score storage."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.core.utils import JsonObjectReadError, read_json_object
from src.services.text_to_speech.models import GeneratedAudio
from src.services.evaluation.models import EvaluationResult, ProviderAggregate, ScoreRecord
from .score_store import ScoreStore


def _entry_to_record(entry: dict[str, Any]) -> ScoreRecord:
    return ScoreRecord(
        provider=entry["provider"],
        request_id=entry["request_id"],
        language_code=entry["language_code"],
        expected_text=entry["expected_text"],
        transcription=entry["transcription"],
        pronunciation_accuracy=float(entry["pronunciation_accuracy"]),
        evaluation_latency_seconds=float(entry["evaluation_latency_seconds"]),
        raw_path=entry["raw_path"],
        normalized_path=entry["normalized_path"],
        success=bool(entry["success"]),
        error=entry["error"],
    )


class JsonScoreStore(ScoreStore):
    """Persist evaluation history and provider aggregates as JSON files."""
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
        provider_bucket["total_score"] = round(
            provider_bucket["total_score"] + evaluation_result.pronunciation_accuracy,
            4,
        )
        provider_bucket["average_score"] = round(
            provider_bucket["total_score"] / provider_bucket["evaluation_count"],
            4,
        )
        aggregates[generated_audio.provider] = provider_bucket

        self.history_path.write_text(json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")
        self.aggregate_path.write_text(json.dumps(aggregates, indent=2, ensure_ascii=False), encoding="utf-8")

        return ProviderAggregate(
            provider=generated_audio.provider,
            evaluation_count=provider_bucket["evaluation_count"],
            total_score=provider_bucket["total_score"],
            average_score=provider_bucket["average_score"],
        )

    async def get_best_score(
            self,
            provider: str | None = None,
            language_code: str | None = None,
            request_id: str | None = None,
    ) -> ScoreRecord | None:
        candidates = self._filter_history(provider=provider, language_code=language_code, request_id=request_id)
        if not candidates:
            return None
        best = max(candidates, key=lambda entry: entry["pronunciation_accuracy"])
        return _entry_to_record(best)

    async def get_scores_by_request(self, request_id: str) -> list[ScoreRecord]:
        return [_entry_to_record(entry) for entry in self._filter_history(request_id=request_id)]

    async def get_scores_by_provider(self, provider: str) -> list[ScoreRecord]:
        return [_entry_to_record(entry) for entry in self._filter_history(provider=provider)]

    async def get_scores_by_language(self, language_code: str) -> list[ScoreRecord]:
        return [_entry_to_record(entry) for entry in self._filter_history(language_code=language_code)]

    async def clear(self) -> None:
        self.history_path.write_text(json.dumps([], indent=2, ensure_ascii=False), encoding="utf-8")
        self.aggregate_path.write_text(json.dumps({}, indent=2, ensure_ascii=False), encoding="utf-8")

    def _filter_history(
            self,
            provider: str | None = None,
            language_code: str | None = None,
            request_id: str | None = None,
    ) -> list[dict[str, Any]]:
        history = self._load_json(self.history_path, [])
        return [
            entry
            for entry in history
            if (provider is None or entry["provider"] == provider)
            and (language_code is None or entry["language_code"] == language_code)
            and (request_id is None or entry["request_id"] == request_id)
        ]

    @staticmethod
    def _load_json(path: Path, default: Any) -> Any:
        if not path.exists():
            return default
        try:
            payload = read_json_object(path, require_object=False)
        except JsonObjectReadError:
            return default
        if isinstance(default, dict) and isinstance(payload, dict):
            return payload
        if isinstance(default, list) and isinstance(payload, list):
            return payload
        return default
