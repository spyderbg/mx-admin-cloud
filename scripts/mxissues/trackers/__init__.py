"""Issue tracker backends. create() picks one from testenv.toml's [issues] tracker.

Backends import lazily, so python-gitlab is only needed when GitLab is actually selected.
Adding a tracker: implement mxissues.tracker.IssueTracker in a module here, add its name
to mxissues.config.TRACKERS, and a branch below.
"""

from __future__ import annotations

from ..config import IssuesConfig
from ..tracker import IssueTracker, TrackerError


def create(config: IssuesConfig) -> IssueTracker:
    """Build (but don't connect) the configured tracker. Raises TrackerError."""
    if config.tracker == "gitlab":
        from .gitlab import GitLabTracker  # noqa: PLC0415
        return GitLabTracker.from_config(config.gitlab)
    if config.tracker == "github":
        from .github import GitHubTracker  # noqa: PLC0415
        return GitHubTracker.from_config(config.github)
    raise TrackerError(f"Unknown issue tracker {config.tracker!r}")


def connect(config: IssuesConfig) -> IssueTracker:
    """create() + connect() — what everything except `issues.py doctor` wants."""
    tracker = create(config)
    tracker.connect()
    return tracker
