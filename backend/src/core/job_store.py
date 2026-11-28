"""In-memory async job store with TTL-based eviction.

Replace the backing store with SQLite or Redis for multi-worker deployments.

Expiry lifecycle (all times from :func:`time.monotonic`):

* ``running`` jobs are never evicted, however old they are.
* ``pending`` (and any other non-terminal) jobs expire once
  ``now - created_at >= ttl``.
* ``completed`` / ``failed`` jobs expire once ``now - terminal_at >= ttl``,
  where ``terminal_at`` is recorded when the job first enters a terminal state.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

RUNNING_STATUS = "running"
TERMINAL_STATUSES = frozenset({"completed", "failed"})


def _monotonic() -> float:
    """Return the current monotonic time (looked up at call time so tests can patch it)."""
    return time.monotonic()


@dataclass
class JobRecord:
    """Tracks the state and result of a single async pipeline job."""

    job_id: str
    status: str = "pending"          # pending | running | completed | failed
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: float = field(default_factory=_monotonic)
    terminal_at: float | None = None  # monotonic time the job entered completed/failed


class JobStore:
    """Thread-safe, in-memory job store with TTL eviction.

    All mutations are guarded by an asyncio.Lock so concurrent route handlers
    cannot race on the same job record.
    """

    def __init__(self, ttl_seconds: int = 3600) -> None:
        self._jobs: dict[str, JobRecord] = {}
        self._lock: asyncio.Lock = asyncio.Lock()
        self._ttl = ttl_seconds

    async def create(self, job_id: str) -> JobRecord:
        """Insert a new job record with status 'pending'."""
        async with self._lock:
            record = JobRecord(job_id=job_id)
            self._jobs[job_id] = record
            return record

    async def get(self, job_id: str) -> JobRecord | None:
        """Return the record for *job_id*, or None if it does not exist."""
        async with self._lock:
            return self._jobs.get(job_id)

    async def update(self, job_id: str, **kwargs: Any) -> None:
        """Set arbitrary fields on an existing job record.

        Updates for unknown *job_id* values are ignored silently. When the
        status first enters a terminal state, ``terminal_at`` is recorded;
        moving back to a non-terminal state clears it, so the creation-time
        (or running) rules apply again.
        """
        async with self._lock:
            record = self._jobs.get(job_id)
            if record is None:
                return
            for key, value in kwargs.items():
                setattr(record, key, value)
            if "status" in kwargs and "terminal_at" not in kwargs:
                if record.status in TERMINAL_STATUSES:
                    if record.terminal_at is None:
                        record.terminal_at = _monotonic()
                else:
                    record.terminal_at = None

    def _is_expired(self, record: JobRecord, now: float) -> bool:
        """Return True if *record* should be evicted at monotonic time *now*."""
        if record.status == RUNNING_STATUS:
            return False
        if record.status in TERMINAL_STATUSES:
            # Fall back to created_at if terminal_at was never recorded
            # (e.g. status assigned directly on the record object).
            reference = record.terminal_at if record.terminal_at is not None else record.created_at
            return now - reference >= self._ttl
        return now - record.created_at >= self._ttl

    async def evict_expired(self) -> None:
        """Delete expired records according to the lifecycle rules. Call from a background task."""
        async with self._lock:
            now = _monotonic()
            expired = [jid for jid, r in self._jobs.items() if self._is_expired(r, now)]
            for jid in expired:
                del self._jobs[jid]
