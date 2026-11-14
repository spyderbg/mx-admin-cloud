"""W4.7 (M1, M2): integration jobs — the record, the lifecycle state machine and their persistence.

Design: docs_agents/coordination/existing-branch-integration-design.md (§4 lifecycle, §8.2 record). Operator
notes, M1/M2 decisions and open questions: docs_agents/coordination/integration-jobs.md.

An integration job brings an EXISTING branch (or PR head) into the base branch by immutable SHA. M1 implements
the record, the state machine and `intake` / `status` / `abort` (integration_intake.py, integration_commands.py).
M2 adds the operator authorization (`approvals`, integration_approval.py) and the read-only inspection (`paths`,
`evidence` of kind `inspection`, integration_inspect.py). Nothing here builds a candidate, runs a gate, reviews,
creates a PR, writes Git or merges.

M2 record rules (validate_job, on every load and save):
  * `approvals` entries carry the exact signed authorization record text; its SHA-256, its parsed fields and the
    job's pinned source/base SHAs, Issue and PR must all agree. `approvals` and `evidence` are append-only.
  * `paths` is the classified changed-path list of the latest inspection; `evidence` entries of kind `inspection`
    reference an existing approval and are bound to the pinned source/base SHAs by a SHA-256 of their payload.
  * `deferred` may be recorded in INTAKE (M1) and INSPECTING (M2: a read could not complete during inspection).
The state machine itself is unchanged: the approval requirement for INSPECTING is enforced by the only code path
that enters it (integration_inspect.py), so every M1 behaviour stays as accepted.

Lifecycle (design §4; every state before MERGED may also be ABORTED by the operator):

    INTAKE ─▶ AWAITING_APPROVAL ─▶ INSPECTING ─▶ CANDIDATE_BUILT ─▶ VALIDATING ─▶ IN_REVIEW
       COMPLETED ◀─ MERGED ◀─ AWAITING_MERGE_APPROVAL ◀──────────────────────────────┘

Rules enforced here:
  * One JSON record per job (`integration/<job id>.json` in the coordinator state directory), written with the
    store's atomic write (temp file + fsync + rename + directory fsync) inside `StateStore.transaction()`
    (coordinator.lock), revision-checked against concurrent writers.
  * The job ID, the captured source and base (repository, ref, full SHA), the Issue, the requested PR number, the
    request key and `supersedes` are immutable; `history` is append-only. `IntegrationJobs.save` compares every
    write with the record on disk and refuses anything else.
  * Transitions follow JOB_TRANSITIONS only. A transition to the current state is a no-op (the coordinator-wide
    convention, model.py). A terminal state has no outgoing transition: a terminal job is never reopened.
  * INTAKE has no STALE transition: nothing is pinned yet, so a ref, base or PR that does not match its stated SHA
    is invalid input → REFUSED (design §4, "States without a STALE transition").
  * MERGED needs the actual merge commit (`merge_result`) and COMPLETED a verified one; M1 never records either.

The design's `NEEDS_REPAIR → INTAKE` is a NEW job linked to the old one (§6.4), not an in-place transition: an
in-place edge would reopen a job with different SHAs. It is therefore not an edge of this table (see the open
question in integration-jobs.md).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterable, Mapping

from .errors import CoordinatorError, IllegalTransitionError, StateConflictError, StateDirError
from .model import check_id, utc_now

JOB_SCHEMA = 1
RECORD_KIND = "integration"
SHA_RE = re.compile(r"[0-9a-f]{40}")
_JOB_ID_RE = re.compile(r"int-(\d{8})-(\d{4,6})")
MAX_REASON = 500


class IntegrationError(CoordinatorError):
    """A deliberate refusal of an integration-job operation (malformed input, unknown job, conflict)."""


class JobState(str, Enum):
    """Integration job states (design §4). Values are the design's names, stored verbatim."""

    INTAKE = "INTAKE"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    INSPECTING = "INSPECTING"
    CANDIDATE_BUILT = "CANDIDATE_BUILT"
    VALIDATING = "VALIDATING"
    IN_REVIEW = "IN_REVIEW"
    AWAITING_MERGE_APPROVAL = "AWAITING_MERGE_APPROVAL"
    MERGED = "MERGED"
    COMPLETED = "COMPLETED"
    NEEDS_REPAIR = "NEEDS_REPAIR"
    REFUSED = "REFUSED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"
    ABORTED = "ABORTED"
    STALE = "STALE"


S = JobState
TERMINAL_STATES: frozenset[JobState] = frozenset({S.COMPLETED, S.REFUSED, S.REJECTED, S.EXPIRED, S.FAILED,
                                                  S.ABORTED, S.STALE})
# Every non-terminal state before MERGED may be aborted by the operator (design §4, below the table).
ABORTABLE_STATES: frozenset[JobState] = frozenset({S.INTAKE, S.AWAITING_APPROVAL, S.INSPECTING, S.CANDIDATE_BUILT,
                                                   S.VALIDATING, S.IN_REVIEW, S.AWAITING_MERGE_APPROVAL,
                                                   S.NEEDS_REPAIR})
_TABLE: Mapping[JobState, frozenset[JobState]] = {
    S.INTAKE: frozenset({S.AWAITING_APPROVAL, S.REFUSED}),
    S.AWAITING_APPROVAL: frozenset({S.INSPECTING, S.REJECTED, S.EXPIRED, S.STALE}),
    S.INSPECTING: frozenset({S.CANDIDATE_BUILT, S.REFUSED, S.NEEDS_REPAIR, S.STALE}),
    S.CANDIDATE_BUILT: frozenset({S.VALIDATING, S.REFUSED, S.STALE}),
    S.VALIDATING: frozenset({S.IN_REVIEW, S.NEEDS_REPAIR, S.FAILED, S.STALE}),
    S.IN_REVIEW: frozenset({S.AWAITING_MERGE_APPROVAL, S.NEEDS_REPAIR, S.FAILED, S.STALE}),
    S.AWAITING_MERGE_APPROVAL: frozenset({S.MERGED, S.REJECTED, S.STALE}),
    S.MERGED: frozenset({S.COMPLETED, S.FAILED}),
    S.NEEDS_REPAIR: frozenset({S.STALE}),  # + ABORTED; `→ INTAKE` is a new linked job, not an edge (docstring)
}
JOB_TRANSITIONS: Mapping[JobState, frozenset[JobState]] = {
    state: (_TABLE.get(state, frozenset()) | ({S.ABORTED} if state in ABORTABLE_STATES else frozenset()))
    for state in JobState
}
assert all(not JOB_TRANSITIONS[t] for t in TERMINAL_STATES), "terminal states have no outgoing transition"


def allowed_next(state: JobState) -> list[str]:
    """The states *state* may move to, sorted (for status output)."""
    return sorted(s.value for s in JOB_TRANSITIONS[state])


