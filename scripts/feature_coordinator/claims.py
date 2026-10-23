"""Atomic task + role-slot claims, full-project exclusivity and crash recovery (v5 §3.1–§3.2).

Claiming a task, inside one coordinator.lock critical section:
  1. refuse while a full-project session is requested (scheduling of scoped work stops);
  2. refuse if the task already has a claim (live → busy; dead → stale, run recover());
  3. refuse unless the task is `ready` and the caller's role matches;
  4. take project.lock SHARED (fails while a full-project writer holds it exclusively);
  5. take role-<role>.lock EXCLUSIVE (fails while another writer of that role is active);
  6. record the claim (pid + start time + boot id, token, branch/HEAD) and move the task
     to `in-progress`.
The two flocks stay held by the claiming process until release() — or until it dies,
when the kernel drops them. recover() reclaims a claim only when its holder is provably
gone (PID absent, start time different, zombie, or another boot): a lock is never stolen
from a live worker.

W4: while a sequential Worker (max_parallel 1) runs, it holds the `agent-exclusive` flag. A claim
is then refused while ANY other claim exists in the checkout — live, or stale and not yet
recovered (its agent may still write) — so a manually launched agent can never run next to the
Worker's, and the Worker never starts a second writer while ownership is uncertain. The check
runs inside the claim's coordinator.lock section, so two claims cannot both pass it.

W3: a dead holder is not enough. A native agent runs in its own session and can outlive the
worker that supervised it; the claim records the run's tag and the agent's process identity
(the claim's progress data), and recover() keeps a claim whose run still has a live process
(`orphaned`), so no second execution of the task can start while the old one may write.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .errors import (CheckoutBusyError, ClaimError, FullProjectActiveError, LockBusyError, RoleBusyError,
                     WritersActiveError)
from .locks import FileLock, ProcessIdentity
from .model import TaskState, transition_task, utc_now
from .store import StateStore
from .supervisor import RUN_TAG_ENV, tagged_processes

FULL_PROJECT_FLAG = "full-project"
EXCLUSIVE_FLAG = "agent-exclusive"  # W4: held by a sequential Worker; see the module docstring


def exclusive_holder(store: StateStore) -> dict[str, Any] | None:
    """The live holder of the agent-exclusive flag, or None (absent, or its holder is gone)."""
    flag = store.get_flag(EXCLUSIVE_FLAG)
    if not flag:
        return None
    try:
        alive = ProcessIdentity.from_dict(flag["identity"]).is_alive()
    except (KeyError, TypeError, ValueError):
        return None
    return flag if alive else None


def hold_exclusive(store: StateStore, holder: str) -> dict[str, Any]:
    """Take the agent-exclusive flag for this process (refused while another live holder has it)."""
    with store.transaction():
        current = exclusive_holder(store)
        me = ProcessIdentity.current().to_dict()
        if current and current["identity"] != me:
            raise CheckoutBusyError(f"another sequential Worker ({current.get('holder')}, pid "
                                    f"{current['identity']['pid']}) already owns this checkout")
        flag = {"holder": holder, "identity": me, "since": utc_now()}
        store.set_flag(EXCLUSIVE_FLAG, flag)
        store.append_event("agent-exclusive-held", holder=holder, pid=me["pid"])
    return flag


def drop_exclusive(store: StateStore) -> None:
    """Give the agent-exclusive flag back (only this process's own flag)."""
    with store.transaction():
        flag = store.get_flag(EXCLUSIVE_FLAG)
        if flag and flag.get("identity") == ProcessIdentity.current().to_dict():
            store.set_flag(EXCLUSIVE_FLAG, None)
            store.append_event("agent-exclusive-released", holder=flag.get("holder"))


@dataclass
class Claim:  # pylint: disable=too-many-instance-attributes
    """A live claim held by this process."""

    task_id: str
    feature_id: str
    role: str
    token: str
    identity: ProcessIdentity
    branch: str
    head_sha: str
    acquired_at: str = field(default_factory=utc_now)
    heartbeat_at: str = field(default_factory=utc_now)
    checkpoint: dict[str, Any] = field(default_factory=dict)
    locks: list[FileLock] = field(default_factory=list, repr=False)

    def to_dict(self) -> dict[str, Any]:
        """The persisted claim record (no secrets: the token only identifies the claim)."""
        return {
            "task_id": self.task_id, "feature_id": self.feature_id, "role": self.role, "token": self.token,
            "identity": self.identity.to_dict(), "branch": self.branch, "head_sha": self.head_sha,
            "acquired_at": self.acquired_at, "heartbeat_at": self.heartbeat_at, "checkpoint": self.checkpoint,
        }

    def release_locks(self) -> None:
        """Drop the flocks (innermost first)."""
        for lock in reversed(self.locks):
            lock.release()
        self.locks.clear()


@dataclass
class FullProjectClaim:
    """Exclusive access to the whole checkout while no scoped writer is active."""

    token: str
    identity: ProcessIdentity
    lock: FileLock = field(repr=False)


@dataclass
class RecoveryReport:
    """What recover() found."""

    recovered: list[dict[str, Any]] = field(default_factory=list)
    live: list[dict[str, Any]] = field(default_factory=list)
    unknown_lock_holders: list[str] = field(default_factory=list)
    cleared_full_project_request: bool = False
    unresponsive: list[dict[str, Any]] = field(default_factory=list)  # alive, but no recent heartbeat
    orphaned: list[dict[str, Any]] = field(default_factory=list)  # W3: holder dead, agent processes alive


def orphaned_agents(claim: dict[str, Any] | None) -> list[int]:
    """Processes of the run recorded in *claim* that are still alive (W3): the agent itself, by
    its process identity (pid + start time + boot id, so a reused PID never matches), and every
    process carrying the run's tag in its environment."""
    progress = dict((claim or {}).get("checkpoint") or {})
    found: set[int] = set()
    if progress.get("run_tag"):
        found.update(tagged_processes(RUN_TAG_ENV, str(progress["run_tag"])))
    agent = progress.get("agent")
    if isinstance(agent, dict):
        try:
            identity = ProcessIdentity.from_dict(agent)
        except (KeyError, TypeError, ValueError):
            identity = None
        if identity is not None and identity.is_alive():
            found.add(identity.pid)
    return sorted(found)


def heartbeat_age(record: dict[str, Any]) -> float | None:
    """Seconds since the claim's last heartbeat (None if unknown)."""
    try:
        stamp = datetime.fromisoformat(str(record["heartbeat_at"]).replace("Z", "+00:00"))
    except (KeyError, ValueError):
        return None
    return (datetime.now(timezone.utc) - stamp).total_seconds()


class ClaimManager:
    """Claims for one state store."""

    def __init__(self, store: StateStore) -> None:
        self.store = store

    # -- scoped claims -------------------------------------------------------

    def claim(self, task_id: str, *, role: str, branch: str, head_sha: str) -> Claim:
        """Atomically claim *task_id* and the role slot. See the module docstring."""
        locks = self.store.locks
        with self.store.transaction():
            if self.store.get_flag(FULL_PROJECT_FLAG):
                raise FullProjectActiveError("a full-project session is requested; no new scoped claims")
            task = self.store.load_task(task_id)
            if task.role != role:
                raise ClaimError(f"task {task_id!r} belongs to role {task.role!r}, not {role!r}")
            excl = exclusive_holder(self.store)
            others = sorted(t for t in self.store.list_claims() if t != task_id)
            if excl and others:
                raise CheckoutBusyError(f"sequential mode ({excl.get('holder')}, pid {excl['identity']['pid']}): "
                                        f"another agent's claim exists on this checkout ({', '.join(others)})")
            existing = self.store.load_claim(task_id)
            if existing is not None:
                holder = ProcessIdentity.from_dict(existing["identity"])
                if holder.is_alive():
                    raise ClaimError(f"task {task_id!r} is already claimed by live pid {holder.pid}")
                raise ClaimError(f"task {task_id!r} has a stale claim (pid {holder.pid} is gone); run recovery first")
            if task.state is not TaskState.READY:
                raise ClaimError(f"task {task_id!r} is {task.state.value}, not ready")
            held: list[FileLock] = []
            try:
                held.append(locks.project(shared=True, busy_error=FullProjectActiveError).acquire())
                held.append(locks.role(role, busy_error=RoleBusyError).acquire())
                claim = Claim(task_id=task_id, feature_id=task.feature_id, role=role, token=secrets.token_hex(16),
                              identity=ProcessIdentity.current(), branch=branch, head_sha=head_sha, locks=held)
                transition_task(task, TaskState.IN_PROGRESS, f"claimed by pid {claim.identity.pid}")
                self.store.save_task(task)
                self.store.save_claim(task_id, claim.to_dict())
                self.store.append_event("claim", task=task_id, role=role, pid=claim.identity.pid)
            except BaseException:
                for lock in reversed(held):
                    lock.release()
                raise
        return claim

    def _check_owner(self, claim: Claim) -> None:
        record = self.store.load_claim(claim.task_id)
        if record is None or record.get("token") != claim.token:
            raise ClaimError(f"claim on {claim.task_id!r} is no longer held by this worker")

    def heartbeat(self, claim: Claim, checkpoint: dict[str, Any] | None = None) -> None:
        """Refresh the heartbeat (and optionally the checkpoint) of a live claim."""
        with self.store.transaction():
            self._check_owner(claim)
            claim.heartbeat_at = utc_now()
            if checkpoint is not None:
                claim.checkpoint = dict(checkpoint)
            self.store.save_claim(claim.task_id, claim.to_dict())

    def release(self, claim: Claim, *, yield_task: bool = False) -> None:
        """Delete the claim record and drop the locks. With *yield_task*, an unfinished
        `in-progress` task goes back to `ready` for a later claim."""
        try:
            with self.store.transaction():
                self._check_owner(claim)
                if yield_task:
                    task = self.store.load_task(claim.task_id)
                    if task.state is TaskState.IN_PROGRESS:
                        transition_task(task, TaskState.READY, "worker yielded")
                        self.store.save_task(task)
                self.store.delete_claim(claim.task_id)
                self.store.append_event("release", task=claim.task_id, role=claim.role, yielded=yield_task)
        finally:
            claim.release_locks()

    def live_claims(self) -> dict[str, dict[str, Any]]:
        """Claim records whose holder process is alive."""
        return {task_id: rec for task_id, rec in self.store.list_claims().items()
                if ProcessIdentity.from_dict(rec["identity"]).is_alive()}

    # -- full-project exclusivity --------------------------------------------

    def request_full_project(self, reason: str) -> None:
        """Stop scheduling new scoped claims; running writers continue until they finish."""
        with self.store.transaction():
            self.store.set_flag(FULL_PROJECT_FLAG, {"reason": reason, "requested_at": utc_now(),
                                                    "identity": ProcessIdentity.current().to_dict()})
            self.store.append_event("full-project-requested", reason=reason)

    def withdraw_full_project_request(self, reason: str) -> None:
        """Withdraw a pending full-project request (before it was claimed); scoped work resumes."""
        with self.store.transaction():
            self.store.set_flag(FULL_PROJECT_FLAG, None)
            self.store.append_event("full-project-request-withdrawn", reason=reason)

    def claim_full_project(self) -> FullProjectClaim:
        """Take project.lock exclusively. Fails while any scoped writer is still active."""
        with self.store.transaction():
            if not self.store.get_flag(FULL_PROJECT_FLAG):
                raise ClaimError("request_full_project() first, so that no new scoped work is scheduled")
            active = sorted({rec["role"] for rec in self.live_claims().values()})
            try:
                lock = self.store.locks.project(shared=False, busy_error=WritersActiveError).acquire()
            except WritersActiveError as exc:
                raise WritersActiveError(f"scoped writers still active ({', '.join(active) or 'unknown'}); "
                                         "wait for them to finish") from exc
            self.store.append_event("full-project-claimed")
        return FullProjectClaim(token=secrets.token_hex(16), identity=ProcessIdentity.current(), lock=lock)

    def release_full_project(self, claim: FullProjectClaim) -> None:
        """End the full-project session and allow scoped claims again."""
        try:
            with self.store.transaction():
                self.store.set_flag(FULL_PROJECT_FLAG, None)
                self.store.append_event("full-project-released")
        finally:
            claim.lock.release()

    # -- recovery ------------------------------------------------------------

    def recover(self, *, stale_after_seconds: float | None = None,
                task_ids: list[str] | None = None) -> RecoveryReport:
        """Reclaim claims whose holder is provably dead; report everything else.

        A live holder is never reclaimed. With *stale_after_seconds*, a live holder whose last
        heartbeat is older than that is reported as unresponsive (the operator may `stop` it).
        A dead holder whose run still has live agent processes is kept and reported as orphaned
        (W3). *task_ids* limits recovery to those tasks (default: every claim)."""
        report = RecoveryReport()
        with self.store.transaction():
            for task_id, record in self.store.list_claims().items():
                if task_ids is not None and task_id not in task_ids:
                    continue
                holder = ProcessIdentity.from_dict(record["identity"])
                if holder.is_alive():
                    report.live.append(record)
                    age = heartbeat_age(record)
                    if stale_after_seconds is not None and age is not None and age > stale_after_seconds:
                        report.unresponsive.append({**record, "heartbeat_age_seconds": round(age, 1)})
                    continue
                orphans = orphaned_agents(record)
                if orphans:  # the worker is gone, but its agent may still write: no second execution
                    report.orphaned.append({**record, "orphans": orphans})
                    self.store.append_event("recovery-refused", task=task_id, role=record["role"], pid=holder.pid,
                                            orphans=orphans)
                    continue
                task = self.store.load_task(task_id)
                if task.state is TaskState.IN_PROGRESS:
                    transition_task(task, TaskState.READY, f"recovered: worker pid {holder.pid} is gone")
                    self.store.save_task(task)
                self.store.delete_claim(task_id)
                self.store.append_event("recovered", task=task_id, role=record["role"], pid=holder.pid)
                report.recovered.append(record)
            live_roles = {rec["role"] for rec in report.live}
            for role in sorted({rec["role"] for rec in report.recovered} - live_roles):
                probe = self.store.locks.role(role)
                try:
                    probe.acquire()
                except LockBusyError:
                    report.unknown_lock_holders.append(role)  # held by a process with no claim: do not touch
                else:
                    probe.release()
            flag = self.store.get_flag(FULL_PROJECT_FLAG)
            if flag and not ProcessIdentity.from_dict(flag["identity"]).is_alive():
                self.store.set_flag(FULL_PROJECT_FLAG, None)
                self.store.append_event("full-project-request-cleared", reason="requester is gone")
                report.cleared_full_project_request = True
        return report
