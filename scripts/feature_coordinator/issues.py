"""Feature-task issue coordination (v5 §3.4, §7.2, §7.4): tracker types, ownership markers and
the in-memory/JSON mock tracker.

Issues and labels are the human-visible record of feature work, never the lock service. The
feature workflow uses only the `feature:*` label namespace and refuses to touch any issue
carrying the independent bug workflow's labels (`mx-auto`, `source::*`, `severity::*`).

Ownership. Every coordinator-created Issue starts with one machine-readable marker:

    <!-- feature-coordinator:v1 coordinator=<id> feature=<feature> task=<task|-> purpose=<p> key=<key> -->

carrying the schema version, the coordinator identity, the feature and task IDs, the Issue
purpose and its idempotency key. An Issue counts as owned only if ALL of these hold
(`verify_ownership`): it is an Issue (not a PR); it has no bug-workflow label; it carries
exactly one marker and that marker equals the expected one; it was created by the
coordinator's account; and, once mapped, its number and database ID equal the recorded
ones. Being open, or having a `feature:*` label, never makes an Issue ours.

Coordinator comments carry an operation marker (`feature-coordinator:op`) so a repeated
request after a crash can find the comment it already posted instead of posting it twice.

The live adapter is github_api.GitHubClient; MockIssueTracker implements the same
TrackerClient protocol for tests, the demo and the local JSON tracker.
"""

from __future__ import annotations

import dataclasses
import fcntl
import json
import re
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Protocol

from .errors import GitHubNotFoundError, GitHubValidationError, OwnershipError, PullRequestPolicyError, TrackerError

FEATURE_PREFIX = "feature:"
PARENT_LABEL = "feature:parent"
TEST_LABEL = "feature:test"
BLOCKED_LABEL = "feature:blocked"
IN_PROGRESS_LABEL = "feature:in-progress"
REVIEW_LABEL = "feature:review"
VERIFIED_LABEL = "feature:verified"
ROLE_LABELS = {
    "backend": "feature:backend",
    "cli": "feature:cli",
    "frontend": "feature:frontend",
    "automation": "feature:automation",
    "coordination": "feature:coordination",
}
STATE_LABELS = (IN_PROGRESS_LABEL, BLOCKED_LABEL, REVIEW_LABEL, VERIFIED_LABEL)
# (color, description) — created only when missing; existing labels are never edited.
LABEL_SPECS: dict[str, tuple[str, str]] = {
    PARENT_LABEL: ("5319e7", "Feature coordinator: parent feature Issue"),
    TEST_LABEL: ("c5def5", "Feature coordinator: test Issue (safe to ignore)"),
    BLOCKED_LABEL: ("b60205", "Feature coordinator: blocked, needs a human, or failed"),
    IN_PROGRESS_LABEL: ("fbca04", "Feature coordinator: in progress"),
    REVIEW_LABEL: ("0e8a16", "Feature coordinator: single feature PR candidate in review"),
    VERIFIED_LABEL: ("1d76db", "Feature coordinator: verified with evidence on the feature branch"),
    "feature:backend": ("d4c5f9", "Feature coordinator: Backend component task"),
    "feature:cli": ("d4c5f9", "Feature coordinator: CLI component task"),
    "feature:frontend": ("d4c5f9", "Feature coordinator: Frontend component task"),
    "feature:automation": ("d4c5f9", "Feature coordinator: Automation component task"),
    "feature:coordination": ("d4c5f9", "Feature coordinator: Coordination task"),
}
KNOWN_LABELS = frozenset(LABEL_SPECS)
FOREIGN_LABELS = ("mx-auto",)
FOREIGN_LABEL_PREFIXES = ("source::", "severity::")

MARKER_SCHEMA = 1
PURPOSES = ("feature-parent", "component-task", "legacy")
_VALUE = r"[A-Za-z0-9._:/@-]{1,160}"
_MARKER_RE = re.compile(
    r"<!-- feature-coordinator:v(\d+) coordinator=(" + _VALUE + r") feature=(" + _VALUE + r") task=(" + _VALUE
    + r") purpose=(" + _VALUE + r") key=(" + _VALUE + r") -->")
_ANY_MARKER_RE = re.compile(r"<!-- feature-coordinator:v\d+ ")
_OP_RE = re.compile(r"<!-- feature-coordinator:op v1 coordinator=(" + _VALUE + r") key=(" + _VALUE
                    + r") op=([A-Za-z0-9._-]{1,80}) -->")
