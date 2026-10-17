"""The single Git mutation coordinator for the shared checkout (v5 §3.3).

Agents never run mutating Git commands. Every mutation goes through GitCoordinator,
serialized by git.lock; operations that change the working tree (creating/switching the
feature branch) additionally need project.lock EXCLUSIVE, i.e. no scoped writer active.

Role-scoped commits never touch the shared index while building the commit and never
read another role's files:

  1. under git.lock, verify the checkout is on the feature branch;
  2. validate every manifest path against the role's write scope (deny-by-default);
  3. refuse if the shared index already holds staged content (foreign changes);
  4. if HEAD moved since the request was prepared, continue only when the new commits
     are descendants and touch none of the manifest paths (disjoint parallel work);
  5. verify each file still has the SHA-256 the worker declared (stability);
  6. build the tree in a PRIVATE index (`GIT_INDEX_FILE`): read-tree HEAD, then add or
     remove exactly the manifest paths — nothing else from the working tree can enter;
  7. re-read the staged blobs and compare them with the declared hashes again;
  8. `commit-tree`, then `update-ref <branch> <new> <old>` (atomic compare-and-swap);
  9. refresh only those paths in the shared index so `git status` stays truthful.

Plumbing commands run no hooks. Push and PR creation are rendered as dry-run plans only
(Phases 2–3); live pushes and PRs need operator approval (Phase 4/5).
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Sequence

from .errors import (
    ForeignStagedContentError,
    GitOperationError,
    HeadMismatchError,
    NothingToCommitError,
    UnstableFileError,
    WritersActiveError,
)
from .locks import LockSet
from .paths import PathPolicy

_SCRUBBED_ENV = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_COMMON_DIR")


def file_digest(path: Path) -> str | None:
    """SHA-256 of a file's bytes, or None if it does not exist."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


@dataclass(frozen=True)
class CommitRequest:
    """A role-scoped commit: an explicit manifest with the content hash of each file
    (None = the file is deleted), the HEAD the worker last saw, and task identity."""

    feature_id: str
    task_id: str
    role: str
    branch: str
    expected_head: str
    files: Mapping[str, str | None]
    message: str
    grants: tuple[str, ...] = ()


@dataclass(frozen=True)
class CommitResult:
    """A landed commit."""

    sha: str
    parent: str
    paths: tuple[str, ...]
    moved_over: tuple[str, ...] = field(default_factory=tuple)


def branch_contains(repo: Path, branch: str, sha: str, *, env: Mapping[str, str] | None = None) -> bool:
    """True when commit *sha* is an ancestor of (or equal to) refs/heads/<branch> in *repo*."""
    if not re.fullmatch(r"[0-9a-f]{7,40}", sha):
        return False
    result = subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", sha, f"refs/heads/{branch}"],
                            capture_output=True, text=True, env=None if env is None else dict(env), check=False)
    return result.returncode == 0


