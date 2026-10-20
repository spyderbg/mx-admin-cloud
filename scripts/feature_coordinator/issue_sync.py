"""Synchronization between the local coordinator state and GitHub Issues (Phase 4).

Direction of authority. The local state (features/, tasks/, claims/, locks) is the only
authority for task states, claims, workers, locks and execution permissions. GitHub is the
external, human-visible record. A sync therefore *derives* each Issue from the local record
and converges the Issue to it:

  * existence  — one Issue per feature (parent) and per task, found by its ownership marker
                 and idempotency key before anything is created (issues.py);
  * body       — only the managed block between the begin/end markers is rewritten; text a
                 human adds outside it is preserved;
  * labels     — the `feature:*` labels of the record's state are added/removed one by one
                 (other labels are never touched);
  * open/closed — open unless the feature is done or a test cleanup closed it; a manual close
                 is reported as drift and reverted, because closing an Issue never completes a
                 task;
  * comments   — state changes since the last sync (the history list is the outbox; the
                 operation ID is its index range) and explicit outbox entries (outbox/<op>.json).

The only GitHub → local path is a validated command in a comment (`/feature hold <task>
<reason>`, `/feature release <task>`) from an operator listed in `github.command_authors` with at
least `command_min_permission` on the repository. A command can move a task into or out of
`needs-human`; it can never verify or unblock a dependency, grant a path, a tool or shell
access. Labels, edits, closing and every other comment are ignored as input.

Idempotency and recovery. Every remote write is preceded by a persisted operation record:
`pending_create` before creating an Issue, `pending_history` / outbox `attempts` before a
comment. After a crash or an ambiguous failure (GitHubUncertainWriteError) the next sync
searches for the Issue by its key, or for the comment by its operation marker, before it
writes again — so a request that succeeded remotely is reused, never duplicated.

Fault points (tests and the live recovery checks): crash-after-create, crash-after-link,
crash-after-comment, crash-after-outbox and fail-before-update, each followed by `:<record-id>`.

Locks. A sync holds only `github-sync.lock` (one sync at a time) while it talks to GitHub;
state changes happen in short `StateStore.transaction()` sections that always reload the
record first, so a stale GitHub view can never overwrite newer local state, and a failed
request can never corrupt the dependency graph (the sync never edits dependencies).
"""

from __future__ import annotations

import hashlib
import re
import secrets
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .config import ProjectConfig
from .errors import (
    ClaimError,
    GitHubError,
    GitHubNotFoundError,
    IllegalTransitionError,
    OwnershipError,
    StateDirError,
    SyncScopeError,
)
from .escalation import hold_task, release_task
from .github_api import sanitize
from .issues import (
    BLOCKED_LABEL,
    IN_PROGRESS_LABEL,
    KNOWN_LABELS,
    LABEL_SPECS,
    PARENT_LABEL,
    REVIEW_LABEL,
    ROLE_LABELS,
    STATE_LABELS,
    TEST_LABEL,
    VERIFIED_LABEL,
    OwnershipMarker,
    RemoteComment,
    RemoteIssue,
    TrackerClient,
    escape_markdown_comment,
    extract_block,
    feature_marker,
    is_foreign,
    normalize_body,
    op_marker,
    parse_op,
    replace_block,
    task_marker,
    verify_ownership,
)
from .model import Feature, FeatureState, HistoryEntry, Task, TaskState, last_entered, utc_now
from .store import StateStore

LINK_SCHEMA = 1
PERMISSION_RANK = {"admin": 4, "maintain": 3, "write": 2, "triage": 1, "read": 0, "none": -1}
_CMD_RE = re.compile(r"/feature[ \t]+(hold|release)[ \t]+([a-z0-9][a-z0-9._-]{0,79})(?:[ \t]+(\S.{0,199}))?")
_PLACEHOLDER_RE = re.compile(r"\{\{issue:([a-z0-9][a-z0-9._-]{0,79})\}\}")
MAX_COMMAND_COMMENT = 2000
MAX_INGESTED = 500


class SimulatedCrash(BaseException):
    """Raised by a fault-injection point (tests and the live recovery check): a process dying
    right after a remote write succeeded and before the local state recorded it."""


@dataclass
class IssueLink:  # pylint: disable=too-many-instance-attributes
    """The GitHub side of one local record: mapping, identity and sync cursors."""

    id: str                      # the feature or task ID
    kind: str                    # "feature" | "task"
    feature_id: str
    key: str                     # the idempotency key in the ownership marker
    issue_number: int | None = None
    issue_id: int | None = None  # GitHub database ID: exact identity check before every write
    url: str = ""
    author: str = ""
    pending_create: dict[str, str] | None = None
    history_cursor: int = 0      # history entries already reported in comments
    pending_history: dict[str, int] | None = None
    comment_cursor: int = 0      # highest comment ID already ingested
    ingested: list[int] = field(default_factory=list)
    block_hash: str = ""         # what the coordinator last wrote: a difference on GitHub is drift
    labels_written: list[str] = field(default_factory=list)
    state_written: str = ""
    hold_comment_id: int | None = None  # the GitHub comment that caused the current human hold
    hold_at: str = ""                   # ... and the history timestamp of that hold
    closed_by_cleanup: bool = False
    last_synced_at: str = ""
    revision: int = 0

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        return {**asdict(self), "schema_version": LINK_SCHEMA}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "IssueLink":
        """Inverse of to_dict()."""
        if data.get("schema_version") != LINK_SCHEMA:
            raise StateDirError(f"unsupported github link schema {data.get('schema_version')!r}")
        values = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}  # pylint: disable=no-member
        return cls(**values)


