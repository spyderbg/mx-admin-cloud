"""Security S1 — the file-system guard: a bounded, deterministic inventory of the checkout's SENSITIVE entries,
read from the file system itself, independent of Git tracking, `.gitignore` and `git status`.

One rule set, shared by the bwrap sandbox plan (sandbox.py), both execution backends' post-run judgement
(launchers.RealCliRunner, native_backend.NativeCliRunner) and the workspace hold (workspace_hold.py). An entry is
sensitive when:

  * protected — it or one of its ancestor directories is protected (paths.protected_prefix: the configured
    protected patterns, and Git's own directory `.git` at any depth and in every alias);
  * secret — it or an ancestor matches `runtime.secret_patterns` WITHOUT an exempt suffix, each component judged on
    its own (paths.secret_prefix: `backend/.env.example/.env` is secret);
  * excluded — it or an ancestor matches a role's excluded pattern (an exact path used as a directory included);
  * a symlink into one — a symbolic link whose target resolves to a protected or secret path.

The walk

  * covers the whole checkout except Git's own top-level `.git` directory (only the Git coordinator mutates it; the
    backends check the Git state separately) — INCLUDING dependency and build trees (node_modules, .venv, dist, …),
    which the sandbox planner otherwise skips for speed: they are read name by name (directory listings only; no
    file is opened, only sensitive entries are stat'ed), so a protected name below them is always found;
  * never follows a symbolic link: each directory is opened relative to its parent with O_NOFOLLOW, so a directory
    swapped for a link while the walk runs is never entered;
  * is deterministic (names sorted) and bounded (MAX_ENTRIES entries — counted while a directory is listed —
    MAX_DEPTH levels, MAX_SECONDS — checked for every entry and before the result is returned): a walk that cannot
    finish raises InventoryError, and every caller fails closed (no agent starts; a finished run is rejected);
  * records each sensitive entry's kind and a stamp — file type, mode, size, inode, link count, mtime and ctime
    (an unprivileged process cannot set ctime back) — never its content.

CONSISTENCY (Security S1 round 3). A directory walk is NOT an atomic snapshot: a name inserted into a directory
after it was listed is simply absent from that walk. So a walk only counts when it is VERIFIED: every directory's
identity and change stamps (inode, device, mode, mtime, ctime) are taken before it is listed and compared again
after the whole walk; an entry or directory that vanishes or changes type while it is read also marks the walk.
An unverified walk is repeated (SCAN_ATTEMPTS, with a growing pause) and, if the checkout keeps changing, the scan
raises InventoryUnstableError — it NEVER returns a result whose completeness was not established. What this proves:
no directory of the walk changed between its listing and the end of the verification pass. What it cannot prove:
anything about changes made AFTER that pass (the caller's use of the result is later in time), or by a writer that
changes a directory and restores it bit for bit including ctime (not possible without privileges). Excluding
Worker-controlled writers is the admission protocol's job (workspace_hold.py); a host process that ignores the
Coordinator's locks can always write after any scan — see "Quiescence boundary" there.
"""

from __future__ import annotations

import errno
import os
import re
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from .config import ProjectConfig, pattern_regex
from .errors import CoordinatorError
from .paths import PathPolicy, is_git_dir_name

MAX_ENTRIES = 1_000_000     # directory entries read in one walk (all of them, sensitive or not)
MAX_DEPTH = 256             # directory levels below the checkout
MAX_SECONDS = 300.0         # wall time of one scan (all its attempts)
SCAN_ATTEMPTS = 4           # walks of one scan before a checkout that keeps changing is given up (fail closed)
SETTLE_SECONDS = 0.05       # pause before the next attempt (doubled each time)
VANISHED = "vanished"
_RACES = (errno.ENOENT, errno.ELOOP, errno.ENOTDIR)   # the entry changed between its listing and its use
PROTECTED, SECRET, EXCLUDED, SYMLINK = "protected", "secret", "excluded", "symlink"
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC

Flags = tuple[str | None, str | None, str | None]   # (protected, secret, excluded): the prefix that makes it so
NONE: Flags = (None, None, None)


class InventoryError(CoordinatorError):
    """The checkout's inventory could not be completed (bounded, fail closed)."""


class InventoryUnstableError(InventoryError):
    """The checkout kept changing while it was inventoried: no complete, consistent result exists (fail closed)."""


