"""Security S2: the integrity of a checkout's Git control files around an agent run (both execution backends, whatever
`runtime.native_hardening` says).

The Git control files decide what a later Git command in the checkout does — including programs it runs: `.git/config`
(and what it includes), `config.worktree`, `commondir`, `hooks/`, `info/` (attributes, exclude, grafts) and the legacy
remote definitions `remotes/` and `branches/`. Only the Git coordinator mutates `.git`, and it never writes these.

A run's protocol:

    control = capture(root)            before the agent starts: the files, then the settings (scanned: gitsafe.py)
    …the agent runs and is stopped…
    verdict = verify(root, control)    FIRST thing after the run, before any other Git command in the checkout

verify() compares the files from the file system alone — no Git process — and only then lists the effective
repository-scoped settings (a listing runs no program), which also covers an included file outside `.git`. Whatever
cannot be verified is a problem; only a host short of resources raises (OSError, gitsafe.TRANSIENT_ERRNO). With any
problem the run is rejected, everything is left in place, and the residue puts the checkout on hold
(workspace_hold.py). Git then judges the rest of the run — as evidence only — when the settings are known to be safe
and the repository itself is the same (`git_safe`); otherwise it does not run, and the residue says so (NOT_JUDGED).
From then on every Worker Git command is refused by gitsafe (guarded_argv) while the settings name a program. The state
is also recorded with the registered execution, so an interrupted run (a killed Worker, a runner that raised) is
compared when its execution is dropped (workspace_hold.reap, abandon_execution).

Settings that tools write by themselves and that name no program (IGNORED_SETTINGS) do not count, nor does a change of
`config` that leaves every other effective setting as it was (a comment).

Stamps are content digests, never content. Nothing is repaired, removed or restored.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

from .errors import GitConfigExecutionError
from .gitsafe import TRANSIENT_ERRNO, findings_in, printable, untrusted_records

RESIDUE = "Git control files (.git/config, hooks or info) changed"   # workspace_hold: an operator must decide
REJECTED = ("the run changed Git control files (.git/config, hooks or info); left as is for the operator — review "
            "before any Git command runs in this checkout")
MAX_CONTROL_BYTES = 16 * 1024 * 1024    # a larger control file, or more of them, is refused: never half compared
MAX_CONTROL_FILES = 10_000
UNJUDGED = "the run could not be judged; everything is left in place for the operator"
NOT_JUDGED = ("unverified workspace: the run's other changes (files of other roles, branches, commits) were NOT judged "
              "by Git: compare the checkout with `before` in violations.json before accepting anything")
# Written by VS Code (after a branch switch) and by git-lfs (its clean filter) on their own: key → the plain value.
IGNORED_SETTINGS: Mapping[str, str] = {r"branch\..+\.vscode-merge-base": r"[A-Za-z0-9][A-Za-z0-9._/+-]*",
                                       r"lfs\.repositoryformatversion": "0"}
MAX_TOTAL_BYTES = 64 * 1024 * 1024
_HEAD = re.compile(rb"(ref: refs/[^\s\x00-\x1f]+|[0-9a-f]{40}|[0-9a-f]{64})\s*")
_TEXT_FILES = frozenset({"config", "common/config"})           # compared by their effective settings
_IDENTITY = frozenset({".git", "commondir", "HEAD", "objects", "refs"})   # changed: perhaps another repository
_FILES = ("config", "config.worktree", "commondir")
_DIRECTORIES = ("hooks", "info", "remotes", "branches")
# Claude Code's sandbox guard, and the list a repack writes for dumb HTTP servers (local Git never reads it).
_IGNORED = frozenset({".claude-sandbox-noreplace"})
_IGNORED_PATHS = frozenset({"info/refs"})


@dataclass(frozen=True)
class GitControl:
    """The control files (name → stamp) and the repository-scoped settings (a digest) before a run."""

    files: Mapping[str, str]
    settings: str

    def to_dict(self) -> dict[str, Any]:
        """For the registered execution (workspace_hold.py)."""
        return {"files": dict(self.files), "settings": self.settings}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "GitControl":
        """The inverse (ValueError: not such a record)."""
        files, settings = data.get("files"), data.get("settings")
        if not isinstance(files, dict) or not isinstance(settings, str) \
                or not all(isinstance(v, str) for v in files.values()):
            raise ValueError("not a recorded Git control state")
        return cls(dict(files), settings)


@dataclass(frozen=True)
class Verdict:
    """verify(): what changed or is unsafe ([]: nothing), and whether Git commands may still inspect the checkout.
    *unjudged*: why the judgement itself failed (launchers.unjudgeable)."""

    problems: list[str] = field(default_factory=list)
    git_safe: bool = True
    unjudged: str = ""


def _read(path: Path) -> bytes | None:
    """The content of a regular file (None: not one, or unreadable; GitConfigExecutionError: too large to compare).
    Never follows a symlink, never blocks on a FIFO."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError as exc:
        if exc.errno in TRANSIENT_ERRNO:
            raise
        return None
    with os.fdopen(fd, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            return None
        data = handle.read(MAX_CONTROL_BYTES + 1)
    if len(data) > MAX_CONTROL_BYTES:
        raise GitConfigExecutionError(f"the Git control file {path} is too large to be verified")
    return data


def _stamp(path: Path) -> str:
    """A regular file's mode and content digest; for anything else (a symlink, a FIFO, an unreadable file) what it is
    and its change stamps."""
    try:
        info = os.lstat(path)
    except OSError as exc:
        return f"special:{type(exc).__name__}"
    data = _read(path) if stat.S_ISREG(info.st_mode) else None
    if data is None:
        target = f":{os.readlink(path)}" if stat.S_ISLNK(info.st_mode) else ""
        return f"special:{info.st_mode:o}:{info.st_ino}:{info.st_size}:{info.st_mtime_ns}:{info.st_ctime_ns}{target}"
    return f"{stat.S_IMODE(info.st_mode):o}:{hashlib.sha256(data).hexdigest()}"


def _git_directories(root: Path) -> tuple[Path, Path, dict[str, str]]:
    """(the checkout's Git directory, its common directory, stamps of what selects them) — read without Git."""
    entry, stamps = root / ".git", {}
    if entry.is_symlink():
        stamps[".git"] = _stamp(entry)
    git_dir = entry
    if not entry.is_dir():              # a linked worktree's `.git` file, or something that replaced the directory
        stamps[".git"] = _stamp(entry)
        text = (_read(entry) or b"").decode("utf-8", "surrogateescape").strip()
        if text.startswith("gitdir:"):
            git_dir = root / text[len("gitdir:"):].strip()
    common = git_dir
    redirect = (_read(git_dir / "commondir") or b"").decode("utf-8", "surrogateescape").strip()
    if redirect:
        common = git_dir / redirect
    return git_dir, common, stamps


def _entries(directory: Path) -> Iterator[Path]:
    """Every non-directory entry below *directory* ([]: it is not one), sorted. *directory* itself may be a symlink (it
    is what Git reads); no other is followed. An unreadable directory raises."""
    def fail(exc: OSError) -> None:
        raise exc
    if not directory.is_dir():
        return
    for base, names, files in os.walk(directory, followlinks=False, onerror=fail):
        names.sort()
        for name in sorted([*files, *(n for n in names if os.path.islink(os.path.join(base, n)))]):
            yield Path(base) / name


def _benign(name: str, path: Path) -> bool:
    """Claude Code's own guards: an empty `config.worktree`, and a `commondir` that names the directory itself."""
    data = _read(path)
    return data is not None and (name, data.strip()) in (("config.worktree", b""), ("commondir", b"."))


def control_files(root: Path) -> dict[str, str]:
    """Control file name (printable) → stamp. File system only: no Git command runs. GitConfigExecutionError when they
    cannot all be compared (unreadable directories, too large, too many)."""
    try:
        return {printable(name): stamp for name, stamp in _control_files(Path(root)).items()}
    except (OSError, ValueError) as exc:
        if getattr(exc, "errno", None) in TRANSIENT_ERRNO:
            raise
        raise GitConfigExecutionError(f"the Git control files of {root} cannot be verified: "
                                      f"{printable(str(exc))}") from exc


def _control_files(root: Path) -> dict[str, str]:
    git_dir, common, stamps = _git_directories(root)
    wanted = [(name, git_dir / name) for name in _FILES]
    if common != git_dir:
        wanted.append(("common/config", common / "config"))
    for name, path in wanted:
        if os.path.lexists(path) and not _benign(name, path):
            stamps[name] = _stamp(path)
    for name, base in (("HEAD", git_dir), ("objects", common), ("refs", common)):   # that this IS the repository
        try:
            info = os.lstat(base / name)
        except (FileNotFoundError, NotADirectoryError):
            continue
        mode = stat.S_IFMT(info.st_mode) if name == "HEAD" else info.st_mode    # (HEAD is rewritten by every switch)
        stamps[name] = f"{mode:o}" + (f":{os.readlink(base / name)}" if stat.S_ISLNK(info.st_mode) else "")
        if name == "HEAD" and stat.S_ISREG(info.st_mode) and not _HEAD.fullmatch(_read(base / name) or b""):
            stamps[name] += ":not a HEAD"   # Git would look for a repository further up
    total = 0
    for name in _DIRECTORIES:
        base = common / name
        if os.path.lexists(base) and not stat.S_ISDIR(os.lstat(base).st_mode):
            stamps[name] = _stamp(base)
        for count, path in enumerate(_entries(base)):
            rel = path.relative_to(common).as_posix()
            total += os.lstat(path).st_size
            if count >= MAX_CONTROL_FILES or total > MAX_TOTAL_BYTES:
                raise GitConfigExecutionError(f"the Git control directory {base} has too much content to be verified")
            if path.name not in _IGNORED and rel not in _IGNORED_PATHS:
                stamps[rel] = _stamp(path)
    return stamps


def _ignored(key: str, value: str) -> bool:
    return any(re.fullmatch(name, key.lower()) and re.fullmatch(plain, value)
               for name, plain in IGNORED_SETTINGS.items())


def _settings_digest(records: Iterable[tuple[str, str, str]]) -> str:
    kept = [f"{s}\n{k}\n{v}" for s, k, v in records if not _ignored(k, v)]
    return hashlib.sha256("\0".join(kept).encode("utf-8", "surrogateescape")).hexdigest()


def capture(root: Path) -> GitControl:
    """The state before a run. GitConfigExecutionError when the settings name a program, or anything cannot be
    verified: nothing may be started in this checkout."""
    files = control_files(root)
    records = untrusted_records(root)
    findings = findings_in(records)
    if findings:
        raise GitConfigExecutionError(
            f"the Git configuration of the workspace would run a program during a Coordinator Git operation "
            f"(repository-scoped execution settings: {findings}); no agent is started until an operator removes them")
    return GitControl(files, _settings_digest(records))


def verify(root: Path, before: GitControl) -> Verdict:
    """What changed since *before* or is unsafe now. The files are compared first, without Git. Raises only when the
    host is short of resources (OSError, TRANSIENT_ERRNO): the execution stays registered or is compared again."""
    try:
        files = control_files(root)
    except GitConfigExecutionError as exc:
        return Verdict([f"Git control files cannot be verified: {exc}"], git_safe=False)
    changed = {name for name in set(files) | set(before.files) if files.get(name) != before.files.get(name)}
    unsafe: list[str] = []
    try:
        records = untrusted_records(root)
        unsafe = [f"Git control setting names a program: {finding}" for finding in findings_in(records)]
        same = _settings_digest(records) == before.settings
        if not unsafe and same:         # only ignored settings or comments differ: the same kind of file, same mode
            changed -= {n for n in changed & _TEXT_FILES if not files.get(n, "special:").startswith("special:")
                        and files[n].split(":")[0] == before.files.get(n, "").split(":")[0]}
        elif not unsafe and not changed:
            unsafe = ["Git control settings changed (an included configuration file)"]
    except GitConfigExecutionError as exc:
        unsafe = [f"Git control settings cannot be verified: {exc}"]
    problems = [f"Git control file {'added' if n not in before.files else 'removed' if n not in files else 'changed'}: "
                f"{n if n == '.git' else '.git/' + n}" for n in sorted(changed)]
    return Verdict(problems + unsafe, git_safe=not unsafe and not changed & _IDENTITY)