def check_transition(current: JobState, target: JobState) -> None:
    """IllegalTransitionError unless current → target is an edge of JOB_TRANSITIONS (current == target is not
    checked here: callers treat it as a no-op)."""
    if current in TERMINAL_STATES:
        raise IllegalTransitionError(f"{current.value} is terminal: a terminal integration job is never reopened "
                                     f"(requested {target.value}); start a new job with fresh inputs")
    if target not in JOB_TRANSITIONS[current]:
        raise IllegalTransitionError(f"integration job: {current.value} → {target.value} is not allowed "
                                     f"(allowed: {', '.join(allowed_next(current))})")


# ---------------------------------------------------------------------------
# The request (immutable inputs) and its key
# ---------------------------------------------------------------------------

_REPO_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}")
_REF_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}")


def check_sha(value: Any, label: str) -> str:
    """A full 40-character lowercase hexadecimal commit SHA (never abbreviated), else IntegrationError."""
    if not isinstance(value, str) or not SHA_RE.fullmatch(value):
        raise IntegrationError(f"{label} must be the full 40-character lowercase hexadecimal commit SHA "
                               f"(abbreviated, uppercase or symbolic values are refused), got {_shown(value)}")
    return value


def check_ref(value: Any, label: str) -> str:
    """A plain branch name (no `refs/` prefix, no revision syntax), conservatively validated."""
    if not isinstance(value, str) or not _REF_RE.fullmatch(value):
        raise IntegrationError(f"{label} must be a branch name (letters, digits, '.', '_', '-', '/'), got "
                               f"{_shown(value)}")
    parts = value.split("/")
    if (value.startswith("refs/") or any(not p or p.startswith(".") or p.endswith(".lock") for p in parts)
            or ".." in value or value.endswith((".", "/")) or value == "HEAD"):
        raise IntegrationError(f"{label} {value!r} is not a valid plain branch name")
    return value


def check_repo(value: Any, label: str) -> str:
    """owner/name."""
    if not isinstance(value, str) or not _REPO_RE.fullmatch(value) or value.endswith((".git", ".")):
        raise IntegrationError(f"{label} must be 'owner/name', got {_shown(value)}")
    return value


def check_number(value: Any, label: str) -> int:
    """A positive integer (an Issue or PR number)."""
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 10_000_000:
        raise IntegrationError(f"{label} must be a positive integer, got {_shown(value)}")
    return value


def check_reason(value: Any, label: str = "--reason") -> str:
    """A non-empty single-line reason, bounded."""
    if not isinstance(value, str) or not value.strip():
        raise IntegrationError(f"{label} must be a non-empty text")
    text = " ".join(value.split())
    if len(text) > MAX_REASON or re.search(r"[\x00-\x1f\x7f]", text):
        raise IntegrationError(f"{label} must be at most {MAX_REASON} printable characters")
    return text


def clean_text(value: Any, limit: int = MAX_REASON) -> str:
    """*value* as one printable line of at most *limit* characters (internal reasons; never raises)."""
    text = " ".join(re.sub(r"[\x00-\x1f\x7f]", " ", str(value)).split())
    return text if len(text) <= limit else text[:limit - 3] + "..."


def _shown(value: Any) -> str:
    text = repr(value)
    return text if len(text) <= 60 else text[:57] + "..."


@dataclass(frozen=True)
class IntakeRequest:  # pylint: disable=too-many-instance-attributes
    """The operator's exact inputs (design §3). Every field is validated on construction."""

    source_repo: str
    source_ref: str
    source_sha: str
    base_ref: str
    base_sha: str
    issue: int
    pr_number: int | None = None

    def __post_init__(self) -> None:
        check_repo(self.source_repo, "--source-repo")
        check_ref(self.source_ref, "--source-ref")
        check_sha(self.source_sha, "--source-sha")
        check_ref(self.base_ref, "--base")
        check_sha(self.base_sha, "--base-sha")
        check_number(self.issue, "--issue")
        if self.pr_number is not None:
            check_number(self.pr_number, "--pr")

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "IntakeRequest":
        """From a mapping with exactly these keys (strict: a missing or unknown field is refused)."""
        required = ("source_repo", "source_ref", "source_sha", "base_ref", "base_sha", "issue")
        missing = [k for k in required if data.get(k) in (None, "")]
        if missing:
            raise IntegrationError(f"missing required intake field(s): {', '.join(missing)}")
        unknown = sorted(set(data) - {*required, "pr_number"})
        if unknown:
            raise IntegrationError(f"unknown intake field(s): {', '.join(unknown)}")
        return cls(**{k: data[k] for k in (*required, "pr_number") if k in data})

    def canonical(self) -> dict[str, Any]:
        """The normalized inputs that identify a request (repository compared case-insensitively)."""
        return {"source_repo": self.source_repo.lower(), "source_ref": self.source_ref, "source_sha": self.source_sha,
                "base_ref": self.base_ref, "base_sha": self.base_sha, "issue": self.issue, "pr_number": self.pr_number}

    @property
    def key(self) -> str:
        """SHA-256 of the canonical inputs: the same request always has the same key (idempotent intake)."""
        return hashlib.sha256(json.dumps(self.canonical(), sort_keys=True).encode("utf-8")).hexdigest()

    def conflict_keys(self) -> set[str]:
        """What at most one ACTIVE job may hold: the source branch, the source commit and the PR."""
        keys = {f"branch:{self.source_repo.lower()}:{self.source_ref}", f"sha:{self.source_sha}"}
        if self.pr_number is not None:
            keys.add(f"pr:{self.source_repo.lower()}#{self.pr_number}")
        return keys


# ---------------------------------------------------------------------------
# The job record
# ---------------------------------------------------------------------------

_FIELDS = frozenset({"schema_version", "job_id", "state", "issue", "source", "base", "pr", "request_key",
                     "supersedes", "provenance", "candidate", "merge_result", "paths", "evidence", "approvals",
                     "attention", "deferred", "history", "created_at", "updated_at", "revision"})
_HISTORY_FIELDS = frozenset({"from", "to", "at", "reason", "actor"})
_IMMUTABLE = ("job_id", "issue", "source", "base", "request_key", "supersedes", "created_at")
_TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z")   # model.utc_now()
MAX_ACTOR = 80
MAX_TEXT = 1000
# The PR as requested ({number}) or as verified at intake (every key). Types per key.
_PR_VERIFIED: Mapping[str, type] = {"number": int, "head_repo": str, "head_ref": str, "head_sha": str,
                                    "base_repo": str, "base_ref": str, "draft": bool, "url": str, "node_id": str,
                                    "id": int, "verified_at": str}
# What intake records (integration_intake.IntegrationIntake._provenance).
_PROVENANCE_KEYS = frozenset({"authors", "committers", "commits", "head_commit", "history", "origin"})
_PROVENANCE_HISTORY_KEYS = frozenset({"boundary_parents", "base_commits_read"})
_PROVENANCE_ORIGIN_KEYS = frozenset({"repo", "ref", "resolved_via", "at"})
_ATTENTION_KEYS = frozenset({"reason", "reasons", "observed", "since"})
_DEFERRED_KEYS = frozenset({"reason", "at"})
DEFERRABLE_STATES: frozenset[JobState] = frozenset({JobState.INTAKE, JobState.INSPECTING})

