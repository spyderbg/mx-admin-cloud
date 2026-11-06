"""W4: autonomous Worker policy — persistent scheduling, outcome classification, bounded retries,
health. Pure policy and records; the long-running loop that applies them is service.py.

Records (the existing state store; nothing here is a second task database):

  scheduling/<task-id>.json   PORTABLE, per task: launches (counted BEFORE an agent starts, so a
                              crash can never refund a budget), failures per category, the next
                              allowed attempt, why the task waits, what needs an operator, the
                              launch in flight, a bounded outcome history
  service/worker.json         MACHINE-SPECIFIC: the service's state, process identity, heartbeat,
                              current task, last error, startup-failure history
  flags/worker-control.json   operator requests: pause, resume, stop (graceful or after the
                              current task)

The task state machine is unchanged. A scheduling status (ready, running, waiting-retry,
waiting-subscription, blocked, verified, needs-attention, …) is derived from the task's state
plus its scheduling record; FAILED → READY is used only when the policy schedules a retry.

Outcome categories (classify):

  success / dependency / cancelled / interrupted / external   — not failures, no budget used
  transient     network errors, a CLI briefly missing, an agent timeout, a deferred commit —
                bounded exponential backoff (initial · 2^(n-1), capped), then attention
  subscription  a usage limit was reported — WAITING; the next attempt at the reset time the CLI
                reported in a machine-readable form, else after a conservative recheck interval;
                bounded; never another provider, model or billing method
  validation    required gates failed — attention, unless auto-fix is enabled AND the task has
                steps: then its last step is redone (W3 --redo-from), bounded
  critical      integrity problems: recovery conflicts, a rewritten branch, foreign commits,
                unexplained file changes, scope violations, an API key or unknown login, a
                surviving agent, a required gate that could not run at all (its sandbox or tool
                unavailable: the code was never judged) — never retried; the task needs an operator
  unknown       anything not recognized — fails closed exactly like critical

The text patterns that recognize usage limits and network errors are heuristics over the CLIs'
own output; they are tested with fake agents only (a real exhausted subscription was never
observed). Unrecognized text is `unknown`, never assumed transient.
"""

from __future__ import annotations

import os
import re
import secrets
import socket
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from .claims import heartbeat_age, orphaned_agents
from .config import ProjectConfig, WorkerConfig
from .errors import CoordinatorError, StateDirError
from .github_api import redact_secrets, sanitize
from .locks import ProcessIdentity
from .model import Task, TaskState, utc_now, validate_dag
from .store import StateStore

SCHEDULING_SCHEMA = 1
SERVICE_SCHEMA = 1
CONTROL_FLAG = "worker-control"
SERVICE_ID = "worker"
HISTORY_LIMIT = 20
EXIT_ALREADY_RUNNING = 73   # EX_CANTCREAT: another service owns this state directory
EXIT_DO_NOT_RESTART = 78    # EX_CONFIG: persistent startup failure; systemd RestartPreventExitStatus

SUCCESS, DEPENDENCY, CANCELLED, INTERRUPTED, EXTERNAL = "success", "dependency", "cancelled", "interrupted", "external"
TRANSIENT, SUBSCRIPTION, VALIDATION = "transient", "subscription", "validation"
CRITICAL, UNKNOWN = "critical", "unknown"
NOT_FAILURES = (SUCCESS, DEPENDENCY, CANCELLED, INTERRUPTED, EXTERNAL)

# Service states (conceptual W4 states; OFFLINE is derived when the recorded process is gone).
AVAILABLE, BUSY, WAITING, PAUSED, RECOVERING = "AVAILABLE", "BUSY", "WAITING", "PAUSED", "RECOVERING"
NEEDS_ATTENTION, STOPPING, OFFLINE, UNRESPONSIVE = "NEEDS_ATTENTION", "STOPPING", "OFFLINE", "UNRESPONSIVE"

_USAGE_LIMIT = re.compile(r"usage[ _]limit|limit reached|hit your (?:usage )?limit|quota (?:exceeded|exhausted)"
                          r"|out of (?:credits|usage)", re.IGNORECASE)