@dataclass
class OutboxEntry:  # pylint: disable=too-many-instance-attributes
    """A coordinator comment waiting to be posted. `{{issue:<record-id>}}` placeholders in
    the body become `#<number>` when it is sent."""

    id: str
    record_id: str
    body: str
    status: str = "pending"
    attempts: int = 0
    created_at: str = field(default_factory=utc_now)
    sent_at: str = ""
    comment_id: int | None = None
    last_error: str = ""
    revision: int = 0

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        return {**asdict(self), "schema_version": LINK_SCHEMA}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "OutboxEntry":
        """Inverse of to_dict()."""
        values = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}  # pylint: disable=no-member
        return cls(**values)


@dataclass
class SyncReport:  # pylint: disable=too-many-instance-attributes
    """What a sync did (apply) or would do (dry run)."""

    feature_id: str
    applied: bool
    writes: list[str] = field(default_factory=list)
    created: dict[str, int] = field(default_factory=dict)
    adopted: dict[str, int] = field(default_factory=dict)
    protected: dict[str, str] = field(default_factory=dict)
    duplicates: dict[str, list[int]] = field(default_factory=dict)
    drift: list[str] = field(default_factory=list)
    commands: list[dict[str, Any]] = field(default_factory=list)
    deferred: list[str] = field(default_factory=list)
    issues: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """True when nothing was deferred and no Issue was refused as not owned."""
        return not self.deferred and not self.protected

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        return {**asdict(self), "ok": self.ok}


def enqueue_comment(store: StateStore, record_id: str, body: str) -> OutboxEntry:
    """Queue a coordinator comment for *record_id*'s Issue (call inside a transaction, together
    with the state change it reports, so both persist atomically)."""
    entry = OutboxEntry(id=f"o{secrets.token_hex(8)}", record_id=record_id, body=body)
    store.save_record("outbox", entry)
    return entry


def parse_command(body: str) -> tuple[str, str, str] | None:
    """(verb, task id, reason) of a `/feature` command comment; ("invalid", line, "") for a
    malformed `/feature` line; None if the comment is not a command."""
    if len(body) > MAX_COMMAND_COMMENT:
        return None
    first = next((line.strip() for line in body.splitlines() if line.strip()), "")
    if not first.startswith("/feature"):
        return None
    match = _CMD_RE.fullmatch(first)
    if match is None:
        return ("invalid", sanitize(first, 120), "")
    return match.group(1), match.group(2), sanitize(match.group(3) or "", 200)


def _digest(text: str) -> str:
    return hashlib.sha256(normalize_body(text).encode("utf-8")).hexdigest()


