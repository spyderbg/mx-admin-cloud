"""Worktree management under .mxworktrees/ only.

`.kilo/worktrees/` is already in active use by another tool (confirmed: a real worktree
exists there right now) — this module never lists, prunes, or otherwise touches
anything outside `.mxworktrees/`, and deliberately never calls a bare `git worktree
prune` at all (implementation_plan.md risk #17): prune operates on the whole
repository's worktree metadata with no path-scoping option in git itself, so the only
fully safe operation is removing the one specific path this tool itself created.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from testenvlib.paths import REPO_ROOT

MXWORKTREES_DIR = REPO_ROOT / ".mxworktrees"


class WorktreeError(Exception):
    """Raised for any git-worktree operation failure, or an attempt to touch a path
    outside .mxworktrees/."""


def _run_git(args: list[str], *, cwd: Path = REPO_ROOT) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)


def repo_is_clean() -> tuple[bool, str]:
    """True if the MAIN working tree (not a worktree under .mxworktrees/) has no
    uncommitted changes — `run` refuses to start "heal"/"batch" otherwise
    (implementation_plan.md §18).
    """
    result = _run_git(["status", "--porcelain"])
    if result.returncode != 0:
        raise WorktreeError(f"git status failed: {result.stderr.strip()}")
    dirty = result.stdout.strip()
    return not dirty, dirty


def fetch_base_branch(base_branch: str) -> None:
    """Fetch origin/<base_branch> so new worktrees start from the latest, not a stale
    local copy — this tool's worktrees are always created FROM the remote ref, never
    from whatever the local branch of the same name happens to point at.
    """
    result = _run_git(["fetch", "origin", base_branch])
    if result.returncode != 0:
        raise WorktreeError(f"git fetch origin {base_branch!r} failed: {result.stderr.strip()}")


def create(name: str, branch: str, *, base_branch: str) -> Path:
    """Create .mxworktrees/<name> on new local branch *branch*, off origin/<base_branch>."""
    MXWORKTREES_DIR.mkdir(parents=True, exist_ok=True)
    path = MXWORKTREES_DIR / name
    if path.exists():
        raise WorktreeError(f"{path} already exists — remove() it before creating a fresh one for the same name.")

    result = _run_git(["worktree", "add", "-b", branch, str(path), f"origin/{base_branch}"])
    if result.returncode != 0:
        raise WorktreeError(f"git worktree add {path} failed: {result.stderr.strip()}")
    return path


def remove(path: Path, *, force: bool = False) -> None:
    """Remove one worktree this tool created. Refuses anything outside .mxworktrees/."""
    resolved = path.resolve()
    try:
        resolved.relative_to(MXWORKTREES_DIR.resolve())
    except ValueError as exc:
        raise WorktreeError(
            f"refusing to remove {resolved} — it is not under {MXWORKTREES_DIR}. "
            "This tool only ever removes worktrees it created itself.",
        ) from exc

    cmd = ["worktree", "remove", str(path)]
    if force:
        cmd.append("--force")
    result = _run_git(cmd)
    if result.returncode != 0:
        raise WorktreeError(f"git worktree remove {path} failed: {result.stderr.strip()}")


def list_managed() -> list[Path]:
    """Directories under .mxworktrees/ — never the full `git worktree list`, which would
    also include .kilo/worktrees/ and the main tree.
    """
    if not MXWORKTREES_DIR.is_dir():
        return []
    return sorted(p for p in MXWORKTREES_DIR.iterdir() if p.is_dir())