BLOCK_BEGIN = "<!-- feature-coordinator:begin -->"
BLOCK_END = "<!-- feature-coordinator:end -->"


def is_foreign(labels: Iterable[str]) -> bool:
    """True if *labels* mark an issue of the independent bug workflow."""
    return any(lbl in FOREIGN_LABELS or lbl.startswith(FOREIGN_LABEL_PREFIXES) for lbl in labels)


def check_feature_labels(labels: Iterable[str]) -> list[str]:
    """The labels, de-duplicated; TrackerError if any is outside the `feature:*` namespace."""
    labels = list(dict.fromkeys(labels))
    bad = [lbl for lbl in labels if not lbl.startswith(FEATURE_PREFIX)]
    if bad:
        raise TrackerError(f"labels {bad} are outside the feature:* namespace")
    return labels


# ---------------------------------------------------------------------------
# Tracker data (validated copies of untrusted tracker responses)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RemoteIssue:  # pylint: disable=too-many-instance-attributes
    """An Issue as returned by the tracker. Every field was type-checked on parsing."""

    number: int
    id: int
    title: str
    body: str
    state: str
    labels: tuple[str, ...]
    author: str
    url: str = ""
    is_pull_request: bool = False
    updated_at: str = ""
    comments: int = 0
    state_reason: str | None = None
    node_id: str = ""            # GraphQL global ID (adding the Issue to a project needs it)


@dataclass(frozen=True)
class RemoteComment:
    """An Issue comment as returned by the tracker."""

    id: int
    author: str
    body: str
    created_at: str
    url: str = ""


@dataclass
class PullRequest:  # pylint: disable=too-many-instance-attributes
    """A tracker pull request. W4.3.3: the identity and merge fields every adoption and completion
    decision is checked against (publish.py); "" / False / 0 when the tracker did not report them."""

    number: int
    head: str                    # the head branch name
    base: str                    # the base branch name
    title: str
    body: str
    state: str = "open"          # open | closed
    head_sha: str = ""
    head_repo: str = ""          # owner/name of the head and base repositories
    base_repo: str = ""
    draft: bool = False
    merged: bool = False
    merged_at: str = ""
    merged_by: str = ""
    merge_commit_sha: str = ""
    author: str = ""
    url: str = ""
    node_id: str = ""
    id: int = 0


class TrackerClient(Protocol):
    """What the issue synchronizer needs from an issue tracker (GitHub or the mock)."""

    live: bool

    def viewer_login(self) -> str:
        """The account the coordinator acts as."""

    def get_issue(self, number: int) -> RemoteIssue:
        """One Issue by number (GitHubNotFoundError if absent)."""

    def list_issues(self, *, labels: Iterable[str] = (), state: str = "all",
                    creator: str | None = None) -> list[RemoteIssue]:
        """Issues (and PRs, flagged) carrying all *labels*."""

    def create_issue(self, title: str, body: str, labels: Iterable[str]) -> RemoteIssue:
        """Create an Issue (NOT idempotent: search first)."""

    def update_issue(self, number: int, *, body: str | None = None, state: str | None = None,
                     state_reason: str | None = None) -> RemoteIssue:
        """Set the body and/or the open/closed state."""

    def add_labels(self, number: int, labels: Iterable[str]) -> tuple[str, ...]:
        """Add labels (idempotent)."""

    def remove_label(self, number: int, label: str) -> None:
        """Remove one label (idempotent: absent is fine)."""

    def list_comments(self, number: int) -> list[RemoteComment]:
        """All comments of an Issue, oldest first."""

    def create_comment(self, number: int, body: str) -> RemoteComment:
        """Add a comment (NOT idempotent: carry an operation marker)."""

    def ensure_label(self, name: str, color: str, description: str) -> str:
        """'exists' or 'created'; never edits an existing label."""

    def author_permission(self, login: str) -> str:
        """The repository permission of *login* (admin, maintain, write, triage, read or none)."""

    def create_pull_request(self, head: str, base: str, title: str, body: str) -> PullRequest:
        """Open the single PR of a feature branch (W4.3.3: the real adapter takes `draft` and opens only Draft
        PRs it was authorized for)."""

    def list_pull_requests(self, head: str | None = None, state: str = "all") -> list[PullRequest]:
        """PRs, optionally only those whose head branch is *head*."""

    def get_pull_request(self, number: int) -> PullRequest:
        """W4.3.3: one PR by number, with its merge state (GitHubNotFoundError if absent)."""


