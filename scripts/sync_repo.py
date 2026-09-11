#!/usr/bin/env python3
"""
Sync version-controlled files from one local repository to another.

Only files tracked by git are copied, so ignored and untracked files (build
output, virtual environments, caches, databases) are never synced. Files matching
the special-file patterns are the exception: they are skipped by default and
copied only when --include-special is passed.

Copying is done with pure Python (shutil), so the script runs unchanged on
Linux, macOS and Windows.

Profiles live in .sync_repo.json at the repository root. Its "default" key names
the profile used when --profile is not given; every profile setting can be
overridden on the command line. Every sync needs a destination: it comes from the
profile's dst_repo_path or from --dest, and the run fails when neither supplies
one. A profile without src_repo_path syncs from the repository this script lives in.

The destination follows both directions of change: files removed from the source
are pruned from the destination (disable with --no-prune), and files edited at
the destination since the last sync are reported as conflicts and left alone
unless --force is passed.

Every run records what it changed under <git-dir>/sync_repo/<profile>/, keeping a
copy of each overwritten or deleted file. The 'revert' command undoes the last
sync and puts back the destination-local edits that --force stashed.

Usage:
    python scripts/sync_repo.py
        # sync using the default profile from .sync_repo.json

    python scripts/sync_repo.py -p backup -s
        # sync using the 'backup' profile, including special files (.env, ...)

    python scripts/sync_repo.py -d /path/to/other/mx-admin --no-prune
        # override the destination and keep destination-only files

    python scripts/sync_repo.py --only backend cli --since main
        # sync only backend/ and cli/, and only what changed since 'main'

    python scripts/sync_repo.py -n
        # dry run: report what would change, write nothing

    python scripts/sync_repo.py revert
        # undo the last sync of the default profile

Commands:
    sync (default)           Copy the source repository onto the destination
    revert                   Undo the changes made by the last sync

Arguments:
    -p, --profile NAME       Profile from .sync_repo.json (default: its "default" key)
    -c, --config PATH        Profile file to read (default: <repo>/.sync_repo.json)
        --src PATH           Override the profile's src_repo_path
    -d, --dest PATH          Override the profile's dst_repo_path
    -s, --include-special    Copy special files (--no-include-special to skip them)
        --prune/--no-prune   Delete destination files removed from the source (default: prune)
        --only GLOB [...]    Sync only paths matching these globs or directories
        --exclude GLOB [...] Skip paths matching these globs or directories
        --since REF          Sync only files changed since a git ref (branch, tag, commit)
    -f, --force              Overwrite (and stash) destination files edited since the last sync
    -n, --dry-run            Show what would change without writing anything
    -q, --quiet              Print only the summary
    -h, --help               Show this help message and exit

Exit codes:
    0   success
    1   error
    2   completed, but some files were skipped as conflicts
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

CONFIG_FILENAME = "scripts/.sync_repo.json"
STATE_DIRNAME = "sync_repo"          # created inside the source repository's git dir
MANIFEST_FILE = "manifest.json"
MANIFEST_PREV_FILE = "manifest.prev.json"
LAST_RUN_FILE = "last_run.json"
BACKUP_DIRNAME = "files"

# Files that are never synced unless --include-special is passed, and that prune
# never deletes. Paths are relative to the repository root and may contain glob
# wildcards. A profile may replace this list via its "special_files" key.
DEFAULT_SPECIAL_FILES = (
    ".env",
    ".env.local",
    ".env.*.local",
    "backend/.env",
    "backend/.env.local",
    "cli/.env",
    "docker/.env",
    "frontend/.env",
    "frontend/.env.local",
    "frontend/.env.*.local",
    CONFIG_FILENAME,
)

_PROFILE_KEYS = frozenset({
    "description",
    "src_repo_path",
    "dst_repo_path",
    "prune",
    "include_special",
    "special_files",
    "only",
    "exclude",
})

_COLOR = sys.stdout.isatty()
GREEN  = "\033[92m" if _COLOR else ""
YELLOW = "\033[93m" if _COLOR else ""
RED    = "\033[91m" if _COLOR else ""
CYAN   = "\033[96m" if _COLOR else ""
BOLD   = "\033[1m"  if _COLOR else ""
DIM    = "\033[2m"  if _COLOR else ""
RESET  = "\033[0m"  if _COLOR else ""

_CHUNK_SIZE = 1 << 20  # 1 MiB, used when comparing and hashing files

_SUMMARY_ROWS = (
    ("new",       "New files",         GREEN),
    ("updated",   "Updated files",     CYAN),
    ("special",   "Special files",     YELLOW),
    ("pruned",    "Pruned files",      RED),
    ("restored",  "Restored files",    GREEN),
    ("removed",   "Removed files",     RED),
    ("unchanged", "Unchanged files",   DIM),
    ("skipped",   "Skipped (special)", DIM),
    ("conflicts", "Conflicts",         YELLOW),
    ("missing",   "Missing files",     YELLOW),
    ("errors",    "Errors",            RED),
)


# ── options ────────────────────────────────────────────────────────────────────

@dataclass
class Profile:
    """A resolved sync profile: where files come from, where they go, and what is copied."""

    # pylint: disable=too-many-instance-attributes

    name: str
    src: Path
    dst: Path
    prune: bool = True
    include_special: bool = False
    special_files: tuple[str, ...] = DEFAULT_SPECIAL_FILES
    only: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    description: str = ""


@dataclass
class RunOptions:
    """Per-invocation switches that are never stored in a profile."""

    since: str | None = None
    force: bool = False
    dry_run: bool = False
    quiet: bool = False


# ── git helpers ────────────────────────────────────────────────────────────────

def _git(cwd: Path, *args: str) -> str:
    """Run a git command and return its stdout."""
    try:
        result = subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True,
            text=True,
            check=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("Error: git is not installed or not on PATH.") from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() or exc.stdout.strip()
        raise RuntimeError(f"Error: 'git {' '.join(args)}' failed: {detail}") from exc
    return result.stdout


def _repo_root() -> Path:
    """Return the root of the repository this script lives in."""
    script_dir = Path(__file__).resolve().parent
    try:
        toplevel = _git(script_dir, "rev-parse", "--show-toplevel").strip()
    except RuntimeError as exc:
        raise RuntimeError(
            f"Error: {script_dir} is not inside a git repository; "
            f"pass --src PATH to name the source repository."
        ) from exc
    return Path(toplevel).resolve()


def _resolve_repo(raw_path: str) -> Path:
    """Expand a configured path and return the git repository root it points at."""
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    if not path.is_dir():
        raise RuntimeError(f"Error: source '{path}' is not an existing directory.")
    try:
        toplevel = _git(path, "rev-parse", "--show-toplevel").strip()
    except RuntimeError as exc:
        raise RuntimeError(f"Error: source '{path}' is not inside a git repository.") from exc
    return Path(toplevel).resolve()


def _is_git_repo(path: Path) -> bool:
    return (path / ".git").exists()


def _tracked_files(repo_root: Path) -> list[Path]:
    """Return every git-tracked path, relative to the repository root."""
    output = _git(repo_root, "ls-files", "-z", "--cached", "--exclude-standard")
    return [Path(entry) for entry in output.split("\0") if entry]


def _name_status(repo_root: Path, ref: str) -> list[tuple[str, Path]]:
    """Return (status, path) pairs between ref and the working tree."""
    try:
        _git(repo_root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    except RuntimeError as exc:
        raise RuntimeError(f"Error: '{ref}' is not a commit in {repo_root}.") from exc
    output = _git(repo_root, "diff", "--name-status", "-z", "--no-renames", ref)
    fields = [field for field in output.split("\0") if field]
    return [(fields[i][0], Path(fields[i + 1])) for i in range(0, len(fields) - 1, 2)]


def _changed_files(repo_root: Path, ref: str) -> list[Path]:
    """Tracked files added or modified since ref, including uncommitted work."""
    return [rel for status, rel in _name_status(repo_root, ref) if status != "D"]


def _deleted_files(repo_root: Path, ref: str) -> set[Path]:
    """Tracked files deleted since ref, including uncommitted deletions."""
    return {rel for status, rel in _name_status(repo_root, ref) if status == "D"}


# ── path matching ──────────────────────────────────────────────────────────────

def _path_matches(rel_path: Path, pattern: str) -> bool:
    """Match a relative path against a glob, a file name or a directory prefix."""
    posix = rel_path.as_posix()
    pattern = pattern.strip().rstrip("/")
    if not pattern:
        return False
    return (
        fnmatch.fnmatchcase(posix, pattern)
        or fnmatch.fnmatchcase(posix, f"{pattern}/*")
        or posix.startswith(f"{pattern}/")
    )


def _matches_any(rel_path: Path, patterns: tuple[str, ...]) -> bool:
    return any(_path_matches(rel_path, pattern) for pattern in patterns)


def _special_files(repo_root: Path, patterns: tuple[str, ...]) -> list[Path]:
    """Expand the special-file patterns against the working tree."""
    found: list[Path] = []
    seen: set[Path] = set()
    for pattern in patterns:
        for match in sorted(repo_root.glob(pattern)):
            if not match.is_file():
                continue
            rel = match.relative_to(repo_root)
            if rel not in seen:
                seen.add(rel)
                found.append(rel)
    return found


# ── file helpers ───────────────────────────────────────────────────────────────

def _same_content(source: Path, target: Path) -> bool:
    with source.open("rb") as src_file, target.open("rb") as dst_file:
        while True:
            src_chunk = src_file.read(_CHUNK_SIZE)
            dst_chunk = dst_file.read(_CHUNK_SIZE)
            if src_chunk != dst_chunk:
                return False
            if not src_chunk:
                return True


def _needs_copy(source: Path, target: Path) -> bool:
    """True when target is missing or differs from source."""
    if source.is_symlink() or target.is_symlink():
        if not (source.is_symlink() and target.is_symlink()):
            return True
        return os.readlink(source) != os.readlink(target)

    try:
        target_stat = target.stat()
    except OSError:
        return True

    source_stat = source.stat()
    if source_stat.st_size != target_stat.st_size:
        return True
    # copy2 preserves mtime, so an identical timestamp means an untouched copy.
    if int(source_stat.st_mtime) == int(target_stat.st_mtime):
        return False
    return not _same_content(source, target)


def _copy_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink() or target.exists():
        if target.is_dir() and not target.is_symlink():
            raise RuntimeError("destination path is a directory")
        target.unlink()
    if source.is_symlink():
        os.symlink(os.readlink(source), target)
    else:
        shutil.copy2(source, target)


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fingerprint(path: Path) -> dict:
    """Describe a file well enough to notice later edits."""
    if path.is_symlink():
        return {"link": os.readlink(path)}
    stat_result = path.stat()
    return {"hash": _file_hash(path), "size": stat_result.st_size, "mtime": stat_result.st_mtime}


def _matches_fingerprint(path: Path, entry: dict | None) -> bool:
    """True when path still looks exactly as it did when entry was recorded."""
    if not entry:
        return False
    if path.is_symlink() or "link" in entry:
        return path.is_symlink() and entry.get("link") == os.readlink(path)
    try:
        stat_result = path.stat()
    except OSError:
        return False
    if stat_result.st_size != entry.get("size"):
        return False
    if int(stat_result.st_mtime) == int(entry.get("mtime", -1)):
        return True
    return _file_hash(path) == entry.get("hash")


def _remove_empty_dirs(directory: Path, root: Path) -> None:
    """Remove directories left empty by a prune, stopping at root."""
    current = directory
    while current != root and root in current.parents:
        try:
            if any(current.iterdir()):
                return
            current.rmdir()
        except OSError:
            return
        current = current.parent


# ── state ──────────────────────────────────────────────────────────────────────

def _state_dir(profile: Profile) -> Path:
    """Per-profile directory holding the manifest, the last run and its backups."""
    git_dir = Path(_git(profile.src, "rev-parse", "--absolute-git-dir").strip())
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", profile.name or "default")
    return git_dir / STATE_DIRNAME / slug


def _load_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Error: cannot read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"Error: {path} must contain a JSON object.")
    return data


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


# ── profile resolution ─────────────────────────────────────────────────────────

def _select_profile(config: dict, requested: str | None, config_path: Path) -> tuple[str, dict]:
    """Pick the requested profile, or the one named by the config's "default" key."""
    profiles = config.get("profiles") or {}
    if not isinstance(profiles, dict):
        raise RuntimeError(f'Error: "profiles" in {config_path} must be a JSON object.')
    if not profiles:
        if requested:
            raise RuntimeError(
                f"Error: {config_path} defines no profiles, so '--profile {requested}' "
                f"cannot be resolved."
            )
        return "", {}

    name = requested or config.get("default")
    if not name:
        raise RuntimeError(f'Error: {config_path} has no "default" profile name; pass --profile.')
    if name not in profiles:
        raise RuntimeError(
            f"Error: unknown profile '{name}'. Available: {', '.join(sorted(profiles))}"
        )

    entry = profiles[name]
    if not isinstance(entry, dict):
        raise RuntimeError(f"Error: profile '{name}' in {config_path} must be a JSON object.")
    unknown = sorted(set(entry) - _PROFILE_KEYS)
    if unknown:
        print(
            f"{YELLOW}Warning: profile '{name}' has unknown keys: {', '.join(unknown)}{RESET}",
            file=sys.stderr,
        )
    return name, entry