def union_regex(patterns: Iterable[str]) -> re.Pattern[str]:
    """One regex whose `.match(rel)` holds exactly when `config.matches(p, rel)` holds for some *p* (none: never)."""
    alternatives = [f"(?:{pattern_regex(p).pattern})" for p in dict.fromkeys(patterns)]
    return re.compile("|".join(alternatives) if alternatives else r"(?!)")


class Classifier:
    """The sensitive-path rules of one configuration, compiled once (one regex match per rule and entry)."""

    def __init__(self, config: ProjectConfig) -> None:
        self.protected = union_regex(config.protected)
        self.secret = union_regex(config.runtime.secret_patterns)
        self.exempt = tuple(config.runtime.secret_exempt_suffixes)
        self.excluded = union_regex(p for role in config.roles.values() for p in role.excluded)

    def child(self, parent: Flags, rel: str, name: str) -> Flags:
        """The flags of *rel* (whose last component is *name*) given its parent directory's flags: a flag is
        inherited from the nearest flagged ancestor, else *rel* itself is tested (equal to paths.protected_prefix
        and paths.secret_prefix, incrementally)."""
        protected, secret, excluded = parent
        if protected is None and (is_git_dir_name(name) or self.protected.match(rel)):
            protected = rel
        if secret is None and not name.endswith(self.exempt) and self.secret.match(rel):
            secret = rel
        if excluded is None and self.excluded.match(rel):
            excluded = rel
        return protected, secret, excluded

    def of(self, rel: str) -> Flags:
        """The flags of a repository-relative path, from scratch."""
        flags, prefix = NONE, ""
        for name in rel.split("/"):
            prefix = f"{prefix}/{name}" if prefix else name
            flags = self.child(flags, prefix, name)
        return flags


def reasons_of(flags: Flags) -> tuple[str, ...]:
    """`protected:<prefix>`, `secret:<prefix>`, `excluded:<prefix>` for the flags that are set."""
    return tuple(f"{label}:{prefix}" for label, prefix in zip((PROTECTED, SECRET, EXCLUDED), flags) if prefix)


@dataclass(frozen=True)
class Entry:
    """One sensitive entry: its kind (file | dir | symlink | other | vanished), stamp and why it is sensitive."""

    kind: str
    stamp: str
    reasons: tuple[str, ...]


@dataclass
class Inventory:
    """Every sensitive entry of a checkout at one moment (repository-relative POSIX paths)."""

    entries: dict[str, Entry]
    scanned: int = 0

    def having(self, label: str) -> list[str]:
        """The entries with a reason *label* (protected | secret | excluded | symlink), sorted."""
        return sorted(p for p, e in self.entries.items() if any(r.startswith(f"{label}:") for r in e.reasons))

    def secret_files(self) -> list[str]:
        """Secret entries that are not directories (the file itself, or a file inside a secret directory)."""
        return [p for p in self.having(SECRET) if self.entries[p].kind not in ("dir", VANISHED)]


