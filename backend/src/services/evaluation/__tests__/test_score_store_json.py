"""JsonScoreStore: save() validates both files before writing, and lookups on valid data."""

# pylint: disable=missing-function-docstring

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.services.evaluation.models import EvaluationResult
from src.services.evaluation.score_store_json import JsonScoreStore, ScoreStoreCorruptedError
from src.services.text_to_speech import GeneratedAudio

HISTORY = "evaluations.json"
AGGREGATES = "provider_aggregates.json"


def _audio(tmp_path: Path, *, provider: str = "alpha", request_id: str = "req-1",
           language_code: str = "bg") -> GeneratedAudio:
    path = tmp_path / f"{provider}-{request_id}.wav"
    return GeneratedAudio(
        provider=provider, request_id=request_id, language_code=language_code, language_name="Bulgarian",
        expected_text="hello world", filename_text="hello", output_format="mp3", raw_path=path,
        raw_extension="mp3", latency_seconds=0.1, success=True, normalized_path=None,
    )


def _result(score: float, *, provider: str = "alpha", request_id: str = "req-1") -> EvaluationResult:
    return EvaluationResult(
        provider=provider, request_id=request_id, expected_text="hello world", transcription="hello world",
        levenshtein_distance=0, char_accuracy=score, word_accuracy=score, pronunciation_accuracy=score,
        latency_seconds=0.2, success=True,
    )


def _entry(provider: str, request_id: str, score: float, language_code: str = "bg") -> dict:
    return {
        "provider": provider, "request_id": request_id, "language_code": language_code,
        "expected_text": "hello world", "transcription": "hello world", "pronunciation_accuracy": score,
        "evaluation_latency_seconds": 0.2, "raw_path": f"/tmp/{request_id}.wav", "normalized_path": None,
        "success": True, "error": None,
    }


async def test_save_into_empty_directory_creates_both_files(tmp_path: Path) -> None:
    store_dir = tmp_path / "store"
    store = JsonScoreStore(store_dir)

    aggregate = await store.save(_audio(tmp_path), _result(0.8))

    history = json.loads((store_dir / HISTORY).read_text(encoding="utf-8"))
    aggregates = json.loads((store_dir / AGGREGATES).read_text(encoding="utf-8"))
    assert isinstance(history, list) and len(history) == 1
    assert history[0]["provider"] == "alpha" and history[0]["pronunciation_accuracy"] == 0.8
    assert aggregates == {"alpha": {"evaluation_count": 1, "total_score": 0.8, "average_score": 0.8}}
    assert (aggregate.provider, aggregate.evaluation_count, aggregate.total_score, aggregate.average_score) == (
        "alpha", 1, 0.8, 0.8,
    )
    assert sorted(p.name for p in store_dir.iterdir()) == sorted([HISTORY, AGGREGATES])


async def test_save_preserves_and_extends_existing_valid_data(tmp_path: Path) -> None:
    (tmp_path / HISTORY).write_text(
        json.dumps([_entry("alpha", "req-0", 0.6), _entry("beta", "req-0", 0.4)]), encoding="utf-8"
    )
    (tmp_path / AGGREGATES).write_text(json.dumps({
        "alpha": {"evaluation_count": 1, "total_score": 0.6, "average_score": 0.6},
        "beta": {"evaluation_count": 1, "total_score": 0.4, "average_score": 0.4},
    }), encoding="utf-8")
    store = JsonScoreStore(tmp_path)

    alpha = await store.save(_audio(tmp_path, request_id="req-1"), _result(1.0))
    gamma = await store.save(_audio(tmp_path, provider="gamma", request_id="req-2"),
                             _result(0.5, provider="gamma", request_id="req-2"))

    history = json.loads((tmp_path / HISTORY).read_text(encoding="utf-8"))
    aggregates = json.loads((tmp_path / AGGREGATES).read_text(encoding="utf-8"))
    assert [(e["provider"], e["request_id"]) for e in history] == [
        ("alpha", "req-0"), ("beta", "req-0"), ("alpha", "req-1"), ("gamma", "req-2"),
    ]
    assert aggregates == {
        "alpha": {"evaluation_count": 2, "total_score": 1.6, "average_score": 0.8},
        "beta": {"evaluation_count": 1, "total_score": 0.4, "average_score": 0.4},
        "gamma": {"evaluation_count": 1, "total_score": 0.5, "average_score": 0.5},
    }
    assert (alpha.evaluation_count, alpha.total_score, alpha.average_score) == (2, 1.6, 0.8)
    assert (gamma.evaluation_count, gamma.total_score, gamma.average_score) == (1, 0.5, 0.5)