# -- M2: the operator authorization record (integration_approval.py signs/verifies; here: schema and binding) ------
APPROVAL_KIND = "w47-integration-approval"
APPROVAL_VERSION = 1
APPROVAL_STAGES = ("inspect",)                    # `merge` is M5
APPROVAL_NAMESPACE = "mx-admin-cloud-integration-approval@feature-coordinator"  # ssh-keygen -Y sign -n …
MAX_RECORD_BYTES = 64 * 1024
MAX_SIGNATURE_BYTES = 16 * 1024
MAX_APPROVED_PATHS = 2000
MAX_PATH_TEXT = 1000
_RECORD_REQUIRED = ("kind", "version", "job", "stage", "operator", "issued_at", "expires_at", "source", "base", "issue",
                    "pr", "paths", "accept_unverified_provenance")
_RECORD_KEYS = frozenset({*_RECORD_REQUIRED, "note"})
_APPROVAL_KEYS = frozenset({"id", "stage", "operator", "issued_at", "expires_at", "source_sha", "base_sha", "issue",
                            "pr", "paths", "accept_unverified_provenance", "note", "record", "record_sha256",
                            "signature", "signer", "recorded_at", "actor"})
_SIGNER_KEYS = frozenset({"method", "namespace", "principal", "key"})
_APPROVAL_ID_RE = re.compile(r"apr-(\d{4})")
_LOGIN_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")
_KEY_FINGERPRINT_RE = re.compile(r"SHA256:[A-Za-z0-9+/]{43}")
_RECORD_TIME_RE = re.compile(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,6}))?Z")
SIGNATURE_HEADER = "-----BEGIN SSH SIGNATURE-----"

# -- M2: the classified changed paths and the inspection evidence ---------------------------------------------------
PATH_CLASSES = ("owned", "approval-required", "unowned", "protected")
PATH_CHANGES = ("added", "deleted", "modified", "mode", "type")
PATH_FLAGS = ("symlink", "gitlink", "executable", "git-control", "renamed", "history-only", "empty-tree")
PATH_AUTHORIZATION = ("not-needed", "authorized", "required", "refused")
_PATH_KEYS = frozenset({"path", "class", "role", "changes", "flags", "authorization"})
INSPECTION_RESULTS = ("passed", "needs-authorization", "refused", "stale")
_INSPECTION_KEYS = frozenset({"kind", "id", "at", "approval", "source_sha", "base_sha", "result", "pr_match",
                              "provenance", "paths", "reasons", "sha256"})
_PR_MATCH_KEYS = frozenset({"result", "number", "candidates"})
PR_MATCH_RESULTS = ("none", "reuse", "refused", "stale", "unknown")
_INSPECTION_PROVENANCE_KEYS = frozenset({"source", "verified", "unverified", "unverifiable", "accepted"})
PROVENANCE_SOURCES = ("github", "unavailable", "unknown")
_INSPECTION_PATHS_KEYS = frozenset({"count", "sha256", "required", "authorized", "refused"})
_EVIDENCE_ID_RE = re.compile(r"ins-(\d{4})")
_HEX64_RE = re.compile(r"[0-9a-f]{64}")
MAX_EVIDENCE = 500
MAX_APPROVALS = 200


@dataclass
class IntegrationJob:  # pylint: disable=too-many-instance-attributes
    """One integration job (design §8.2). `revision` is the store's optimistic-concurrency counter."""

    job_id: str
    issue: int
    source: dict[str, str]                 # {repo, ref, sha} — immutable
    base: dict[str, str]                   # {ref, expected_sha} — immutable
    request_key: str                       # IntakeRequest.key — immutable
    state: JobState = JobState.INTAKE
    pr: dict[str, Any] | None = None       # {number} (immutable) + what intake observed: head/base, url, ids
    supersedes: str | None = None          # the terminal job with the same request this one replaces — immutable
    provenance: dict[str, Any] | None = None   # {authors, committers, commits, origin} — recorded at intake
    candidate: dict[str, Any] | None = None    # M3: {worktree, head_sha, tree_sha}
    merge_result: dict[str, Any] | None = None  # M5: {commit_sha, parents, tree_sha, verified}
    paths: list[dict[str, Any]] = field(default_factory=list)      # M2
    evidence: list[dict[str, Any]] = field(default_factory=list)   # M3/M4
    approvals: list[dict[str, Any]] = field(default_factory=list)  # M5 (and the inspect stage, see the notes)
    attention: dict[str, Any] | None = None    # {reason, reasons, observed, since}: an operator must look
    deferred: dict[str, Any] | None = None     # {reason, at}: a read could not complete; the state is unchanged
    history: list[dict[str, Any]] = field(default_factory=list)    # append-only {from, to, at, reason, actor}
    created_at: str = field(default_factory=utc_now)
    updated_at: str = ""
    revision: int = 0

    @property
    def id(self) -> str:  # pylint: disable=invalid-name
        """The record ID used by StateStore (the file name)."""
        return self.job_id

    @property
    def terminal(self) -> bool:
        """True in a terminal state."""
        return self.state in TERMINAL_STATES

    @classmethod
    def new(cls, job_id: str, request: IntakeRequest, *, actor: str, supersedes: str | None = None,
            reason: str = "intake request recorded") -> "IntegrationJob":
        """A job in INTAKE for *request* (write-ahead: recorded before any check runs)."""
        now = utc_now()
        job = cls(job_id=check_job_id(job_id), issue=request.issue,
                  source={"repo": request.source_repo, "ref": request.source_ref, "sha": request.source_sha},
                  base={"ref": request.base_ref, "expected_sha": request.base_sha}, request_key=request.key,
                  pr={"number": request.pr_number} if request.pr_number is not None else None,
                  supersedes=supersedes, created_at=now, updated_at=now)
        job.history.append({"from": None, "to": JobState.INTAKE.value, "at": now, "reason": clean_text(reason),
                            "actor": clean_text(actor, MAX_ACTOR)})
        return job

    def request(self) -> IntakeRequest:
        """The immutable inputs, as a request."""
        return IntakeRequest(source_repo=self.source["repo"], source_ref=self.source["ref"],
                             source_sha=self.source["sha"], base_ref=self.base["ref"],
                             base_sha=self.base["expected_sha"], issue=self.issue,
                             pr_number=(self.pr or {}).get("number"))

    def to_dict(self) -> dict[str, Any]:
        """JSON form (design §8.2 keys)."""
        return {"schema_version": JOB_SCHEMA, "job_id": self.job_id, "state": self.state.value, "issue": self.issue,
                "source": dict(self.source), "base": dict(self.base), "pr": _copy(self.pr),
                "request_key": self.request_key, "supersedes": self.supersedes, "provenance": _copy(self.provenance),
                "candidate": _copy(self.candidate), "merge_result": _copy(self.merge_result),
                "paths": _copy(self.paths), "evidence": _copy(self.evidence), "approvals": _copy(self.approvals),
                "attention": _copy(self.attention), "deferred": _copy(self.deferred), "history": _copy(self.history),
                "created_at": self.created_at, "updated_at": self.updated_at, "revision": self.revision}

    @classmethod
    def from_dict(cls, data: Any) -> "IntegrationJob":
        """Strict, for a record read from disk: anything but a JSON object, another schema, an unknown or missing
        field, a value of the wrong type or outside its constraints, or a broken invariant (validate_job) is
        refused with StateDirError — never loaded with data silently dropped or repaired."""
        if not isinstance(data, Mapping):
            raise StateDirError(f"an integration job record must be a JSON object, not {type(data).__name__}")
        where = f"integration job {_shown(data.get('job_id'))}"
        version = data.get("schema_version")
        if type(version) is not int or version != JOB_SCHEMA:  # pylint: disable=unidiomatic-typecheck  # not bool
            raise StateDirError(f"{where}: unsupported schema {_shown(version)}")
        unknown = sorted(map(str, set(data) - _FIELDS))
        missing = sorted(_FIELDS - set(data))
        if unknown or missing:
            raise StateDirError(f"{where}: unknown fields {unknown}, missing fields {missing}")
        try:
            state = JobState(data["state"])
            job = cls(**{k: _copy(v) for k, v in data.items() if k not in ("schema_version", "state")}, state=state)
            validate_job(job, persisted=True)
        except (ValueError, TypeError, KeyError, AttributeError, CoordinatorError) as exc:
            raise StateDirError(f"{where} is malformed: {exc}") from exc
        return job


