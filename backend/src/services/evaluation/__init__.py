"""Evaluation and score persistence modules."""

from .engine import EvaluationEngine
from .models import EvaluationResult, ProviderAggregate, ScoreRecord
from .score_store import MultiScoreStore, ScoreStore
from .score_store_json import JsonScoreStore
from .score_store_sqlite import SQLiteScoreStore

# Import the STT providers AFTER the engine (and with it SpeechToTextProviderFactory) so each provider
# package can register itself without triggering circular package initialization. The providers and
# their factory stay internal: EvaluationEngine is the only consumer.
from . import providers  # noqa: F401, E402

__all__ = [
    "EvaluationEngine",
    "EvaluationResult",
    "ProviderAggregate",
    "ScoreRecord",
    "ScoreStore",
    "MultiScoreStore",
    "JsonScoreStore",
    "SQLiteScoreStore",
]