_RESET_EPOCH = re.compile(r"limit reached\|(\d{10})\b", re.IGNORECASE)  # Claude Code's machine-readable form
_TRANSIENT = re.compile(r"ECONNRESET|ECONNREFUSED|ETIMEDOUT|ENOTFOUND|EAI_AGAIN|ENETUNREACH|network (?:error|is "
                        r"unreachable)|connection (?:reset|refused|timed out|error)|socket hang up|fetch failed"
                        r"|getaddrinfo|temporar(?:y|ily) unavailable|service unavailable|overloaded"
                        r"|stream disconnected|\b(?:502|503|504|529)\b", re.IGNORECASE)
_CRITICAL = re.compile(r"API key|apiKeySource|model substitution|subscription authentication|not logged in"
                       r"|identity mismatch|history was rewritten|recovery conflict|commit conflicts|commit refused"
                       r"|may not write|changed the Git state|rejected|lacks required flags|interpreter",
                       re.IGNORECASE)

OPERATOR_ACTIONS = {
    TRANSIENT: "check connectivity and the agent CLI; then "
               "`python -m feature_coordinator worker reset-retries TASK --apply`",
    SUBSCRIPTION: "check the subscription's usage; then "
                  "`python -m feature_coordinator worker reset-retries TASK --apply`",
    VALIDATION: "inspect the failed gates (`python -m feature_coordinator checkpoints TASK`); fix or "
                "`python -m feature_coordinator recover TASK --apply --redo-from STEP`, then "
                "`python -m feature_coordinator worker reset-retries TASK --apply`",
    CRITICAL: "inspect the evidence (`python -m feature_coordinator recover TASK`, the launch log, the run "
              "directory); resolve it, then `python -m feature_coordinator worker reset-retries TASK --apply` "
              "(logins are never changed automatically)",
    UNKNOWN: "inspect the launch log and the run directory; decide, then "
             "`python -m feature_coordinator worker reset-retries TASK --apply`",
}


def scrub(text: Any, limit: int = 300) -> str:
    """`sanitize` (GitHub tokens, control sequences, length) plus provider keys and secret
    assignments: everything the W4 service records, emits as events or prints."""
    return sanitize(redact_secrets(str(text)), limit)


def iso(seconds: float | None) -> str | None:
    """ISO-8601 UTC for an epoch timestamp (None stays None)."""
    if seconds is None:
        return None
    return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def epoch(stamp: str | None) -> float | None:
    """Inverse of iso() / utc_now() (None or unparsable → None)."""
    if not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------

_SCHEDULING_FIELDS = frozenset({"schema_version", "task_id", "feature_id", "launches", "attempts",
                                "next_attempt_at", "waiting", "attention", "in_flight", "history", "revision"})


@dataclass
class Scheduling:  # pylint: disable=too-many-instance-attributes
    """Scheduling metadata of one task (portable)."""

    id: str
    feature_id: str
    launches: int = 0                                     # agent launches by any service instance (write-ahead)
    attempts: dict[str, int] = field(default_factory=dict)  # failures per category
    next_attempt_at: float | None = None                  # epoch seconds; None = no earliest time
    waiting: dict[str, Any] | None = None                 # {category, reason, since, until}
    attention: dict[str, Any] | None = None               # {category, reason, action, since}
    in_flight: dict[str, Any] | None = None               # {launch, instance, worker_id, started_at}
    history: list[dict[str, Any]] = field(default_factory=list)
    revision: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Deterministic JSON form."""
        data = asdict(self)
        data["task_id"] = data.pop("id")
        data["schema_version"] = SCHEDULING_SCHEMA
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Scheduling":
        """Strict: another schema or unknown fields are refused (never dropped)."""
        if data.get("schema_version") != SCHEDULING_SCHEMA:
            raise StateDirError(f"scheduling record {data.get('task_id')!r}: unsupported schema "
                                f"{data.get('schema_version')!r}")
        unknown = sorted(set(data) - _SCHEDULING_FIELDS)
        if unknown:
            raise StateDirError(f"scheduling record {data.get('task_id')!r} has unknown fields {unknown}")
        values = {k: v for k, v in data.items() if k not in ("schema_version", "task_id")}
        return cls(id=str(data["task_id"]), **values)


def load_scheduling(store: StateStore, task: Task) -> Scheduling:
    """The task's scheduling record (a new, empty one if it has none yet)."""
    raw = store.load_record("scheduling", task.id)
    return Scheduling.from_dict(raw) if raw else Scheduling(id=task.id, feature_id=task.feature_id)


