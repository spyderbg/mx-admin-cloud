"""W4.7 (M3): the integration candidate — the merge of an integration job's pinned source into its pinned base, built
in a coordinator-owned repository, and the disposable checkout of it that the validation gates run against.

Isolation (integration-jobs.md §14: the blocker for write-capable milestones, items 1-3):

  1. Coordinator-owned repositories only. Per job, `<state>/integration-candidates/<job>/repo.git` is a bare repository
     written by this module (no `git init`, no template, no hooks directory): `HEAD` names an UNBORN branch and never
     resolves (a bare repository reads `.gitattributes` from a resolvable HEAD — reproduced with Git 2.43, test in
     test_fc_integration_validate.py), `config` is integration_isolation.PRIVATE_CONFIG, and `objects/info/alternates`
     names the checkout's object directory: the checkout contributes object DATA only. New objects (merged trees and
     blobs, the candidate commit) land in the private object store; for an object that already exists in the
     checkout's store Git only refreshes its modification time ("freshening") — no content, ref, index or working-tree
     file of the checkout is ever written. Nothing here runs
     `git worktree add`, `checkout`, a ref update, a push, a rebase or an amend, in the checkout or anywhere else.
  2. No content-triggered execution. Every Git process runs with no system and no global configuration
     (GIT_CONFIG_NOSYSTEM, GIT_CONFIG_GLOBAL=/dev/null, a private HOME and XDG_CONFIG_HOME), no system or global
     attributes (GIT_ATTR_NOSYSTEM, core.attributesFile=/dev/null) and attributes read from the EMPTY tree
     (`--attr-source`): no `.gitattributes` of the source, the base or the candidate can select a filter, a merge
     driver or a diff driver, and no driver is defined anyway. Hooks are off, every transport is refused, and replace
     refs, grafts and the commit-graph are ignored (integration_intake.LOCAL_GIT_ARGS / LOCAL_GIT_ENV). Only the
     subcommands in CANDIDATE_SUBCOMMANDS run. gitsafe.py is not changed (integration-jobs.md §15, item 6).
  3. Git's merge machinery: `git merge-tree --write-tree <base_sha> <source_sha>` (Git >= 2.38) with Git's own merge
     defaults — the ort merge GitHub performs — never a working-tree merge. A conflict is reported with the conflicted
     paths; the source is never patched.

Determinism and recovery. The candidate commit object is composed here from fixed parts only — the merged tree, the
parents base_sha and source_sha in that order (GitHub's merge commit has the same parents, design §5.10), a fixed
coordinator identity, the job's creation time, and a message built from the job's immutable inputs — so building again
from the same inputs yields the same commit SHA, which is computed here and must equal what Git stores. Every build
re-runs the merge; a job that already recorded a candidate must get exactly the same head and tree SHAs again,
otherwise its candidate identity changed (the caller makes the job STALE, design §4). Objects are read back through
the hash-verifying reader (integration_objects) before they are believed. A crash at any point leaves at most
unreferenced objects behind; the next build writes or finds the same ones.

The workspace. Gates need a checkout: `<job>/worktree` is materialized here from hash-verified objects — every file
written byte for byte from its blob (no checkout, no filter, no end-of-line conversion; executable bit from the mode),
symlinks as symlinks, submodule entries as empty directories — plus its own `.git` (HEAD detached at the candidate
commit, the index from `read-tree`, objects through the alternates chain), so tools that ask Git (`git ls-files` in
docs-check, `git status` in the coordinator tests, the lint gates' `git diff base..head` and `git archive base`) see
exactly the candidate. `git status` must report it clean before it is used. It is built in a fresh temporary directory
and swapped in, so a crash never leaves a half-written workspace in use; leftovers are removed by the next run.
"""

from __future__ import annotations

