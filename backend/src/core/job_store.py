"""In-memory async job store with TTL-based eviction.

Replace the backing store with SQLite or Redis for multi-worker deployments.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class JobRecord:
    """Tracks the state and result of a single async pipeline job."""

    job_id: str
    status: str = "pending"          # pending | running | completed | failed
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.monotonic)


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
        """Set arbitrary fields on an existing job record."""
        async with self._lock:
            if job_id in self._jobs:
                for key, value in kwargs.items():
                    setattr(self._jobs[job_id], key, value)

    async def evict_expired(self) -> None:
        """Delete records older than *ttl_seconds*. Call from a background task."""
        cutoff = time.monotonic() - self._ttl
        async with self._lock:
            expired = [jid for jid, r in self._jobs.items() if r.created_at < cutoff]
            for jid in expired:
                del self._jobs[jid]