def validate_job(job: IntegrationJob, *, persisted: bool) -> None:
    """IntegrationError unless *job* satisfies the record's types, constraints and invariants. Applied on every load
    (from_dict, *persisted*: the store has written it, so revision ≥ 1) and before every save."""
    minimum = 1 if persisted else 0
    _expect(_is_int(job.revision, minimum), f"revision must be an integer ≥ {minimum}, got {_shown(job.revision)}")
    check_job_id(job.job_id)
    _expect(isinstance(job.state, JobState), "state must be a job state")
    _expect(isinstance(job.source, dict) and set(job.source) == {"repo", "ref", "sha"},
            "source must have exactly repo, ref and sha")
    _expect(isinstance(job.base, dict) and set(job.base) == {"ref", "expected_sha"},
            "base must have exactly ref and expected_sha")
    _check_pr(job)
    request = job.request()  # validates the pinned source, base, Issue and PR number
    _expect(isinstance(job.request_key, str) and job.request_key == request.key,
            "request_key does not match the recorded inputs")
    if job.supersedes is not None:
        check_job_id(job.supersedes)
        _expect(job.supersedes != job.job_id, "a job cannot supersede itself")
    if job.provenance is not None:
        _check_provenance(job.provenance, job.source["sha"])
    for name in ("candidate", "merge_result"):
        _expect(getattr(job, name) is None or isinstance(getattr(job, name), dict), f"{name} must be null or an object")
    for name in ("paths", "evidence", "approvals"):
        value = getattr(job, name)
        _expect(isinstance(value, list) and all(isinstance(item, dict) for item in value),
                f"{name} must be a list of objects")
    _check_approvals(job)
    _check_paths(job.paths)
    _check_evidence(job)
    if job.attention is not None:
        att = job.attention
        _expect(isinstance(att, dict) and set(att) == _ATTENTION_KEYS and _is_text(att["reason"])
                and _is_text_list(att["reasons"]) and isinstance(att["observed"], dict) and _is_time(att["since"]),
                f"attention must have exactly {sorted(_ATTENTION_KEYS)} (texts, an object, a timestamp)")
    if job.deferred is not None:
        _expect(isinstance(job.deferred, dict) and set(job.deferred) == _DEFERRED_KEYS
                and _is_text(job.deferred["reason"]) and _is_time(job.deferred["at"]),
                f"deferred must be null or have exactly {sorted(_DEFERRED_KEYS)}")
        _expect(job.state in DEFERRABLE_STATES, "deferred is only recorded while the job is in INTAKE (M1) or "
                                                "INSPECTING (M2)")
    _expect(_is_time(job.created_at) and _is_time(job.updated_at), "created_at and updated_at must be timestamps")
    _check_history(job)
    reached = {entry["to"] for entry in job.history}
    _expect(JobState.AWAITING_APPROVAL.value not in reached or job.provenance is not None,
            "a job that reached AWAITING_APPROVAL must carry its provenance")
    _expect(JobState.MERGED.value not in reached
            or SHA_RE.fullmatch(str((job.merge_result or {}).get("commit_sha", ""))) is not None,
            "a job that reached MERGED must carry the merge commit (merge_result)")
    _expect(JobState.COMPLETED.value not in reached or (job.merge_result or {}).get("verified") is True,
            "a COMPLETED job must carry a verified merge_result")


def _check_pr(job: IntegrationJob) -> None:
    pr = job.pr
    if pr is None:
        return
    _expect(isinstance(pr, dict) and "number" in pr, "pr must be null or carry its number")
    if set(pr) == {"number"}:
        return
    _expect(set(pr) == set(_PR_VERIFIED), f"a verified pr must have exactly {sorted(_PR_VERIFIED)}")
    for key, kind in _PR_VERIFIED.items():
        value = pr[key]
        valid = type(value) is kind if kind is not str else _is_text(value)  # pylint: disable=unidiomatic-typecheck
        _expect(valid, f"pr.{key} must be a {kind.__name__}")
    _expect(pr["head_sha"] == job.source["sha"] and pr["head_ref"] == job.source["ref"]
            and pr["base_ref"] == job.base["ref"] and _is_time(pr["verified_at"]),
            "the verified pr does not match the job's source and base")


def _check_provenance(prov: Any, source_sha: str) -> None:
    _expect(isinstance(prov, dict) and set(prov) == _PROVENANCE_KEYS,
            f"provenance must have exactly {sorted(_PROVENANCE_KEYS)}")
    _expect(_is_text_list(prov["authors"]) and _is_text_list(prov["committers"]),
            "provenance authors and committers must be lists of texts")
    _expect(_is_int(prov["commits"], 1), "provenance.commits must be a positive integer")
    _expect(prov["head_commit"] == source_sha, "provenance.head_commit must be the job's source SHA")
    history, origin = prov["history"], prov["origin"]
    _expect(isinstance(history, dict) and set(history) == _PROVENANCE_HISTORY_KEYS
            and all(_is_int(history[k], 0) for k in _PROVENANCE_HISTORY_KEYS),
            f"provenance.history must have exactly {sorted(_PROVENANCE_HISTORY_KEYS)} (counts)")
    _expect(isinstance(origin, dict) and set(origin) == _PROVENANCE_ORIGIN_KEYS
            and all(_is_text(origin[k]) for k in ("repo", "ref", "resolved_via")) and _is_time(origin["at"]),
            f"provenance.origin must have exactly {sorted(_PROVENANCE_ORIGIN_KEYS)}")


# ---------------------------------------------------------------------------
# M2: authorization records, classified paths and inspection evidence (pure: no Git, no network)
# ---------------------------------------------------------------------------

def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [key for key, _value in pairs]
    duplicated = sorted({key for key in keys if keys.count(key) > 1})
    if duplicated:
        raise IntegrationError(f"the authorization record names {duplicated} more than once")
    return dict(pairs)


