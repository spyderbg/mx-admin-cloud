"""Feature and task records, lifecycle state machines and the dependency DAG (v5 §7.2).

Feature: planned → active → integration → review → done   (side states: blocked, failed)
Task:    proposed → ready → in-progress → verified         (side states: blocked, failed, needs-human)

Rules:
  * A transition to the state a record is already in is a no-op (idempotent).
  * W3: a task may define ordered `steps`; each is one bounded agent invocation that ends in a
    checkpoint (checkpoints.py). The lifecycle is unchanged: step progress is checkpoint
    metadata, not a task state.
  * `verified` needs Evidence bound to a HEAD SHA, with every required gate passing.
  * A feature reaches `done` only with merge evidence — creating a PR is never enough.
  * A feature owns exactly one branch and at most one PR candidate.
  * Dependencies form a DAG; unknown IDs, self-dependencies and cycles are rejected. A task
    with unverified dependencies is `blocked`; it resumes (`ready`) once they are verified.
    Re-opening a verified task invalidates its verified dependents (they must re-verify).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterable, Mapping

from .config import ROLES
from .errors import DependencyError, EvidenceError, IllegalTransitionError

SCHEMA_VERSION = 1  # feature records
# Task records: v1 (Phases 2–W2) → v2 (W3: `steps`). v1 records are migrated explicitly on load
# (migrate_task_record); a record with unknown fields or another version is refused, never
# loaded with data silently dropped. Written records are always v2.
TASK_SCHEMA_VERSION = 2
TASK_SCHEMA_VERSIONS = (1, 2)
MAX_TASK_STEPS = 20
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")
_STEP_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")


def utc_now() -> str:
    """ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def check_id(value: str, label: str = "id") -> str:
    """Validate a record ID (it becomes a file name, so no traversal is possible)."""
    if not _ID_RE.fullmatch(value):
        raise ValueError(f"invalid {label} {value!r} (lowercase letters, digits, '.', '_', '-'; max 80)")
    return value


class TaskState(str, Enum):
    """Component task states."""

    PROPOSED = "proposed"
    READY = "ready"
    IN_PROGRESS = "in-progress"
    VERIFIED = "verified"
    BLOCKED = "blocked"
    FAILED = "failed"
    NEEDS_HUMAN = "needs-human"


class FeatureState(str, Enum):
    """Parent feature states."""

    PLANNED = "planned"
    ACTIVE = "active"
    INTEGRATION = "integration"
    REVIEW = "review"
    DONE = "done"
    BLOCKED = "blocked"
    FAILED = "failed"


T = TaskState
TASK_TRANSITIONS: Mapping[TaskState, frozenset[TaskState]] = {
    T.PROPOSED: frozenset({T.READY, T.BLOCKED, T.NEEDS_HUMAN}),
    T.READY: frozenset({T.IN_PROGRESS, T.BLOCKED, T.NEEDS_HUMAN}),
    # in-progress → ready: the worker yielded or crashed and the claim was recovered.
    T.IN_PROGRESS: frozenset({T.VERIFIED, T.READY, T.BLOCKED, T.FAILED, T.NEEDS_HUMAN}),
    # verified → ready / blocked: a contract it depends on (or it itself) changed; re-verify.
    T.VERIFIED: frozenset({T.READY, T.BLOCKED}),
    T.BLOCKED: frozenset({T.READY, T.NEEDS_HUMAN, T.FAILED}),
    T.FAILED: frozenset({T.READY, T.NEEDS_HUMAN}),
    T.NEEDS_HUMAN: frozenset({T.READY, T.FAILED}),
}

F = FeatureState
FEATURE_TRANSITIONS: Mapping[FeatureState, frozenset[FeatureState]] = {
    F.PLANNED: frozenset({F.ACTIVE, F.BLOCKED, F.FAILED}),
    F.ACTIVE: frozenset({F.INTEGRATION, F.BLOCKED, F.FAILED}),
    F.INTEGRATION: frozenset({F.REVIEW, F.ACTIVE, F.BLOCKED, F.FAILED}),
    F.REVIEW: frozenset({F.DONE, F.ACTIVE, F.BLOCKED, F.FAILED}),
    F.BLOCKED: frozenset({F.PLANNED, F.ACTIVE, F.FAILED}),
    F.FAILED: frozenset({F.PLANNED}),
    F.DONE: frozenset(),
}