import functools
import os
import shutil
import stat
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import GitOperationError
from .gitsafe import SAFE_CONFIG_ARGS, hardened_env
from .integration import SHA_RE, IntegrationJob, check_job_id, clean_text, parse_record_time
from .integration_intake import LOCAL_GIT_ARGS, LOCAL_GIT_ENV
from .integration_isolation import PRIVATE_CONFIG
from .integration_objects import HistoryRefused, HistoryUnverifiable, _CommitReader, object_sha
from .integration_paths import EXEC, GITLINK, LINK, MAX_SYMLINK_TARGET, TREE, PathChange, TreeReader, \
    candidate_changes
from .paths import is_git_dir_name

CANDIDATE_DIR = "integration-candidates"          # below the coordinator state directory
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
CANDIDATE_IDENTITY = "W4.7 integration candidate <integration-candidate@feature-coordinator.invalid>"
CANDIDATE_SUBCOMMANDS = frozenset({"merge-tree", "hash-object", "cat-file", "read-tree", "update-index", "status"})
ISOLATION_ARGS: tuple[str, ...] = (f"--attr-source={EMPTY_TREE}", "-c", "core.attributesFile=/dev/null")
ISOLATION_ENV = {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_ATTR_NOSYSTEM": "1"}
_UNSET_ENV = frozenset({"GIT_SHALLOW_FILE", "GIT_REPLACE_REF_BASE", "GIT_QUARANTINE_PATH"})
UNBORN_HEAD = "ref: refs/heads/isolated\n"
WORKSPACE_GIT_CONFIG = "[core]\n\trepositoryformatversion = 0\n\tfilemode = true\n\tbare = false\n"
MAX_BLOB = 512 << 20                # a larger file refuses the workspace (never truncated)
MAX_WORKSPACE_ENTRIES = 200_000
WORKSPACE_TIMEOUT = 1800.0          # reading every object of the candidate tree


@functools.lru_cache(maxsize=1)
def git_version() -> str:
    """`git version` of the Git that builds candidates (recorded with every candidate: merge results are specified
    per Git version)."""
    try:
        proc = subprocess.run(["git", "version"], capture_output=True, text=True, env=hardened_env(), timeout=30,
                              check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise GitOperationError(f"git cannot be run: {exc}") from exc
    return clean_text(proc.stdout, 200) if proc.returncode == 0 else "unknown"


class CandidateGit:
    """Git for candidate construction (module docstring, items 2-3): coordinator-written repositories, no system or
    global configuration, no attributes, no hooks, no transport, no replace refs, grafts or commit-graph."""

    def __init__(self, home: Path, *, timeout: float) -> None:
        self.home = Path(home)
        self.timeout = timeout

    def env(self) -> dict[str, str]:
        """The environment of every candidate Git process."""
        env = {k: v for k, v in os.environ.items() if k not in _UNSET_ENV}
        env.update(LOCAL_GIT_ENV)
        env = hardened_env(env)
        env.update(ISOLATION_ENV)
        env.update({"HOME": str(self.home), "XDG_CONFIG_HOME": str(self.home)})
        return env

    def argv(self, git_dir: Path, *args: str, work_tree: Path | None = None) -> list[str]:
        """`git --git-dir=<private> [--work-tree=<workspace>] <hardening> <args>` for an allowed subcommand only."""
        if not args or args[0] not in CANDIDATE_SUBCOMMANDS:
            raise GitOperationError(f"integration candidate: git {args[0] if args else ''} is not an allowed command")
        location = [f"--git-dir={git_dir}"] + ([f"--work-tree={work_tree}"] if work_tree is not None else [])
        return ["git", *location, *SAFE_CONFIG_ARGS, *LOCAL_GIT_ARGS, *ISOLATION_ARGS, *args]

    def run(self, git_dir: Path, *args: str, work_tree: Path | None = None, stdin: bytes | None = None,
            timeout: float | None = None) -> subprocess.CompletedProcess[bytes]:
        """One bounded Git process (bytes in, bytes out)."""
        self.home.mkdir(mode=0o700, parents=True, exist_ok=True)
        argv = self.argv(git_dir, *args, work_tree=work_tree)
        try:
            if stdin is None:
                return subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, env=self.env(),
                                      cwd=str(work_tree or git_dir), timeout=timeout or self.timeout, check=False)
            return subprocess.run(argv, input=stdin, capture_output=True, env=self.env(),
                                  cwd=str(work_tree or git_dir), timeout=timeout or self.timeout, check=False)
        except subprocess.TimeoutExpired as exc:
            raise GitOperationError(f"git {args[0]} did not finish within {timeout or self.timeout:.0f}s") from exc

    def reader(self, git_dir: Path, *, timeout: float | None = None) -> _CommitReader:
        """A hash-verifying object reader of *git_dir* (its own objects and, through the alternates, the checkout's)."""
        self.home.mkdir(mode=0o700, parents=True, exist_ok=True)
        return _CommitReader(self.argv(git_dir, "cat-file", "--batch"), self.env(), timeout or self.timeout)


def _stderr(proc: subprocess.CompletedProcess[bytes]) -> str:
    return clean_text((proc.stderr or b"").decode("utf-8", "replace"), 300)


# ---------------------------------------------------------------------------
# The coordinator-owned area of one job
# ---------------------------------------------------------------------------

class CandidateArea:
    """`<state>/integration-candidates/<job>/` (mode 0700): `repo.git` (the candidate repository), `worktree` (the
    materialized workspace), `logs` (gate evidence, kept), `home` (the empty HOME of candidate Git processes)."""

    def __init__(self, state_root: Path, job_id: str) -> None:
        self.root = Path(state_root) / CANDIDATE_DIR / check_job_id(job_id)
        self.repo = self.root / "repo.git"
        self.worktree = self.root / "worktree"
        self.logs = self.root / "logs"
        self.home = self.root / "home"

    def ensure(self, objects_dir: Path) -> bool:
        """Create the area and (re)create the candidate repository unless it is exactly as this module writes it.
        True when the repository was (re)created."""
        for path in (self.root.parent, self.root, self.logs, self.home):
            path.mkdir(mode=0o700, exist_ok=True)
        return ensure_repository(self.repo, objects_dir)

    def discard_leftovers(self) -> None:
        """Remove half-built or replaced workspaces of an interrupted run (the job lock is held by the caller)."""
        if self.root.is_dir():
            for entry in self.root.iterdir():
                if entry.name.startswith(("worktree.tmp-", "worktree.old-")):
                    shutil.rmtree(entry, ignore_errors=True)

    def discard_worktree(self) -> None:
        """Remove the materialized workspace (it is rebuilt from verified objects whenever gates run)."""
        self.discard_leftovers()
        shutil.rmtree(self.worktree, ignore_errors=True)

    def discard(self) -> None:
        """After an abort or a terminal state: remove the workspace, the repository and the private HOME; the gate
        logs stay as evidence."""
        self.discard_worktree()
        shutil.rmtree(self.repo, ignore_errors=True)
        shutil.rmtree(self.home, ignore_errors=True)


def repository_problem(path: Path, objects_dir: Path) -> str | None:  # pylint: disable=too-many-return-statements
    """Why *path* is not exactly a candidate repository as this module writes it (None: it is): only HEAD (unborn),
    config, an empty refs/ and objects/ with the one alternate; no symlink anywhere in that structure."""
    try:
        names = set(os.listdir(path))
    except FileNotFoundError:
        return "it does not exist"
    if names != {"HEAD", "config", "refs", "objects"}:
        return f"unexpected entries {sorted(names ^ {'HEAD', 'config', 'refs', 'objects'})[:5]}"
    for name, text in (("HEAD", UNBORN_HEAD), ("config", PRIVATE_CONFIG)):
        info = os.lstat(path / name)
        if not stat.S_ISREG(info.st_mode) or (path / name).read_text(encoding="utf-8", errors="replace") != text:
            return f"{name} is not the coordinator's"
    for directory in (path / "refs", path / "objects", path / "objects" / "info"):
        if not stat.S_ISDIR(os.lstat(directory).st_mode):
            return f"{directory.name} is not a directory"
    for dirpath, dirnames, filenames in os.walk(path / "refs"):
        if filenames or any(os.path.islink(os.path.join(dirpath, d)) for d in dirnames):
            return "it holds a ref (HEAD must never resolve)"
    for name in os.listdir(path / "objects"):
        entry = path / "objects" / name
        if entry.is_symlink() or not (name in ("info", "pack") or (len(name) == 2 and all(c in "0123456789abcdef"
                                                                                           for c in name))):
            return f"unexpected object store entry {name!r}"
    if set(os.listdir(path / "objects" / "info")) - {"alternates", "packs"}:
        return "unexpected files in objects/info"
    alternates = path / "objects" / "info" / "alternates"
    if not stat.S_ISREG(os.lstat(alternates).st_mode) \
            or alternates.read_text(encoding="utf-8", errors="replace") != f"{objects_dir}\n":
        return "its alternate is not the checkout's object directory"
    return None


def ensure_repository(path: Path, objects_dir: Path) -> bool:
    """Make *path* exactly a candidate repository (module docstring, item 1); True when it was (re)created. Anything
    else found there is removed first: everything in it is derived from the pinned SHAs and is rebuilt and re-verified
    by every build."""
    objects_dir = Path(objects_dir)
    if not objects_dir.is_absolute() or not objects_dir.is_dir():
        raise GitOperationError(f"the checkout's object directory {objects_dir} cannot be used as an alternate")
    try:
        problem = repository_problem(path, objects_dir)
    except OSError as exc:
        problem = f"it cannot be read ({exc})"
    if problem is None:
        return False
    if os.path.lexists(path):
        if os.path.islink(path) or not os.path.isdir(path):
            os.unlink(path)
        else:
            shutil.rmtree(path)
    path.mkdir(mode=0o700)
    (path / "refs").mkdir(mode=0o700)
    (path / "objects" / "info").mkdir(mode=0o700, parents=True)
    (path / "HEAD").write_text(UNBORN_HEAD, encoding="ascii")
    (path / "config").write_text(PRIVATE_CONFIG, encoding="ascii")
    (path / "objects" / "info" / "alternates").write_text(f"{objects_dir}\n", encoding="utf-8")
    return True


# ---------------------------------------------------------------------------
# Building the candidate
# ---------------------------------------------------------------------------

@dataclass
class Candidate:  # pylint: disable=too-many-instance-attributes
    """One build of the candidate: the merged tree, the candidate commit (None: conflict), the conflicted paths,
    Git's merge messages, the base tree and the exact changes base tree → candidate tree."""

    tree: str
    head: str | None
    base_tree: str
    conflicts: list[str] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)
    changes: dict[str, PathChange] = field(default_factory=dict)
    git: str = ""

    @property
    def clean(self) -> bool:
        """True when the merge had no conflict."""
        return self.head is not None