def _no_constants(name: str) -> Any:
    raise IntegrationError(f"the authorization record contains {name}, which is not JSON")


def parse_record_time(value: Any, label: str) -> datetime:
    """A UTC timestamp `YYYY-MM-DDTHH:MM:SS[.f…]Z` (1 to 6 fractional digits) of an authorization record, as an
    aware datetime with its FULL precision: `…:00.9Z` is 900 ms after `…:00.1Z` (approvals are ordered by it, so a
    truncated fraction would make two distinct records look simultaneous). More than 6 digits are refused."""
    match = _RECORD_TIME_RE.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        raise IntegrationError(f"{label} must be a UTC timestamp like 2026-11-12T09:00:00Z or "
                               f"2026-11-12T09:00:00.250000Z, got {_shown(value)}")
    try:
        seconds = datetime.strptime(match.group(1), "%Y-%m-%dT%H:%M:%S")
    except ValueError as exc:
        raise IntegrationError(f"{label} is not a valid date and time: {_shown(value)}") from exc
    return seconds.replace(microsecond=int((match.group(2) or "").ljust(6, "0")), tzinfo=timezone.utc)


def check_path_text(value: Any, label: str = "path") -> str:
    """A repository-relative POSIX path as an operator names it in an authorization record (no glob, no `..`)."""
    valid = isinstance(value, str) and 0 < len(value) <= MAX_PATH_TEXT and not value.startswith("/")
    if not valid or re.search(r"[\x00-\x1f\x7f\\]", value) or any(p in ("", ".", "..") for p in value.split("/")):
        raise IntegrationError(f"{label} must be a repository-relative path (no '..', no leading '/', no control "
                               f"characters), got {_shown(value)}")
    return value


def parse_approval_record(text: Any) -> dict[str, Any]:
    """The operator authorization record (the exact JSON text that was signed), strictly: one JSON object with
    exactly the record keys (`note` optional), no duplicate key, no NaN/Infinity, every field of its type. Binding to
    a job is checked separately (approval_binding_problems); the signature in integration_approval.py."""
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_RECORD_BYTES:
        raise IntegrationError(f"the authorization record must be UTF-8 JSON text of at most {MAX_RECORD_BYTES} bytes")
    try:
        data = json.loads(text, object_pairs_hook=_no_duplicates, parse_constant=_no_constants)
    except ValueError as exc:
        raise IntegrationError(f"the authorization record is not valid JSON: {clean_text(exc, 200)}") from exc
    if not isinstance(data, dict):
        raise IntegrationError("the authorization record must be a JSON object")
    missing = [key for key in _RECORD_REQUIRED if key not in data]
    unknown = sorted(set(data) - _RECORD_KEYS)
    if missing or unknown:
        raise IntegrationError(f"the authorization record has missing field(s) {missing} and unknown field(s) "
                               f"{unknown}")
    _expect(data["kind"] == APPROVAL_KIND and type(data["version"]) is int  # pylint: disable=unidiomatic-typecheck
            and data["version"] == APPROVAL_VERSION,
            f"the authorization record must be kind {APPROVAL_KIND!r}, version {APPROVAL_VERSION}")
    check_job_id(data["job"])
    _expect(data["stage"] in APPROVAL_STAGES, f"stage must be one of {list(APPROVAL_STAGES)} (the merge stage is M5), "
                                              f"got {_shown(data['stage'])}")
    _expect(isinstance(data["operator"], str) and _LOGIN_RE.fullmatch(data["operator"]) is not None,
            f"operator must be a GitHub login, got {_shown(data['operator'])}")
    issued = parse_record_time(data["issued_at"], "issued_at")
    expires = parse_record_time(data["expires_at"], "expires_at")
    _expect(expires > issued, "expires_at must be later than issued_at")
    source, base = data["source"], data["base"]
    _expect(isinstance(source, dict) and set(source) == {"repo", "ref", "sha"},
            "source must have exactly repo, ref and sha")
    _expect(isinstance(base, dict) and set(base) == {"ref", "sha"}, "base must have exactly ref and sha")
    check_repo(source["repo"], "source.repo")
    check_ref(source["ref"], "source.ref")
    check_sha(source["sha"], "source.sha")
    check_ref(base["ref"], "base.ref")
    check_sha(base["sha"], "base.sha")
    check_number(data["issue"], "issue")
    if data["pr"] is not None:
        check_number(data["pr"], "pr")
    paths = data["paths"]
    _expect(isinstance(paths, list) and len(paths) <= MAX_APPROVED_PATHS,
            f"paths must be a list of at most {MAX_APPROVED_PATHS} repository paths")
    for path in paths:
        check_path_text(path, "an authorized path")
    _expect(len(set(paths)) == len(paths), "paths lists a path more than once")
    _expect(isinstance(data["accept_unverified_provenance"], bool),
            "accept_unverified_provenance must be true or false")
    note = data.get("note", "")
    _expect(isinstance(note, str) and len(note) <= MAX_REASON and not re.search(r"[\x00-\x1f\x7f]", note),
            f"note must be one line of at most {MAX_REASON} printable characters")
    return data


def approval_binding_problems(record: Mapping[str, Any], job: "IntegrationJob") -> list[str]:
    """Why a parsed authorization record does not bind exactly this job's pinned inputs (empty: it does). The job
    ID binds it to one job (an identical request with --supersede is another job and needs its own approval)."""
    problems = []
    if record["job"] != job.job_id:
        problems.append(f"it authorizes job {record['job']}, not {job.job_id}")
    source, base = record["source"], record["base"]
    if source["repo"].lower() != job.source["repo"].lower() or source["ref"] != job.source["ref"]:
        problems.append(f"its source is {source['repo']}:{source['ref']}, not {job.source['repo']}:{job.source['ref']}")
    if source["sha"] != job.source["sha"]:
        problems.append(f"its source SHA {source['sha']} is not the job's pinned source SHA {job.source['sha']}")
    if base["ref"] != job.base["ref"] or base["sha"] != job.base["expected_sha"]:
        problems.append(f"its base {base['ref']}@{base['sha']} is not the job's pinned base "
                        f"{job.base['ref']}@{job.base['expected_sha']}")
    if record["issue"] != job.issue:
        problems.append(f"it names Issue #{record['issue']}, not #{job.issue}")
    if record["pr"] != (job.pr or {}).get("number"):
        problems.append(f"it names PR {record['pr']}, but the job's PR is {(job.pr or {}).get('number')}")
    return problems