GATE_STATUSES = ("passed", "failed", "timeout", "blocked", "error")
EXIT_TIMEOUT, EXIT_BLOCKED, EXIT_ERROR = 124, 125, 126


@dataclass(frozen=True)
class GateResult:  # pylint: disable=too-many-instance-attributes
    """One executed (or refused) check and its outcome.

    `status` is passed | failed | timeout (killed at its deadline) | blocked (not run: unsafe
    against this checkout, or a tool is missing) | error. Only `passed` counts as passing."""

    gate: str
    exit_code: int
    command: tuple[str, ...] = ()
    status: str = ""
    duration_seconds: float = 0.0
    log: str = ""
    summary: str = ""

    def __post_init__(self) -> None:
        if not self.status:
            object.__setattr__(self, "status", "passed" if self.exit_code == 0 else "failed")
        if self.status not in GATE_STATUSES:
            raise ValueError(f"gate {self.gate!r}: unknown status {self.status!r}")

    @property
    def passed(self) -> bool:
        """True when the gate ran and exited 0."""
        return self.exit_code == 0 and self.status == "passed"

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable form."""
        return {"gate": self.gate, "exit_code": self.exit_code, "command": list(self.command),
                "status": self.status,
                "duration_seconds": round(self.duration_seconds, 3), "log": self.log, "summary": self.summary}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "GateResult":
        """Inverse of to_dict()."""
        return cls(gate=data["gate"], exit_code=int(data["exit_code"]), command=tuple(data.get("command", ())),
                   status=data.get("status", ""), duration_seconds=float(data.get("duration_seconds", 0.0)),
                   log=data.get("log", ""), summary=data.get("summary", ""))


@dataclass(frozen=True)
class Evidence:
    """Verification evidence for a task (or the integration of a feature) at one HEAD."""

    head_sha: str
    gates: tuple[GateResult, ...]
    summary: str = ""
    recorded_at: str = field(default_factory=utc_now)

    def require(self, required: Iterable[str], expected_head: str) -> None:
        """Raise EvidenceError unless every required gate passed at *expected_head*."""
        if not re.fullmatch(r"[0-9a-f]{40}", self.head_sha or ""):
            raise EvidenceError("evidence must name a full 40-hex HEAD SHA")
        if self.head_sha != expected_head:
            raise EvidenceError(
                f"evidence is bound to {self.head_sha[:12]}, but the feature branch HEAD is {expected_head[:12]}; "
                "re-run the gates on the combined branch",
            )
        results = {g.gate: g for g in self.gates}
        missing = [name for name in required if name not in results]
        failing = [g.gate for g in self.gates if not g.passed]
        if missing or failing:
            raise EvidenceError(f"evidence incomplete: missing gates {missing}, failing gates {failing}")

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable form."""
        return {"head_sha": self.head_sha, "summary": self.summary, "recorded_at": self.recorded_at,
                "gates": [g.to_dict() for g in self.gates]}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Evidence":
        """Inverse of to_dict()."""
        gates = tuple(GateResult.from_dict(g) for g in data["gates"])
        return cls(head_sha=data["head_sha"], gates=gates, summary=data.get("summary", ""),
                   recorded_at=data.get("recorded_at", utc_now()))


@dataclass(frozen=True)
class HistoryEntry:
    """One recorded state change."""

    at: str
    from_state: str
    to_state: str
    reason: str


