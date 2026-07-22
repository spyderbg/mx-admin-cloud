"""Storage interfaces for cumulative evaluation history."""

from __future__ import annotations

from abc import ABC, abstractmethod

from src.core.models import EvaluationResult, GeneratedAudio, ProviderAggregate


class ScoreStore(ABC):
    """Interface for persisting evaluation results and computing provider aggregates."""

    @abstractmethod
    async def save(self, generated_audio: GeneratedAudio, evaluation_result: EvaluationResult) -> ProviderAggregate:
        """Persist one evaluation and return the latest provider aggregate."""


class MultiScoreStore(ScoreStore):
    """Fan out each save() call to multiple ScoreStore backends."""

    def __init__(self, stores: list[ScoreStore]) -> None:
        self.stores = stores

    async def save(self, generated_audio: GeneratedAudio, evaluation_result: EvaluationResult) -> ProviderAggregate:
        aggregate: ProviderAggregate | None = None
        for store in self.stores:
            aggregate = await store.save(generated_audio, evaluation_result)
        if aggregate is None:
            raise RuntimeError("No score stores were configured.")
        return aggregate
