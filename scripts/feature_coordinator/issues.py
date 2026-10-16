"""Feature-task issue coordination (v5 §3.4, §7.2, §7.4) — interface plus an in-memory mock.

Issues and labels are the human-visible record of feature work, never the lock service.
The feature workflow uses only the `feature:*` label namespace and refuses to touch any
issue carrying the independent bug workflow's labels (`mx-auto`, `source::*`,
`severity::*`). Duplicate requests reuse the existing issue (a hidden key marker in the
body). At most one PR exists per feature branch.

Phase 2 ships only MockIssueTracker; the live GitHub adapter (gh CLI / REST, no Projects
v2) is Phase 4 and needs operator approval before any write.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Protocol

from .errors import PullRequestPolicyError, TrackerError

FEATURE_PREFIX = "feature:"
PARENT_LABEL = "feature:parent"
BLOCKED_LABEL = "feature:blocked"
REVIEW_LABEL = "feature:review"
ROLE_LABELS = {
    "backend": "feature:backend",
    "cli": "feature:cli",
    "frontend": "feature:frontend",
    "automation": "feature:automation",
    "coordination": "feature:coordination",
}
FOREIGN_LABELS = ("mx-auto",)
FOREIGN_LABEL_PREFIXES = ("source::", "severity::")
_KEY_MARKER = "<!-- feature-coordinator key={key} -->"
_KEY_RE = re.compile(r"<!-- feature-coordinator key=([A-Za-z0-9._:/-]+) -->")


def is_foreign(labels: Iterable[str]) -> bool:
    """True if *labels* mark an issue of the independent bug workflow."""
    return any(lbl in FOREIGN_LABELS or lbl.startswith(FOREIGN_LABEL_PREFIXES) for lbl in labels)


@dataclass
class Issue:  # pylint: disable=too-many-instance-attributes
    """A tracker issue as the coordinator sees it."""

    number: int
    title: str
    body: str
    labels: list[str]
    state: str = "open"
    depends_on: list[int] = field(default_factory=list)
    comments: list[str] = field(default_factory=list)

    @property
    def key(self) -> str | None:
        """The coordinator's deduplication key, if the issue carries one."""
        match = _KEY_RE.search(self.body)
        return match.group(1) if match else None


@dataclass
class PullRequest:
    """A tracker pull request."""

    number: int
    head: str
    base: str
    title: str
    body: str
    state: str = "open"


class IssueTracker(Protocol):
    """What the feature coordinator needs from an issue tracker."""

    def ensure_issue(self, key: str, title: str, body: str, labels: Iterable[str]) -> Issue:
        """Return the open issue for *key*, creating it once."""

    def set_labels(self, number: int, add: Iterable[str] = (), remove: Iterable[str] = ()) -> Issue:
        """Add/remove `feature:*` labels."""

    def comment(self, number: int, body: str) -> None:
        """Add a comment."""

    def link_dependency(self, number: int, depends_on: int) -> None:
        """Record that issue *number* depends on issue *depends_on*."""

    def close(self, number: int) -> None:
        """Close an issue."""

    def get(self, number: int) -> Issue:
        """Fetch an issue."""

    def create_pull_request(self, head: str, base: str, title: str, body: str) -> PullRequest:
        """Open the single PR of a feature branch."""

    def pull_requests(self, head: str | None = None) -> list[PullRequest]:
        """PRs, optionally filtered by head branch."""