@dataclass(frozen=True)
class TaskStep:
    """One step of a multi-step task (W3): a key (unique in the task) and the instruction."""

    key: str
    title: str

    def __post_init__(self) -> None:
        if not _STEP_KEY_RE.fullmatch(self.key):
            raise ValueError(f"invalid step key {self.key!r} (lowercase letters, digits, '_', '-'; max 40)")
        if not self.title.strip():
            raise ValueError(f"step {self.key!r} needs a title")


def check_steps(task_id: str, steps: Iterable[TaskStep]) -> tuple[TaskStep, ...]:
    """Validate a task's steps: unique keys, at most MAX_TASK_STEPS (bounded model calls)."""
    steps = tuple(steps)
    keys = [s.key for s in steps]
    if len(set(keys)) != len(keys):
        raise ValueError(f"task {task_id!r}: step keys must be unique ({keys})")
    if len(steps) > MAX_TASK_STEPS:
        raise ValueError(f"task {task_id!r}: at most {MAX_TASK_STEPS} steps ({len(steps)} given)")
    return steps


@dataclass
class Task:  # pylint: disable=too-many-instance-attributes
    """One component task: exactly one role, on the feature's shared branch."""

    id: str
    feature_id: str
    role: str
    title: str
    acceptance: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    required_gates: tuple[str, ...] = ()
    provides_contract: bool = False
    grants: tuple[str, ...] = ()
    state: TaskState = TaskState.PROPOSED
    blocked_reason: str = ""
    evidence: Evidence | None = None
    commits: list[str] = field(default_factory=list)
    issue_number: int | None = None
    history: list[HistoryEntry] = field(default_factory=list)
    revision: int = 0
    steps: tuple[TaskStep, ...] = ()  # W3: empty = one implicit step (the W2 behaviour)

    def __post_init__(self) -> None:
        check_id(self.id, "task id")
        check_id(self.feature_id, "feature id")
        if self.role not in ROLES:
            raise ValueError(f"task {self.id!r}: unknown role {self.role!r}")
        if self.id in self.depends_on:
            raise DependencyError(f"task {self.id!r} depends on itself")
        self.steps = check_steps(self.id, self.steps)

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable form (always the current task schema)."""
        data = asdict(self)
        data.update(schema_version=TASK_SCHEMA_VERSION, state=self.state.value,
                    evidence=self.evidence.to_dict() if self.evidence else None)
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Task":
        """Inverse of to_dict(); v1 records are migrated (migrate_task_record)."""
        data = migrate_task_record(data)
        return cls(
            id=data["id"], feature_id=data["feature_id"], role=data["role"], title=data["title"],
            acceptance=tuple(data.get("acceptance", ())), depends_on=tuple(data.get("depends_on", ())),
            required_gates=tuple(data.get("required_gates", ())), provides_contract=bool(data["provides_contract"]),
            grants=tuple(data.get("grants", ())), state=TaskState(data["state"]),
            blocked_reason=data.get("blocked_reason", ""),
            evidence=Evidence.from_dict(data["evidence"]) if data.get("evidence") else None,
            commits=list(data.get("commits", ())), issue_number=data.get("issue_number"),
            history=[HistoryEntry(**h) for h in data.get("history", ())], revision=int(data.get("revision", 0)),
            steps=tuple(TaskStep(key=s["key"], title=s["title"]) for s in data.get("steps", ())),
        )


TASK_FIELDS_V1 = frozenset({
    "schema_version", "id", "feature_id", "role", "title", "acceptance", "depends_on", "required_gates",
    "provides_contract", "grants", "state", "blocked_reason", "evidence", "commits", "issue_number", "history",
    "revision"})
TASK_FIELDS_V2 = TASK_FIELDS_V1 | {"steps"}


def migrate_task_record(data: Mapping[str, Any]) -> dict[str, Any]:
    """A task record in the current schema (v2). v1 (W2 and earlier) gains `steps: []`. Any other
    version, or a field this version does not know, is refused (ValueError): loading it would
    drop data on the next save."""
    version = data.get("schema_version")
    if version not in TASK_SCHEMA_VERSIONS:
        raise ValueError(f"unsupported task record schema_version {version!r} (supported {TASK_SCHEMA_VERSIONS})")
    known = TASK_FIELDS_V1 if version == 1 else TASK_FIELDS_V2
    unknown = sorted(set(data) - known)
    if unknown:
        raise ValueError(f"task record {data.get('id')!r} (schema {version}) has unknown fields {unknown}; "
                         "refusing to load it (they would be lost on the next save)")
    migrated = dict(data)
    if version == 1:
        migrated.update(schema_version=TASK_SCHEMA_VERSION, steps=[])
    return migrated


@dataclass
class PullRequestCandidate:
    """The single aggregate PR of a feature (rendered locally; created only with approval)."""

    head: str
    base: str
    title: str
    body: str
    head_sha: str
    url: str | None = None
    ci_state: str = "not-run"


@dataclass
class Feature:  # pylint: disable=too-many-instance-attributes
    """A parent feature: one base, one branch, many component tasks, one final PR."""

    id: str
    slug: str
    title: str
    base_branch: str
    branch: str
    acceptance: tuple[str, ...] = ()
    base_sha: str = ""
    state: FeatureState = FeatureState.PLANNED
    task_ids: list[str] = field(default_factory=list)
    issue_number: int | None = None
    pull_request: PullRequestCandidate | None = None
    integration_evidence: Evidence | None = None
    merged_sha: str | None = None
    test: bool = False  # a coordinator test feature: its Issues carry `feature:test` (Phase 4)
    workspace: dict[str, Any] = field(default_factory=dict)  # W2: repository identity recorded at activation
    history: list[HistoryEntry] = field(default_factory=list)
    revision: int = 0

    def __post_init__(self) -> None:
        check_id(self.id, "feature id")

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable form."""
        data = asdict(self)
        data.update(schema_version=SCHEMA_VERSION, state=self.state.value,
                    integration_evidence=self.integration_evidence.to_dict() if self.integration_evidence else None)
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Feature":
        """Inverse of to_dict()."""
        _check_schema(data)
        pr = data.get("pull_request")
        evidence = data.get("integration_evidence")
        return cls(
            id=data["id"], slug=data["slug"], title=data["title"], base_branch=data["base_branch"],
            branch=data["branch"], acceptance=tuple(data.get("acceptance", ())), base_sha=data.get("base_sha", ""),
            state=FeatureState(data["state"]), task_ids=list(data.get("task_ids", ())),
            issue_number=data.get("issue_number"), pull_request=PullRequestCandidate(**pr) if pr else None,
            integration_evidence=Evidence.from_dict(evidence) if evidence else None,
            merged_sha=data.get("merged_sha"), test=bool(data.get("test", False)),
            workspace=dict(data.get("workspace") or {}),
            history=[HistoryEntry(**h) for h in data.get("history", ())], revision=int(data.get("revision", 0)),
        )