def _stamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class IssueSync:  # pylint: disable=too-many-instance-attributes,too-many-public-methods
    """Synchronizes one state store with one tracker. See the module docstring."""

    def __init__(self, store: StateStore, client: TrackerClient, config: ProjectConfig, *,
                 sleep: Callable[[float], None] = time.sleep, faults: Iterable[str] = ()) -> None:
        self.store = store
        self.client = client
        self.config = config
        self.gh = config.github
        self.sleep = sleep
        self.faults = set(faults)
        self._viewer: str | None = None
        self._labels_ready: set[str] = set()
        self._permissions: dict[str, str] = {}
        self._home = str(Path.home())

    # -- identity --------------------------------------------------------------

    @property
    def coordinator_id(self) -> str:
        """This coordinator's identity in every marker."""
        return self.gh.coordinator_id

    def viewer(self) -> str:
        """The account the tracker acts as (cached)."""
        if self._viewer is None:
            self._viewer = self.client.viewer_login()
        return self._viewer

    def expected_marker(self, feature_id: str, task_id: str | None = None) -> OwnershipMarker:
        """The marker an owned Issue of this record must carry."""
        if task_id is None:
            return feature_marker(self.coordinator_id, feature_id)
        return task_marker(self.coordinator_id, feature_id, task_id)

    def _fault(self, name: str) -> None:
        if name in self.faults:
            self.faults.discard(name)
            raise SimulatedCrash(name)

    # -- local records -----------------------------------------------------------

    def load_link(self, record_id: str) -> IssueLink | None:
        """The link record of a feature or task, if any."""
        data = self.store.load_record("github", record_id)
        return IssueLink.from_dict(data) if data else None

    def links(self, feature_id: str | None = None) -> list[IssueLink]:
        """All link records (of one feature)."""
        links = [IssueLink.from_dict(d) for d in self.store.list_records("github")]
        return [link for link in links if feature_id is None or link.feature_id == feature_id]

    def outbox(self, record_id: str | None = None, status: str | None = "pending") -> list[OutboxEntry]:
        """Outbox entries, oldest first."""
        entries = [OutboxEntry.from_dict(d) for d in self.store.list_records("outbox")]
        return sorted((e for e in entries if (record_id is None or e.record_id == record_id)
                       and (status is None or e.status == status)), key=lambda e: (e.created_at, e.id))

    def _new_link(self, feature: Feature, task: Task | None) -> IssueLink:
        marker = self.expected_marker(feature.id, task.id if task else None)
        return IssueLink(id=task.id if task else feature.id, kind="task" if task else "feature",
                         feature_id=feature.id, key=marker.key)

    def _update_link(self, feature: Feature, task: Task | None, mutate: Callable[[IssueLink], None]) -> IssueLink:
        """Reload, change and save a link inside one short transaction."""
        record_id = task.id if task else feature.id
        with self.store.transaction():
            link = self.load_link(record_id) or self._new_link(feature, task)
            mutate(link)
            self.store.save_record("github", link)
        return link

    def _record_mapping(self, feature: Feature, task: Task | None, issue: RemoteIssue) -> None:
        """Store the Issue number on the local record and its identity on the link (atomically).
        A record already mapped to a different Issue is a conflict for a human, never overwritten."""
        with self.store.transaction():
            record: Feature | Task = self.store.load_task(task.id) if task else self.store.load_feature(feature.id)
            link = self.load_link(record.id) or self._new_link(feature, task)
            for known in (record.issue_number, link.issue_number):
                if known is not None and known != issue.number:
                    raise OwnershipError(f"{record.id} is already mapped to Issue #{known}, not #{issue.number}")
            if record.issue_number != issue.number:
                record.issue_number = issue.number
                if task:
                    self.store.save_task(record)  # type: ignore[arg-type]
                else:
                    self.store.save_feature(record)  # type: ignore[arg-type]
            link.issue_number, link.issue_id, link.url, link.author = issue.number, issue.id, issue.url, issue.author
            link.pending_create = None
            self.store.save_record("github", link)
            self.store.append_event("github-issue-linked", record=record.id, issue=issue.number)

    # -- desired state -------------------------------------------------------------

    def desired_labels(self, feature: Feature, task: Task | None) -> set[str]:
        """The `feature:*` labels the record's Issue must carry."""
        labels = {ROLE_LABELS[task.role]} if task else {PARENT_LABEL}
        if feature.test:
            labels.add(TEST_LABEL)
        if task is not None:
            state_label = {TaskState.IN_PROGRESS: IN_PROGRESS_LABEL, TaskState.BLOCKED: BLOCKED_LABEL,
                           TaskState.NEEDS_HUMAN: BLOCKED_LABEL, TaskState.FAILED: BLOCKED_LABEL,
                           TaskState.VERIFIED: VERIFIED_LABEL}.get(task.state)
        else:
            state_label = {FeatureState.ACTIVE: IN_PROGRESS_LABEL, FeatureState.INTEGRATION: IN_PROGRESS_LABEL,
                           FeatureState.REVIEW: REVIEW_LABEL, FeatureState.DONE: VERIFIED_LABEL,
                           FeatureState.BLOCKED: BLOCKED_LABEL, FeatureState.FAILED: BLOCKED_LABEL}.get(feature.state)
        if state_label:
            labels.add(state_label)
        return labels

    @staticmethod
    def desired_open(feature: Feature, link: IssueLink | None) -> bool:
        """Open unless the feature is done or a test cleanup closed its Issues."""
        return feature.state is not FeatureState.DONE and not (link and link.closed_by_cleanup)

    def _text(self, value: str, limit: int = 300) -> str:
        return escape_markdown_comment(sanitize(value.replace(self._home, "~"), limit))

    def title(self, feature: Feature, task: Task | None) -> str:
        """The Issue title (set at creation; never used as an identifier)."""
        prefix = self.gh.test_title_prefix if feature.test else ""
        text = f"[{task.role}] {task.title}" if task else feature.title
        return sanitize(prefix + text, 200)

    @staticmethod
    def _ref(record_id: str, numbers: Mapping[str, int | None]) -> str:
        number = numbers.get(record_id)
        return f"#{number}" if number else f"`{record_id}` (no Issue yet)"

    def render_block(self, feature: Feature, task: Task | None, tasks: Mapping[str, Task]) -> str:
        """The managed block of the record's Issue."""
        numbers: dict[str, int | None] = {feature.id: feature.issue_number,
                                          **{t.id: t.issue_number for t in tasks.values()}}
        if task is None:
            return self._render_feature(feature, tasks, numbers)
        return self._render_task(feature, task, tasks, numbers)

    def _state_line(self, record: Feature | Task) -> str:
        state = record.state.value
        detail = ""
        if isinstance(record, Task) and record.state is TaskState.BLOCKED:
            detail = record.blocked_reason
        elif record.history and record.state.value in ("needs-human", "failed", "blocked"):
            detail = record.history[-1].reason
        return f"**{state}**" + (f" — {self._text(detail, 200)}" if detail else "")

    def _render_task(self, feature: Feature, task: Task, tasks: Mapping[str, Task],
                     numbers: Mapping[str, int | None]) -> str:
        role = self.config.roles[task.role]
        paths = ", ".join(f"`{p}`" for p in role.owned)
        if role.excluded:
            paths += "; excluded " + ", ".join(f"`{p}`" for p in role.excluded)
        if task.grants:
            paths += "; task grants " + ", ".join(f"`{g}`" for g in task.grants)
        lines = [f"### Component task `{task.id}`", "", "| Field | Value |", "| --- | --- |",
                 f"| Feature | `{feature.id}`, parent Issue {self._ref(feature.id, numbers)} |",
                 f"| Role | `{task.role}` (one writer per role) |",
                 f"| State | {self._state_line(task)} |",
                 f"| Writable paths | {paths} |",
                 f"| Feature branch | `{feature.branch}` from `{feature.base_branch}` (shared by every role) |",
                 f"| Required gates | {', '.join(f'`{g}`' for g in task.required_gates) or 'none'} |",
                 f"| Provides an API contract | {'yes' if task.provides_contract else 'no'} |",
                 "", "#### Acceptance criteria",
                 *[f"- {self._text(a)}" for a in task.acceptance or ("(see the parent Issue)",)],
                 "", "#### Depends on"]
        deps = [f"- {self._ref(d, numbers)} `{d}` ({tasks[d].role}): **{tasks[d].state.value}**"
                for d in task.depends_on if d in tasks]
        lines += deps or ["- nothing"]
        dependents = [f"- {self._ref(t.id, numbers)} `{t.id}` ({t.role}): **{t.state.value}**"
                      for t in tasks.values() if task.id in t.depends_on]
        lines += ["", "#### Required by", *(dependents or ["- nothing"]), "", "#### Verification evidence"]
        lines += self._evidence_lines(task) or [
            "- none yet. A task is verified only by passing gates bound to the feature-branch HEAD, never by a "
            "comment, a label or a closed Issue."]
        lines += ["", "#### Commits", *([f"- `{c[:12]}`" for c in task.commits] or ["- none"]), "", _FOOTER]
        return "\n".join(lines)

    def _evidence_lines(self, task: Task) -> list[str]:
        if task.evidence is None:
            return []
        evidence = task.evidence
        gates = [f"- `{g.gate}`: {g.status} (exit {g.exit_code}, {g.duration_seconds:.1f} s)" for g in evidence.gates]
        return [f"- HEAD `{evidence.head_sha}` recorded {evidence.recorded_at}", *gates]

    def _render_feature(self, feature: Feature, tasks: Mapping[str, Task], numbers: Mapping[str, int | None]) -> str:
        roles = sorted({t.role for t in tasks.values()})
        lines = [f"### Feature `{feature.id}`: {self._text(feature.title, 200)}", "",
                 "| Field | Value |", "| --- | --- |",
                 f"| State | {self._state_line(feature)} |",
                 f"| Branch | `{feature.branch}` from `{feature.base_branch}`"
                 + (f" at `{feature.base_sha[:12]}`" if feature.base_sha else "") + " |",
                 f"| Roles | {', '.join(f'`{r}`' for r in roles)} |",
                 "| Pull requests | exactly one final PR after every gate passes (`one-after-all-gates`) |",
                 f"| Test feature | {'yes' if feature.test else 'no'} |",
                 "", "#### Acceptance criteria",
                 *[f"- {self._text(a)}" for a in feature.acceptance or ("(to be defined)",)],
                 "", "#### Component tasks"]
        for task in tasks.values():
            mark = "x" if task.state is TaskState.VERIFIED else " "
            lines.append(f"- [{mark}] {self._ref(task.id, numbers)} `{task.id}` ({task.role}): "
                         f"**{task.state.value}**")
        edges = [f"- {self._ref(t.id, numbers)} `{t.id}` depends on {self._ref(d, numbers)} `{d}`"
                 for t in tasks.values() for d in t.depends_on]
        lines += ["", "#### Dependency graph", *(edges or ["- no dependencies"]), "", "#### Verification requirements"]
        lines += [f"- `{t.id}`: {', '.join(f'`{g}`' for g in t.required_gates) or 'none'}" for t in tasks.values()]
        pr = feature.pull_request
        lines += ["", "#### Pull request candidate",
                  f"- `{pr.head}` → `{pr.base}` at `{pr.head_sha[:12]}`: "
                  + (f"opened ({self._text(pr.url)})" if pr.url else "rendered locally, **not opened**")
                  if pr else "- none yet (rendered only after integration)"]
        recent = feature.history[-5:]
        lines += ["", "#### Recent history", *([f"- `{h.at}` {h.from_state} → {h.to_state}: {self._text(h.reason)}"
                                                for h in recent] or ["- none"]), "", _FOOTER]
        return "\n".join(lines)

    def _history_comment(self, record: Feature | Task, entries: list[HistoryEntry], start: int) -> str:
        lines = [f"**Coordinator update** for `{record.id}` (state changes {start + 1}–{start + len(entries)})", ""]
        lines += [f"- `{h.at}` {h.from_state} → **{h.to_state}**: {self._text(h.reason)}" for h in entries]
        if isinstance(record, Task) and entries[-1].to_state == "verified" and record.state is TaskState.VERIFIED:
            lines += ["", "Verification evidence:", *self._evidence_lines(record)]
        return "\n".join(lines)

    # -- sync ------------------------------------------------------------------------

    def sync(self, feature_id: str, *, apply: bool = True, ingest: bool = True) -> SyncReport:
        """Converge the feature's Issues to the local state (and ingest validated commands).
        Holds only github-sync.lock. Dry run (apply=False) reads GitHub but writes nothing."""
        report = SyncReport(feature_id, apply)
        lock = self.store.locks.github_sync().acquire(timeout=self.gh.sync_lock_timeout_seconds)
        try:
            self._sync(feature_id, report, apply, ingest)
        except GitHubError as exc:
            report.deferred.append(sanitize(str(exc)))
        finally:
            lock.release()
        if apply:
            with self.store.transaction():
                self.store.append_event("github-sync", feature=feature_id, writes=len(report.writes),
                                        created=report.created, adopted=report.adopted, deferred=report.deferred,
                                        protected=report.protected, duplicates=report.duplicates)
        return report

    def _check_scope(self, feature: Feature) -> None:
        if getattr(self.client, "live", False) and self.gh.write_scope == "test-features-only" and not feature.test:
            raise SyncScopeError(f"feature {feature.id!r} is not a test feature; the GitHub write scope is "
                                 "'test-features-only' (Phase 4). Widening it needs operator approval")

    def _sync(self, feature_id: str, report: SyncReport, apply: bool, ingest: bool) -> None:
        feature = self.store.load_feature(feature_id)
        if apply:
            self._check_scope(feature)
        tasks = self.store.load_tasks(feature)
        if apply:
            self._ensure_labels(feature, tasks.values(), report)
        issues: dict[str, RemoteIssue] = {}
        for task in (None, *tasks.values()):
            issue = self._ensure_issue(feature, task, tasks, report, apply)
            if issue is not None:
                issues[task.id if task else feature.id] = issue
        feature = self.store.load_feature(feature_id)
        tasks = self.store.load_tasks(feature)
        if ingest and self._ingest(feature, issues, report, apply):
            feature = self.store.load_feature(feature_id)
            tasks = self.store.load_tasks(feature)
        for task in (None, *tasks.values()):
            record_id = task.id if task else feature.id
            if record_id in issues:
                issues[record_id] = self._reconcile(feature, task, tasks, issues[record_id], report, apply)
        numbers = {feature.id: feature.issue_number, **{t.id: t.issue_number for t in tasks.values()}}
        for task in (None, *tasks.values()):
            record_id = task.id if task else feature.id
            if record_id in issues:
                self._post_history(feature, task, issues[record_id], report, apply)
                self._post_outbox(record_id, issues[record_id], numbers, report, apply)
        report.issues = {rid: {"number": i.number, "url": i.url, "state": i.state, "labels": sorted(i.labels)}
                         for rid, i in issues.items()}

    def _ensure_labels(self, feature: Feature, tasks: Iterable[Task], report: SyncReport) -> None:
        wanted = {PARENT_LABEL, *STATE_LABELS, *(ROLE_LABELS[t.role] for t in tasks)}
        if feature.test:
            wanted.add(TEST_LABEL)
        for name in sorted(wanted - self._labels_ready):
            color, description = LABEL_SPECS[name]
            if self.client.ensure_label(name, color, description) == "created":
                report.writes.append(f"created label {name}")
            self._labels_ready.add(name)

    def search(self, expected: OwnershipMarker, label: str) -> list[RemoteIssue]:
        """Coordinator-created Issues carrying exactly *expected* (oldest first)."""
        viewer = self.viewer()
        found = []
        for issue in self.client.list_issues(labels=[label], state="all", creator=viewer):
            if issue.is_pull_request or is_foreign(issue.labels) or issue.author != viewer:
                continue
            if OwnershipMarker.parse(issue.body) == expected:
                found.append(issue)
        return sorted(found, key=lambda i: i.number)

    def _ensure_issue(self, feature: Feature, task: Task | None, tasks: Mapping[str, Task],  # pylint: disable=too-many-arguments
                      report: SyncReport, apply: bool) -> RemoteIssue | None:
        """The record's owned Issue: the mapped one (verified), an existing one found by its key
        (adopted), or a new one (created after a persisted pending-create record)."""
        record_id = task.id if task else feature.id
        expected = self.expected_marker(feature.id, task.id if task else None)
        link = self.load_link(record_id)
        number = (task or feature).issue_number or (link.issue_number if link else None)
        if number is not None:
            return self._mapped_issue(feature, task, link, number, report, apply)
        label = ROLE_LABELS[task.role] if task else PARENT_LABEL
        candidates = self.search(expected, label)
        if not candidates and link is not None and link.pending_create:
            # An earlier creation was interrupted: give GitHub's listing a moment before creating again.
            for _attempt in range(3):
                self.sleep(self.gh.create_settle_seconds)
                candidates = self.search(expected, label)
                if candidates:
                    break
        if candidates:
            return self._adopt(feature, task, candidates, report, apply)
        if not apply:
            report.writes.append(f"would create an Issue for {record_id}")
            return None
        op = f"c{secrets.token_hex(6)}"
        self._update_link(feature, task, lambda l: setattr(l, "pending_create", {"op": op, "started_at": utc_now()}))
        body = replace_block("", expected, self.render_block(feature, task, tasks))
        issue = self.client.create_issue(self.title(feature, task), body, sorted(self.desired_labels(feature, task)))
        self._fault(f"crash-after-create:{record_id}")
        self._record_mapping(feature, task, issue)
        report.created[record_id] = issue.number
        report.writes.append(f"created Issue #{issue.number} for {record_id}")
        self._fault(f"crash-after-link:{record_id}")
        return issue

    def _mapped_issue(self, feature: Feature, task: Task | None, link: IssueLink | None,  # pylint: disable=too-many-arguments
                      number: int, report: SyncReport, apply: bool) -> RemoteIssue | None:
        record_id = task.id if task else feature.id
        try:
            issue = self.client.get_issue(number)
        except GitHubNotFoundError:
            report.protected[record_id] = f"recorded Issue #{number} no longer exists; not recreated automatically"
            return None
        try:
            verify_ownership(issue, self.expected_marker(feature.id, task.id if task else None), viewer=self.viewer(),
                             known_number=number, known_id=link.issue_id if link else None)
            if apply and (link is None or link.issue_id is None or (task or feature).issue_number is None):
                self._record_mapping(feature, task, issue)
        except OwnershipError as exc:
            report.protected[record_id] = str(exc)
            return None
        return issue

    def _adopt(self, feature: Feature, task: Task | None, candidates: list[RemoteIssue], report: SyncReport,
               apply: bool) -> RemoteIssue | None:
        record_id = task.id if task else feature.id
        chosen, duplicates = candidates[0], candidates[1:]
        if duplicates:
            report.duplicates[record_id] = [d.number for d in duplicates]
        if not apply:
            report.writes.append(f"would adopt Issue #{chosen.number} for {record_id}")
            return chosen
        try:
            self._record_mapping(feature, task, chosen)
        except OwnershipError as exc:
            report.protected[record_id] = str(exc)
            return None
        report.adopted[record_id] = chosen.number
        report.writes.append(f"adopted existing Issue #{chosen.number} for {record_id} (no duplicate created)")
        return chosen

    def _reconcile(self, feature: Feature, task: Task | None, tasks: Mapping[str, Task],  # pylint: disable=too-many-arguments
                   issue: RemoteIssue, report: SyncReport, apply: bool) -> RemoteIssue:
        """Converge body, labels and open/closed state; record what was written (for drift detection)."""
        record: Feature | Task = task or feature
        link = self.load_link(record.id) or self._new_link(feature, task)
        block = self.render_block(feature, task, tasks).strip()
        issue = self._reconcile_body(record, self.expected_marker(feature.id, task.id if task else None), link,
                                     issue, block, report, apply)
        desired = self.desired_labels(feature, task)
        relabeled = self._reconcile_labels(link, issue, desired, report, apply)
        want = "open" if self.desired_open(feature, link) else "closed"
        issue = self._reconcile_state(record, issue, want, report, apply)
        if apply:
            if relabeled:
                issue = self.client.get_issue(issue.number)

            def written(l: IssueLink) -> None:
                l.block_hash, l.labels_written, l.state_written = _digest(block), sorted(desired), want
                l.last_synced_at = utc_now()
            self._update_link(feature, task, written)
        return issue

    def _reconcile_body(self, record: Feature | Task, expected: OwnershipMarker, link: IssueLink,  # pylint: disable=too-many-arguments
                        issue: RemoteIssue, block: str, report: SyncReport, apply: bool) -> RemoteIssue:
        current_block = extract_block(issue.body)
        if link.block_hash and (current_block is None or _digest(current_block) != link.block_hash):
            report.drift.append(f"#{issue.number}: managed block edited or removed on GitHub; restored")
        body = replace_block(issue.body, expected, block)
        if normalize_body(body) == normalize_body(issue.body):
            return issue
        report.writes.append(f"{'' if apply else 'would '}update the managed block of #{issue.number}")
        if not apply:
            return issue
        self._fault(f"fail-before-update:{record.id}")
        return self.client.update_issue(issue.number, body=body)

    def _reconcile_labels(self, link: IssueLink, issue: RemoteIssue, desired: set[str], report: SyncReport,
                          apply: bool) -> bool:
        """Add/remove `feature:*` labels one by one (never other labels). True if any changed."""
        current = set(issue.labels) & KNOWN_LABELS
        if link.labels_written and current != set(link.labels_written):
            report.drift.append(f"#{issue.number}: feature labels changed on GitHub ({sorted(current)} instead of "
                                f"{link.labels_written}); restored from the local state")
        to_add, to_remove = sorted(desired - set(issue.labels)), sorted(current - desired)
        verb = "" if apply else "would "
        if to_add:
            report.writes.append(f"{verb}add {to_add} to #{issue.number}")
            if apply:
                self.client.add_labels(issue.number, to_add)
        for label in to_remove:
            report.writes.append(f"{verb}remove {label} from #{issue.number}")
            if apply:
                self.client.remove_label(issue.number, label)
        return bool(to_add or to_remove)

    def _reconcile_state(self, record: Feature | Task, issue: RemoteIssue, want: str, report: SyncReport,
                         apply: bool) -> RemoteIssue:
        """Open unless done/cleaned up. A manual close is drift: closing never completes a task."""
        if issue.state == want:
            return issue
        if want == "open":
            report.drift.append(f"#{issue.number}: closed on GitHub while `{record.id}` is {record.state.value}; "
                                "reopened")
        report.writes.append(f"{'' if apply else 'would '}{'reopen' if want == 'open' else 'close'} #{issue.number}")
        if not apply:
            return issue
        issue = self.client.update_issue(issue.number, state=want,
                                         state_reason="completed" if want == "closed" else None)
        if want == "open":
            with self.store.transaction():
                enqueue_comment(self.store, record.id, (
                    f"Reopened by the feature coordinator: `{record.id}` is **{record.state.value}** in the local "
                    "coordinator state. Closing this Issue does not verify, unblock or complete anything; only "
                    "evidence on the feature branch does."))
        return issue

    # -- comments --------------------------------------------------------------------

    def _find_op(self, number: int, key: str, match: Callable[[str], bool]) -> RemoteComment | None:
        viewer = self.viewer()
        for comment in self.client.list_comments(number):
            parsed = parse_op(comment.body)
            if parsed and parsed[0] == self.coordinator_id and parsed[1] == key and comment.author == viewer \
                    and match(parsed[2]):
                return comment
        return None

    def _post_history(self, feature: Feature, task: Task | None, issue: RemoteIssue,
                      report: SyncReport, apply: bool) -> None:
        record: Feature | Task = task or feature
        link = self.load_link(record.id)
        if link is None:
            return
        cursor = link.history_cursor
        if apply and link.pending_history:
            start = link.pending_history["start"]
            found = self._find_op(issue.number, link.key, lambda op: op.startswith(f"h{start}-"))
            end = int(parse_op(found.body)[2].split("-")[1]) if found else start  # type: ignore[index]
            cursor = end if found else cursor

            def settle(l: IssueLink) -> None:
                l.history_cursor, l.pending_history = cursor, None
            self._update_link(feature, task, settle)
        history = record.history
        if cursor >= len(history):
            return
        start, end = cursor, len(history)
        if not apply:
            report.writes.append(f"would comment on #{issue.number}: {end - start} state change(s)")
            return
        self._update_link(feature, task, lambda l: setattr(l, "pending_history", {"start": start, "end": end}))
        body = (op_marker(self.coordinator_id, link.key, f"h{start}-{end}") + "\n"
                + self._history_comment(record, history[start:end], start))
        self.client.create_comment(issue.number, body)
        self._fault(f"crash-after-comment:{record.id}")

        def advance(l: IssueLink) -> None:
            l.history_cursor, l.pending_history = end, None
        self._update_link(feature, task, advance)
        report.writes.append(f"commented on #{issue.number}: {end - start} state change(s)")

    def _resolve(self, body: str, numbers: Mapping[str, int | None]) -> str:
        return _PLACEHOLDER_RE.sub(lambda m: self._ref(m.group(1), numbers), body)

    def _post_outbox(self, record_id: str, issue: RemoteIssue, numbers: Mapping[str, int | None],
                     report: SyncReport, apply: bool) -> None:
        link = self.load_link(record_id)
        if link is None:
            return
        for entry in self.outbox(record_id):
            if not apply:
                report.writes.append(f"would post queued comment {entry.id} on #{issue.number}")
                continue
            found = None
            if entry.attempts:
                found = self._find_op(issue.number, link.key, lambda op, e=entry: op == e.id)
            if found is None:
                self._set_outbox(entry.id, attempts=entry.attempts + 1)
                body = op_marker(self.coordinator_id, link.key, entry.id) + "\n" + self._resolve(entry.body, numbers)
                found = self.client.create_comment(issue.number, body)
                self._fault(f"crash-after-outbox:{record_id}")
                report.writes.append(f"posted queued comment {entry.id} on #{issue.number}")
            self._set_outbox(entry.id, status="done", sent_at=utc_now(), comment_id=found.id)

    def _set_outbox(self, op: str, **changes: Any) -> None:
        with self.store.transaction():
            entry = OutboxEntry.from_dict(self.store.load_record("outbox", op) or {})
            for name, value in changes.items():
                setattr(entry, name, value)
            self.store.save_record("outbox", entry)

    # -- GitHub → local: validated commands ---------------------------------------------

    def _permission(self, login: str) -> str:
        if login not in self._permissions:
            self._permissions[login] = self.client.author_permission(login)
        return self._permissions[login]

    def _ingest(self, feature: Feature, issues: Mapping[str, RemoteIssue], report: SyncReport, apply: bool) -> bool:
        changed = False
        for record_id, issue in issues.items():
            link = self.load_link(record_id)
            if link is None:
                continue
            seen = set(link.ingested)
            for comment in sorted(self.client.list_comments(issue.number), key=lambda c: c.id):
                if comment.id <= link.comment_cursor or comment.id in seen:
                    continue
                changed |= self._handle_comment(feature, record_id, comment, report, apply)
        return changed

    def _authorize(self, comment: RemoteComment) -> str | None:
        """None if the author may issue commands, else the reason it is ignored."""
        if comment.author not in self.gh.command_authors:
            return f"{comment.author} is not a configured command author"
        permission = self._permission(comment.author)
        if PERMISSION_RANK.get(permission, -1) < PERMISSION_RANK[self.gh.command_min_permission]:
            return f"{comment.author} has repository permission {permission!r}"
        return None

    def _handle_comment(self, feature: Feature, record_id: str, comment: RemoteComment,  # pylint: disable=too-many-arguments
                        report: SyncReport, apply: bool) -> bool:
        """Process one new comment. Returns True if a command changed local state."""
        parsed_op = parse_op(comment.body)
        own = parsed_op is not None and parsed_op[0] == self.coordinator_id and comment.author == self.viewer()
        command = None if own else parse_command(comment.body)
        if command is None:
            if apply:
                self._mark_ingested(feature, record_id, comment)
            return False
        verb, target, reason = command
        entry: dict[str, Any] = {"issue_record": record_id, "comment_id": comment.id, "author": comment.author,
                                 "command": f"{verb} {target}".strip()}
        refusal = self._authorize(comment)
        if refusal is not None:
            entry["result"] = f"ignored: {refusal}"
            report.commands.append(entry)
            if apply:
                self._mark_ingested(feature, record_id, comment, event=entry)
            return False
        result, accepted = self._evaluate(feature, record_id, verb, target, comment)
        if not apply:
            entry["result"] = f"would be {result}"
            report.commands.append(entry)
            return False
        with self.store.transaction():
            if accepted:
                try:
                    if verb == "hold":
                        held = hold_task(self.store, target, reason=reason or "no reason given", actor=comment.author)

                        def remember(l: IssueLink, at: str = held.history[-1].at) -> None:
                            l.hold_comment_id, l.hold_at = comment.id, at
                        self._update_link(feature, held, remember)
                    else:
                        release_task(self.store, target, actor=comment.author)
                    result = f"accepted: `{target}` is now {self.store.load_task(target).state.value}"
                except (IllegalTransitionError, ClaimError) as exc:
                    accepted, result = False, f"rejected: {sanitize(str(exc), 200)}"
            entry["result"] = result
            self._mark_ingested(feature, record_id, comment, event=entry)
            enqueue_comment(self.store, record_id, (
                f"Command from `{comment.author}` (comment {comment.id}): `/feature {verb} {target}` was "
                f"**{result.split(':', 1)[0]}**. {self._text(result.split(':', 1)[-1].strip(), 240)}"))
        report.commands.append(entry)
        return accepted

    def _evaluate(self, feature: Feature, record_id: str, verb: str, target: str,
                  comment: RemoteComment) -> tuple[str, bool]:
        if verb == "invalid":
            return "rejected: unrecognized command (use `/feature hold <task-id> <reason>` or "\
                   "`/feature release <task-id>`)", False
        if target not in feature.task_ids:
            return f"rejected: `{target}` is not a task of feature `{feature.id}`", False
        if record_id not in (feature.id, target):
            return "rejected: a command on a task Issue may only target that task", False
        task = self.store.load_task(target)
        if verb == "release":
            if task.state is not TaskState.NEEDS_HUMAN:
                return (f"rejected: `{target}` is {task.state.value}, not held for a human; dependency blocks are "
                        "released only by verified evidence on the feature branch"), False
            held_at = last_entered(task.history, TaskState.NEEDS_HUMAN.value)
            link = self.load_link(target)
            if link is not None and link.hold_comment_id is not None and link.hold_at == held_at:
                # The hold came from a GitHub comment: comment IDs are monotonic, so no clock is compared.
                stale = comment.id <= link.hold_comment_id
            else:
                # A local hold (feature.py hold): the release must be newer than it.
                stale = bool(held_at) and _stamp(comment.created_at) <= _stamp(held_at)  # type: ignore[arg-type]
            if stale:
                return "rejected: the comment predates the current hold (stale command)", False
        return "accepted", True

    def _mark_ingested(self, feature: Feature, record_id: str, comment: RemoteComment,
                       event: dict[str, Any] | None = None) -> None:
        task = None if record_id == feature.id else self.store.load_task(record_id)

        def mark(l: IssueLink) -> None:
            l.comment_cursor = max(l.comment_cursor, comment.id)
            l.ingested = [*l.ingested, comment.id][-MAX_INGESTED:]
        with self.store.transaction():
            self._update_link(feature, task, mark)
            if event is not None:
                self.store.append_event("github-command", **event)

    # -- cleanup, inspection, discovery ----------------------------------------------------

    def cleanup_test_feature(self, feature_id: str, summary: str, *, apply: bool) -> SyncReport:
        """Final verification comment + close for every Issue of a TEST feature (parent last).
        Repeatable: the final comment has a fixed operation ID and closed Issues are skipped."""
        report = SyncReport(feature_id, apply)
        feature = self.store.load_feature(feature_id)
        if not feature.test:
            raise SyncScopeError(f"feature {feature_id!r} is not a test feature; cleanup refuses it")
        tasks = self.store.load_tasks(feature)
        lock = self.store.locks.github_sync().acquire(timeout=self.gh.sync_lock_timeout_seconds)
        try:
            for task in (*tasks.values(), None):
                self._cleanup_one(feature, task, summary, report, apply)
        except GitHubError as exc:
            report.deferred.append(sanitize(str(exc)))
        finally:
            lock.release()
        return report

    def _cleanup_one(self, feature: Feature, task: Task | None, summary: str, report: SyncReport,
                     apply: bool) -> None:
        record: Feature | Task = task or feature
        link = self.load_link(record.id)
        if link is None or link.issue_number is None:
            report.writes.append(f"{record.id}: no Issue to clean up")
            return
        issue = self.client.get_issue(link.issue_number)
        try:
            verify_ownership(issue, self.expected_marker(feature.id, task.id if task else None), viewer=self.viewer(),
                             known_number=link.issue_number, known_id=link.issue_id)
            if TEST_LABEL not in issue.labels:
                raise OwnershipError(f"#{issue.number} has no {TEST_LABEL} label")
        except OwnershipError as exc:
            report.protected[record.id] = str(exc)
            return
        if not apply:
            report.writes.append(f"would post the final verification comment on #{issue.number} and close it")
            return
        self._update_link(feature, task, lambda l: setattr(l, "closed_by_cleanup", True))
        if self._find_op(issue.number, link.key, lambda op: op == "final") is None:
            body = (op_marker(self.coordinator_id, link.key, "final") + "\n**Phase 4 live test: final verification**"
                    f"\n\nFeature `{feature.id}`, record `{record.id}` (local state **{record.state.value}**).\n\n"
                    + escape_markdown_comment(summary))
            self.client.create_comment(issue.number, body)
            report.writes.append(f"posted the final verification comment on #{issue.number}")
        if issue.state == "open":
            issue = self.client.update_issue(issue.number, state="closed", state_reason="completed")
            report.writes.append(f"closed #{issue.number}")
        report.issues[record.id] = {"number": issue.number, "url": issue.url, "state": issue.state,
                                    "labels": sorted(issue.labels)}

    def inspect(self, number: int) -> dict[str, Any]:
        """Ownership verdict for one Issue number (read-only; content is sanitized for display)."""
        issue = self.client.get_issue(number)
        marker = OwnershipMarker.parse(issue.body)
        link = next((l for l in self.links() if l.issue_number == number), None)
        verdict = "not owned: no local record maps to this Issue"
        if link is not None:
            expected = self.expected_marker(link.feature_id, link.id if link.kind == "task" else None)
            try:
                verify_ownership(issue, expected, viewer=self.viewer(), known_number=link.issue_number,
                                 known_id=link.issue_id)
                verdict = f"owned: {link.kind} `{link.id}`"
            except OwnershipError as exc:
                verdict = f"not owned: {exc}"
        return {"number": issue.number, "title": sanitize(issue.title, 120), "state": issue.state,
                "labels": [sanitize(l, 60) for l in issue.labels], "author": issue.author, "url": issue.url,
                "is_pull_request": issue.is_pull_request, "marker": marker.render() if marker else None,
                "verdict": verdict, "foreign": is_foreign(issue.labels)}

    def find_owned(self, feature_id: str | None = None) -> list[tuple[RemoteIssue, OwnershipMarker]]:
        """Issues created by the coordinator account that carry THIS coordinator's marker."""
        viewer = self.viewer()
        seen: dict[int, tuple[RemoteIssue, OwnershipMarker]] = {}
        for label in (PARENT_LABEL, *ROLE_LABELS.values()):
            for issue in self.client.list_issues(labels=[label], state="all", creator=viewer):
                marker = OwnershipMarker.parse(issue.body)
                if (marker is None or marker.coordinator != self.coordinator_id or issue.is_pull_request
                        or issue.author != viewer or is_foreign(issue.labels)):
                    continue
                if feature_id is None or marker.feature == feature_id:
                    seen[issue.number] = (issue, marker)
        return [seen[n] for n in sorted(seen)]

    def reconcile(self, feature_ids: Iterable[str], *, apply: bool) -> dict[str, Any]:
        """After a restart: sync every listed feature, then compare GitHub with the local
        mapping — duplicates and orphans (owned Issues with no local record) are reported,
        never modified."""
        reports = {fid: self.sync(fid, apply=apply) for fid in feature_ids}
        mapped = {link.issue_number for link in self.links() if link.issue_number}
        known = {link.key for link in self.links()}
        orphans, by_key = [], {}
        for issue, marker in self.find_owned():
            by_key.setdefault(marker.key, []).append(issue.number)
            if issue.number not in mapped and marker.key not in known:
                orphans.append({"number": issue.number, "key": marker.key, "state": issue.state})
        duplicates = {key: numbers for key, numbers in by_key.items() if len(numbers) > 1}
        return {"features": {fid: r.to_dict() for fid, r in reports.items()}, "orphans": orphans,
                "duplicates": duplicates,
                "ok": all(r.ok for r in reports.values()) and not duplicates}


_FOOTER = ("<sub>This block is maintained by the feature coordinator and is overwritten on every sync. The local "
           "coordinator state is authoritative: editing this block, changing labels or closing the Issue does not "
           "change it. Operators listed in the coordinator configuration may comment `/feature hold <task-id> "
           "<reason>` or `/feature release <task-id>`; nothing else in an Issue is treated as an instruction.</sub>")
