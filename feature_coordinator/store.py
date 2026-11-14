"""Durable local coordinator state (D-12): one JSON file per record, outside Git.

Layout under the state directory:

    features/<feature-id>.json   tasks/<task-id>.json   claims/<task-id>.json
    github/<record-id>.json      (Issue mapping and sync cursors of a feature or task, Phase 4)
    outbox/<op-id>.json          (pending coordinator comments with persisted operation IDs)
    checkpoints/<task-id>.json   (W3: a multi-step task's checkpoints, see checkpoints.py)
    scheduling/<task-id>.json    (W4: retry budgets, waits, attention — portable; autonomy.py)
    service/worker.json          (W4: the Worker service's health record — machine-specific)
    intake/<feature-id>.json     (W4.3.1: the approved specification of a GitHub Issue; intake.py)
    revalidation/<feature-id>.json (the revalidations of a Draft PR's changed head; revalidate.py)
    integration/<job-id>.json    (W4.7: an integration job of an existing branch/PR; integration.py)
    worker-startup.json          (W4: consecutive identical startup failures — restart-loop guard)
    events.jsonl (append-only audit trail)   locks/*.lock (flock files)   tmp/

Every write is atomic (temp file in the same directory, fsync, rename, fsync of the
directory) so a crash leaves either the old or the new record, never a torn one. Every
mutation happens inside `transaction()`, which holds `coordinator.lock`; records carry a
`revision` that must match on save (optimistic check against stale in-memory copies).
"""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .errors import StateConflictError, StateDirError
from .locks import LockSet
from .model import Feature, Task, check_id, utc_now


def atomic_write_json(path: Path, data: Any) -> None:
    """Write *data* as JSON to *path* atomically and durably."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    dir_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


class StateStore:
    """Feature, task and claim records for one state directory."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.locks = LockSet(root / "locks")
        self._depth = 0

    # -- locking -------------------------------------------------------------

    @contextmanager
    def transaction(self, timeout: float = 10.0) -> Iterator["StateStore"]:
        """Hold coordinator.lock for the duration (re-entrant within one store object)."""
        if self._depth:
            self._depth += 1
            try:
                yield self
            finally:
                self._depth -= 1
            return
        lock = self.locks.coordinator().acquire(timeout=timeout)
        self._depth = 1
        try:
            yield self
        finally:
            self._depth = 0
            lock.release()

    def _require_lock(self) -> None:
        if not self._depth:
            raise StateDirError("state mutations must run inside StateStore.transaction()")

    # -- generic records -----------------------------------------------------

    def _path(self, kind: str, record_id: str) -> Path:
        return self.root / kind / f"{check_id(record_id)}.json"

    def _read(self, kind: str, record_id: str) -> dict[str, Any] | None:
        path = self._path(kind, record_id)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            raise StateDirError(f"corrupt state record {path}: {exc}") from exc

    def _write(self, kind: str, record: Any) -> None:
        self._require_lock()
        current = self._read(kind, record.id)
        on_disk = int(current.get("revision", 0)) if current else 0
        if on_disk != record.revision:
            raise StateConflictError(f"{kind[:-1]} {record.id!r} changed concurrently "
                                     f"(revision {record.revision}, on disk {on_disk})")
        record.revision += 1
        atomic_write_json(self._path(kind, record.id), record.to_dict())

    # -- features and tasks --------------------------------------------------

    def save_feature(self, feature: Feature) -> None:
        """Persist *feature* (inside a transaction)."""
        self._write("features", feature)

    def load_feature(self, feature_id: str) -> Feature:
        """Load a feature; KeyError if unknown."""
        data = self._read("features", feature_id)
        if data is None:
            raise KeyError(f"unknown feature {feature_id!r}")
        return Feature.from_dict(data)

    def list_features(self) -> list[Feature]:
        """All features, sorted by ID."""
        return [self.load_feature(p.stem) for p in sorted((self.root / "features").glob("*.json"))]

    def save_task(self, task: Task) -> None:
        """Persist *task* (inside a transaction)."""
        self._write("tasks", task)

    def load_task(self, task_id: str) -> Task:
        """Load a task; KeyError if unknown."""
        data = self._read("tasks", task_id)
        if data is None:
            raise KeyError(f"unknown task {task_id!r}")
        return Task.from_dict(data)

    def load_tasks(self, feature: Feature) -> dict[str, Task]:
        """All tasks of *feature*, keyed by ID."""
        return {task_id: self.load_task(task_id) for task_id in feature.task_ids}

    # -- GitHub sync records (see issue_sync.py) -------------------------------

    def save_record(self, kind: str, record: Any) -> None:
        """Persist a revision-checked record of *kind* ('github', 'outbox', 'project', 'checkpoints',
        'scheduling', 'service', 'intake', — W4.5 — 'plan', — W4.6.1 — 'review' or 'revalidation', — W4.7 —
        'integration'; inside a transaction)."""
        if kind not in ("github", "outbox", "project", "checkpoints", "scheduling", "service", "intake", "plan",
                        "review", "revalidation", "integration"):
            raise StateDirError(f"unknown record kind {kind!r}")
        self._write(kind, record)

    def load_record(self, kind: str, record_id: str) -> dict[str, Any] | None:
        """The raw record, or None."""
        return self._read(kind, record_id)

    def list_records(self, kind: str) -> list[dict[str, Any]]:
        """All records of *kind*, sorted by file name."""
        records = []
        for path in sorted((self.root / kind).glob("*.json")):
            data = self._read(kind, path.stem)
            if data is not None:
                records.append(data)
        return records

    # -- claims (plain dicts; see claims.py) ---------------------------------

    def save_claim(self, task_id: str, claim: dict[str, Any]) -> None:
        """Persist a claim record (inside a transaction)."""
        self._require_lock()
        atomic_write_json(self._path("claims", task_id), claim)

    def load_claim(self, task_id: str) -> dict[str, Any] | None:
        """The claim record of *task_id*, if any."""
        return self._read("claims", task_id)

    def delete_claim(self, task_id: str) -> None:
        """Remove a claim record (inside a transaction)."""
        self._require_lock()
        self._path("claims", task_id).unlink(missing_ok=True)

    def list_claims(self) -> dict[str, dict[str, Any]]:
        """All claim records keyed by task ID."""
        claims = {}
        for path in sorted((self.root / "claims").glob("*.json")):
            data = self._read("claims", path.stem)
            if data is not None:
                claims[path.stem] = data
        return claims

    # -- flags and events ----------------------------------------------------

    def get_flag(self, name: str) -> dict[str, Any] | None:
        """A coordinator-wide flag record (e.g. full-project-requested)."""
        return self._read("flags", name)

    def set_flag(self, name: str, value: dict[str, Any] | None) -> None:
        """Set (or clear with None) a coordinator-wide flag (inside a transaction)."""
        self._require_lock()
        if value is None:
            self._path("flags", name).unlink(missing_ok=True)
        else:
            atomic_write_json(self._path("flags", name), value)

    def append_event(self, kind: str, **fields: Any) -> None:
        """Append one audit event (inside a transaction)."""
        self._require_lock()
        line = json.dumps({"at": utc_now(), "kind": kind, **fields}, sort_keys=True)
        with open(self.root / "events.jsonl", "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def events(self) -> list[dict[str, Any]]:
        """All audit events, oldest first."""
        path = self.root / "events.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
