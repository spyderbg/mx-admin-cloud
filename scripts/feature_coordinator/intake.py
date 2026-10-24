"""W4.3.1: GitHub Issue intake — an approved GitHub Issue becomes a Worker task (no YAML file).

Flow:

  1. A person, or an external tool, opens an Issue. Nothing happens: it may be edited freely, it
     is not added to the project board, and nothing is written to it.
  2. An operator reviews it and comments, as the first line,

         /worker approve <role> <spec-sha256>

     where <spec-sha256> is printed by `feature.py github intake show N` for the Issue's current
     title and body. The approval is accepted only from a `github.command_authors` login with at
     least `github.command_min_permission` on the repository (the `/feature` command rules,
     issue_sync.py), and only while the digest still matches the Issue: it binds the exact text
     the operator reviewed.
  3. A poll (the Worker service every `github.intake.poll_interval_seconds`, or
     `feature.py github intake poll --apply`) records the approved title and body in
     intake/issue-<N>.json BEFORE anything else (write-ahead), then plans feature `issue-<N>`
     with ONE task of that role (workflow.plan: the existing records, state machine and Issue and
     board synchronization). No Issue is created for it: the approved Issue is the task's Issue,
     and the board sync adds exactly that Issue to the configured project (Backlog). One
     acknowledgement comment is posted on it.
  4. When no other feature is active on the checkout, the poll activates the oldest planned
     intake feature (branch from the base; the task becomes Ready). The existing W4 service runs
     it; the board shows its states. Nothing on the board is input.
  5. The approval stays valid only while the Issue is open and unchanged. It is re-read (fresh,
     with no coordinator lock held) by every poll, by the service right before a launch and while
     its agent runs, at the start of every run, right after a run released its claim (a result
     accepted meanwhile is withdrawn) and before integration; while the claim is held, the
     acceptance is refused locally once a change is known (`issue_problem`, workflow.py). A
     different title or body, a closed, deleted, transferred or relabeled Issue holds the task for
     an operator (needs-human), stops a running agent through the existing graceful cancellation
     (completed checkpoints stay) and posts one notice: fail closed. The Issue is never reopened or
     edited. The task itself always runs the recorded text, never the Issue's current one.

Duplicates: one intake record per Issue number, created inside a state transaction; a second
approval, a repeated poll or a restarted coordinator finds it and changes nothing. Polls are
serialized by github-intake.lock. Discovery is a bounded poll of the open Issues: an Issue is read
again only when its `updated_at` changed (intake-poll flag), at most `max_issues_per_poll` per poll.

This module holds the record, the approval grammar, the digest and the identity check, which the
Issue and board synchronizers use; the poll itself is intake_poll.py.

Not eligible (never read for commands, never written): pull requests, Issues of the bug workflow
(`mx-auto`, `source::*`, `severity::*`), Issues that carry a coordinator marker, closed Issues.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

from .errors import GitHubNotFoundError, OwnershipError, StateDirError
from .github_api import sanitize
from .issues import RemoteIssue, is_foreign, normalize_body
from .model import utc_now

INTAKE_SCHEMA = 1
COMMAND_PREFIX = "/worker"
MAX_COMMAND_COMMENT = 2000
_APPROVE_RE = re.compile(r"/worker[ \t]+approve[ \t]+([a-z][a-z-]{0,39})[ \t]+([0-9a-f]{64})")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_MARKER_TEXT = "<!-- feature-coordinator:"


def feature_id_for(number: int) -> str:
    """The feature (and intake record) ID of an approved Issue."""
    return f"issue-{int(number)}"


def spec_digest(title: str, body: str) -> str:
    """The SHA-256 of an Issue's task specification: its trimmed title, a blank line, and its body
    with line endings and trailing whitespace normalized (GitHub may return CRLF)."""
    return hashlib.sha256(f"{title.strip()}\n\n{normalize_body(body)}".encode("utf-8")).hexdigest()


def parse_worker_command(body: str) -> tuple[str, str, str] | None:
    """("approve", role, digest) for a `/worker approve` comment; ("invalid", line, "") for any
    other `/worker` line; None when the comment is not a `/worker` command (first non-empty line)."""
    if len(body) > MAX_COMMAND_COMMENT:
        return None
    first = next((line.strip() for line in body.splitlines() if line.strip()), "")
    if first != COMMAND_PREFIX and not first.startswith(COMMAND_PREFIX + " ") \
            and not first.startswith(COMMAND_PREFIX + "\t"):
        return None
    match = _APPROVE_RE.fullmatch(first)
    if match is None:
        return ("invalid", sanitize(first, 120), "")
    return ("approve", match.group(1), match.group(2))


def has_coordinator_marker(body: str) -> bool:
    """True if *body* contains any coordinator marker (owned Issues use the `/feature` path)."""
    return _MARKER_TEXT in (body or "")


def spec_problem(issue: RemoteIssue, max_characters: int) -> str | None:
    """Why the Issue cannot be a task specification (None when it can)."""
    title, body = issue.title.strip(), normalize_body(issue.body)
    if not title:
        return "the Issue has no title"
    if _CONTROL_RE.search(title) or "\n" in title or "\t" in title:
        return "the Issue title contains control characters"
    if not body:
        return "the Issue body (the task specification) is empty"
    if _CONTROL_RE.search(body):
        return "the Issue body contains control characters"
    if len(body) > max_characters:
        return f"the Issue body has {len(body)} characters; at most {max_characters} are accepted"
    return None


def not_eligible(issue: RemoteIssue) -> str | None:
    """Why an Issue is never an intake candidate (None when it may be one)."""
    if issue.is_pull_request:
        return "a pull request"
    if issue.state != "open":
        return "closed"
    if is_foreign(issue.labels):
        return "an Issue of the independent bug workflow"
    if has_coordinator_marker(issue.body):
        return "a coordinator-owned Issue (use `/feature` commands there)"
    return None


# ---------------------------------------------------------------------------
# The intake record (state store kind "intake", one per approved Issue)
# ---------------------------------------------------------------------------

_FIELDS = frozenset({"schema_version", "id", "issue_number", "issue_id", "node_id", "url", "issue_author", "title",
                     "body", "digest", "role", "gates", "task_id", "approved_by", "approval_comment_id",
                     "approval_comment_url", "approved_at", "recorded_at", "status", "spec_changed",
                     "activation_error", "revision"})
STATUSES = ("approved", "planned", "active")


@dataclass
class IntakeRecord:  # pylint: disable=too-many-instance-attributes
    """The exact approved specification of one Issue and what the intake made of it."""

    id: str                       # = the feature ID (issue-<N>)
    issue_number: int
    issue_id: int                 # GitHub database ID: exact identity
    node_id: str
    url: str
    issue_author: str
    title: str                    # the approved title (trimmed)
    body: str                     # the approved body (normalized)
    digest: str                   # spec_digest(title, body), as given in the approval
    role: str
    gates: list[str]
    task_id: str
    approved_by: str
    approval_comment_id: int
    approval_comment_url: str
    approved_at: str              # the approval comment's creation time (GitHub)
    recorded_at: str = field(default_factory=utc_now)
    status: str = "approved"      # approved (recorded) → planned (feature + task exist) → active
    spec_changed: dict[str, Any] | None = None  # {digest, reason, detected_at} while the approval does not
                                                # hold: text changed, Issue closed, deleted or relabeled
    activation_error: str = ""
    revision: int = 0

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        return {**asdict(self), "schema_version": INTAKE_SCHEMA}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "IntakeRecord":
        """Strict: another schema or unknown fields are refused (never dropped)."""
        if data.get("schema_version") != INTAKE_SCHEMA:
            raise StateDirError(f"intake record {data.get('id')!r}: unsupported schema {data.get('schema_version')!r}")
        unknown = sorted(set(data) - _FIELDS)
        if unknown:
            raise StateDirError(f"intake record {data.get('id')!r} has unknown fields {unknown}")
        record = cls(**{k: v for k, v in data.items() if k != "schema_version"})
        if record.status not in STATUSES:
            raise StateDirError(f"intake record {record.id!r}: unknown status {record.status!r}")
        return record


def load_intake(store: Any, feature_id: str) -> IntakeRecord | None:
    """The intake record of a feature (None for every feature not created by the intake)."""
    raw = store.load_record("intake", feature_id)
    return IntakeRecord.from_dict(raw) if raw else None


def verify_intake_issue(issue: RemoteIssue, record: IntakeRecord, *, record_id: str | None = None,
                        known_id: int | None = None) -> None:
    """Raise OwnershipError unless *issue* is exactly the approved Issue (number, database ID and
    node ID) and is still an ordinary Issue outside the bug workflow. *record_id* (the local record
    asking): only the intake task has this Issue — the feature has none. *known_id*: the database ID
    recorded when the task was mapped."""
    where = f"#{issue.number}"
    if record_id is not None and record_id != record.task_id:
        raise OwnershipError(f"{where}: `{record_id}` has no Issue; feature {record.id!r} came from an approved "
                             f"Issue, which belongs to its task {record.task_id!r} only")
    if known_id is not None and issue.id != known_id:
        raise OwnershipError(f"{where}: database ID {issue.id} differs from the recorded {known_id}")
    if issue.is_pull_request:
        raise OwnershipError(f"{where} is a pull request, not the approved Issue")
    if is_foreign(issue.labels):
        raise OwnershipError(f"{where} now carries bug-workflow labels; it is never modified")
    if issue.number != record.issue_number or issue.id != record.issue_id:
        raise OwnershipError(f"{where} (ID {issue.id}) is not the approved Issue #{record.issue_number} "
                             f"(ID {record.issue_id})")
    if record.node_id and issue.node_id and issue.node_id != record.node_id:
        raise OwnershipError(f"{where}: node ID differs from the approved Issue's")


def issue_problem(client: Any, record: IntakeRecord) -> tuple[str, str]:
    """(current digest, reason) from a fresh read of the approved Issue; reason is "" while the
    approval holds: the same Issue, open, with the approved title and body. Callers must hold no
    coordinator, project or role lock (a tracker request). Read failures other than a deleted Issue
    propagate (GitHubError): the caller must not proceed unchecked."""
    try:
        issue = client.get_issue(record.issue_number)
    except GitHubNotFoundError:
        return "", f"the approved Issue #{record.issue_number} no longer exists"
    try:
        verify_intake_issue(issue, record)
    except OwnershipError as exc:
        return "", str(exc)
    current = spec_digest(issue.title, issue.body)
    if current != record.digest:
        return current, (f"the Issue's title or body changed after approval (approved sha256 {record.digest[:16]}, "
                         f"now {current[:16]})")
    if issue.state != "open":
        return current, f"the approved Issue #{record.issue_number} was closed on GitHub"
    return current, ""


def known_problem(store: Any, task: Any) -> str | None:
    """Why an intake task's approval is known to be withdrawn (the intake record, no request), or
    None — also for every task that did not come from the intake."""
    record = load_intake(store, task.feature_id)
    if record is None or record.task_id != task.id or not record.spec_changed:
        return None
    return f"approved Issue #{record.issue_number}: {record.spec_changed.get('reason', 'changed after approval')}"