def _check_approvals(job: "IntegrationJob") -> None:
    _expect(len(job.approvals) <= MAX_APPROVALS, f"at most {MAX_APPROVALS} approvals")
    latest: dict[str, datetime] = {}
    for index, entry in enumerate(job.approvals):
        where = f"approval {index + 1}"
        _expect(set(entry) == _APPROVAL_KEYS, f"{where} must have exactly {sorted(_APPROVAL_KEYS)}")
        match = _APPROVAL_ID_RE.fullmatch(str(entry["id"]))
        _expect(match is not None and int(match.group(1)) == index + 1, f"{where} must have the id apr-{index + 1:04d}")
        record_text = entry["record"]
        _expect(isinstance(record_text, str) and isinstance(entry["record_sha256"], str)
                and hashlib.sha256(record_text.encode("utf-8")).hexdigest() == entry["record_sha256"],
                f"{where}: the stored authorization record does not match its SHA-256")
        record = parse_approval_record(record_text)
        problems = approval_binding_problems(record, job)
        _expect(not problems, f"{where} does not bind this job: {'; '.join(problems)}")
        derived = {"stage": record["stage"], "operator": record["operator"], "issued_at": record["issued_at"],
                   "expires_at": record["expires_at"], "source_sha": record["source"]["sha"],
                   "base_sha": record["base"]["sha"], "issue": record["issue"], "pr": record["pr"],
                   "paths": record["paths"], "accept_unverified_provenance": record["accept_unverified_provenance"],
                   "note": record.get("note", "")}
        differing = sorted(key for key, value in derived.items() if entry[key] != value)
        _expect(not differing, f"{where}: field(s) {differing} differ from the signed authorization record")
        signature, signer = entry["signature"], entry["signer"]
        _expect(isinstance(signature, str) and len(signature) <= MAX_SIGNATURE_BYTES
                and signature.startswith(SIGNATURE_HEADER), f"{where}: the signature must be an SSH signature")
        _expect(isinstance(signer, dict) and set(signer) == _SIGNER_KEYS and signer["method"] == "ssh-sig"
                and signer["namespace"] == APPROVAL_NAMESPACE and signer["principal"] == record["operator"]
                and isinstance(signer["key"], str) and _KEY_FINGERPRINT_RE.fullmatch(signer["key"]) is not None,
                f"{where}: signer must be the operator's verified SSH key (method, namespace, principal, key)")
        _expect(_is_time(entry["recorded_at"]) and entry["actor"] == record["operator"],
                f"{where}: recorded_at must be a timestamp and actor the signing operator")
        issued = parse_record_time(record["issued_at"], "issued_at")
        previous = latest.get(record["stage"])
        _expect(previous is None or issued > previous,
                f"{where} is not issued strictly after the approval it follows (approvals of a stage are recorded in "
                "strictly increasing issue order, at full timestamp precision)")
        latest[record["stage"]] = issued


def _check_paths(paths: list[dict[str, Any]]) -> None:
    seen: set[str] = set()
    for index, entry in enumerate(paths):
        where = f"paths[{index}]"
        _expect(set(entry) == _PATH_KEYS, f"{where} must have exactly {sorted(_PATH_KEYS)}")
        check_path_text(entry["path"], f"{where}: path")
        _expect(entry["path"] not in seen, f"{where}: path must be a unique repository path")
        seen.add(entry["path"])
        _expect(entry["class"] in PATH_CLASSES, f"{where}: class must be one of {list(PATH_CLASSES)}")
        _expect(entry["role"] is None or _is_text(entry["role"], 40), f"{where}: role must be null or a role name")
        _expect((entry["role"] is not None) == (entry["class"] == "owned") or entry["class"] == "approval-required",
                f"{where}: an owned path names its role, a protected or unowned path none")
        for key, allowed in (("changes", PATH_CHANGES), ("flags", PATH_FLAGS)):
            value = entry[key]
            _expect(isinstance(value, list) and all(item in allowed for item in value)
                    and value == sorted(set(value)), f"{where}: {key} must be a sorted subset of {list(allowed)}")
        _expect(bool(entry["changes"]), f"{where}: changes must not be empty")
        _expect(entry["authorization"] in PATH_AUTHORIZATION,
                f"{where}: authorization must be one of {list(PATH_AUTHORIZATION)}")
        needs = entry["class"] in ("approval-required", "unowned") or bool({"symlink", "git-control"}
                                                                              & set(entry["flags"]))
        must_refuse = entry["class"] == "protected" or bool({"gitlink", "empty-tree"} & set(entry["flags"]))
        status = entry["authorization"]
        _expect(status == "refused" if must_refuse else (status != "not-needed" or not needs),
                f"{where}: authorization {status!r} contradicts class {entry['class']!r} and flags {entry['flags']}")
        _expect(status not in ("authorized", "required") or needs,
                f"{where}: authorization {status!r} for a path that needs none")


def inspection_digest(entry: Mapping[str, Any]) -> str:
    """SHA-256 of an inspection evidence payload (everything but its id, time and digest): two inspections with the
    same digest found exactly the same thing under the same approval for the same pinned SHAs."""
    payload = {k: v for k, v in entry.items() if k not in ("id", "at", "sha256")}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _check_evidence(job: "IntegrationJob") -> None:
    _expect(len(job.evidence) <= MAX_EVIDENCE, f"at most {MAX_EVIDENCE} evidence entries")
    approval_ids = {entry["id"] for entry in job.approvals}
    for index, entry in enumerate(job.evidence):
        where = f"evidence {index + 1}"
        _expect(entry.get("kind") == "inspection", f"{where}: kind must be 'inspection' (gates and reviews are M3/M4)")
        _expect(set(entry) == _INSPECTION_KEYS, f"{where} must have exactly {sorted(_INSPECTION_KEYS)}")
        match = _EVIDENCE_ID_RE.fullmatch(str(entry["id"]))
        _expect(match is not None and int(match.group(1)) == index + 1, f"{where} must have the id ins-{index + 1:04d}")
        _expect(_is_time(entry["at"]), f"{where}: at must be a timestamp")
        _expect(entry["approval"] in approval_ids, f"{where} names approval {_shown(entry['approval'])}, which this "
                                                   "job does not carry")
        _expect(entry["source_sha"] == job.source["sha"] and entry["base_sha"] == job.base["expected_sha"],
                f"{where} is bound to other SHAs than the job's pinned source and base")
        _expect(entry["result"] in INSPECTION_RESULTS, f"{where}: result must be one of {list(INSPECTION_RESULTS)}")
        pr_match = entry["pr_match"]
        _expect(isinstance(pr_match, dict) and set(pr_match) == _PR_MATCH_KEYS
                and pr_match["result"] in PR_MATCH_RESULTS
                and (pr_match["number"] is None or _is_int(pr_match["number"], 1))
                and isinstance(pr_match["candidates"], list) and all(_is_int(n, 1) for n in pr_match["candidates"]),
                f"{where}: pr_match must have exactly {sorted(_PR_MATCH_KEYS)}")
        prov = entry["provenance"]
        _expect(isinstance(prov, dict) and set(prov) == _INSPECTION_PROVENANCE_KEYS
                and prov["source"] in PROVENANCE_SOURCES and isinstance(prov["accepted"], bool)
                and all(_is_int(prov[k], 0) for k in ("verified", "unverified", "unverifiable")),
                f"{where}: provenance must have exactly {sorted(_INSPECTION_PROVENANCE_KEYS)}")
        summary = entry["paths"]
        _expect(isinstance(summary, dict) and set(summary) == _INSPECTION_PATHS_KEYS
                and all(_is_int(summary[k], 0) for k in ("count", "required", "authorized", "refused"))
                and isinstance(summary["sha256"], str)
                and (summary["sha256"] == "" or _HEX64_RE.fullmatch(summary["sha256"]) is not None),
                f"{where}: paths must have exactly {sorted(_INSPECTION_PATHS_KEYS)}")
        _expect(_is_text_list(entry["reasons"]) and len(entry["reasons"]) <= 20, f"{where}: reasons must be texts")
        _expect(entry["sha256"] == inspection_digest(entry), f"{where}: its SHA-256 does not match its payload")
        if entry["result"] == "passed":
            _expect(summary["required"] == 0 and summary["refused"] == 0 and pr_match["result"] in ("none", "reuse"),
                    f"{where}: a passed inspection cannot leave a path unauthorized or refused or a PR unresolved")
    if job.paths:
        with_paths = [entry for entry in job.evidence if entry["paths"]["count"]]
        _expect(bool(with_paths) and with_paths[-1]["paths"]["sha256"] == paths_digest(job.paths)
                and with_paths[-1]["paths"]["count"] == len(job.paths),
                "paths are not the classified paths bound into the newest inspection evidence")