_SERVICE_FIELDS = frozenset({"schema_version", "id", "worker_id", "state", "state_since", "reason", "identity",
                             "host", "instance", "started_at", "heartbeat_at", "current", "last_error",
                             "last_checkpoint", "counters", "revision"})


@dataclass
class ServiceRecord:  # pylint: disable=too-many-instance-attributes
    """The Worker service's health record (machine-specific)."""

    worker_id: str
    id: str = SERVICE_ID
    state: str = OFFLINE
    state_since: str = ""
    reason: str = ""
    identity: dict[str, Any] | None = None   # ProcessIdentity of the running service (liveness is re-verified)
    host: str = ""
    instance: str = ""                       # random per start: distinguishes service lifetimes
    started_at: str = ""
    heartbeat_at: str = ""
    current: dict[str, Any] | None = None    # {feature, task, role, agent, model, launch, started_at, step}
    last_error: dict[str, Any] | None = None
    last_checkpoint: dict[str, Any] | None = None
    counters: dict[str, int] = field(default_factory=dict)
    revision: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Deterministic JSON form."""
        return {**asdict(self), "schema_version": SERVICE_SCHEMA}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ServiceRecord":
        """Strict, like Scheduling.from_dict."""
        if data.get("schema_version") != SERVICE_SCHEMA:
            raise StateDirError(f"service record: unsupported schema {data.get('schema_version')!r}")
        unknown = sorted(set(data) - _SERVICE_FIELDS)
        if unknown:
            raise StateDirError(f"service record has unknown fields {unknown}")
        return cls(**{k: v for k, v in data.items() if k != "schema_version"})


def load_service(store: StateStore) -> ServiceRecord | None:
    """The service record (None if no service ever ran). Corrupt → StateDirError."""
    raw = store.load_record("service", SERVICE_ID)
    return ServiceRecord.from_dict(raw) if raw else None


def new_service_record() -> ServiceRecord:
    """A first record: a stable random worker id (later reported to a central coordinator)."""
    return ServiceRecord(worker_id=f"worker-{socket.gethostname()}-{secrets.token_hex(4)}", host=socket.gethostname())


def read_control(store: StateStore) -> dict[str, Any]:
    """The operator's requests: {"pause": bool, "stop": None | "graceful" | "after-current", ...}."""
    return dict(store.get_flag(CONTROL_FLAG) or {})


def write_control(store: StateStore, **changes: Any) -> dict[str, Any]:
    """Update the operator's requests (inside a transaction; recorded as an event)."""
    with store.transaction():
        control = read_control(store)
        control.update(changes, requested_at=utc_now())
        store.set_flag(CONTROL_FLAG, control)
        store.append_event("worker-control", **changes)
    return control


def process_start_epoch(identity: ProcessIdentity) -> float | None:
    """When the process started (epoch), from its start ticks and the boot time (None if unknown)."""
    try:
        with open("/proc/stat", encoding="ascii") as stat:
            btime = next(int(line.split()[1]) for line in stat if line.startswith("btime "))
        return btime + identity.start_ticks / os.sysconf("SC_CLK_TCK")
    except (OSError, StopIteration, ValueError):
        return None


def safe_identity(data: Any) -> ProcessIdentity | None:
    """A recorded process identity, or None when it is missing or malformed (ambiguous: the
    caller fails closed — nothing is recovered, stopped or started on its account)."""
    try:
        return ProcessIdentity.from_dict(data)
    except (KeyError, TypeError, ValueError):
        return None


def hung_after_seconds(runtime: Any) -> float:
    """W4 watchdog: a launch whose claim has not heartbeated this long is hung. Longer than any
    legitimate silence of a live launch (a stale heartbeat alone proves nothing): the agent's own
    deadline, a full gate run and the termination grace on top of the stale threshold."""
    return float(runtime.stale_heartbeat_seconds + runtime.task_timeout_seconds + runtime.gate_timeout_seconds
                 + runtime.terminate_grace_seconds)