def _resolve_destination(raw_path: str, src: Path) -> Path:
    """Expand and validate the destination repository path."""
    dst = Path(raw_path).expanduser()
    dst = (Path.cwd() / dst).resolve() if not dst.is_absolute() else dst.resolve()

    if not dst.is_dir():
        raise RuntimeError(f"Error: destination '{dst}' is not an existing directory.")
    if dst == src:
        raise RuntimeError("Error: destination is the source repository.")
    if dst.is_relative_to(src) or src.is_relative_to(dst):
        raise RuntimeError(f"Error: source '{src}' and destination '{dst}' overlap.")
    if not _is_git_repo(dst):
        print(f"{YELLOW}Warning: '{dst}' is not a git repository.{RESET}", file=sys.stderr)
    return dst


def _special_patterns(entry: dict) -> tuple[str, ...]:
    """Read a profile's special-file patterns; an explicit empty list means none."""
    patterns = entry.get("special_files", DEFAULT_SPECIAL_FILES)
    if patterns is None:
        return DEFAULT_SPECIAL_FILES
    return tuple(str(pattern) for pattern in patterns)


def _no_destination_error(name: str, config_path: Path, config_exists: bool) -> RuntimeError:
    """Explain why no destination could be resolved, and how to supply one."""
    if name:
        reason = f'profile {name!r} in {config_path} has no "dst_repo_path"'
        fix = "set it there, or pass --dest PATH"
    elif not config_exists:
        reason = f"there is no profile file at {config_path}"
        fix = 'create one with a "dst_repo_path", point --config at one, or pass --dest PATH'
    else:
        reason = f"{config_path} defines no profiles"
        fix = 'add one with a "dst_repo_path", or pass --dest PATH'
    return RuntimeError(f"Error: nothing to sync to: {reason}.\n       Fix: {fix}.")


