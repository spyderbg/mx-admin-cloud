"""Atomic claims, one writer per role, full-project exclusivity and crash recovery."""

# pylint: disable=missing-function-docstring,redefined-outer-name

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

from feature_coordinator.claims import ClaimManager
from feature_coordinator.errors import ClaimError, FullProjectActiveError, RoleBusyError, WritersActiveError
from feature_coordinator.model import Feature, Task, TaskState
from feature_coordinator.store import StateStore

from fc_helpers import Holder, child_env, run_py

HEAD = "c" * 40

CLAIM_AND_HOLD = """
import sys
from pathlib import Path
from feature_coordinator.claims import ClaimManager
from feature_coordinator.store import StateStore
claim = ClaimManager(StateStore(Path(sys.argv[1]))).claim(sys.argv[2], role=sys.argv[3], branch="feature/f", head_sha="c" * 40)
"""


def seed(store: StateStore, *specs: tuple[str, str]) -> None:
    """Tasks (id, role), all ready, in feature 'f'."""
    with store.transaction():
        store.save_feature(Feature(id="f", slug="f", title="F", base_branch="dev", branch="feature/f",
                                   task_ids=[tid for tid, _ in specs]))
        for tid, role in specs:
            store.save_task(Task(id=tid, feature_id="f", role=role, title=tid, state=TaskState.READY))


@pytest.fixture
def claims(store: StateStore) -> ClaimManager:
    seed(store, ("f.api", "backend"), ("f.db", "backend"), ("f.ui", "frontend"), ("f.cmd", "cli"))
    return ClaimManager(store)


def test_claim_moves_the_task_and_records_the_owner(claims: ClaimManager, store: StateStore) -> None:
    claim = claims.claim("f.api", role="backend", branch="feature/f", head_sha=HEAD)
    assert store.load_task("f.api").state is TaskState.IN_PROGRESS
    record = store.load_claim("f.api")
    assert record["token"] == claim.token and record["identity"]["pid"] == claim.identity.pid
    claims.release(claim)
    assert store.load_claim("f.api") is None


def test_second_writer_of_the_same_role_is_rejected_even_for_another_task(claims: ClaimManager,
                                                                          store: StateStore) -> None:
    first = claims.claim("f.api", role="backend", branch="feature/f", head_sha=HEAD)
    with pytest.raises(RoleBusyError):
        claims.claim("f.db", role="backend", branch="feature/f", head_sha=HEAD)
    assert store.load_task("f.db").state is TaskState.READY  # nothing changed for the rejected claim
    claims.release(first)
    claims.claim("f.db", role="backend", branch="feature/f", head_sha=HEAD).release_locks()


def test_different_roles_work_concurrently(claims: ClaimManager) -> None:
    held = [claims.claim(t, role=r, branch="feature/f", head_sha=HEAD)
            for t, r in (("f.api", "backend"), ("f.ui", "frontend"), ("f.cmd", "cli"))]
    assert {c.role for c in held} == {"backend", "frontend", "cli"}
    for claim in held:
        claims.release(claim)


def test_claim_preconditions(claims: ClaimManager) -> None:
    with pytest.raises(ClaimError, match="belongs to role"):
        claims.claim("f.api", role="cli", branch="feature/f", head_sha=HEAD)
    claim = claims.claim("f.api", role="backend", branch="feature/f", head_sha=HEAD)
    with pytest.raises(ClaimError, match="already claimed"):
        claims.claim("f.api", role="backend", branch="feature/f", head_sha=HEAD)
    claims.release(claim)  # the task stays in-progress (not yielded)
    with pytest.raises(ClaimError, match="not ready"):
        claims.claim("f.api", role="backend", branch="feature/f", head_sha=HEAD)


def test_release_with_yield_returns_the_task_to_ready(claims: ClaimManager, store: StateStore) -> None:
    claim = claims.claim("f.api", role="backend", branch="feature/f", head_sha=HEAD)
    claims.heartbeat(claim, {"step": 2})
    assert store.load_claim("f.api")["checkpoint"] == {"step": 2}
    claims.release(claim, yield_task=True)
    assert store.load_task("f.api").state is TaskState.READY


def test_same_role_writer_in_another_process_is_rejected(claims: ClaimManager, state_dir: Path,
                                                         holders: list[Holder]) -> None:
    holders.append(Holder(CLAIM_AND_HOLD, str(state_dir), "f.api", "backend"))
    with pytest.raises(RoleBusyError):
        claims.claim("f.db", role="backend", branch="feature/f", head_sha=HEAD)
    claims.claim("f.ui", role="frontend", branch="feature/f", head_sha=HEAD).release_locks()  # distinct role: fine


RACER = """
import sys, time
from pathlib import Path
from feature_coordinator.claims import ClaimManager
from feature_coordinator.store import StateStore
go = Path(sys.argv[2])
while not go.exists():
    time.sleep(0.002)
try:
    claim = ClaimManager(StateStore(Path(sys.argv[1]))).claim(sys.argv[3], role=sys.argv[4], branch="feature/f",
                                                              head_sha="c" * 40)
    print("won", flush=True)
except Exception as exc:
    print(type(exc).__name__, flush=True)
sys.stdin.read()
"""