# ---------------------------------------------------------------------------
# Ownership markers
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OwnershipMarker:
    """The machine-readable ownership marker of a coordinator Issue."""

    coordinator: str
    feature: str
    task: str
    purpose: str
    key: str
    schema: int = MARKER_SCHEMA

    def __post_init__(self) -> None:
        for name in ("coordinator", "feature", "task", "purpose", "key"):
            if not re.fullmatch(_VALUE, getattr(self, name)):
                raise ValueError(f"ownership marker field {name}={getattr(self, name)!r} has unsupported characters")
        if self.purpose not in PURPOSES:
            raise ValueError(f"unknown Issue purpose {self.purpose!r}")

    def render(self) -> str:
        """The single-line HTML comment (invisible in rendered Markdown)."""
        return (f"<!-- feature-coordinator:v{self.schema} coordinator={self.coordinator} feature={self.feature} "
                f"task={self.task} purpose={self.purpose} key={self.key} -->")

    @classmethod
    def parse(cls, body: str) -> "OwnershipMarker | None":
        """The marker in *body*; None if absent, malformed or present more than once (ambiguous)."""
        if len(_ANY_MARKER_RE.findall(body or "")) != 1:
            return None
        match = _MARKER_RE.search(body)
        if match is None:
            return None
        try:
            return cls(coordinator=match.group(2), feature=match.group(3), task=match.group(4),
                       purpose=match.group(5), key=match.group(6), schema=int(match.group(1)))
        except ValueError:
            return None


def feature_marker(coordinator: str, feature_id: str) -> OwnershipMarker:
    """The expected marker of a feature's parent Issue."""
    return OwnershipMarker(coordinator, feature_id, "-", "feature-parent", f"{feature_id}/parent")


def task_marker(coordinator: str, feature_id: str, task_id: str) -> OwnershipMarker:
    """The expected marker of a component-task Issue."""
    return OwnershipMarker(coordinator, feature_id, task_id, "component-task", f"{feature_id}/{task_id}")


def op_marker(coordinator: str, key: str, op: str) -> str:
    """The marker of a coordinator comment (one per persisted operation ID)."""
    return f"<!-- feature-coordinator:op v1 coordinator={coordinator} key={key} op={op} -->"


def parse_op(body: str) -> tuple[str, str, str] | None:
    """(coordinator, key, op) of a coordinator comment, or None."""
    match = _OP_RE.search(body or "")
    return (match.group(1), match.group(2), match.group(3)) if match else None


def verify_ownership(issue: RemoteIssue, expected: OwnershipMarker, *, viewer: str,
                     known_number: int | None = None, known_id: int | None = None) -> None:
    """Raise OwnershipError unless *issue* is provably this coordinator's Issue for *expected*."""
    where = f"#{issue.number}"
    if issue.is_pull_request:
        raise OwnershipError(f"{where} is a pull request, not a coordinator Issue")
    if is_foreign(issue.labels):
        raise OwnershipError(f"{where} carries bug-workflow labels; never modified by the feature coordinator")
    marker = OwnershipMarker.parse(issue.body)
    if marker is None:
        raise OwnershipError(f"{where} has no single valid ownership marker")
    if marker != expected:
        diff = [name for name in ("schema", "coordinator", "feature", "task", "purpose", "key")
                if getattr(marker, name) != getattr(expected, name)]
        raise OwnershipError(f"{where}: ownership marker does not match ({', '.join(diff)} differ)")
    if issue.author != viewer:
        raise OwnershipError(f"{where} was created by {issue.author!r}, not the coordinator account {viewer!r}")
    if known_number is not None and issue.number != known_number:
        raise OwnershipError(f"{where} is not the recorded Issue #{known_number}")
    if known_id is not None and issue.id != known_id:
        raise OwnershipError(f"{where}: database ID {issue.id} differs from the recorded {known_id}")