def last_entered(history: Iterable[HistoryEntry], state: str) -> str | None:
    """When the record last entered *state* (ISO timestamp), or None."""
    stamps = [entry.at for entry in history if entry.to_state == state]
    return stamps[-1] if stamps else None


def _check_schema(data: Mapping[str, Any]) -> None:
    version = data.get("schema_version")
    if version != SCHEMA_VERSION:
        raise ValueError(f"unsupported record schema_version {version!r} (expected {SCHEMA_VERSION})")


# ---------------------------------------------------------------------------
# Transitions
# ---------------------------------------------------------------------------

def transition_task(task: Task, target: TaskState, reason: str, *, evidence: Evidence | None = None,
                    expected_head: str | None = None) -> bool:
    """Move *task* to *target*. Returns False for a no-op (already there), True on change."""
    if task.state is target:
        return False
    if target not in TASK_TRANSITIONS[task.state]:
        raise IllegalTransitionError(f"task {task.id!r}: {task.state.value} → {target.value} is not allowed")
    if target is TaskState.VERIFIED:
        if evidence is None or expected_head is None:
            raise EvidenceError(f"task {task.id!r}: verified requires evidence and the branch HEAD")
        evidence.require(task.required_gates, expected_head)
        task.evidence = evidence
    elif task.state is TaskState.VERIFIED:
        task.evidence = None  # re-opened: the old evidence no longer describes the branch
    task.history.append(HistoryEntry(utc_now(), task.state.value, target.value, reason))
    task.state = target
    task.blocked_reason = reason if target is TaskState.BLOCKED else ""
    return True