def _build_profile(args: argparse.Namespace, config_path: Path) -> Profile:
    """Merge the profile file with the command line into a single Profile."""
    config_exists = config_path.is_file()
    if not config_exists:
        if args.config:
            raise RuntimeError(f"Error: no profile file at {config_path} (from --config).")
        if args.profile:
            raise RuntimeError(
                f"Error: no profile file at {config_path}, so '--profile {args.profile}' "
                f"cannot be resolved; pass --config PATH if it lives elsewhere."
            )

    config = _load_json(config_path)
    name, entry = _select_profile(config, args.profile, config_path)

    src_raw = args.src or entry.get("src_repo_path")
    src = _resolve_repo(src_raw) if src_raw else _repo_root()

    dst_raw = args.dest or entry.get("dst_repo_path")
    if not dst_raw:
        raise _no_destination_error(name, config_path, config_exists)

    prune = args.prune if args.prune is not None else bool(entry.get("prune", True))
    include_special = (
        args.include_special if args.include_special is not None
        else bool(entry.get("include_special", False))
    )

    return Profile(
        name=name,
        src=src,
        dst=_resolve_destination(str(dst_raw), src),
        prune=prune,
        include_special=include_special,
        special_files=_special_patterns(entry),
        only=tuple(args.only or entry.get("only") or ()),
        exclude=tuple(args.exclude or entry.get("exclude") or ()),
        description=str(entry.get("description") or ""),
    )


