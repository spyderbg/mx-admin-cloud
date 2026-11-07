"""Storage interfaces for cumulative evaluation history."""

from __future__ import annotations

from abc import ABC, abstractmethod

from src.services.text_to_speech import GeneratedAudio
from src.services.evaluation.models import EvaluationResult, ProviderAggregate, ScoreRecord


class ScoreStore(ABC):
    """Interface for persisting evaluation results and computing provider aggregates."""

    @abstractmethod
    async def save(self, generated_audio: GeneratedAudio, evaluation_result: EvaluationResult) -> ProviderAggregate:
        """Persist one evaluation and return the latest provider aggregate."""

    @abstractmethod
    async def get_best_score(
            self,
            provider: str | None = None,
            language_code: str | None = None,
            request_id: str | None = None,
    ) -> ScoreRecord | None:
        """Retrieve the highest-scoring result for given criteria."""

    @abstractmethod
    async def get_scores_by_request(self, request_id: str) -> list[ScoreRecord]:
        """Retrieve all scores associated with one TTS request."""

    @abstractmethod
    async def get_scores_by_provider(self, provider: str) -> list[ScoreRecord]:
        """Retrieve evaluation history for a specific TTS provider."""

    @abstractmethod
    async def get_scores_by_language(self, language_code: str) -> list[ScoreRecord]:
        """Retrieve evaluation results for a particular language."""

    @abstractmethod
    async def clear(self) -> None:
        """Remove all stored scores, mainly useful for testing or maintenance."""


class MultiScoreStore(ScoreStore):
    """Fan out each save()/clear() call to multiple ScoreStore backends; reads hit the first."""

    def __init__(self, stores: list[ScoreStore]) -> None:
        self.stores = stores

    async def save(self, generated_audio: GeneratedAudio, evaluation_result: EvaluationResult) -> ProviderAggregate:
        aggregate: ProviderAggregate | None = None
        for store in self.stores:
            aggregate = await store.save(generated_audio, evaluation_result)
        if aggregate is None:
            raise RuntimeError("No score stores were configured.")
        return aggregate

    async def get_best_score(
            self,
            provider: str | None = None,
            language_code: str | None = None,
            request_id: str | None = None,
    ) -> ScoreRecord | None:
        return await self._primary_store().get_best_score(provider, language_code, request_id)

    async def get_scores_by_request(self, request_id: str) -> list[ScoreRecord]:
        return await self._primary_store().get_scores_by_request(request_id)

    async def get_scores_by_provider(self, provider: str) -> list[ScoreRecord]:
        return await self._primary_store().get_scores_by_provider(provider)

    async def get_scores_by_language(self, language_code: str) -> list[ScoreRecord]:
        return await self._primary_store().get_scores_by_language(language_code)

    async def clear(self) -> None:
        for store in self.stores:
            await store.clear()

    def _primary_store(self) -> ScoreStore:
        if not self.stores:
            raise RuntimeError("No score stores were configured.")
        return self.stores[0]
