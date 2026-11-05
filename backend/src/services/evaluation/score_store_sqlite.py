"""SQLite-backed evaluation history and aggregate score storage."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from src.core.models import GeneratedAudio
from src.services.evaluation.models import EvaluationResult, ProviderAggregate, ScoreRecord
from .score_store import ScoreStore

_RECORD_COLUMNS = (
    "provider, request_id, language_code, expected_text, transcription, "
    "pronunciation_accuracy, evaluation_latency_seconds, raw_path, normalized_path, "
    "success, error"
)


def _row_to_record(row: tuple) -> ScoreRecord:
    return ScoreRecord(
        provider=row[0],
        request_id=row[1],
        language_code=row[2],
        expected_text=row[3],
        transcription=row[4],
        pronunciation_accuracy=float(row[5]),
        evaluation_latency_seconds=float(row[6]),
        raw_path=row[7],
        normalized_path=row[8],
        success=bool(row[9]),
        error=row[10],
    )


class SQLiteScoreStore(ScoreStore):
    """SQLite-backed ScoreStore: persists evaluations and computes rolling provider aggregates."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    async def save(self, generated_audio: GeneratedAudio, evaluation_result: EvaluationResult) -> ProviderAggregate:
        with sqlite3.connect(self.database_path) as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                INSERT INTO evaluations (
                    provider,
                    request_id,
                    language_code,
                    expected_text,
                    transcription,
                    pronunciation_accuracy,
                    evaluation_latency_seconds,
                    raw_path,
                    normalized_path,
                    success,
                    error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    generated_audio.provider,
                    generated_audio.request_id,
                    generated_audio.language_code,
                    generated_audio.expected_text,
                    evaluation_result.transcription,
                    evaluation_result.pronunciation_accuracy,
                    evaluation_result.latency_seconds,
                    str(generated_audio.raw_path),
                    str(generated_audio.normalized_path) if generated_audio.normalized_path else None,
                    int(evaluation_result.success),
                    evaluation_result.error,
                ),
            )

            cursor.execute(
                """
                INSERT INTO provider_aggregates (provider, evaluation_count, total_score, average_score)
                VALUES (?, 1, ?, ?)
                ON CONFLICT(provider) DO UPDATE SET
                    evaluation_count = evaluation_count + 1,
                    total_score = total_score + excluded.total_score,
                    average_score = (total_score + excluded.total_score) / (evaluation_count + 1)
                """,
                (
                    generated_audio.provider,
                    evaluation_result.pronunciation_accuracy,
                    evaluation_result.pronunciation_accuracy,
                ),
            )

            cursor.execute(
                """
                SELECT evaluation_count, total_score, average_score
                FROM provider_aggregates
                WHERE provider = ?
                """,
                (generated_audio.provider,),
            )
            row = cursor.fetchone()
            connection.commit()

        if row is None:
            raise RuntimeError(f"Failed to update aggregate for {generated_audio.provider}.")

        return ProviderAggregate(
            provider=generated_audio.provider,
            evaluation_count=int(row[0]),
            total_score=float(row[1]),
            average_score=float(row[2]),
        )

    async def get_best_score(
            self,
            provider: str | None = None,
            language_code: str | None = None,
            request_id: str | None = None,
    ) -> ScoreRecord | None:
        filters, params = self._build_filters(provider, language_code, request_id)
        with sqlite3.connect(self.database_path) as connection:
            cursor = connection.cursor()
            cursor.execute(
                f"""
                SELECT {_RECORD_COLUMNS} FROM evaluations
                {filters}
                ORDER BY pronunciation_accuracy DESC
                LIMIT 1
                """,
                params,
            )
            row = cursor.fetchone()
        return _row_to_record(row) if row is not None else None

    async def get_scores_by_request(self, request_id: str) -> list[ScoreRecord]:
        return await self._query_by("request_id", request_id)

    async def get_scores_by_provider(self, provider: str) -> list[ScoreRecord]:
        return await self._query_by("provider", provider)

    async def get_scores_by_language(self, language_code: str) -> list[ScoreRecord]:
        return await self._query_by("language_code", language_code)

    async def clear(self) -> None:
        with sqlite3.connect(self.database_path) as connection:
            cursor = connection.cursor()
            cursor.execute("DELETE FROM evaluations")
            cursor.execute("DELETE FROM provider_aggregates")
            connection.commit()

    async def _query_by(self, column: str, value: str) -> list[ScoreRecord]:
        with sqlite3.connect(self.database_path) as connection:
            cursor = connection.cursor()
            cursor.execute(
                f"SELECT {_RECORD_COLUMNS} FROM evaluations WHERE {column} = ?",
                (value,),
            )
            rows = cursor.fetchall()
        return [_row_to_record(row) for row in rows]

    @staticmethod
    def _build_filters(
            provider: str | None,
            language_code: str | None,
            request_id: str | None,
    ) -> tuple[str, tuple]:
        conditions = []
        params: list[str] = []
        if provider is not None:
            conditions.append("provider = ?")
            params.append(provider)
        if language_code is not None:
            conditions.append("language_code = ?")
            params.append(language_code)
        if request_id is not None:
            conditions.append("request_id = ?")
            params.append(request_id)
        if not conditions:
            return "", ()
        return "WHERE " + " AND ".join(conditions), tuple(params)

    def _initialize(self) -> None:
        with sqlite3.connect(self.database_path) as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS evaluations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    language_code TEXT NOT NULL,
                    expected_text TEXT NOT NULL,
                    transcription TEXT NOT NULL,
                    pronunciation_accuracy REAL NOT NULL,
                    evaluation_latency_seconds REAL NOT NULL,
                    raw_path TEXT NOT NULL,
                    normalized_path TEXT,
                    success INTEGER NOT NULL,
                    error TEXT
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS provider_aggregates (
                    provider TEXT PRIMARY KEY,
                    evaluation_count INTEGER NOT NULL,
                    total_score REAL NOT NULL,
                    average_score REAL NOT NULL
                )
                """
            )
            connection.commit()