@dataclass(frozen=True)
class Change:
    """A sensitive entry that differs between two inventories (None: absent on that side)."""

    path: str
    before: str | None
    after: str | None
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready evidence (stamps and reasons, never content)."""
        return {"path": self.path, "before": self.before, "after": self.after, "reasons": list(self.reasons)}


def _stamp(st: os.stat_result, link: str = "") -> tuple[str, str]:
    mode = st.st_mode
    if stat.S_ISLNK(mode):
        return "symlink", f"symlink:{link}"
    if stat.S_ISDIR(mode):
        return "dir", f"dir:{stat.S_IMODE(mode):o}:{st.st_ino}"
    if stat.S_ISREG(mode):
        return "file", (f"file:{stat.S_IMODE(mode):o}:{st.st_size}:{st.st_ino}:{st.st_nlink}:{st.st_mtime_ns}:"
                        f"{st.st_ctime_ns}")
    return "other", f"other:{mode:o}:{st.st_ino}:{st.st_rdev}:{st.st_ctime_ns}"


def _dir_stamp(st: os.stat_result) -> tuple[int, int, int, int, int]:
    return st.st_ino, st.st_dev, st.st_mode, st.st_mtime_ns, st.st_ctime_ns


class _Walk:  # pylint: disable=too-many-instance-attributes
    def __init__(self, root: Path, classifier: Classifier, *, max_entries: int, max_depth: int,
                 deadline: float, clock: Callable[[], float]) -> None:
        self.root = root
        self.real_root = root.resolve()
        self.classifier = classifier
        self.max_entries = max_entries
        self.max_depth = max_depth
        self.deadline = deadline
        self.clock = clock
        self.entries: dict[str, Entry] = {}
        self.dirs: dict[str, tuple[int, int, int, int, int]] = {}   # every walked directory, as it was when listed
        self.count = 0
        self.unstable = ""   # why this walk cannot be trusted as complete ('' = nothing seen so far)

    def tick(self) -> None:
        """Fail closed once the scan's deadline has passed."""
        if self.clock() > self.deadline:
            raise InventoryError("the inventory did not finish in time")

    def _list(self, fd: int, rel: str) -> list[tuple[str, bool, bool]]:
        names = []
        try:
            self.dirs[rel] = _dir_stamp(os.fstat(fd))
            with os.scandir(fd) as listing:
                for entry in listing:
                    self.count += 1   # counted while listing: a huge directory is never materialised first
                    if self.count > self.max_entries:
                        raise InventoryError(f"the checkout has more than {self.max_entries} entries")
                    if self.count % 256 == 0:
                        self.tick()
                    names.append((entry.name, entry.is_dir(follow_symlinks=False), entry.is_symlink()))
        except OSError as exc:
            raise InventoryError(f"cannot list {rel or '.'!r}: {exc.strerror or exc}") from exc
        return sorted(names)

    def directory(self, fd: int, rel: str, flags: Flags, depth: int) -> None:
        """Walk the directory open as *fd* (repository path *rel*, its flags, its depth below the checkout)."""
        if depth > self.max_depth:
            raise InventoryError(f"the checkout is nested deeper than {self.max_depth} levels at {rel!r}")
        self.tick()
        for name, is_dir, is_link in self._list(fd, rel):
            self.tick()
            child = f"{rel}/{name}" if rel else name
            if child == ".git" and is_dir and not is_link:
                continue  # Git's own top-level directory: the Git coordinator's (see the module docstring)
            child_flags = self.classifier.child(flags, child, name)
            reasons = reasons_of(child_flags)
            if is_link:
                target = self._target(child)
                if target is not None and any(self.classifier.of(target)):
                    reasons += (f"{SYMLINK}:{target}",)
                if reasons:
                    self._record(fd, name, child, reasons)
                continue
            if reasons:
                self._record(fd, name, child, reasons)
            if is_dir:
                self._descend(fd, name, child, child_flags, depth)

    def _descend(self, fd: int, name: str, child: str, flags: Flags, depth: int) -> None:  # pylint: disable=too-many-arguments
        try:
            sub = os.open(name, _DIR_FLAGS, dir_fd=fd)
        except OSError as exc:
            if exc.errno in _RACES:   # removed, or swapped for a link or a file, since it was listed: never entered
                self.unstable = self.unstable or f"{child!r} changed while it was read"
                return
            raise InventoryError(f"cannot open the directory {child!r}: {exc.strerror or exc}") from exc
        try:
            self.directory(sub, child, flags, depth + 1)
        finally:
            os.close(sub)

    def _record(self, fd: int, name: str, child: str, reasons: tuple[str, ...]) -> None:
        try:
            st = os.stat(name, dir_fd=fd, follow_symlinks=False)
            link = os.readlink(name, dir_fd=fd) if stat.S_ISLNK(st.st_mode) else ""
        except OSError as exc:
            if exc.errno not in (*_RACES, errno.EINVAL):   # EINVAL: no longer a link when it was read
                raise InventoryError(f"cannot stat {child!r}: {exc.strerror or exc}") from exc
            self.unstable = self.unstable or f"{child!r} vanished while it was read"
            self.entries[child] = Entry(VANISHED, VANISHED, reasons)
            return
        kind, stamp = _stamp(st, link)
        self.entries[child] = Entry(kind, stamp, reasons)

    def _target(self, child: str) -> str | None:
        """Where the symlink *child* finally points, repository-relative (None: outside, or the root itself).
        realpath only reads links; nothing is opened."""
        real = os.path.realpath(self.root / child)
        rel = os.path.relpath(real, self.real_root)
        return None if rel == "." or rel == ".." or rel.startswith("../") else Path(rel).as_posix()

    def verify(self, root_fd: int) -> None:
        """The verification pass: every walked directory is still the same directory, unchanged since it was
        listed. Otherwise the walk is marked unstable (a name may have been added after its listing)."""
        for rel, stamp in self.dirs.items():
            self.tick()
            try:
                now = _dir_stamp(os.stat(rel or ".", dir_fd=root_fd, follow_symlinks=False))
            except OSError as exc:
                if exc.errno not in _RACES:   # e.g. ENAMETOOLONG: cannot be verified — fail closed
                    raise InventoryError(f"cannot verify {rel or '.'!r}: {exc.strerror or exc}") from exc
                now = None
            if now != stamp:
                self.unstable = self.unstable or f"{rel or '.'!r} changed during the inventory"
                return