def parse_merge_tree(returncode: int, out: bytes) -> tuple[str, list[str], list[str]]:
    """(tree, conflicted paths, messages) from `merge-tree --write-tree -z --name-only --messages`:
    `<tree>NUL (<path>NUL)* NUL (<count>NUL (<path>NUL){count} <type>NUL <message>NUL)*`. Exit 0 = clean (no path),
    1 = conflicts (at least one path); anything else is HistoryUnverifiable."""
    tokens = out.split(b"\0")
    tree = tokens[0].decode("ascii", "replace")
    if not SHA_RE.fullmatch(tree):
        raise HistoryUnverifiable(f"unexpected `git merge-tree` output {clean_text(out[:120], 160)!r}")
    index, conflicts = 1, []
    while index < len(tokens) and tokens[index]:
        conflicts.append(tokens[index].decode("utf-8", "replace"))
        index += 1
    index += 1
    messages: list[str] = []
    while index < len(tokens) and tokens[index].isdigit():
        count = int(tokens[index])
        index += 1 + count             # the paths the message is about
        if index + 1 >= len(tokens):
            break
        messages.append(clean_text(tokens[index + 1].decode("utf-8", "replace"), 300))
        index += 2
    if (returncode == 0) == bool(conflicts) or returncode not in (0, 1):
        raise HistoryUnverifiable(f"`git merge-tree` exited {returncode} but reported {len(conflicts)} conflicted "
                                  "path(s)")
    return tree, conflicts, messages