def replace_block(body: str, marker: OwnershipMarker, block: str) -> str:
    """*body* with its managed block replaced by *block*. Text outside the block is kept;
    the ownership marker stays the first line. A damaged block (unmatched markers) is removed
    line by line and rebuilt; human text around it survives."""
    lines = (body or "").splitlines()
    kept: list[str] = []
    inside = False
    for line in lines:
        stripped = line.strip()
        if stripped == marker.render():
            continue
        if stripped == BLOCK_BEGIN:
            inside = True
            continue
        if stripped == BLOCK_END:
            inside = False
            continue
        if not inside:
            kept.append(line)
    while kept and not kept[0].strip():
        kept.pop(0)
    rest = "\n".join(kept).rstrip()
    managed = f"{marker.render()}\n{BLOCK_BEGIN}\n{block.strip()}\n{BLOCK_END}"
    return f"{managed}\n\n{rest}\n" if rest else f"{managed}\n"


def extract_block(body: str) -> str | None:
    """The managed block's text (between the begin/end markers), or None if it is missing or damaged."""
    lines = normalize_body(body).split("\n")
    try:
        begin = [line.strip() for line in lines].index(BLOCK_BEGIN)
        end = [line.strip() for line in lines].index(BLOCK_END, begin + 1)
    except ValueError:
        return None
    return "\n".join(lines[begin + 1:end]).strip()


def normalize_body(body: str) -> str:
    """Line endings and trailing whitespace normalized (GitHub may return CRLF)."""
    return "\n".join(line.rstrip() for line in (body or "").replace("\r\n", "\n").split("\n")).strip()


def escape_markdown_comment(text: str) -> str:
    """Neutralize HTML comment delimiters in rendered text so it can never forge or break a marker."""
    return text.replace("<!--", "&lt;!--").replace("-->", "--&gt;")


# ---------------------------------------------------------------------------
# Legacy view (Phase 2 API kept for the mock's callers)
# ---------------------------------------------------------------------------

@dataclass
class Issue:  # pylint: disable=too-many-instance-attributes
    """A tracker issue as the Phase 2 mock API presents it."""

    number: int
    title: str
    body: str
    labels: list[str]
    state: str = "open"
    comments: list[str] = field(default_factory=list)

    @property
    def key(self) -> str | None:
        """The coordinator's deduplication key, if the issue carries a marker."""
        marker = OwnershipMarker.parse(self.body)
        return marker.key if marker else None


# ---------------------------------------------------------------------------
# Mock tracker
# ---------------------------------------------------------------------------