class MockIssueTracker:
    """In-memory (optionally JSON-persisted) tracker with the same guards as the live one."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self.issues: dict[int, Issue] = {}
        self.prs: dict[int, PullRequest] = {}
        self.calls: list[str] = []
        self._next = 1
        if path is not None and path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            self.issues = {int(k): Issue(**v) for k, v in data["issues"].items()}
            self.prs = {int(k): PullRequest(**v) for k, v in data["prs"].items()}
            self._next = int(data["next"])

    def _save(self) -> None:
        if self.path is not None:
            data = {"next": self._next, "issues": {n: asdict(i) for n, i in self.issues.items()},
                    "prs": {n: asdict(p) for n, p in self.prs.items()}}
            self.path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")

    def _number(self) -> int:
        number = self._next
        self._next += 1
        return number

    def _own(self, number: int) -> Issue:
        issue = self.get(number)
        if is_foreign(issue.labels):
            raise TrackerError(f"issue #{number} belongs to the independent bug workflow; not touched")
        return issue

    @staticmethod
    def _check_labels(labels: Iterable[str]) -> list[str]:
        labels = list(dict.fromkeys(labels))
        bad = [lbl for lbl in labels if not lbl.startswith(FEATURE_PREFIX)]
        if bad:
            raise TrackerError(f"labels {bad} are outside the feature:* namespace")
        return labels

    def add_foreign_issue(self, title: str, labels: Iterable[str]) -> Issue:
        """Test helper: an issue owned by the independent bug workflow."""
        issue = Issue(self._number(), title, "", list(labels))
        self.issues[issue.number] = issue
        self._save()
        return issue

    def get(self, number: int) -> Issue:
        """The issue numbered *number* (feature-owned or not)."""
        try:
            return self.issues[number]
        except KeyError as exc:
            raise TrackerError(f"unknown issue #{number}") from exc

    def find_by_key(self, key: str) -> Issue | None:
        """The open feature issue carrying *key*."""
        for issue in self.issues.values():
            if issue.state == "open" and issue.key == key and not is_foreign(issue.labels):
                return issue
        return None

    def ensure_issue(self, key: str, title: str, body: str, labels: Iterable[str]) -> Issue:
        """Create the issue for *key*, or return the open one that already carries it (idempotent)."""
        self.calls.append(f"ensure_issue:{key}")
        labels = self._check_labels(labels)
        existing = self.find_by_key(key)
        if existing is not None:
            return existing
        issue = Issue(self._number(), title, f"{body.rstrip()}\n\n{_KEY_MARKER.format(key=key)}", labels)
        self.issues[issue.number] = issue
        self._save()
        return issue

    def set_labels(self, number: int, add: Iterable[str] = (), remove: Iterable[str] = ()) -> Issue:
        """Add and remove feature-namespace labels on a feature-owned issue."""
        self.calls.append(f"set_labels:{number}")
        issue = self._own(number)
        add, remove = self._check_labels(add), self._check_labels(remove)
        issue.labels = [lbl for lbl in dict.fromkeys([*issue.labels, *add]) if lbl not in remove]
        self._save()
        return issue

    def comment(self, number: int, body: str) -> None:
        """Append a comment to a feature-owned issue."""
        self.calls.append(f"comment:{number}")
        self._own(number).comments.append(body)
        self._save()

    def link_dependency(self, number: int, depends_on: int) -> None:
        """Record that issue *number* depends on issue *depends_on* (both feature-owned)."""
        self.calls.append(f"link:{number}->{depends_on}")
        issue = self._own(number)
        self._own(depends_on)
        if depends_on not in issue.depends_on:
            issue.depends_on.append(depends_on)
        self._save()

    def close(self, number: int) -> None:
        """Close a feature-owned issue."""
        self.calls.append(f"close:{number}")
        self._own(number).state = "closed"
        self._save()

    def create_pull_request(self, head: str, base: str, title: str, body: str) -> PullRequest:
        """Open the single PR for a feature branch; a second open PR for the same head is refused."""
        self.calls.append(f"create_pull_request:{head}")
        if not head.startswith("feature/"):
            raise PullRequestPolicyError(f"only feature branches get a PR, not {head!r}")
        if any(pr.head == head and pr.state == "open" for pr in self.prs.values()):
            raise PullRequestPolicyError(f"{head!r} already has its single PR")
        pr = PullRequest(self._number(), head, base, title, body)
        self.prs[pr.number] = pr
        self._save()
        return pr

    def pull_requests(self, head: str | None = None) -> list[PullRequest]:
        """All recorded PRs, optionally only those for *head*."""
        return [pr for pr in self.prs.values() if head is None or pr.head == head]