def commit_body(job: IntegrationJob, tree: str) -> bytes:
    """The candidate commit object (module docstring, determinism): parents base_sha then source_sha, the fixed
    coordinator identity at the job's creation time, a message from the job's immutable inputs."""
    when = int(parse_record_time(job.created_at, "created_at").timestamp())
    ident = f"{CANDIDATE_IDENTITY} {when} +0000"
    source, base = job.source, job.base
    message = (f"Integration candidate for {job.job_id}\n\n"
               f"Merge {source['repo']}:{source['ref']} at {source['sha']}\n"
               f"into {base['ref']} at {base['expected_sha']} (Issue #{job.issue}).\n")
    return (f"tree {tree}\nparent {base['expected_sha']}\nparent {source['sha']}\nauthor {ident}\ncommitter {ident}\n"
            f"\n{message}").encode("utf-8")


def build_candidate(git: CandidateGit, repo: Path, job: IntegrationJob) -> Candidate:
    """Merge the pinned source into the pinned base in *repo* (a candidate repository), write the candidate commit,
    read both back hash-verified and compute the exact change to the base (module docstring). HistoryRefused for a
    crafted object or name; HistoryUnverifiable / GitOperationError when the objects cannot be read or verified."""
    base, source = job.base["expected_sha"], job.source["sha"]
    proc = git.run(repo, "merge-tree", "--write-tree", "-z", "--name-only", "--messages", base, source)
    if proc.returncode not in (0, 1):
        raise GitOperationError(f"git merge-tree failed (exit {proc.returncode}): {_stderr(proc)}")
    tree, conflicts, messages = parse_merge_tree(proc.returncode, proc.stdout)
    head = None
    if not conflicts:
        body = commit_body(job, tree)
        expected = object_sha("commit", body)
        written = git.run(repo, "hash-object", "-t", "commit", "-w", "--stdin", stdin=body)
        if written.returncode != 0 or written.stdout.decode("ascii", "replace").strip() != expected:
            raise GitOperationError(f"the candidate commit could not be written ({_stderr(written)})")
        head = expected
    with git.reader(repo) as reader:
        base_tree = reader.read(base, strict=False).tree
        reader.read(source, strict=False)
        result = Candidate(tree=tree, head=head, base_tree=base_tree, conflicts=conflicts, messages=messages,
                           git=git_version())
        if head is None:
            return result
        commit = reader.read(head, strict=True)
        if commit.tree != tree or commit.parents != (base, source):
            raise HistoryUnverifiable(f"the stored candidate commit {head[:12]} does not have the merged tree and the "
                                      "parents base_sha, source_sha")
        result.changes = candidate_changes(reader, base_tree=base_tree, candidate_tree=tree)
    return result