def paths_digest(paths: list[dict[str, Any]]) -> str:
    """SHA-256 of a classified path list (bound into the inspection evidence)."""
    return hashlib.sha256(json.dumps(paths, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def latest_inspection(job: "IntegrationJob") -> dict[str, Any] | None:
    """The newest inspection evidence entry, or None."""
    entries = [entry for entry in job.evidence if entry.get("kind") == "inspection"]
    return entries[-1] if entries else None


def current_inspection_passed(job: "IntegrationJob") -> bool:
    """True only while the job is INSPECTING, nothing is deferred, and the NEWEST inspection passed under the
    CURRENTLY effective approval. M3 must require this AND re-check that approval's validity at use time
    (integration_approval.effective_approval_problems) before it builds anything: an older passed entry stays in the
    append-only evidence after a newer approval or a later deferral and must not be taken for the current result."""
    latest, approval = latest_inspection(job), effective_approval(job)
    return (job.state is JobState.INSPECTING and job.deferred is None and latest is not None and approval is not None
            and latest["result"] == "passed" and latest["approval"] == approval["id"])


def effective_approval(job: "IntegrationJob", stage: str = "inspect") -> dict[str, Any] | None:
    """The NEWEST approval of *stage* (an operator's newer decision supersedes the older ones; there is no fallback to
    an older approval when the newest one is no longer valid). The last entry is the newest: _check_approvals keeps
    the approvals of a stage in strictly increasing `issued_at` order (full precision), on every load and save."""
    entries = [entry for entry in job.approvals if entry["stage"] == stage]
    return entries[-1] if entries else None


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise IntegrationError(message)


def _is_int(value: Any, minimum: int) -> bool:
    return type(value) is int and value >= minimum  # pylint: disable=unidiomatic-typecheck  # bool is not a count


def _is_text(value: Any, limit: int = MAX_TEXT) -> bool:
    return isinstance(value, str) and len(value) <= limit


def _is_text_list(value: Any) -> bool:
    return isinstance(value, list) and all(_is_text(item) for item in value)


def _is_time(value: Any) -> bool:
    return isinstance(value, str) and _TIMESTAMP_RE.fullmatch(value) is not None


def _copy(value: Any) -> Any:
    return json.loads(json.dumps(value)) if value is not None else None


def check_job_id(value: Any) -> str:
    """`int-YYYYMMDD-NNNN` (design §8.2), also a valid state-store record ID."""
    if not isinstance(value, str) or not _JOB_ID_RE.fullmatch(value):
        raise IntegrationError(f"invalid integration job ID {_shown(value)} (expected int-YYYYMMDD-NNNN)")
    return check_id(value, "integration job id")


def _check_history(job: IntegrationJob) -> None:
    if not isinstance(job.history, list):
        raise IntegrationError("history must be a list")
    if not job.history:
        raise IntegrationError("history is empty")
    previous: str | None = None
    for index, entry in enumerate(job.history):
        if not isinstance(entry, dict) or set(entry) != _HISTORY_FIELDS:
            raise IntegrationError(f"history entry {index} must have exactly {sorted(_HISTORY_FIELDS)}")
        if not (_is_time(entry["at"]) and _is_text(entry["reason"], MAX_REASON)
                and _is_text(entry["actor"], MAX_ACTOR)):
            raise IntegrationError(f"history entry {index} needs a timestamp, a reason and an actor (texts)")
        if entry["from"] != previous:
            raise IntegrationError(f"history entry {index} starts at {entry['from']!r}, not at {previous!r}")
        target = JobState(entry["to"])
        if previous is None:
            if target is not JobState.INTAKE:
                raise IntegrationError("history must start with INTAKE")
        else:
            check_transition(JobState(previous), target)
        previous = target.value
    if previous != job.state.value:
        raise IntegrationError(f"history ends at {previous}, but the state is {job.state.value}")


def transition_job(job: IntegrationJob, target: JobState, reason: str, *, actor: str) -> bool:
    """Move *job* to *target* in memory (persist with IntegrationJobs.save). False for a no-op (already there);
    IllegalTransitionError for an edge outside JOB_TRANSITIONS or a terminal job."""
    if job.state is target:
        return False
    check_transition(job.state, target)
    if target is JobState.AWAITING_APPROVAL and not job.provenance:
        raise IllegalTransitionError(f"{job.job_id}: AWAITING_APPROVAL needs the provenance recorded by intake")
    if target is JobState.MERGED and not SHA_RE.fullmatch(str((job.merge_result or {}).get("commit_sha", ""))):
        raise IllegalTransitionError(f"{job.job_id}: MERGED needs the actual merge commit (merge_result)")
    if target is JobState.COMPLETED and (job.merge_result or {}).get("verified") is not True:
        raise IllegalTransitionError(f"{job.job_id}: COMPLETED needs a verified merge_result (design §5.10); "
                                     "neither the job record nor commit ancestry proves completion")
    now = utc_now()
    job.history.append({"from": job.state.value, "to": target.value, "at": now,
                        "reason": clean_text(reason) or target.value, "actor": clean_text(actor, MAX_ACTOR)})
    job.state = target
    job.updated_at = now
    if target is not JobState.INTAKE:
        job.deferred = None
    return True


# ---------------------------------------------------------------------------
# Persistence (the coordinator state store; no separate mechanism)
# ---------------------------------------------------------------------------

class IntegrationJobs:
    """Integration job records of one state directory, on top of StateStore (record kind `integration`)."""

    def __init__(self, store: Any) -> None:
        self.store = store

    def load(self, job_id: str) -> IntegrationJob:
        """One job; IntegrationError when unknown, StateDirError when its record is corrupt (not JSON, not a valid
        record, or a record of another job under this file name)."""
        raw = self.store.load_record(RECORD_KIND, check_job_id(job_id))
        if raw is None:
            if (self.store.root / RECORD_KIND / f"{job_id}.json").exists():  # the file holds JSON `null`
                raise StateDirError(f"integration record {job_id}.json is not a JSON object: corrupt state directory")
            raise IntegrationError(f"unknown integration job {job_id!r}")
        job = IntegrationJob.from_dict(raw)
        if job.job_id != job_id:
            raise StateDirError(f"integration record {job_id}.json holds job {job.job_id}: corrupt state directory")
        return job

    def list(self) -> list[IntegrationJob]:
        """Every job, oldest ID first. A corrupt record, or a file that is not a job record, raises StateDirError
        (fail closed: never skipped). Hidden temporary files of an interrupted write are not records."""
        folder = self.store.root / RECORD_KIND
        paths = sorted(folder.glob("*.json"))
        strays = [path.name for path in paths if not _JOB_ID_RE.fullmatch(path.stem)]
        if strays:
            raise StateDirError(f"{', '.join(strays[:5])} in {folder} is not an integration job record (expected "
                                "int-YYYYMMDD-NNNN.json): corrupt state directory")
        return [self.load(path.stem) for path in paths]

    def next_id(self, when: datetime | None = None) -> str:
        """The next free `int-YYYYMMDD-NNNN` (inside a transaction, so two processes never pick the same)."""
        day = (when or datetime.now(timezone.utc)).strftime("%Y%m%d")
        used = [int(m.group(2)) for p in (self.store.root / RECORD_KIND).glob(f"int-{day}-*.json")
                if (m := _JOB_ID_RE.fullmatch(p.stem)) and m.group(1) == day]
        number = max(used, default=0) + 1
        if number > 999_999:
            raise IntegrationError(f"too many integration jobs on {day}")
        return f"int-{day}-{number:04d}"

    def save(self, job: IntegrationJob) -> None:
        """Persist *job* (inside a transaction). Refuses a change to an immutable field, a rewritten or shortened
        history and a stale in-memory copy (StateConflictError); a new job must not exist yet."""
        current = self.store.load_record(RECORD_KIND, job.job_id)
        if current is not None and not job.revision:
            raise StateConflictError(f"integration job {job.job_id} already exists; a new job never replaces it")
        if current is not None:
            before = IntegrationJob.from_dict(current)
            changed = [name for name in _IMMUTABLE if getattr(before, name) != getattr(job, name)]
            if (before.pr or {}).get("number") != (job.pr or {}).get("number"):
                changed.append("pr.number")
            if changed:
                raise IntegrationError(f"{job.job_id}: immutable field(s) {changed} cannot change")
            if job.history[:len(before.history)] != before.history:
                raise IntegrationError(f"{job.job_id}: history is append-only (an earlier entry changed or was "
                                       "removed)")
            for name in ("approvals", "evidence"):  # M2: the audit trail of authorizations and inspections
                if getattr(job, name)[:len(getattr(before, name))] != getattr(before, name):
                    raise IntegrationError(f"{job.job_id}: {name} is append-only (an earlier entry changed or was "
                                           "removed)")
            volatile = ("revision", "updated_at")
            if before.terminal and ({k: v for k, v in job.to_dict().items() if k not in volatile}
                                    != {k: v for k, v in before.to_dict().items() if k not in volatile}):
                raise IntegrationError(f"{job.job_id} is {before.state.value} (terminal): its record is final")
        elif job.revision:
            raise StateConflictError(f"{job.job_id} disappeared from the state directory")
        job.updated_at = utc_now()
        validate_job(job, persisted=False)
        self.store.save_record(RECORD_KIND, job)

    def find(self, *, key: str | None = None, supersedes: str | None = None) -> list[IntegrationJob]:
        """Jobs with this request key and/or that supersede this job (oldest first)."""
        return [job for job in self.list()
                if (key is None or job.request_key == key) and (supersedes is None or job.supersedes == supersedes)]

    def active_conflicts(self, request: IntakeRequest) -> list[IntegrationJob]:
        """Non-terminal jobs with ANOTHER request that hold the same source branch, source commit or PR."""
        wanted = request.conflict_keys()
        return [job for job in self.list() if not job.terminal and job.request_key != request.key
                and job.request().conflict_keys() & wanted]

    def transition(self, job_id: str, target: JobState, reason: str, *, actor: str,
                   expect: Iterable[JobState] | None = None, **updates: Any) -> tuple[IntegrationJob, bool]:
        """Load, transition and save one job in one transaction. *expect*: the states the caller's decision was
        based on — anything else (e.g. aborted meanwhile) changes nothing and returns (job, False). *updates*:
        fields set together with the transition (provenance, pr, attention, deferred; M2: paths, and evidence entries
        to APPEND). Returns (job, changed)."""
        with self.store.transaction():
            job = self.load(job_id)
            if expect is not None and job.state not in set(expect) and job.state is not target:
                return job, False
            if job.state is target:
                return job, False
            for name, value in updates.items():
                if name == "evidence":
                    job.evidence.extend(_copy(value))
                    continue
                if name not in ("provenance", "pr", "attention", "deferred", "paths"):
                    raise IntegrationError(f"field {name!r} cannot be set with a transition (M1/M2)")
                setattr(job, name, _copy(value))
            before = job.state
            transition_job(job, target, reason, actor=actor)
            self.save(job)
            entry = job.history[-1]
            self.store.append_event("integration-transition", job=job.job_id, actor=entry["actor"],
                                    reason=entry["reason"], to=target.value, **{"from": before.value})
            return job, True


def job_summary(job: IntegrationJob) -> dict[str, Any]:
    """The status of one job: its record plus derived facts (JSON-serializable). M2: `inspection` is the newest
    inspection evidence, `effective_approval` the id of the newest inspect-stage approval (its validity is checked
    when it is used, not here)."""
    approval = effective_approval(job)
    return {**job.to_dict(), "terminal": job.terminal, "allowed_next": allowed_next(job.state),
            "inspection": _copy(latest_inspection(job)), "effective_approval": approval["id"] if approval else None}


def abort_job(jobs: IntegrationJobs, job_id: str, reason: str, *, actor: str, apply: bool) -> dict[str, Any]:
    """Operator abort (design §8.1 `abort`): any non-terminal state before MERGED → ABORTED. Idempotent: an
    already ABORTED job is reported unchanged. Another terminal state, MERGED or COMPLETED is refused
    (IllegalTransitionError). M1 has no candidate worktree to remove (M3 adds that cleanup). Dry run unless *apply*.
    Returns {outcome, changed, job} with outcome would-abort | aborted | already-aborted."""
    reason = check_reason(reason)
    job = jobs.load(job_id)
    if job.state is JobState.ABORTED:
        return {"outcome": "already-aborted", "changed": False, "job": job_summary(job)}
    check_transition(job.state, JobState.ABORTED)
    if not apply:
        return {"outcome": "would-abort", "changed": False, "job": job_summary(job)}
    job, changed = jobs.transition(job_id, JobState.ABORTED, reason, actor=actor, expect=ABORTABLE_STATES)
    if not changed and job.state is not JobState.ABORTED:
        check_transition(job.state, JobState.ABORTED)  # it became terminal meanwhile: raises with that state
    return {"outcome": "aborted" if changed else "already-aborted", "changed": changed, "job": job_summary(job)}