def transition_feature(feature: Feature, target: FeatureState, reason: str, *, merged_sha: str | None = None) -> bool:
    """Move *feature* to *target*. Returns False for a no-op, True on change."""
    if feature.state is target:
        return False
    if target not in FEATURE_TRANSITIONS[feature.state]:
        raise IllegalTransitionError(f"feature {feature.id!r}: {feature.state.value} → {target.value} is not allowed")
    if target is FeatureState.DONE:
        if feature.pull_request is None or not merged_sha:
            raise EvidenceError(f"feature {feature.id!r}: done requires a merged PR (PR creation alone is not enough)")
        feature.merged_sha = merged_sha
    feature.history.append(HistoryEntry(utc_now(), feature.state.value, target.value, reason))
    feature.state = target
    return True


# ---------------------------------------------------------------------------
# Dependency DAG
# ---------------------------------------------------------------------------

def validate_dag(tasks: Mapping[str, Task]) -> list[str]:
    """Topological order of *tasks*; DependencyError on unknown IDs or cycles."""
    for task in tasks.values():
        unknown = [dep for dep in task.depends_on if dep not in tasks]
        if unknown:
            raise DependencyError(f"task {task.id!r} depends on unknown tasks {unknown}")
    order: list[str] = []
    state: dict[str, int] = {}  # 1 = visiting, 2 = done

    def visit(node: str, path: list[str]) -> None:
        if state.get(node) == 2:
            return
        if state.get(node) == 1:
            cycle = path[path.index(node):] + [node]
            raise DependencyError(f"dependency cycle: {' → '.join(cycle)}")
        state[node] = 1
        for dep in tasks[node].depends_on:
            visit(dep, path + [node])
        state[node] = 2
        order.append(node)

    for task_id in sorted(tasks):
        visit(task_id, [])
    return order


def unmet_dependencies(task: Task, tasks: Mapping[str, Task]) -> list[str]:
    """Dependencies of *task* that are not verified."""
    return [dep for dep in task.depends_on if tasks[dep].state is not TaskState.VERIFIED]


def refresh_readiness(tasks: Mapping[str, Task]) -> list[str]:
    """Block tasks with unmet dependencies and resume blocked ones whose dependencies are met.

    Only proposed/ready/blocked tasks are touched (claimed and terminal ones never are).
    Returns the IDs of tasks whose state changed.
    """
    changed = []
    for task_id in validate_dag(tasks):
        task = tasks[task_id]
        unmet = unmet_dependencies(task, tasks)
        if task.state in (TaskState.PROPOSED, TaskState.READY) and unmet:
            if transition_task(task, TaskState.BLOCKED, f"waiting for {', '.join(unmet)}"):
                changed.append(task_id)
        elif task.state in (TaskState.PROPOSED, TaskState.BLOCKED) and not unmet:
            if task.state is TaskState.PROPOSED or task.blocked_reason.startswith(("waiting for", "dependency")):
                if transition_task(task, TaskState.READY, "dependencies verified"):
                    changed.append(task_id)
    return changed


def invalidate_dependents(task_id: str, tasks: Mapping[str, Task]) -> list[str]:
    """After *task_id* is re-opened, block every verified task that (transitively) depends on it."""
    changed = []
    frontier = [task_id]
    while frontier:
        current = frontier.pop()
        for task in tasks.values():
            if current in task.depends_on and task.state is TaskState.VERIFIED:
                transition_task(task, TaskState.BLOCKED, f"dependency {current} changed; re-verify")
                changed.append(task.id)
                frontier.append(task.id)
    return changed