def identity_problem(recorded: dict[str, Any], candidate: Candidate) -> str | None:
    """Why a rebuilt candidate is not the recorded one (None: it is): design §4, a changed candidate head or tree SHA
    makes the job STALE — evidence of the recorded candidate never transfers to another one."""
    if candidate.head is None:
        return (f"rebuilding the candidate now conflicts ({', '.join(candidate.conflicts[:5])}), but "
                f"{recorded['head_sha']} was recorded")
    if candidate.tree != recorded["tree_sha"] or candidate.head != recorded["head_sha"]:
        return (f"the rebuilt candidate is {candidate.head} (tree {candidate.tree}), not the recorded "
                f"{recorded['head_sha']} (tree {recorded['tree_sha']}) — built with {candidate.git}, recorded with "
                f"{recorded['git']}")
    return None


# ---------------------------------------------------------------------------
# The workspace
# ---------------------------------------------------------------------------

def materialize(git: CandidateGit, area: CandidateArea, *, tree: str, head: str) -> Path:
    """Write the candidate tree *tree* (commit *head*) into a fresh workspace and swap it in as `area.worktree`
    (module docstring). HistoryRefused for an entry that is never written (`.`, `..`, `.git` in any spelling) or a
    tree too large; GitOperationError when Git does not see the workspace as exactly the candidate."""
    area.discard_leftovers()
    tmp = Path(tempfile.mkdtemp(prefix="worktree.tmp-", dir=area.root))
    try:
        with git.reader(area.repo, timeout=max(git.timeout, WORKSPACE_TIMEOUT)) as reader:
            _write_tree(TreeReader(reader), tree, os.fsencode(tmp))
        dot_git = tmp / ".git"
        _write_git_dir(dot_git, head=head, objects=area.repo / "objects")
        for args in (("read-tree", tree), ("update-index", "-q", "--refresh")):
            proc = git.run(dot_git, *args, work_tree=tmp, timeout=max(git.timeout, WORKSPACE_TIMEOUT))
            if proc.returncode != 0:
                raise GitOperationError(f"git {args[0]} in the candidate workspace failed: {_stderr(proc)}")
        proc = git.run(dot_git, "status", "--porcelain=v1", "-z", "--untracked-files=all", work_tree=tmp,
                       timeout=max(git.timeout, WORKSPACE_TIMEOUT))
        if proc.returncode != 0 or proc.stdout:
            shown = clean_text(proc.stdout[:300].replace(b"\0", b" ").decode("utf-8", "replace"), 300)
            raise GitOperationError(f"the materialized workspace is not exactly the candidate "
                                    f"({shown or _stderr(proc)})")
        _swap(tmp, area.worktree)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return area.worktree


