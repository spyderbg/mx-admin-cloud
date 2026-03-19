"""SQLite-backed evaluation history and aggregate score storage."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ..models import EvaluationResult, GeneratedAudio, ProviderAggregate
from .score_store import ScoreStore


class SQLiteScoreStore(ScoreStore):
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