def claim_view(claim: Mapping[str, Any], runtime: Any) -> dict[str, Any]:
    """Read-only watchdog view of one claim: who holds it, is it heartbeating, the agent and its
    run deadline. Status: running | slow (no heartbeat beyond the stale threshold — gates or a long
    step; never a reason to stop) | hung (beyond the hung threshold) | deadline-exceeded (the agent
    is alive past its run deadline) | orphaned (the holder is gone, run processes live on) | stale
    (the holder is gone, nothing of the run lives: recoverable) | ambiguous (unreadable identity)."""
    holder = safe_identity(claim.get("identity"))
    progress = dict(claim.get("checkpoint") or {})
    agent = safe_identity(progress.get("agent")) if isinstance(progress.get("agent"), dict) else None
    started = process_start_epoch(agent) if agent else None
    deadline = started + runtime.task_timeout_seconds + runtime.terminate_grace_seconds if started else None
    agent_alive = agent.is_alive() if agent else None
    age = heartbeat_age(dict(claim))
    if holder is None:
        status = "ambiguous"
    elif holder.is_alive():
        if agent_alive and deadline is not None and time.time() > deadline:
            status = "deadline-exceeded"
        elif age is not None and age > hung_after_seconds(runtime):
            status = "hung"
        elif age is not None and age > runtime.stale_heartbeat_seconds:
            status = "slow"
        else:
            status = "running"
    else:
        status = "orphaned" if orphaned_agents(dict(claim)) else "stale"
    return {"status": status, "holder_pid": (claim.get("identity") or {}).get("pid"),
            "holder_alive": holder.is_alive() if holder else None,
            "heartbeat_age_seconds": round(age, 1) if age is not None else None, "step": progress.get("step"),
            "agent_pid": agent.pid if agent else None, "agent_alive": agent_alive, "agent_started_at": iso(started),
            "deadline": iso(deadline)}


# ---------------------------------------------------------------------------
# Outcome classification
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Outcome:
    """What one launch (or a pre-launch check) amounted to."""

    category: str
    reason: str
    retry_at: float | None = None   # a reset time reported by the CLI in a machine-readable form
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:  # reasons may quote CLI output: scrubbed once, everywhere
        object.__setattr__(self, "reason", scrub(self.reason, 1000))


def _usage_limit(texts: Sequence[str]) -> tuple[bool, float | None]:
    joined = "\n".join(texts)
    if not _USAGE_LIMIT.search(joined):
        return False, None
    reset = _RESET_EPOCH.search(joined)
    return True, float(reset.group(1)) if reset else None


