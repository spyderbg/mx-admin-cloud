"""Regression tests for the JobStore TTL eviction lifecycle (Issue #93).

Time is controlled by replacing ``time`` inside the ``job_store`` module with a
fake clock, so no real sleeps are needed and the event loop's own clock is
left untouched.
"""

# pylint: disable=missing-function-docstring

from __future__ import annotations

import asyncio
import types

import pytest

from src.core import job_store as job_store_module
from src.core.job_store import JobStore

TTL = 100


class FakeClock:
    """A manually advanced monotonic clock."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture(name="clock")
def fixture_clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    clock = FakeClock()
    monkeypatch.setattr(job_store_module, "time", types.SimpleNamespace(monotonic=clock.monotonic))
    return clock


@pytest.fixture(name="store")
def fixture_store() -> JobStore:
    return JobStore(ttl_seconds=TTL)


async def test_created_at_uses_monotonic_clock(clock: FakeClock, store: JobStore) -> None:
    record = await store.create("j1")
    assert record.created_at == clock.now
    assert record.status == "pending"
    assert record.terminal_at is None


async def test_running_job_survives_past_creation_ttl(clock: FakeClock, store: JobStore) -> None:
    await store.create("j1")
    await store.update("j1", status="running")
    clock.advance(TTL * 50)
    await store.evict_expired()
    record = await store.get("j1")
    assert record is not None
    assert record.status == "running"


async def test_pending_job_expires_exactly_at_ttl(clock: FakeClock, store: JobStore) -> None:
    await store.create("j1")
    clock.advance(TTL - 0.001)
    await store.evict_expired()
    assert await store.get("j1") is not None

    clock.advance(0.001)  # now - created_at == ttl
    await store.evict_expired()
    assert await store.get("j1") is None


@pytest.mark.parametrize("terminal_status", ["completed", "failed"])
async def test_terminal_job_expires_from_terminal_timestamp(
        clock: FakeClock, store: JobStore, terminal_status: str,
) -> None:
    await store.create("j1")
    await store.update("j1", status="running")
    clock.advance(TTL * 10)  # created long ago
    await store.update("j1", status=terminal_status)
    terminal_at = clock.now

    record = await store.get("j1")
    assert record is not None
    assert record.terminal_at == terminal_at

    clock.advance(TTL - 0.001)
    await store.evict_expired()
    assert await store.get("j1") is not None

    clock.advance(0.001)  # now - terminal_at == ttl
    await store.evict_expired()
    assert await store.get("j1") is None


async def test_pending_running_completed_lifecycle(clock: FakeClock, store: JobStore) -> None:
    await store.create("j1")
    clock.advance(TTL / 2)
    await store.update("j1", status="running")
    clock.advance(TTL * 3)
    await store.evict_expired()
    assert (await store.get("j1")).status == "running"  # type: ignore[union-attr]

    await store.update("j1", status="completed", result={"ok": True})
    clock.advance(TTL / 2)
    await store.evict_expired()
    record = await store.get("j1")
    assert record is not None
    assert record.status == "completed"
    assert record.result == {"ok": True}

    clock.advance(TTL / 2)
    await store.evict_expired()
    assert await store.get("j1") is None


async def test_terminal_at_set_only_on_first_terminal_transition(
        clock: FakeClock, store: JobStore,
) -> None:
    await store.create("j1")
    await store.update("j1", status="completed")
    first = clock.now
    clock.advance(10)
    await store.update("j1", status="failed", error="late failure")
    await store.update("j1", result={"x": 1})
    record = await store.get("j1")
    assert record is not None
    assert record.terminal_at == first


async def test_leaving_terminal_state_clears_terminal_at(clock: FakeClock, store: JobStore) -> None:
    await store.create("j1")
    await store.update("j1", status="failed")
    clock.advance(TTL * 5)
    await store.update("j1", status="running")  # retried
    record = await store.get("j1")
    assert record is not None
    assert record.terminal_at is None
    await store.evict_expired()
    assert await store.get("j1") is not None

    await store.update("j1", status="completed")
    assert (await store.get("j1")).terminal_at == clock.now  # type: ignore[union-attr]


async def test_update_merges_fields_on_existing_job(clock: FakeClock, store: JobStore) -> None:
    created = await store.create("j1")
    created_at = created.created_at
    clock.advance(5)
    await store.update("j1", status="running")
    await store.update("j1", result={"items": 3})
    record = await store.get("j1")
    assert record is not None
    assert record.status == "running"
    assert record.result == {"items": 3}
    assert record.error is None
    assert record.created_at == created_at

    await store.update("j1", status="failed", error="boom")
    record = await store.get("j1")
    assert record is not None
    assert record.status == "failed"
    assert record.error == "boom"
    assert record.result == {"items": 3}


async def test_update_unknown_job_is_noop(clock: FakeClock, store: JobStore) -> None:
    await store.create("j1")
    result = await store.update("missing", status="completed", result={"a": 1})
    assert result is None
    assert await store.get("missing") is None
    record = await store.get("j1")
    assert record is not None
    assert record.status == "pending"
    clock.advance(1)


async def test_get_unknown_job_returns_none(store: JobStore) -> None:
    assert await store.get("nope") is None


async def test_concurrent_create_update_get_is_consistent(
        clock: FakeClock, store: JobStore,
) -> None:
    ids = [f"job-{i}" for i in range(50)]
    await asyncio.gather(*(store.create(jid) for jid in ids))
    await asyncio.gather(
        *(store.update(jid, status="running") for jid in ids[::2]),
        *(store.update(jid, status="completed", result={"id": jid}) for jid in ids[1::2]),
        *(store.get(jid) for jid in ids),
        store.evict_expired(),
    )
    records = await asyncio.gather(*(store.get(jid) for jid in ids))
    for jid, record in zip(ids, records):
        assert record is not None
        assert record.job_id == jid
    assert all(r.status == "running" for r in records[::2])  # type: ignore[union-attr]
    assert all(
        r.status == "completed" and r.result == {"id": r.job_id} and r.terminal_at == clock.now
        for r in records[1::2]  # type: ignore[union-attr]
    )

    clock.advance(TTL)
    await asyncio.gather(store.evict_expired(), *(store.get(jid) for jid in ids))
    survivors = await asyncio.gather(*(store.get(jid) for jid in ids))
    assert all(r is not None for r in survivors[::2])
    assert all(r is None for r in survivors[1::2])