# ── sync ───────────────────────────────────────────────────────────────────────

class Syncer:
    """Copy a source repository onto a destination repository, recording every change."""

    # pylint: disable=too-few-public-methods

    def __init__(self, profile: Profile, options: RunOptions) -> None:
        self.profile = profile
        self.options = options
        self.state = _state_dir(profile)
        self.manifest = _load_json(self.state / MANIFEST_FILE)
        self.next_manifest = dict(self.manifest)
        self.actions: list[dict] = []
        self.stats = {key: 0 for key, _, _ in _SUMMARY_ROWS}

    # -- public ----------------------------------------------------------------

    def run(self) -> dict[str, int]:
        """Sync the whole selection and return the summary counters."""
        entries = self._entries()
        if not self.options.dry_run:
            shutil.rmtree(self.state / BACKUP_DIRNAME, ignore_errors=True)

        for rel_path, is_special in entries:
            self._sync_file(rel_path, is_special)

        if self.profile.prune:
            self._prune({rel for rel, _ in entries})

        if not self.options.dry_run:
            self._save_state()
        return self.stats

    # -- selection -------------------------------------------------------------

    def _entries(self) -> list[tuple[Path, bool]]:
        """Return the (relative path, is_special) pairs that make up one sync run."""
        src = self.profile.src
        tracked = (
            _changed_files(src, self.options.since) if self.options.since else _tracked_files(src)
        )
        entries = [(rel, False) for rel in tracked if self._in_scope(rel)]

        if self.profile.include_special:
            known = {rel for rel, _ in entries}
            entries += [
                (rel, True)
                for rel in _special_files(src, self.profile.special_files)
                if rel not in known and self._in_scope(rel)
            ]
        return entries

    def _in_scope(self, rel_path: Path) -> bool:
        if self.profile.only and not _matches_any(rel_path, self.profile.only):
            return False
        return not _matches_any(rel_path, self.profile.exclude)

    def _is_special(self, rel_path: Path) -> bool:
        return _matches_any(rel_path, self.profile.special_files)

    # -- copying ---------------------------------------------------------------

    def _sync_file(self, rel_path: Path, is_special: bool) -> None:
        name = rel_path.as_posix()
        source = self.profile.src / rel_path
        target = self.profile.dst / rel_path

        if not is_special and self._is_special(rel_path):
            self.stats["skipped"] += 1
            self._report(f"  {DIM}·{RESET} {name} {DIM}(special){RESET}")
            return

        if not source.is_file() and not source.is_symlink():
            # Tracked but deleted in the working tree, or a submodule gitlink.
            self.stats["missing"] += 1
            self._report(f"  {YELLOW}?{RESET} {name} {DIM}(not a file in source){RESET}")
            return

        if not _needs_copy(source, target):
            self.stats["unchanged"] += 1
            self.next_manifest[name] = self._reuse_fingerprint(name, target)
            return

        modified = self._modified_at_destination(name, target)
        if modified and not self.options.force:
            self.stats["conflicts"] += 1
            self._report(f"  {YELLOW}!{RESET} {name} {DIM}(edited at destination; use --force){RESET}")
            return

        self._write_file(rel_path, is_special=is_special, stashed=modified)

    def _write_file(self, rel_path: Path, *, is_special: bool, stashed: bool) -> None:
        """Back up the destination file if it exists, then copy the source over it."""
        name = rel_path.as_posix()
        source = self.profile.src / rel_path
        target = self.profile.dst / rel_path
        existed = target.exists() or target.is_symlink()
        try:
            backup = self._backup(rel_path, target) if existed else None
            if not self.options.dry_run:
                _copy_file(source, target)
                self.next_manifest[name] = _fingerprint(target)
        except (OSError, RuntimeError) as exc:
            self.stats["errors"] += 1
            print(f"  {RED}E{RESET} {name}: {exc}", file=sys.stderr)
            return

        self.actions.append({
            "path": name,
            "action": "updated" if existed else "created",
            "backup": backup,
            "stashed": stashed,
        })

        if is_special:
            self.stats["special"] += 1
            marker, color = "*", YELLOW
        elif existed:
            self.stats["updated"] += 1
            marker, color = "~", CYAN
        else:
            self.stats["new"] += 1
            marker, color = "+", GREEN
        note = f" {DIM}(stashed destination edit){RESET}" if stashed else ""
        self._report(f"  {color}{marker}{RESET} {name}{note}")

    # -- pruning ---------------------------------------------------------------

    def _prune(self, source_set: set[Path]) -> None:
        """Delete destination files that the source no longer has."""
        for rel_path in self._prune_candidates(source_set):
            name = rel_path.as_posix()
            target = self.profile.dst / rel_path

            if not target.is_file() and not target.is_symlink():
                self.next_manifest.pop(name, None)
                continue

            if self._modified_at_destination(name, target) and not self.options.force:
                self.stats["conflicts"] += 1
                self._report(f"  {YELLOW}!{RESET} {name} {DIM}(edited at destination; not pruned){RESET}")
                continue

            try:
                backup = self._backup(rel_path, target)
                if not self.options.dry_run:
                    target.unlink()
                    _remove_empty_dirs(target.parent, self.profile.dst)
            except OSError as exc:
                self.stats["errors"] += 1
                print(f"  {RED}E{RESET} {name}: {exc}", file=sys.stderr)
                continue

            self.next_manifest.pop(name, None)
            self.actions.append({"path": name, "action": "deleted", "backup": backup, "stashed": False})
            self.stats["pruned"] += 1
            self._report(f"  {RED}x{RESET} {name}")

    def _prune_candidates(self, source_set: set[Path]) -> list[Path]:
        """Destination files that are safe to consider for deletion."""
        if self.options.since:
            candidates = _deleted_files(self.profile.src, self.options.since)
        else:
            # Only files a previous sync wrote, or files the destination tracks in
            # git, are ever considered: never its untracked or ignored work.
            candidates = {Path(name) for name in self.manifest}
            if _is_git_repo(self.profile.dst):
                candidates |= set(_tracked_files(self.profile.dst))
            candidates -= source_set

        return sorted(
            rel for rel in candidates
            if self._in_scope(rel) and not self._is_special(rel)
        )

    # -- state -----------------------------------------------------------------

    def _modified_at_destination(self, name: str, target: Path) -> bool:
        """True when a previous sync wrote this file and something changed it since."""
        entry = self.manifest.get(name)
        if not entry or not (target.exists() or target.is_symlink()):
            return False
        return not _matches_fingerprint(target, entry)

    def _reuse_fingerprint(self, name: str, target: Path) -> dict:
        """Return the recorded fingerprint when it is still valid, else a fresh one."""
        entry = self.manifest.get(name)
        if entry and _matches_fingerprint(target, entry):
            return entry
        try:
            return _fingerprint(target)
        except OSError:
            return entry or {}

    def _backup(self, rel_path: Path, target: Path) -> str:
        """Keep a copy of a destination file so 'revert' can put it back."""
        backup_path = self.state / BACKUP_DIRNAME / rel_path
        if not self.options.dry_run:
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            if backup_path.is_symlink() or backup_path.exists():
                backup_path.unlink()
            if target.is_symlink():
                os.symlink(os.readlink(target), backup_path)
            else:
                shutil.copy2(target, backup_path)
        return rel_path.as_posix()

    def _save_state(self) -> None:
        _write_json(self.state / MANIFEST_PREV_FILE, self.manifest)
        _write_json(self.state / MANIFEST_FILE, self.next_manifest)
        _write_json(self.state / LAST_RUN_FILE, {
            "profile": self.profile.name or "default",
            "synced_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "src": str(self.profile.src),
            "dst": str(self.profile.dst),
            "since": self.options.since,
            "actions": self.actions,
        })

    def _report(self, line: str) -> None:
        if not self.options.quiet:
            print(line)