class GitCoordinator:
    """Serialized Git mutations for one checkout."""

    def __init__(self, repo_root: Path, locks: LockSet, policy: PathPolicy, *, tmp_dir: Path,
                 lock_timeout: float = 30.0) -> None:
        self.root = Path(repo_root)
        self.locks = locks
        self.policy = policy
        self.tmp_dir = tmp_dir
        self.lock_timeout = lock_timeout

    # -- process helpers -----------------------------------------------------

    def _env(self, extra: Mapping[str, str] | None = None) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if k not in _SCRUBBED_ENV}
        env.update({"LC_ALL": "C", "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"})
        env.update(extra or {})
        return env

    def _git(self, *args: str, env: Mapping[str, str] | None = None, check: bool = True) -> str:
        result = subprocess.run(["git", "-C", str(self.root), *args], capture_output=True, text=True,
                                env=self._env(env), check=False)
        if check and result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise GitOperationError(f"git {' '.join(args[:2])} failed: {detail}")
        return result.stdout

    def _git_bytes(self, *args: str, env: Mapping[str, str] | None = None) -> bytes:
        result = subprocess.run(["git", "-C", str(self.root), *args], capture_output=True, env=self._env(env),
                                check=False)
        if result.returncode != 0:
            raise GitOperationError(f"git {' '.join(args[:2])} failed: {result.stderr.decode(errors='replace')}")
        return result.stdout

    # -- read-only queries (safe without locks) ------------------------------

    def current_branch(self) -> str | None:
        """The checked-out branch, or None when HEAD is detached."""
        out = self._git("symbolic-ref", "--quiet", "--short", "HEAD", check=False).strip()
        return out or None

    def rev_parse(self, ref: str) -> str:
        """Full SHA of *ref*."""
        return self._git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").strip()

    def contains(self, branch: str, sha: str) -> bool:
        """True when commit *sha* is on *branch* (read-only; no lock needed)."""
        return branch_contains(self.root, branch, sha, env=self._env())

    def branch_exists(self, branch: str) -> bool:
        """True if refs/heads/<branch> exists."""
        result = subprocess.run(["git", "-C", str(self.root), "show-ref", "--verify", "--quiet",
                                 f"refs/heads/{branch}"], env=self._env(), check=False)
        return result.returncode == 0

    def staged_paths(self) -> list[str]:
        """Paths with staged changes in the shared index."""
        out = self._git("diff", "--cached", "--name-only", "-z")
        return [p for p in out.split("\0") if p]

    def tracked_changes(self) -> list[str]:
        """Tracked files modified in the working tree or index (untracked files ignored)."""
        out = self._git("status", "--porcelain=v1", "-z", "--untracked-files=no")
        return [entry[3:] for entry in out.split("\0") if entry]

    def untracked_files(self) -> list[str]:
        """Untracked, not ignored files in the working tree."""
        out = self._git("ls-files", "--others", "--exclude-standard", "-z")
        return [p for p in out.split("\0") if p]

    def changed_paths(self, base: str, head: str) -> list[str]:
        """Paths changed between two commits."""
        out = self._git("diff", "--name-only", "-z", f"{base}..{head}")
        return [p for p in out.split("\0") if p]

    # -- working-tree-changing operations (need quiescence) ------------------

    def _quiescent(self):
        try:
            return self.locks.project(shared=False, busy_error=WritersActiveError).acquire()
        except WritersActiveError as exc:
            raise WritersActiveError("branch operations change the working tree and require every writer "
                                     "to be quiescent; scoped writers are active") from exc

    def create_feature_branch(self, branch: str, base_ref: str) -> str:
        """Create *branch* at *base_ref* and check it out. Returns the base SHA."""
        project = self._quiescent()
        try:
            with self.locks.git().acquire(timeout=self.lock_timeout):
                if self.branch_exists(branch):
                    raise GitOperationError(f"branch {branch!r} already exists; one branch per feature")
                if self.tracked_changes():
                    raise GitOperationError("the shared checkout has uncommitted tracked changes")
                base_sha = self.rev_parse(base_ref)
                self._git("branch", "--no-track", branch, base_sha)
                self._git("switch", "--quiet", branch)
                return base_sha
        finally:
            project.release()

    def switch_branch(self, branch: str) -> None:
        """Check out an existing branch — refused while any scoped writer is active."""
        project = self._quiescent()
        try:
            with self.locks.git().acquire(timeout=self.lock_timeout):
                if self.tracked_changes():
                    raise GitOperationError("the shared checkout has uncommitted tracked changes")
                self._git("switch", "--quiet", branch)
        finally:
            project.release()

    # -- role-scoped commits -------------------------------------------------

    def commit(self, request: CommitRequest, *, author: tuple[str, str] | None = None) -> CommitResult:
        """Land a role-scoped commit on the feature branch. See the module docstring."""
        with self.locks.git().acquire(timeout=self.lock_timeout):
            if self.current_branch() != request.branch:
                raise HeadMismatchError(f"checkout is on {self.current_branch()!r}, not {request.branch!r}")
            head = self.rev_parse(f"refs/heads/{request.branch}")
            normalized = self.policy.require(request.role, request.files, grants=request.grants)
            declared = self._declared_hashes(request)
            if self.staged_paths():
                raise ForeignStagedContentError(f"the shared index has staged content {self.staged_paths()}; "
                                                "refusing to commit around it")
            moved_over = self._check_head(request.expected_head, head, normalized)
            self._check_stable(declared)
            tree = self._build_tree(head, declared)
            if tree == self._git("rev-parse", f"{head}^{{tree}}").strip():
                raise NothingToCommitError(f"{list(declared)} already match {head[:12]}")
            self._check_staged_blobs(tree, declared)
            commit_env = {}
            if author:
                name, email = author
                commit_env = {"GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email,
                              "GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": email}
            message = (f"{request.message.strip()}\n\nFeature: {request.feature_id}\n"
                       f"Feature-Task: {request.task_id}\nFeature-Role: {request.role}\n")
            sha = self._commit_tree(tree, head, message, commit_env)
            result = subprocess.run(
                ["git", "-C", str(self.root), "update-ref", "-m", f"feature-coordinator: {request.task_id}",
                 f"refs/heads/{request.branch}", sha, head],
                capture_output=True, text=True, env=self._env(), check=False)
            if result.returncode != 0:
                raise HeadMismatchError(f"branch moved during the commit (compare-and-swap failed): {result.stderr}")
            self._refresh_index(list(declared))
            return CommitResult(sha=sha, parent=head, paths=tuple(declared), moved_over=moved_over)

    def _declared_hashes(self, request: CommitRequest) -> dict[str, str | None]:
        declared: dict[str, str | None] = {}
        for raw, digest in request.files.items():
            rel = self.policy.normalize(raw)[0]
            if digest is not None and (len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest)):
                raise GitOperationError(f"manifest entry {raw!r} needs a SHA-256 hex digest or None")
            declared[rel] = digest
        if not declared:
            raise NothingToCommitError("empty manifest")
        return declared

    def _check_head(self, expected: str, head: str, paths: Sequence[str]) -> tuple[str, ...]:
        if expected == head:
            return ()
        ancestor = subprocess.run(["git", "-C", str(self.root), "merge-base", "--is-ancestor", expected, head],
                                  env=self._env(), check=False)
        if ancestor.returncode != 0:
            raise HeadMismatchError(f"expected HEAD {expected[:12]} is not an ancestor of {head[:12]}")
        touched = [p for p in self._git("diff", "--name-only", "-z", expected, head, "--", *paths).split("\0") if p]
        if touched:
            raise HeadMismatchError(f"{touched} changed on the branch since {expected[:12]}; rebase the work first")
        return tuple(self._git("rev-list", "--reverse", f"{expected}..{head}").split())

    def _check_stable(self, declared: Mapping[str, str | None]) -> None:
        changed = [p for p, digest in declared.items() if file_digest(self.root / p) != digest]
        if changed:
            raise UnstableFileError(f"{changed} differ from the declared content; resubmit when stable")

    def _build_tree(self, head: str, declared: Mapping[str, str | None]) -> str:
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        fd, index_path = tempfile.mkstemp(prefix="git-index-", dir=self.tmp_dir)
        os.close(fd)
        os.unlink(index_path)  # git creates the index file itself
        env = {"GIT_INDEX_FILE": index_path}
        try:
            self._git("read-tree", head, env=env)
            present = [p for p, digest in declared.items() if digest is not None]
            removed = [p for p, digest in declared.items() if digest is None]
            if present:
                self._git("update-index", "--add", "--", *present, env=env)
            if removed:
                self._git("update-index", "--force-remove", "--", *removed, env=env)
            return self._git("write-tree", env=env).strip()
        finally:
            Path(index_path).unlink(missing_ok=True)

    def _check_staged_blobs(self, tree: str, declared: Mapping[str, str | None]) -> None:
        for path, digest in declared.items():
            if digest is None:
                continue
            blob = self._git_bytes("cat-file", "blob", f"{tree}:{path}")
            if hashlib.sha256(blob).hexdigest() != digest:
                raise UnstableFileError(f"{path!r} changed while it was being staged; resubmit when stable")

    def _commit_tree(self, tree: str, parent: str, message: str, env: Mapping[str, str]) -> str:
        result = subprocess.run(["git", "-C", str(self.root), "commit-tree", tree, "-p", parent],
                                input=message, capture_output=True, text=True, env=self._env(env), check=False)
        if result.returncode != 0:
            raise GitOperationError(f"git commit-tree failed: {result.stderr.strip()}")
        return result.stdout.strip()

    def _refresh_index(self, paths: Sequence[str], attempts: int = 5) -> None:
        for attempt in range(attempts):
            result = subprocess.run(["git", "-C", str(self.root), "reset", "-q", "HEAD", "--", *paths],
                                    capture_output=True, text=True, env=self._env(), check=False)
            if result.returncode == 0:
                return
            if "index.lock" not in result.stderr or attempt == attempts - 1:
                raise GitOperationError(f"commit landed but refreshing the index failed: {result.stderr.strip()}")
            time.sleep(0.1 * (attempt + 1))

    # -- dry-run plans -------------------------------------------------------

    @staticmethod
    def push_plan(branch: str, remote: str = "origin") -> list[str]:
        """The non-force push the coordinator would run (never executed yet)."""
        return ["git", "push", "--set-upstream", remote, f"refs/heads/{branch}:refs/heads/{branch}"]