def classify(before: Task, after: Task, events: Sequence[Mapping[str, Any]], *,  # pylint: disable=too-many-return-statements,too-many-branches
             exit_code: int | None, log_text: str = "", run_texts: Sequence[str] = ()) -> Outcome:
    """Classify one launch of *before* (the task as it was) from the task's new state, the
    coordinator's events of this launch, the launch's exit code and log, and the agent output."""
    del before
    kinds = {e.get("kind"): e for e in events}
    last_reason = after.history[-1].reason if after.history else ""
    if after.state is TaskState.VERIFIED:
        return Outcome(SUCCESS, "verified")
    if after.state is TaskState.BLOCKED:
        return Outcome(DEPENDENCY, after.blocked_reason or "blocked")
    if after.state is TaskState.NEEDS_HUMAN:
        return Outcome(CRITICAL, last_reason or "needs a human decision")
    if after.state is TaskState.IN_PROGRESS:
        return Outcome(INTERRUPTED, "the launch ended without releasing its claim (recovered next)")
    if after.state is TaskState.FAILED:
        texts = [last_reason, *run_texts]
        if last_reason.startswith("gates could not run"):  # the environment, not the code: never an AI redo
            return Outcome(CRITICAL, last_reason)
        if last_reason.startswith("gates failed"):
            return Outcome(VALIDATION, last_reason)
        if _CRITICAL.search(last_reason):
            return Outcome(CRITICAL, last_reason)
        limited, reset = _usage_limit(texts)
        if limited:
            return Outcome(SUBSCRIPTION, "the subscription reported a usage limit", retry_at=reset)
        if "timed out" in last_reason:
            return Outcome(TRANSIENT, f"agent timeout: {last_reason}")
        if _TRANSIENT.search("\n".join(texts)):
            return Outcome(TRANSIENT, last_reason)
        return Outcome(UNKNOWN, last_reason or "failed without a recognized reason")
    # READY (deferred) or unchanged
    if "run-cancelled" in kinds:
        return Outcome(CANCELLED, str(kinds["run-cancelled"].get("error") or "stopped"))
    if "runner-unavailable" in kinds:
        error = str(kinds["runner-unavailable"].get("error") or "")
        limited, reset = _usage_limit([error, *run_texts])
        if "subscription authentication" in error or _CRITICAL.search(error):
            return Outcome(CRITICAL, error)
        if "disabled by configuration" in error:
            return Outcome(CRITICAL, error)  # the scheduler never launches a disabled role: inconsistent state
        if limited:
            return Outcome(SUBSCRIPTION, "the subscription reported a usage limit", retry_at=reset)
        if "is not installed" in error or _TRANSIENT.search(error):
            return Outcome(TRANSIENT, error)
        return Outcome(UNKNOWN, error or "runner unavailable")
    for kind in ("commit-deferred", "verification-deferred"):
        if kind in kinds:
            return Outcome(TRANSIENT, f"{kind}: {kinds[kind].get('error', '')}")
    if "sequential mode" in log_text or "already claimed by live pid" in log_text:
        return Outcome(EXTERNAL, "another agent owns the checkout")
    if "stale claim" in log_text:
        return Outcome(INTERRUPTED, "a stale claim must be recovered first")
    if "workspace check failed" in log_text or "identity mismatch" in log_text:
        return Outcome(CRITICAL, "the workspace is not the feature's checkout")
    return Outcome(UNKNOWN, f"launch exit {exit_code} without a recognized outcome")


# ---------------------------------------------------------------------------
# Retry policy
# ---------------------------------------------------------------------------

def backoff_seconds(config: WorkerConfig, failures: int) -> int:
    """initial · 2^(failures-1), capped at max_delay (failures ≥ 1)."""
    return min(config.transient_initial_delay_seconds * 2 ** max(failures - 1, 0), config.transient_max_delay_seconds)


def apply_outcome(rec: Scheduling, task: Task, outcome: Outcome, config: WorkerConfig, now: float,  # pylint: disable=too-many-branches
                  launch: int | None) -> dict[str, Any]:
    """Update *rec* for *outcome* (bounded, category-specific). Returns the actions the service
    performs: {"ready": bool (FAILED → READY for a retry), "redo_step": str | None}."""
    actions: dict[str, Any] = {"ready": False, "redo_step": None}
    rec.history = (rec.history + [{"at": iso(now), "launch": launch, "category": outcome.category,
                                   "reason": outcome.reason[:300], "state": task.state.value}])[-HISTORY_LIMIT:]
    if outcome.category in NOT_FAILURES:
        rec.next_attempt_at = None
        rec.waiting = ({"category": EXTERNAL, "reason": outcome.reason, "since": iso(now), "until": None}
                       if outcome.category == EXTERNAL else None)
        return actions
    rec.attempts[outcome.category] = failures = rec.attempts.get(outcome.category, 0) + 1

    def wait_until(when: float, why: str) -> None:
        rec.next_attempt_at = when
        rec.waiting = {"category": outcome.category, "reason": why, "since": iso(now), "until": iso(when)}
        actions["ready"] = task.state is TaskState.FAILED

    def attention(why: str) -> None:
        rec.attention = {"category": outcome.category, "reason": why[:1000], "since": iso(now),
                         "action": OPERATOR_ACTIONS.get(outcome.category, OPERATOR_ACTIONS[UNKNOWN])}
        rec.next_attempt_at, rec.waiting = None, None

    if outcome.category == TRANSIENT:
        if config.retry_enabled and failures <= config.transient_max_attempts:
            wait_until(now + backoff_seconds(config, failures), outcome.reason)
        else:
            attention(f"transient failures exceeded the retry budget ({config.transient_max_attempts}): "
                      f"{outcome.reason}")
    elif outcome.category == SUBSCRIPTION:
        if config.retry_enabled and config.subscription_enabled and failures <= config.subscription_max_rechecks:
            reported = outcome.retry_at if outcome.retry_at and now < outcome.retry_at <= now + 7 * 86400 else None
            wait_until(reported or now + config.subscription_recheck_interval_seconds,
                       outcome.reason + (" (reset time reported by the CLI)" if reported else
                                         " (no reliable reset time: conservative recheck)"))
        else:
            attention(f"the subscription stayed unavailable beyond {config.subscription_max_rechecks} rechecks")
    elif outcome.category == VALIDATION:
        steps = [s.key for s in task.steps]
        if config.retry_enabled and config.validation_auto_fix_enabled and steps \
                and failures <= config.validation_max_attempts:
            actions["redo_step"] = steps[-1]
            rec.next_attempt_at, rec.waiting = now, None
        else:
            attention(outcome.reason if config.validation_auto_fix_enabled and steps else
                      f"{outcome.reason} (validation auto-fix is "
                      f"{'enabled' if config.validation_auto_fix_enabled else 'disabled'}"
                      f"{'' if steps else '; the task has no steps to redo'})")
    else:  # critical, unknown: fail closed
        attention(outcome.reason)
    return actions


