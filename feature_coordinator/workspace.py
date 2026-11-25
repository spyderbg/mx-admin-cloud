"""Local workspace verification for the Worker (W2).

A Worker operates on ONE explicitly selected checkout (`python -m feature_coordinator --repo PATH`). Before an
agent runs there, and when a Worker starts, the checkout must still be what the feature was
activated on:

  * repository identity — the root commit(s), and the `origin` URL when one was recorded at
    activation (`feature.workspace`); a different repository is refused;
  * the checkout's top level is exactly the selected path;
  * the expected branch — the feature branch is checked out;
  * the expected base revision — the feature's recorded base commit is an ancestor of HEAD;
  * no unfinished merge, rebase, cherry-pick, revert or bisect.

Clean starting state is required where the workflow already requires it (activation and
integration). Nothing here resets, stashes, cleans or switches anything: a mismatch is reported
and the operation is refused. Conflicting coordinator executions are excluded by the existing
claims and role locks, and by the Worker's own per-feature lock (worker.py).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from .errors import GitConfigExecutionError, GitOperationError
from .gitsafe import run_git
from .model import Feature

GIT_OPERATION_MARKERS = ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "BISECT_LOG", "rebase-merge", "rebase-apply")


class WorkspaceError(GitOperationError):
    """The selected checkout is not the feature's workspace, or not in a usable state."""


def _git(repo: Path, *args: str, check: bool = True) -> str:
    return run_git(repo, *args, check=check).stdout.strip()


def git_operation_in_progress(repo: Path) -> list[str]:
    """Markers of an unfinished merge, rebase, cherry-pick, revert or bisect."""
    git_dir = Path(_git(repo, "rev-parse", "--absolute-git-dir"))
    return [m for m in GIT_OPERATION_MARKERS if (git_dir / m).exists()]


def identity(repo: Path) -> dict[str, Any]:
    """What identifies the repository: its root commit(s) and its `origin` URL (if any)."""
    roots = sorted(_git(repo, "rev-list", "--max-parents=0", "HEAD").split())
    return {"root_commits": roots, "origin": _git(repo, "remote", "get-url", "origin", check=False)}


def workspace_problems(repo: Path, feature: Feature) -> list[str]:
    """Everything that makes *repo* unusable for *feature* right now (empty: OK). A checkout whose own Git
    configuration names a program (or cannot be listed) is unusable: no Git command runs in it (gitsafe)."""
    try:
        return _problems(repo, feature)
    except GitConfigExecutionError as exc:
        return [str(exc)]


def _problems(repo: Path, feature: Feature) -> list[str]:
    try:
        toplevel = Path(_git(repo, "rev-parse", "--show-toplevel")).resolve()
    except (OSError, subprocess.CalledProcessError):
        return [f"{repo} is not a Git checkout"]
    problems = []
    if toplevel != Path(repo).resolve():
        problems.append(f"{repo} is not the top level of its checkout ({toplevel})")
    recorded = feature.workspace or {}
    current = identity(repo)
    if recorded.get("root_commits") and recorded["root_commits"] != current["root_commits"]:
        problems.append("repository identity mismatch: different root commit(s) than at activation")
    if recorded.get("origin") and recorded["origin"] != current["origin"]:
        problems.append(f"repository identity mismatch: origin is {current['origin'] or '(none)'!r}, "
                        f"activation recorded {recorded['origin']!r}")
    branch = _git(repo, "symbolic-ref", "-q", "--short", "HEAD", check=False)
    if branch != feature.branch:
        problems.append(f"the checkout is on {branch or 'a detached HEAD'!r}, not the feature branch "
                        f"{feature.branch!r} (it is never switched automatically)")
    if feature.base_sha and run_git(repo, "merge-base", "--is-ancestor", feature.base_sha, "HEAD").returncode != 0:
        problems.append(f"HEAD does not contain the feature's base revision {feature.base_sha[:12]}")
    ongoing = git_operation_in_progress(repo)
    if ongoing:
        problems.append(f"an unfinished Git operation is in progress ({', '.join(ongoing)})")
    return problems


def require_workspace(repo: Path, feature: Feature) -> None:
    """Raise WorkspaceError listing every problem; never repairs anything."""
    problems = workspace_problems(repo, feature)
    if problems:
        raise WorkspaceError(f"workspace check failed for {feature.id!r}: " + "; ".join(problems))