class MockIssueTracker:  # pylint: disable=too-many-public-methods,too-many-instance-attributes
    """In-memory (optionally JSON-persisted) tracker implementing TrackerClient.

    Mutations made through it are counted in `writes`. With a *path*, every operation reloads
    and saves the JSON file under an flock, so several processes can share it (the local
    tracker of `feature.py --tracker local`). `fail` holds injected failures: a list of
    (operation name, exception) pairs consumed in order. W4.3.3: `lost` holds failures raised
    AFTER the operation was applied (a response lost on the way back), and `ref_resolver` (branch →
    commit on the "remote", or None) gives a new PR its head commit, as GitHub reads it from the
    pushed branch.
    """

    live = False

    def __init__(self, path: Path | None = None, *, viewer: str = "feature-coordinator",
                 repository: str = "example/repo", permissions: dict[str, str] | None = None,
                 legacy_coordinator: str = "mx-admin-cloud.feature-coordinator") -> None:
        self.path = path
        self.viewer = viewer
        self.repository = repository
        self.permissions = dict(permissions or {viewer: "admin"})
        self.legacy_coordinator = legacy_coordinator
        self.issues: dict[int, dict[str, Any]] = {}
        self.prs: dict[int, PullRequest] = {}
        self.labels: dict[str, list[str]] = {}
        self.calls: list[str] = []
        self.writes = 0
        self.fail: list[tuple[str, BaseException]] = []
        self.lost: list[tuple[str, BaseException]] = []
        self.ref_resolver: Callable[[str], str | None] | None = None
        self.authorized: list[tuple[str, str]] = []
        self._next = 1
        self._next_comment = 1000
        self._clock = 0
        if path is not None and path.exists():
            self._load()

    # -- persistence ------------------------------------------------------------

    def _load(self) -> None:
        assert self.path is not None
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.issues = {int(k): v for k, v in data["issues"].items()}
        self.prs = {int(k): PullRequest(**v) for k, v in data["prs"].items()}
        self.labels = dict(data.get("labels", {}))
        self._next = int(data["next"])
        self._next_comment = int(data.get("next_comment", 1000))
        self._clock = int(data.get("clock", 0))
        self.writes = int(data.get("writes", 0))

    def _save(self) -> None:
        if self.path is None:
            return
        data = {"next": self._next, "next_comment": self._next_comment, "clock": self._clock, "writes": self.writes,
                "issues": {str(n): i for n, i in self.issues.items()},
                "prs": {str(n): asdict(p) for n, p in self.prs.items()}, "labels": self.labels}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)

    @contextmanager
    def _op(self, name: str, *, write: bool) -> Iterator[None]:
        self.calls.append(name)
        for index, (operation, exc) in enumerate(self.fail):
            if operation == name.split(":", 1)[0]:
                del self.fail[index]
                raise exc
        if self.path is None:
            yield
            if write:
                self.writes += 1
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path.with_suffix(".lock"), "a+", encoding="utf-8") as handle:
                fcntl.flock(handle, fcntl.LOCK_EX)
                if self.path.exists():
                    self._load()
                yield
                if write:
                    self.writes += 1
                    self._save()
        for index, (operation, exc) in enumerate(self.lost):  # applied, but the caller never hears of it
            if operation == name.split(":", 1)[0]:
                del self.lost[index]
                raise exc

    def _tick(self) -> str:
        self._clock += 1
        return f"2026-01-01T00:{self._clock // 60 % 60:02d}:{self._clock % 60:02d}Z"

    def _record(self, number: int) -> dict[str, Any]:
        try:
            return self.issues[number]
        except KeyError as exc:
            raise GitHubNotFoundError(f"unknown issue #{number}", status=404) from exc

    @staticmethod
    def _remote(rec: dict[str, Any]) -> RemoteIssue:
        return RemoteIssue(number=rec["number"], id=rec["id"], title=rec["title"], body=rec["body"],
                           state=rec["state"], labels=tuple(rec["labels"]), author=rec["author"],
                           url=f"mock://issues/{rec['number']}", is_pull_request=rec.get("is_pull_request", False),
                           updated_at=rec["updated_at"], comments=len(rec["comments"]),
                           state_reason=rec.get("state_reason"), node_id=f"I_mock{rec['id']}")

    def _guard(self, rec: dict[str, Any]) -> None:
        if is_foreign(rec["labels"]):
            raise TrackerError(f"issue #{rec['number']} belongs to the independent bug workflow; not touched")

    def _new_issue(self, title: str, body: str, labels: Iterable[str], author: str) -> dict[str, Any]:
        number = self._next
        self._next += 1
        rec = {"number": number, "id": 900000 + number, "title": title, "body": body, "state": "open",
               "state_reason": None, "labels": list(labels), "author": author, "comments": [],
               "updated_at": self._tick(), "is_pull_request": False}
        self.issues[number] = rec
        return rec

    # -- TrackerClient ------------------------------------------------------------

    def viewer_login(self) -> str:
        """The mock account."""
        return self.viewer

    def get_issue(self, number: int) -> RemoteIssue:
        """One issue."""
        with self._op(f"get_issue:{number}", write=False):
            return self._remote(self._record(number))

    def list_issues(self, *, labels: Iterable[str] = (), state: str = "all",
                    creator: str | None = None) -> list[RemoteIssue]:
        """Issues carrying every label in *labels*."""
        wanted = set(labels)
        with self._op("list_issues", write=False):
            return [self._remote(rec) for _n, rec in sorted(self.issues.items())
                    if wanted <= set(rec["labels"]) and state in ("all", rec["state"])
                    and (creator is None or rec["author"] == creator)]

    def create_issue(self, title: str, body: str, labels: Iterable[str]) -> RemoteIssue:
        """Create an issue authored by the viewer."""
        labels = check_feature_labels(labels)
        with self._op("create_issue", write=True):
            return self._remote(self._new_issue(title, body, labels, self.viewer))

    def update_issue(self, number: int, *, body: str | None = None, state: str | None = None,
                     state_reason: str | None = None) -> RemoteIssue:
        """Update the body and/or state."""
        with self._op(f"update_issue:{number}", write=True):
            rec = self._record(number)
            self._guard(rec)
            if body is not None:
                rec["body"] = body
            if state is not None:
                rec["state"] = state
                rec["state_reason"] = state_reason if state == "closed" else None
            rec["updated_at"] = self._tick()
            return self._remote(rec)

    def add_labels(self, number: int, labels: Iterable[str]) -> tuple[str, ...]:
        """Add feature labels."""
        labels = check_feature_labels(labels)
        with self._op(f"add_labels:{number}", write=True):
            rec = self._record(number)
            self._guard(rec)
            rec["labels"] = list(dict.fromkeys([*rec["labels"], *labels]))
            rec["updated_at"] = self._tick()
            return tuple(rec["labels"])

    def remove_label(self, number: int, label: str) -> None:
        """Remove a feature label (absent is fine)."""
        check_feature_labels([label])
        with self._op(f"remove_label:{number}", write=True):
            rec = self._record(number)
            self._guard(rec)
            if label in rec["labels"]:
                rec["labels"].remove(label)
                rec["updated_at"] = self._tick()

    def list_comments(self, number: int) -> list[RemoteComment]:
        """Comments, oldest first."""
        with self._op(f"list_comments:{number}", write=False):
            return [RemoteComment(**c) for c in self._record(number)["comments"]]

    def create_comment(self, number: int, body: str) -> RemoteComment:
        """Comment as the viewer."""
        return self.add_comment_as(number, body, self.viewer)

    def add_comment_as(self, number: int, body: str, author: str) -> RemoteComment:
        """Comment as *author* (tests: a human operator or a stranger)."""
        with self._op(f"create_comment:{number}", write=True):
            rec = self._record(number)
            self._guard(rec)
            self._next_comment += 1
            comment = {"id": self._next_comment, "author": author, "body": body, "created_at": self._tick(),
                       "url": f"mock://issues/{number}#comment-{self._next_comment}"}
            rec["comments"].append(comment)
            rec["updated_at"] = comment["created_at"]
            return RemoteComment(**comment)

    def ensure_label(self, name: str, color: str, description: str) -> str:
        """Create a feature label once."""
        check_feature_labels([name])
        with self._op(f"ensure_label:{name}", write=name not in self.labels):
            if name in self.labels:
                return "exists"
            self.labels[name] = [color, description]
            return "created"

    def author_permission(self, login: str) -> str:
        """Configured permission of *login* (default none)."""
        return self.permissions.get(login, "none")

    def create_pull_request(self, head: str, base: str, title: str, body: str, *,  # pylint: disable=too-many-arguments
                            draft: bool = False) -> PullRequest:
        """Open the single PR for a feature branch; a second open PR for the same head is refused."""
        with self._op(f"create_pull_request:{head}", write=True):
            if not head.startswith("feature/"):
                raise PullRequestPolicyError(f"only feature branches get a PR, not {head!r}")
            if any(pr.head == head and pr.state == "open" for pr in self.prs.values()):
                raise PullRequestPolicyError(f"{head!r} already has its single PR")
            head_sha = (self.ref_resolver(head) or "") if self.ref_resolver is not None else ""
            if self.ref_resolver is not None and not head_sha:
                raise GitHubValidationError(f"POST pulls: HTTP 422: no head branch {head!r}", status=422)
            return self._new_pull(head, base, title, body, author=self.viewer, head_sha=head_sha, draft=draft)

    def _new_pull(self, head: str, base: str, title: str, body: str, **fields: Any) -> PullRequest:  # pylint: disable=too-many-arguments
        number = self._next
        self._next += 1
        fields = {"head_repo": self.repository, "base_repo": self.repository, "url": f"mock://pull/{number}",
                  "node_id": f"PR_mock{number}", "id": 800000 + number, **fields}
        pr = PullRequest(number, head, base, title, body, **fields)
        self.prs[pr.number] = pr
        return dataclasses.replace(pr)

    def list_pull_requests(self, head: str | None = None, state: str = "all") -> list[PullRequest]:
        """Recorded PRs."""
        with self._op("list_pull_requests", write=False):
            return [dataclasses.replace(pr) for pr in self.prs.values()
                    if (head is None or pr.head == head) and state in ("all", pr.state)]

    def get_pull_request(self, number: int) -> PullRequest:
        """One PR (W4.3.3)."""
        with self._op(f"get_pull_request:{number}", write=False):
            try:
                return dataclasses.replace(self.prs[number])
            except KeyError as exc:
                raise GitHubNotFoundError(f"unknown pull request #{number}", status=404) from exc

    def authorize_pull_request(self, head: str, base: str) -> None:
        """Recorded only (the mock also serves the Phase 2 `open_pull_request` path, which never authorizes)."""
        self.authorized.append((head, base))

    def add_pull_request_as(self, author: str, head: str, base: str, *, head_sha: str, title: str = "PR",  # pylint: disable=too-many-arguments
                            body: str = "", **fields: Any) -> PullRequest:
        """Test helper: a PR a person opened (not through the coordinator)."""
        with self._op("add_pull_request_as", write=True):
            return self._new_pull(head, base, title, body, author=author, head_sha=head_sha, **fields)

    def update_pull_request(self, number: int, **changes: Any) -> PullRequest:
        """Test helper: what a person (or GitHub) does to a PR — mark it ready, merge, close, push to it."""
        with self._op(f"update_pull_request:{number}", write=True):
            pr = dataclasses.replace(self.prs[number], **changes)
            self.prs[number] = pr
            return dataclasses.replace(pr)

    def merge_pull_request(self, number: int, merge_sha: str, *, merged_by: str = "spyderbg") -> PullRequest:
        """Test helper: a person merged the PR on GitHub as *merge_sha*."""
        return self.update_pull_request(number, state="closed", merged=True, merge_commit_sha=merge_sha,
                                        merged_at=self._tick(), merged_by=merged_by)

    # -- test helpers and the Phase 2 API --------------------------------------------

    def add_foreign_issue(self, title: str, labels: Iterable[str], *, body: str = "",
                          author: str | None = None) -> Issue:
        """An issue NOT created by the coordinator (the bug workflow's, or a human's)."""
        with self._op("add_foreign_issue", write=True):
            rec = self._new_issue(title, body, list(labels), author or "someone-else")
            return self._legacy(rec)

    def tamper(self, number: int, **changes: Any) -> None:
        """Test helper: change an issue as a human would (labels, body, state, author)."""
        with self._op(f"tamper:{number}", write=True):
            rec = self._record(number)
            rec.update(changes)
            rec["updated_at"] = self._tick()

    @staticmethod
    def _legacy(rec: dict[str, Any]) -> Issue:
        return Issue(rec["number"], rec["title"], rec["body"], list(rec["labels"]), rec["state"],
                     [c["body"] for c in rec["comments"]])

    def get(self, number: int) -> Issue:
        """The issue numbered *number* (feature-owned or not), Phase 2 view."""
        with self._op(f"get:{number}", write=False):
            try:
                return self._legacy(self.issues[number])
            except KeyError as exc:
                raise TrackerError(f"unknown issue #{number}") from exc

    def find_by_key(self, key: str) -> Issue | None:
        """The open, coordinator-created issue whose marker carries *key*."""
        with self._op("find_by_key", write=False):
            for rec in self.issues.values():
                marker = OwnershipMarker.parse(rec["body"])
                if (rec["state"] == "open" and marker is not None and marker.key == key
                        and not is_foreign(rec["labels"]) and rec["author"] == self.viewer):
                    return self._legacy(rec)
        return None

    def ensure_issue(self, key: str, title: str, body: str, labels: Iterable[str]) -> Issue:
        """Phase 2 API: create the issue for *key* once, or return the open one carrying it."""
        labels = check_feature_labels(labels)
        existing = self.find_by_key(key)
        if existing is not None:
            return existing
        marker = OwnershipMarker(self.legacy_coordinator, "-", "-", "legacy", key)
        return self._legacy(self.issues[self.create_issue(title, f"{marker.render()}\n{body.rstrip()}\n",
                                                          labels).number])

    def set_labels(self, number: int, add: Iterable[str] = (), remove: Iterable[str] = ()) -> Issue:
        """Phase 2 API: add and remove feature labels."""
        add, remove = check_feature_labels(add), check_feature_labels(remove)
        if add:
            self.add_labels(number, add)
        for label in remove:
            self.remove_label(number, label)
        return self.get(number)

    def comment(self, number: int, body: str) -> None:
        """Phase 2 API: comment."""
        self.create_comment(number, body)

    def close(self, number: int) -> None:
        """Phase 2 API: close."""
        self.update_issue(number, state="closed", state_reason="completed")

    def pull_requests(self, head: str | None = None) -> list[PullRequest]:
        """Phase 2 API: PRs, optionally for *head*."""
        return self.list_pull_requests(head)