def eligible_tasks(config: ProjectConfig, tasks: Mapping[str, Task], schedules: Mapping[str, Scheduling],
                   now: float) -> list[Task]:
    """READY tasks (dependencies verified — READY implies it) of enabled roles, without an
    attention record, whose next allowed attempt has come; in dependency order."""
    chosen = []
    for task_id in validate_dag(tasks):
        task, rec = tasks[task_id], schedules.get(task_id)
        if task.state is not TaskState.READY or not config.role(task.role).enabled:
            continue
        if rec is not None and (rec.attention or (rec.next_attempt_at is not None and rec.next_attempt_at > now)):
            continue
        chosen.append(task)
    return chosen


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

def task_status(task: Task, rec: Scheduling | None, claim: Mapping[str, Any] | None,  # pylint: disable=too-many-return-statements
                now: float) -> str:
    """The derived scheduling status of one task."""
    if claim is not None:
        holder = safe_identity(claim.get("identity"))
        if holder is None:
            return "needs-attention"  # an unreadable claim: nothing is assumed about its processes
        if holder.is_alive():
            return "running"
        return "orphaned" if orphaned_agents(dict(claim)) else "interrupted"
    if task.state is TaskState.VERIFIED:
        return "verified"
    if (rec is not None and rec.attention) or task.state is TaskState.NEEDS_HUMAN:
        return "needs-attention"
    if task.state is TaskState.BLOCKED:
        return "blocked"
    if rec is not None and rec.next_attempt_at is not None and rec.next_attempt_at > now:
        return "waiting-subscription" if (rec.waiting or {}).get("category") == SUBSCRIPTION else "waiting-retry"
    if task.state is TaskState.FAILED:
        return "failed"
    return task.state.value