def scan(root: Path, config: ProjectConfig, *, max_entries: int = MAX_ENTRIES, max_depth: int = MAX_DEPTH,  # pylint: disable=too-many-arguments
         max_seconds: float = MAX_SECONDS, clock: Callable[[], float] = time.monotonic,
         attempts: int = SCAN_ATTEMPTS, sleep: Callable[[float], None] = time.sleep) -> Inventory:
    """The sensitive entries of the checkout at *root*, from a VERIFIED walk (see the module docstring).
    InventoryError: the walk could not finish within its bounds; InventoryUnstableError: the checkout kept changing
    for *attempts* walks. Either way no result is returned — callers fail closed."""
    root = Path(os.path.abspath(root))
    classifier, deadline, why = Classifier(config), clock() + max_seconds, ""
    for attempt in range(max(attempts, 1)):
        walk = _Walk(root, classifier, max_entries=max_entries, max_depth=max_depth, deadline=deadline, clock=clock)
        try:
            fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        except OSError as exc:
            raise InventoryError(f"cannot open the checkout {root}: {exc.strerror or exc}") from exc
        try:
            walk.directory(fd, "", NONE, 0)
            walk.verify(fd)
        finally:
            os.close(fd)
        walk.tick()
        if not walk.unstable:
            return Inventory(walk.entries, walk.count)
        why = walk.unstable
        if attempt + 1 < attempts:
            sleep(SETTLE_SECONDS * 2 ** attempt)
    raise InventoryUnstableError(f"the checkout kept changing during the inventory ({attempts} attempts; last: "
                                 f"{why}): its completeness cannot be established")


def _inside(path: str, roots: Iterable[str]) -> bool:
    return any(path == root or path.startswith(f"{root}/") for root in roots)


def compare(before: Inventory, after: Inventory, *, within: Sequence[str] | None = None) -> list[Change]:
    """Every sensitive entry that appeared, disappeared or changed (a `vanished` entry always counts), optionally
    only inside the repository-relative *within* roots."""
    changes = []
    for path in sorted(set(before.entries) | set(after.entries)):
        old, new = before.entries.get(path), after.entries.get(path)
        if old is not None and new is not None and old.stamp == new.stamp != VANISHED:
            continue
        if within is not None and not _inside(path, within):
            continue
        reasons = tuple(dict.fromkeys((*(old.reasons if old else ()), *(new.reasons if new else ()))))
        changes.append(Change(path, old.stamp if old else None, new.stamp if new else None, reasons))
    return changes


def violations(changes: Iterable[Change], policy: PathPolicy, role: str, grants: Sequence[str] = (), *,
               tolerated: Callable[[str], bool] = lambda path: False) -> list[Change]:
    """The changes *role* was not allowed to make: every change to a protected entry or to a symlink into a
    protected or secret path, and any other sensitive change PathPolicy refuses for the role (*tolerated*: paths
    a backend explains itself, e.g. native hardening's inert launcher anchors)."""
    found = []
    for change in changes:
        if tolerated(change.path):
            continue
        always = any(r.startswith((f"{PROTECTED}:", f"{SYMLINK}:")) for r in change.reasons)
        if always or not policy.check(role, change.path, grants=grants).allowed:
            found.append(change)
    return found


def summary(changes: Sequence[Change], limit: int = 20) -> str:
    """`path (how)` for the first *limit* changes."""
    def how(change: Change) -> str:
        """created | removed | changed."""
        if change.before is None:
            return "created"
        if change.after is None:
            return "removed"
        return "changed"
    shown = [f"{c.path} ({how(c)})" for c in changes[:limit]]
    return ", ".join(shown) + (f" and {len(changes) - limit} more" if len(changes) > limit else "")