# ── revert ─────────────────────────────────────────────────────────────────────

def _revert_entry(entry: dict, dst: Path, state: Path, options: RunOptions) -> tuple[str, str]:
    """Undo one recorded action and return its (summary key, report line)."""
    name = str(entry.get("path", ""))
    rel_path = Path(name)
    target = dst / rel_path
    action = entry.get("action")
    exists = target.exists() or target.is_symlink()

    if action == "created":
        if not exists:
            return "missing", f"  {DIM}·{RESET} {name} {DIM}(already gone){RESET}"
        if not options.dry_run:
            target.unlink()
            _remove_empty_dirs(target.parent, dst)
        return "removed", f"  {RED}x{RESET} {name}"

    backup = state / BACKUP_DIRNAME / rel_path
    if not (backup.is_file() or backup.is_symlink()):
        return "missing", f"  {YELLOW}?{RESET} {name} {DIM}(no stored copy){RESET}"
    if not options.dry_run:
        _copy_file(backup, target)
    note = f" {DIM}(destination edit){RESET}" if entry.get("stashed") else ""
    return "restored", f"  {GREEN}<{RESET} {name}{note}"


def revert(profile: Profile, options: RunOptions) -> dict[str, int]:
    """Undo the last recorded sync for this profile."""
    state = _state_dir(profile)
    record = _load_json(state / LAST_RUN_FILE)
    if not record:
        raise RuntimeError(f"Error: no sync recorded for profile '{profile.name or 'default'}'.")
    if record.get("reverted_at"):
        raise RuntimeError(
            f"Error: the sync of {record.get('synced_at', 'unknown time')} was already "
            f"reverted at {record['reverted_at']}."
        )

    dst = Path(record.get("dst", profile.dst))
    if not dst.is_dir():
        raise RuntimeError(f"Error: destination '{dst}' is not an existing directory.")

    stats = {key: 0 for key, _, _ in _SUMMARY_ROWS}
    manifest = _load_json(state / MANIFEST_FILE)

    for entry in reversed(record.get("actions", [])):
        name = str(entry.get("path", ""))
        target = dst / name
        if (target.exists() or target.is_symlink()) and not options.force:
            if not _matches_fingerprint(target, manifest.get(name)) and entry.get("action") != "deleted":
                stats["conflicts"] += 1
                if not options.quiet:
                    print(f"  {YELLOW}!{RESET} {name} {DIM}(changed since the sync; use --force){RESET}")
                continue
        try:
            key, line = _revert_entry(entry, dst, state, options)
        except (OSError, RuntimeError) as exc:
            stats["errors"] += 1
            print(f"  {RED}E{RESET} {name}: {exc}", file=sys.stderr)
            continue
        stats[key] += 1
        if line and not options.quiet:
            print(line)

    if not options.dry_run:
        _write_json(state / MANIFEST_FILE, _load_json(state / MANIFEST_PREV_FILE))
        record["reverted_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        _write_json(state / LAST_RUN_FILE, record)
    return stats


# ── reporting ──────────────────────────────────────────────────────────────────

def _print_header(profile: Profile, options: RunOptions, command: str) -> None:
    label = profile.name or "(command line only)"
    if profile.description:
        label = f"{label}  {DIM}{profile.description}{RESET}"
    modes = [
        "prune" if profile.prune else "no-prune",
        "special files" if profile.include_special else "no special files",
    ]
    if options.since:
        modes.append(f"since {options.since}")
    if options.force:
        modes.append("force")
    if profile.only:
        modes.append(f"only {' '.join(profile.only)}")
    if profile.exclude:
        modes.append(f"exclude {' '.join(profile.exclude)}")

    print(f"{BOLD}Profile:{RESET}     {label}")
    print(f"{BOLD}Source:{RESET}      {profile.src}")
    print(f"{BOLD}Destination:{RESET} {profile.dst}")
    print(f"{BOLD}Mode:{RESET}        {command} · {' · '.join(modes)}")
    if options.dry_run:
        print(f"{DIM}Dry run — nothing will be written.{RESET}")
    print()


def _print_summary(stats: dict[str, int], options: RunOptions, command: str) -> None:
    rows = [(label, str(stats[key]), color) for key, label, color in _SUMMARY_ROWS if stats[key]]

    print()
    if not rows:
        print(f"{DIM}  Nothing to {command}.{RESET}\n")
        return

    label_width = max(len(row[0]) for row in rows)
    value_width = max(len(row[1]) for row in rows)
    title = f"{command.capitalize()} {'Preview' if options.dry_run else 'Summary'}"
    full_width = max(label_width + value_width + 3, len(title) + 2)

    print(f"{BOLD}┌{'─' * full_width}┐{RESET}")
    print(f"{BOLD}│{title.center(full_width)}│{RESET}")
    print(f"├{'─' * full_width}┤")
    for label, value, color in rows:
        padding = full_width - label_width - value_width - 3
        print(f"│ {color}{label.ljust(label_width)}{RESET} {value.rjust(value_width + padding)} │")
    print(f"└{'─' * full_width}┘")
    print()


# ── main ───────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    """Build the command line parser and parse sys.argv."""
    parser = argparse.ArgumentParser(
        description="Sync git-tracked files from one local repository to another."
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=("sync", "revert"),
        default="sync",
        help="sync the destination (default), or revert the last sync",
    )
    parser.add_argument("-p", "--profile", help=f"profile from {CONFIG_FILENAME}")
    parser.add_argument("-c", "--config", help=f"profile file to read (default: <repo>/{CONFIG_FILENAME})")
    parser.add_argument("--src", help="override the profile's src_repo_path")
    parser.add_argument("-d", "--dest", help="override the profile's dst_repo_path")
    parser.add_argument(
        "-s", "--include-special",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="copy special files such as .env (default: from the profile)",
    )
    parser.add_argument(
        "--prune",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="delete destination files removed from the source (default: on)",
    )
    parser.add_argument("--only", nargs="+", metavar="GLOB", help="sync only these paths")
    parser.add_argument("--exclude", nargs="+", metavar="GLOB", help="skip these paths")
    parser.add_argument("--since", metavar="REF", help="sync only files changed since a git ref")
    parser.add_argument(
        "-f", "--force",
        action="store_true",
        help="overwrite (and stash) destination files edited since the last sync",
    )
    parser.add_argument("-n", "--dry-run", action="store_true", help="write nothing")
    parser.add_argument("-q", "--quiet", action="store_true", help="print only the summary")
    return parser.parse_args()


def main() -> int:
    """Entry point: resolve the profile, run the command, and report the result."""
    args = _parse_args()
    options = RunOptions(
        since=args.since, force=args.force, dry_run=args.dry_run, quiet=args.quiet
    )

    try:
        config_path = (
            Path(args.config).expanduser() if args.config else _repo_root() / CONFIG_FILENAME
        )
        profile = _build_profile(args, config_path)
        _print_header(profile, options, args.command)
        stats = revert(profile, options) if args.command == "revert" else Syncer(profile, options).run()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    _print_summary(stats, options, args.command)

    if stats["errors"]:
        print(f"{RED}{args.command.capitalize()} completed with {stats['errors']} error(s).{RESET}",
              file=sys.stderr)
        return 1
    if stats["conflicts"]:
        print(f"{YELLOW}{stats['conflicts']} file(s) skipped as conflicts; "
              f"re-run with --force to overwrite them.{RESET}", file=sys.stderr)
        return 2
    if options.dry_run:
        print(f"{GREEN}Dry run complete.{RESET}")
    else:
        print(f"{GREEN}{args.command.capitalize()} completed successfully.{RESET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