def health_report(store: StateStore, config: ProjectConfig, now: float) -> dict[str, Any]:  # pylint: disable=too-many-locals
    """Read-only, structured health: the service (alive? responsive? state), the current agent,
    every task's scheduling status, what waits and what needs an operator; W4.6.1 R2: the AI reviews (an ERROR
    review of a feature in review needs an operator)."""
    record = load_service(store)
    claims = store.list_claims()
    identity = safe_identity(record.identity) if record and record.identity else None
    alive = bool(identity and identity.is_alive())
    beat = epoch(record.heartbeat_at) if record else None
    stale_after = 3 * config.worker.heartbeat_seconds
    if record is None:
        state = "NEVER-STARTED"
    elif not alive or record.state == OFFLINE:  # gone, or stopped cleanly (its process may still be exiting)
        state = OFFLINE
    elif beat is None or now - beat > stale_after:
        state = UNRESPONSIVE
    else:
        state = record.state
    tasks: list[dict[str, Any]] = []
    for feature in store.list_features():
        if feature.state.value not in ("active", "integration", "review"):
            continue
        for task in store.load_tasks(feature).values():
            try:
                rec: Scheduling | None = load_scheduling(store, task)
            except CoordinatorError as exc:
                rec = None
                tasks.append({"task": task.id, "status": "needs-attention", "reason": str(exc)})
                continue
            claim = claims.get(task.id)
            entry = {"task": task.id, "role": task.role, "state": task.state.value,
                     "status": task_status(task, rec, claim, now), "launches": rec.launches,
                     "attempts": dict(rec.attempts), "next_attempt_at": iso(rec.next_attempt_at),
                     "waiting": rec.waiting, "attention": rec.attention}
            if claim is not None:
                view = claim_view(claim, config.runtime)
                entry["claim"] = {"pid": view["holder_pid"], "progress": claim.get("checkpoint", {}),
                                  "heartbeat_age_seconds": view["heartbeat_age_seconds"], "watchdog": view}
            blocking = blocking_survivors(rec)
            if blocking:
                entry["blocking_processes"] = blocking
            tasks.append(entry)
    attention = [t for t in tasks if t["status"] == "needs-attention"]
    waiting = [t for t in tasks if t["status"] in ("waiting-retry", "waiting-subscription")]
    from .review_worker import review_view  # pylint: disable=import-outside-toplevel
    reviews = review_view(store)
    healthy = alive and state not in (UNRESPONSIVE, NEEDS_ATTENTION) and not attention and not any(
        r.get("feature_state") == "review" and r["category"] in ("error", "unreadable") for r in reviews)
    return {"worker_id": record.worker_id if record else None, "state": state,
            "diagnosis": diagnose(state, record if alive else None, tasks, waiting),
            "recorded_state": record.state if record else None, "reason": record.reason if record else "",
            "alive": alive, "pid": (record.identity or {}).get("pid") if record else None,
            "host": record.host if record else None, "started_at": record.started_at if record else None,
            "heartbeat_at": record.heartbeat_at if record else None,
            "heartbeat_age_seconds": round(now - beat, 1) if beat is not None else None,
            "current": record.current if record else None, "last_error": record.last_error if record else None,
            "last_checkpoint": record.last_checkpoint if record else None,
            "counters": record.counters if record else {}, "tasks": tasks, "waiting": waiting,
            "attention": attention, "pending": sum(t["status"] in ("ready", "waiting-retry", "waiting-subscription",
                                                                    "interrupted", "orphaned", "running")
                                                   for t in tasks),
            "control": read_control(store), "reviews": reviews, "healthy": healthy}


def blocking_survivors(rec: Scheduling | None) -> list[int]:
    """Live processes of an earlier run that the service could not stop (their run tags are kept
    in the task's attention record): while any lives, no agent may start on the checkout."""
    from .supervisor import RUN_TAG_ENV, tagged_processes  # pylint: disable=import-outside-toplevel
    tags = ((rec.attention or {}).get("blocking_run_tags") or []) if rec is not None else []
    return sorted({pid for tag in tags for pid in tagged_processes(RUN_TAG_ENV, str(tag))})


def diagnose(state: str, live_record: ServiceRecord | None, tasks: Sequence[Mapping[str, Any]],  # pylint: disable=too-many-return-statements
             waiting: Sequence[Any]) -> str:
    """One word for what is going on (health): the most urgent condition first."""
    views = {t["claim"]["watchdog"]["status"] for t in tasks if t.get("claim")}
    phase = (((live_record.current or {}).get("watchdog") or {}).get("phase")) if live_record else None
    uncertain = phase == "uncertain" or any(t.get("blocking_processes") for t in tasks) or (
        state == NEEDS_ATTENTION and live_record is not None
        and (live_record.last_error or {}).get("category") == "cleanup-uncertain")
    for condition, word in ((state == UNRESPONSIVE, "service-heartbeat-stale"),
                            (uncertain, "cleanup-uncertain"),
                            (phase == "terminating", "termination-in-progress"),
                            ("orphaned" in views, "orphan-detected"),
                            (bool(views & {"deadline-exceeded", "hung"}), "agent-deadline-exceeded"),
                            ("ambiguous" in views or state == NEEDS_ATTENTION
                             or any(t["status"] == "needs-attention" for t in tasks), "attention-required"),
                            ("slow" in views, "agent-slow"),
                            ("running" in views, "agent-running"),
                            (state in (OFFLINE, "NEVER-STARTED"), "offline"),
                            (bool(waiting), "waiting")):
        if condition:
            return word
    return "idle"