def _write_tree(trees: TreeReader, root_tree: str, root: bytes) -> None:
    """Every entry of *root_tree* below the (new, empty) directory *root*: each path is a fresh child of a directory
    created here, files are opened O_EXCL|O_NOFOLLOW, so nothing is ever written through a symlink."""
    pending = [(root_tree, root)]
    count = 0
    while pending:
        sha, directory = pending.pop()
        for name, entry in trees.tree(sha, strict=True).items():
            if name in (b".", b"..") or is_git_dir_name(name.decode("utf-8", "replace")):
                raise HistoryRefused(f"the candidate tree has an entry named {name[:80]!r}, which is never written "
                                     "into a workspace")
            count += 1
            if count > MAX_WORKSPACE_ENTRIES:
                raise HistoryRefused(f"the candidate tree has more than {MAX_WORKSPACE_ENTRIES} entries")
            path = os.path.join(directory, name)
            if entry.mode in (TREE, GITLINK):
                os.mkdir(path, 0o755)
                if entry.mode == TREE:
                    pending.append((entry.sha, path))
            elif entry.mode == LINK:
                os.symlink(trees.blob(entry.sha, limit=MAX_SYMLINK_TARGET), path)
            else:
                data = trees.reader.read_object(entry.sha, "blob", limit=MAX_BLOB, strict=True)
                mode = 0o755 if entry.mode == EXEC else 0o644
                handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, mode)
                with os.fdopen(handle, "wb") as out:
                    out.write(data)
                    os.fchmod(out.fileno(), mode)


def _write_git_dir(path: Path, *, head: str, objects: Path) -> None:
    """The workspace's own `.git`: HEAD detached at the candidate commit, a minimal config, no hooks, objects through
    the candidate repository (and its alternate, the checkout's object directory)."""
    (path / "refs" / "heads").mkdir(parents=True)
    (path / "refs" / "tags").mkdir()
    (path / "objects" / "info").mkdir(parents=True)
    (path / "HEAD").write_text(f"{head}\n", encoding="ascii")
    (path / "config").write_text(WORKSPACE_GIT_CONFIG, encoding="ascii")
    (path / "objects" / "info" / "alternates").write_text(f"{objects}\n", encoding="utf-8")


def _swap(new: Path, target: Path) -> None:
    old = None
    if os.path.lexists(target):
        old = target.with_name(f"worktree.old-{os.urandom(4).hex()}")
        os.rename(target, old)
    os.rename(new, target)
    if old is not None:
        shutil.rmtree(old, ignore_errors=True)