VALID_HISTORY = json.dumps([_entry("alpha", "req-0", 0.6)]).encode("utf-8")
VALID_AGGREGATES = json.dumps(
    {"alpha": {"evaluation_count": 1, "total_score": 0.6, "average_score": 0.6}}
).encode("utf-8")


@pytest.mark.parametrize(
    ("history_bytes", "aggregate_bytes", "bad_file"),
    [
        pytest.param(b"{not json", VALID_AGGREGATES, HISTORY, id="malformed-history"),
        pytest.param(VALID_HISTORY, b"{not json", AGGREGATES, id="malformed-aggregates"),
        pytest.param(b'{"a": 1}', VALID_AGGREGATES, HISTORY, id="history-dict-root"),
        pytest.param(b"42", VALID_AGGREGATES, HISTORY, id="history-scalar-root"),
        pytest.param(VALID_HISTORY, b"[]", AGGREGATES, id="aggregates-list-root"),
        pytest.param(b"\xff\xfe\x00garbage", VALID_AGGREGATES, HISTORY, id="history-not-utf8"),
    ],
)
async def test_invalid_file_blocks_save_and_leaves_both_files_unchanged(
        tmp_path: Path, history_bytes: bytes, aggregate_bytes: bytes, bad_file: str
) -> None:
    (tmp_path / HISTORY).write_bytes(history_bytes)
    (tmp_path / AGGREGATES).write_bytes(aggregate_bytes)
    store = JsonScoreStore(tmp_path)

    with pytest.raises(ScoreStoreCorruptedError, match=bad_file):
        await store.save(_audio(tmp_path), _result(0.9))

    assert (tmp_path / HISTORY).read_bytes() == history_bytes
    assert (tmp_path / AGGREGATES).read_bytes() == aggregate_bytes
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted([HISTORY, AGGREGATES])


async def test_invalid_aggregates_with_missing_history_creates_nothing(tmp_path: Path) -> None:
    (tmp_path / AGGREGATES).write_bytes(b"[1, 2]")
    store = JsonScoreStore(tmp_path)

    with pytest.raises(ScoreStoreCorruptedError):
        await store.save(_audio(tmp_path), _result(0.9))

    assert not (tmp_path / HISTORY).exists()
    assert (tmp_path / AGGREGATES).read_bytes() == b"[1, 2]"


def test_corrupted_error_is_a_value_error() -> None:
    assert issubclass(ScoreStoreCorruptedError, ValueError)


async def test_save_is_visible_to_a_new_instance(tmp_path: Path) -> None:
    await JsonScoreStore(tmp_path).save(_audio(tmp_path), _result(0.7))

    reopened = JsonScoreStore(tmp_path)
    records = await reopened.get_scores_by_request("req-1")
    assert [(r.provider, r.pronunciation_accuracy) for r in records] == [("alpha", 0.7)]

    aggregate = await reopened.save(_audio(tmp_path, request_id="req-2"), _result(0.9, request_id="req-2"))
    assert (aggregate.evaluation_count, aggregate.total_score, aggregate.average_score) == (2, 1.6, 0.8)


async def test_lookup_methods_on_valid_data(tmp_path: Path) -> None:
    store = JsonScoreStore(tmp_path)
    await store.save(_audio(tmp_path, provider="alpha", request_id="r1"), _result(0.5, request_id="r1"))
    await store.save(_audio(tmp_path, provider="beta", request_id="r1"),
                     _result(0.9, provider="beta", request_id="r1"))
    await store.save(_audio(tmp_path, provider="alpha", request_id="r2", language_code="en"),
                     _result(0.7, request_id="r2"))

    best = await store.get_best_score()
    assert best is not None and (best.provider, best.request_id, best.pronunciation_accuracy) == ("beta", "r1", 0.9)
    best_alpha = await store.get_best_score(provider="alpha")
    assert best_alpha is not None and best_alpha.request_id == "r2"
    best_bg_alpha = await store.get_best_score(provider="alpha", language_code="bg")
    assert best_bg_alpha is not None and best_bg_alpha.pronunciation_accuracy == 0.5
    assert await store.get_best_score(provider="missing") is None

    assert [r.provider for r in await store.get_scores_by_request("r1")] == ["alpha", "beta"]
    assert [r.request_id for r in await store.get_scores_by_provider("alpha")] == ["r1", "r2"]
    assert [r.request_id for r in await store.get_scores_by_language("en")] == ["r2"]

    await store.clear()
    assert await store.get_scores_by_provider("alpha") == []
