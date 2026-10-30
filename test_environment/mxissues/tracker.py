"""The tracker-agnostic issue model and the IssueTracker interface every backend
(trackers/gitlab.py, trackers/github.py) implements.

Everything above this layer — issues.py, sync.py, test_environment/mxfix/strategy/*.py — only
ever sees `Issue`/`Comment` and calls `IssueTracker` methods, never python-gitlab objects
or raw GitHub API payloads. Selecting a backend is testenv.toml's [issues] tracker (see
its comments), resolved by trackers.create().

Ownership rule (implementation_plan.md §10), so manual issues are never clobbered —
unchanged from the GitLab-only version and enforced by callers, not backends:
  - This tool CREATES issues only with the `mx-auto` label.
  - It rewrites descriptions, auto-reopens, or auto-closes only issues carrying `mx-auto`.
  - It transitions status on ANY issue when named explicitly (`issues.py start 42`),
    manual ones included — a human said so, that's enough.
  - `sync` never touches the body of an issue it doesn't own.

Status is deliberately NOT modelled as a label here: it's one of STATUSES, and how a
backend stores it is its own business — GitLab uses exclusive `status::*` labels,
GitHub a single-select field on a GitHub Project (see each backend's docstring).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal

STATUSES = ("open", "in-progress", "done", "needs-human")

OWNERSHIP_LABEL = "mx-auto"
SOURCE_LABELS = ("source::detector", "source::e2e", "source::pytest", "source::pylint", "source::manual")
SEVERITY_LABELS = ("severity::high", "severity::medium", "severity::low")

# Labels every backend creates in `init` — status is not among them (see module docstring).
METADATA_LABELS = SOURCE_LABELS + SEVERITY_LABELS + (OWNERSHIP_LABEL,)

# Label colors (hex, no leading #) — cosmetic, but consistent beats random.
LABEL_COLORS: dict[str, str] = {
    "status::open": "428BCA",
    "status::in-progress": "F0AD4E",
    "status::done": "5CB85C",
    "status::needs-human": "D9534F",
    "source::detector": "8E44AD",
    "source::e2e": "16A085",
    "source::pytest": "2980B9",
    "source::pylint": "7F8C8D",
    "source::manual": "34495E",
    "severity::high": "D9534F",
    "severity::medium": "F0AD4E",
    "severity::low": "5BC0DE",
    OWNERSHIP_LABEL: "000000",
}


class TrackerError(Exception):
    """Missing/invalid tracker configuration, or the tracker is unreachable/unauthorized.
    Always carries a human-actionable message — callers print it and stop.
    """


class IssueNotFoundError(TrackerError):
    """No issue with the requested number in the configured project/repository."""


class TransitionError(Exception):
    """Raised when a status transition's read-back assertion fails."""


@dataclass(frozen=True)
class Issue:
    number: int  # GitLab iid / GitHub issue number — what humans type on the CLI
    title: str
    description: str
    labels: tuple[str, ...]
    state: Literal["open", "closed"]
    status: str | None  # one of STATUSES, or None if the issue has none (or an ambiguous one)
    web_url: str
    raw: Any = field(default=None, compare=False, repr=False)  # backend-native object, backend use only

    @property
    def owned(self) -> bool:
        """True if this tool created the issue (carries the mx-auto label). Only owned
        issues get their description rewritten or get auto-reopened/auto-closed by `sync`.
        """
        return OWNERSHIP_LABEL in self.labels

    def _single_scoped(self, prefix: str) -> str | None:
        found = [label.removeprefix(prefix) for label in self.labels if label.startswith(prefix)]
        return found[0] if len(found) == 1 else None

    @property
    def source(self) -> str | None:
        """Bare source name (e.g. "pytest") from the single source::* label, or None if it
        has none/multiple. Used by strategy/common.py's review_diff() — a pytest/e2e issue
        "fixed" by a test-only diff is the check-weakening shape implementation_plan.md
        risk #15 warns about.
        """
        return self._single_scoped("source::")

    @property
    def severity(self) -> str | None:
        return self._single_scoped("severity::")


