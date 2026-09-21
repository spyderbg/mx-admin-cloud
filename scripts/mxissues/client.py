"""python-gitlab connection + the mx-auto ownership guard.

Ownership rule (implementation_plan.md §10), so manual issues are never clobbered:
  - This tool CREATES issues only with the `mx-auto` label.
  - It rewrites descriptions, auto-reopens, or auto-closes only issues carrying `mx-auto`.
  - It transitions state on ANY issue when named explicitly (`issues.py start 42`),
    manual ones included — a human said so, that's enough.
  - `sync` never touches the body of an issue it doesn't own.
"""

from __future__ import annotations

import gitlab
from gitlab.v4.objects import Project, ProjectIssue

from .config import GitLabConfig

OWNERSHIP_LABEL = "mx-auto"

STATUS_LABELS = ("status::open", "status::in-progress", "status::done", "status::needs-human")
SOURCE_LABELS = ("source::detector", "source::e2e", "source::pytest", "source::pylint", "source::manual")
SEVERITY_LABELS = ("severity::high", "severity::medium", "severity::low")

ALL_LABELS = STATUS_LABELS + SOURCE_LABELS + SEVERITY_LABELS + (OWNERSHIP_LABEL,)

# GitLab label colors (hex, no leading #) — cosmetic, but consistent beats random.
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


def connect(config: GitLabConfig) -> gitlab.Gitlab:
    gl = gitlab.Gitlab(config.host, private_token=config.token, timeout=30)
    gl.auth()  # raises GitlabAuthenticationError on a bad/expired token
    return gl


def get_project(gl: gitlab.Gitlab, config: GitLabConfig) -> Project:
    return gl.projects.get(config.project)


def is_owned(issue: ProjectIssue) -> bool:
    """True if this tool created *issue* (carries the mx-auto label).

    Only owned issues get their description rewritten or get auto-reopened/auto-closed
    by `sync`. State transitions named explicitly by iid (`start`/`done`/... ) apply to
    any issue regardless — see the module docstring.
    """
    return OWNERSHIP_LABEL in (issue.labels or [])


def status_label_of(issue: ProjectIssue) -> str | None:
    """Return the single status::* label on *issue*, or None if it has none/multiple.

    More than one status label existing at all would mean a previous transition wasn't
    atomic — callers should treat that as a data problem, not silently pick one.
    """
    found = [label for label in (issue.labels or []) if label.startswith("status::")]
    return found[0] if len(found) == 1 else None