def race(state_dir: Path, tmp_path: Path, contenders: list[tuple[str, str]]) -> list[str]:
    """Start one process per (task, role), release them together, return their outcomes."""
    go = tmp_path / "go"
    procs = [subprocess.Popen([sys.executable, "-c", RACER, str(state_dir), str(go), task_id, role],  # pylint: disable=consider-using-with
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                              env=child_env()) for task_id, role in contenders]
    try:
        time.sleep(0.5)
        go.touch()
        return [p.stdout.readline().strip() for p in procs]
    finally:
        for proc in procs:
            proc.stdin.close()
            proc.wait(timeout=30)


def test_racing_processes_claim_a_task_exactly_once(claims: ClaimManager, state_dir: Path, tmp_path: Path) -> None:
    del claims
    outcomes = race(state_dir, tmp_path, [("f.api", "backend")] * 6)
    assert outcomes.count("won") == 1, outcomes
    assert set(outcomes) - {"won"} <= {"ClaimError", "RoleBusyError", "LockBusyError"}


def test_racing_same_role_different_tasks_admits_one_writer(claims: ClaimManager, state_dir: Path,
                                                            tmp_path: Path) -> None:
    del claims
    outcomes = race(state_dir, tmp_path, [("f.api", "backend"), ("f.db", "backend")] * 3)
    assert outcomes.count("won") == 1, outcomes


def test_racing_distinct_roles_all_proceed(claims: ClaimManager, state_dir: Path, tmp_path: Path) -> None:
    del claims
    outcomes = race(state_dir, tmp_path, [("f.api", "backend"), ("f.ui", "frontend"), ("f.cmd", "cli")])
    assert outcomes == ["won", "won", "won"]


def test_full_project_waits_for_scoped_writers_then_runs_exclusively(claims: ClaimManager) -> None:
    writer = claims.claim("f.ui", role="frontend", branch="feature/f", head_sha=HEAD)
    with pytest.raises(ClaimError, match="request_full_project"):
        claims.claim_full_project()
    claims.request_full_project("shared root change")
    with pytest.raises(FullProjectActiveError):  # scheduling of new scoped work has stopped
        claims.claim("f.api", role="backend", branch="feature/f", head_sha=HEAD)
    with pytest.raises(WritersActiveError, match="frontend"):  # the running writer is not preempted
        claims.claim_full_project()
    claims.release(writer, yield_task=True)
    full = claims.claim_full_project()
    claims.release_full_project(full)
    claims.claim("f.api", role="backend", branch="feature/f", head_sha=HEAD).release_locks()


def test_crashed_worker_is_recovered_without_a_duplicate_writer(claims: ClaimManager, store: StateStore,
                                                                 state_dir: Path, holders: list[Holder]) -> None:
    holder = Holder(CLAIM_AND_HOLD, str(state_dir), "f.api", "backend")
    holders.append(holder)
    report = claims.recover()
    assert [r["task_id"] for r in report.live] == ["f.api"] and not report.recovered  # alive: untouched
    holder.kill()
    with pytest.raises(ClaimError, match="stale claim"):  # no replacement before recovery
        claims.claim("f.api", role="backend", branch="feature/f", head_sha=HEAD)
    report = claims.recover()
    assert [r["task_id"] for r in report.recovered] == ["f.api"] and not report.unknown_lock_holders
    assert store.load_task("f.api").state is TaskState.READY
    assert any(e["kind"] == "recovered" for e in store.events())
    claims.claim("f.api", role="backend", branch="feature/f", head_sha=HEAD).release_locks()


def test_recovery_never_steals_a_role_lock_held_without_a_claim(claims: ClaimManager, state_dir: Path,
                                                                 holders: list[Holder]) -> None:
    holder = Holder(CLAIM_AND_HOLD, str(state_dir), "f.api", "backend")
    holders.append(holder)
    holder.kill()
    rogue = Holder("import sys\nfrom pathlib import Path\nfrom feature_coordinator.locks import FileLock\n"
                   "lock = FileLock(Path(sys.argv[1]) / 'locks' / 'role-backend.lock').acquire()", str(state_dir))
    holders.append(rogue)
    report = claims.recover()
    assert report.unknown_lock_holders == ["backend"]
    with pytest.raises(RoleBusyError):
        claims.claim("f.db", role="backend", branch="feature/f", head_sha=HEAD)


def test_stale_full_project_request_is_cleared(claims: ClaimManager, state_dir: Path) -> None:
    result = run_py("import sys\nfrom pathlib import Path\nfrom feature_coordinator.claims import ClaimManager\n"
                    "from feature_coordinator.store import StateStore\n"
                    "ClaimManager(StateStore(Path(sys.argv[1]))).request_full_project('then crash')", str(state_dir))
    assert result.returncode == 0, result.stderr
    with pytest.raises(FullProjectActiveError):
        claims.claim("f.api", role="backend", branch="feature/f", head_sha=HEAD)
    assert claims.recover().cleared_full_project_request
    claims.claim("f.api", role="backend", branch="feature/f", head_sha=HEAD).release_locks()


def test_state_writes_are_revision_checked(store: StateStore) -> None:
    seed(store, ("f.api", "backend"))
    stale, fresh = store.load_task("f.api"), store.load_task("f.api")
    with store.transaction():
        fresh.title = "new"
        store.save_task(fresh)
        stale.title = "old"
        with pytest.raises(Exception, match="concurrently"):
            store.save_task(stale)
    with pytest.raises(Exception, match="transaction"):
        store.save_task(store.load_task("f.api"))