@dataclass(frozen=True)
class Comment:
    body: str
    created_at: str  # ISO 8601 — only ever compared against other comments from the same tracker
    system: bool = False  # tracker-generated (GitLab system notes), not a human or AI comment


@dataclass(frozen=True)
class TransitionResult:
    number: int
    from_status: str | None
    to_status: str
    closed: bool
    reopened: bool


DoctorLevel = Literal["ok", "warn", "fail", "info"]


@dataclass(frozen=True)
class DoctorCheck:
    level: DoctorLevel
    message: str
    details: tuple[str, ...] = ()
    fixed_by_init: bool = False  # `issues.py init` would resolve this


def check_status(status: str) -> None:
    if status not in STATUSES:
        raise ValueError(f"Unknown status: {status!r} (expected one of {STATUSES})")


class IssueTracker(ABC):
    """One configured project/repository on one issue tracker.

    Lifecycle: trackers.create() builds it from config (no network), connect() checks
    credentials and reachability, then every other method may be called. Write methods
    take an issue *number* and re-fetch as needed rather than trusting a possibly-stale
    Issue the caller holds.
    """

    name: str  # config key: "gitlab" | "github"
    display_name: str  # "GitLab" | "GitHub" — for messages

    # -- setup / diagnostics ---------------------------------------------------

    @abstractmethod
    def describe(self) -> list[tuple[str, str]]:
        """(key, value) pairs identifying where issues go — printed by `doctor` before
        connecting, so a typo'd project is visible even when the connection fails.
        """

    def config_warnings(self) -> list[str]:
        """Warnings about the configuration itself, checkable without connecting."""
        return []

    @abstractmethod
    def connect(self) -> None:
        """Validate credentials and reachability. Raises TrackerError."""

    @abstractmethod
    def doctor(self) -> list[DoctorCheck]:
        """Backend-specific health checks, run after connect()."""

    @abstractmethod
    def init(self) -> tuple[list[str], list[str]]:
        """Idempotently create whatever the tracker needs (labels, status field...).
        Returns (created, problems) — problems are things init can't fix on its own.
        """

    # -- reads -----------------------------------------------------------------

    @abstractmethod
    def get(self, number: int) -> Issue:
        """Raises IssueNotFoundError."""

    @abstractmethod
    def list_issues(
            self, *, status: str | None = None, labels: tuple[str, ...] = (),
            owned_only: bool = True, state: Literal["open", "closed", "all"] = "all",
    ) -> list[Issue]:
        """Issues matching ALL of the given filters."""

    @abstractmethod
    def find_by_fingerprint(self, fp: str) -> Issue | None:
        """The issue whose ```mx-meta block carries *fp* (any state, owned or not), or None.
        The tracker API is the source of truth — see sync.py and cache.py.
        """

    @abstractmethod
    def comments(self, number: int) -> list[Comment]:
        """Every comment on the issue, oldest first."""

    # -- writes ----------------------------------------------------------------

    @abstractmethod
    def create(self, *, title: str, description: str, labels: list[str], status: str = "open") -> Issue:
        """File a new issue with *labels* and *status* already set."""

    @abstractmethod
    def update_description(self, number: int, description: str) -> None: ...

    @abstractmethod
    def add_comment(self, number: int, body: str) -> None: ...

    @abstractmethod
    def set_status(self, number: int, target: str) -> TransitionResult:
        """Set *number*'s status to exactly *target*, closing (done) or reopening (anything
        else, on a closed issue) as needed, then re-fetch and assert the result — raising
        TransitionError if the tracker doesn't show exactly that afterward.
        """

    # -- cross-references for commits and PRs (test_environment/mxfix/git_target.py) ----

    @abstractmethod
    def issue_url(self, number: int) -> str: ...

    @abstractmethod
    def closing_reference(self, number: int) -> str:
        """What follows `Closes ` in a fix commit's message so the tracker auto-closes the
        issue once the commit reaches its default branch — and nothing else ever does.
        """

    def link_line(self, number: int) -> str:
        """A PR-body back-link that deliberately avoids any closing keyword."""
        return f"{self.display_name} issue: {self.issue_url(number)}"
