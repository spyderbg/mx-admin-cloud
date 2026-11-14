"""Deny-by-default write-scope policy (v5 §3.2) and a scoped workspace for mock runners.

A path is writable by a role only when all of these hold:

  1. it is inside the repository after lexical normalization (no `..` escape);
  2. after resolving symlinks it is still inside the repository and still owned by the
     same role (a symlink cannot launder a write into another role's tree);
  3. the path itself is not a symlink (symlink entries are never committed);
  4. it is not protected (`.git/**`, `.env*`, `content/**`, `data/**`,
     `backend/data/**`, the independent `mxfix`/`mxissues` trees) — no grant overrides this;
  5. it is owned by the role (and not in the role's exclusions), or it matches an explicit
     task-level grant for an approval-required path.

Everything else is refused. Generated outputs follow destination ownership: the
generator's role does not gain write access to the files it generates.

Rules 4 and 5 judge the path AND EVERY ANCESTOR DIRECTORY, for both the lexical and the resolved
path: `backend/src/.env/secret.py` is protected through `backend/src/.env` (`**/.env`) although no
pattern matches the full path, so a protected name used as a directory cannot launder the files
below it into an otherwise writable role tree (likewise an excluded path). Git's own directory
`.git` is protected at ANY depth and in every alias Git itself refuses as a path component (any
case, trailing dots or spaces, HFS+-ignorable code points, the NTFS short name `git~N`, NTFS
stream suffixes; is_git_dir_name), whatever the configuration lists.

This module is a *policy and detector*. Mock runners and the OpenAI-compatible runner write
only through ScopedWorkspace, which enforces the policy at write time. Real Claude Code and
Codex runners are contained by bubblewrap (sandbox.py), whose mounts are derived from this
same policy; the Git coordinator re-checks every committed path against it.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable

from .config import FULL_PROJECT, ProjectConfig, matches
from .errors import ScopeViolationError

GENERATED_OUTPUTS: tuple[tuple[str, str], ...] = (
    ("frontend/src/types/*Schemas.ts", "scripts/generate_ts_schemas.py"),
    ("frontend/src/api/*Client.ts", "scripts/generate_ts_client.py"),
    ("frontend/src/mocks/*Handlers.ts", "scripts/generate_ts_client.py"),
    ("frontend/src/data/mockData.ts", "scripts/generate_mockdata.py"),
    ("backend/src/services/*/openapi.yaml", "scripts/export_openapi.py"),
    ("backend/docs/openapi.yaml", "scripts/export_openapi.py --all"),
    ("docs_agents/backend/services/*/openapi.yaml", "scripts/export_openapi.py --docs-agents"),
    ("docs_agents/backend/api/openapi.yaml", "scripts/export_openapi.py --docs-agents"),
)


GIT_DIR_NAME = ".git"
# HFS+ ignores these code points in names (zero-width and bidirectional controls): a name containing one can
# alias `.git` or display as another name. Written as code points so this source contains no invisible text.
HFS_IGNORABLE = frozenset(chr(c) for c in (0x200C, 0x200D, 0x200E, 0x200F, 0x202A, 0x202B, 0x202C, 0x202D,
                                           0x202E, 0x206A, 0x206B, 0x206C, 0x206D, 0x206E, 0x206F, 0xFEFF))
_NTFS_SHORT_GIT = re.compile(r"git~\d+")


def _matches_any(patterns: Iterable[str], rel: str) -> bool:
    return any(matches(p, rel) for p in patterns)


def path_and_ancestors(rel: str) -> list[str]:
    """`a/b/c` → [`a`, `a/b`, `a/b/c`]: the path and every directory that contains it."""
    parts = rel.split("/")
    return ["/".join(parts[:index]) for index in range(1, len(parts) + 1)]


def is_git_dir_name(name: str) -> bool:
    """True for a name that is, or aliases, Git's own directory `.git`: in any case, with trailing dots or spaces,
    with code points HFS+ ignores, as the NTFS short name `git~N`, or any of these followed by an NTFS stream
    suffix (`.git:foo`, `.git::$INDEX_ALLOCATION`, `git~1:x`). Git refuses each as a path component (they alias
    `.git` on case-insensitive, macOS and Windows file systems). The ONE rule for the Worker's PathPolicy, its
    file-system inventory and the W4.7 integration inspection (integration_paths.name_problem)."""
    stripped = "".join(ch for ch in name if ch not in HFS_IGNORABLE).split(":", 1)[0].rstrip(". ").lower()
    return stripped == GIT_DIR_NAME or _NTFS_SHORT_GIT.fullmatch(stripped) is not None


def matching_prefix(patterns: Iterable[str], rel: str) -> str | None:
    """The shortest of *rel* and its ancestor directories that matches one of *patterns* (None: none does)."""
    patterns = tuple(patterns)
    return next((prefix for prefix in path_and_ancestors(rel) if _matches_any(patterns, prefix)), None)


def protected_prefix(protected: Iterable[str], rel: str) -> str | None:
    """The shortest of *rel* and its ancestor directories that is protected — it matches one of the
    *protected* patterns or is Git's own directory — or None. Everything below a protected path is protected."""
    protected = tuple(protected)
    return next((prefix for prefix in path_and_ancestors(rel)
                 if is_git_dir_name(prefix.rsplit("/", 1)[-1]) or _matches_any(protected, prefix)), None)


def secret_prefix(patterns: Iterable[str], exempt_suffixes: Iterable[str], rel: str) -> str | None:
    """The shortest of *rel* and its ancestor directories that matches a secret pattern WITHOUT an exempt suffix, or
    None. Each component is judged on its own: an exemption applies only to the name it matches, so an exempt
    directory never exempts what it contains — `backend/.env.example/.env` is secret through its inner `.env`
    although the exempt `backend/.env.example` matches first."""
    patterns, exempt = tuple(patterns), tuple(exempt_suffixes)
    return next((prefix for prefix in path_and_ancestors(rel)
                 if not prefix.endswith(exempt) and _matches_any(patterns, prefix)), None)


@dataclass(frozen=True)
class PathDecision:
    """The outcome of one write check."""

    requested: str
    path: str  # normalized repository-relative POSIX path ('' when not normalizable)
    allowed: bool
    reason: str
    owner: str | None = None


class PathPolicy:
    """Write-scope checks for one repository checkout."""

    def __init__(self, repo_root: Path, config: ProjectConfig) -> None:
        self.root = Path(os.path.abspath(repo_root))
        self.real_root = self.root.resolve()
        self.config = config

    # -- normalization -------------------------------------------------------

    def normalize(self, path: str | os.PathLike[str]) -> tuple[str, str]:
        """(lexical_rel, resolved_rel) for *path*, or ScopeViolationError if it escapes the repo."""
        text = os.fspath(path)
        if not text or "\x00" in text:
            raise ScopeViolationError(f"invalid path {text!r}")
        candidate = Path(text)
        joined = candidate if candidate.is_absolute() else self.root / candidate
        lexical = Path(os.path.normpath(joined))
        try:
            lexical_rel = lexical.relative_to(self.root).as_posix()
        except ValueError as exc:
            raise ScopeViolationError(f"{text!r} escapes the repository (lexical traversal)") from exc
        if lexical_rel in ("", "."):
            raise ScopeViolationError(f"{text!r} is the repository root, not a file")
        resolved = (self.root / lexical_rel).resolve(strict=False)
        try:
            resolved_rel = resolved.relative_to(self.real_root).as_posix()
        except ValueError as exc:
            raise ScopeViolationError(f"{text!r} resolves outside the repository (symlink escape)") from exc
        return lexical_rel, resolved_rel

    # -- ownership -----------------------------------------------------------

    def protected_by(self, rel: str) -> str | None:
        """The shortest of *rel* and its ancestor directories that is protected (None: *rel* is not protected)."""
        return protected_prefix(self.config.protected, rel)

    def owner_of(self, rel: str) -> str | None:
        """The role owning *rel*, or None (a protected path, or one inside a protected directory, has no owner;
        neither has a path that is, or lies inside, one of the owning role's exclusions)."""
        if self.protected_by(rel) is not None:
            return None
        for name, role in self.config.roles.items():
            if _matches_any(role.owned, rel) and matching_prefix(role.excluded, rel) is None:
                return name
        return None

    def validate_grants(self, grants: Iterable[str]) -> tuple[str, ...]:
        """Grants are only valid for approval-required paths, never for protected ones (nor for anything inside
        a protected directory). check() refuses protected paths before it looks at grants, whatever was granted."""
        checked = []
        for grant in grants:
            target = grant.rstrip("/*") or grant
            through = self.protected_by(target)
            if through is not None:
                inside = f" (inside {through!r})" if through != target else ""
                raise ScopeViolationError(f"grant {grant!r} targets a protected path{inside}")
            if grant not in self.config.approval_required and not _matches_any(self.config.approval_required, grant):
                raise ScopeViolationError(f"grant {grant!r} is not an approval-required path")
            checked.append(grant)
        return tuple(checked)

    def _deny_reason(self, role: str, rel: str) -> str:
        owner = self.owner_of(rel)
        generated = next((gen for pattern, gen in GENERATED_OUTPUTS if matches(pattern, rel)), None)
        suffix = f" (generated output of {generated}; destination ownership applies)" if generated else ""
        if owner is not None:
            return f"owned by role {owner!r}, not {role!r}{suffix}"
        if _matches_any(self.config.approval_required, rel):
            return "requires an explicit task-specific approval grant"
        for name, cfg in self.config.roles.items():
            if name == role and matching_prefix(cfg.excluded, rel) is not None:
                return f"excluded from role {role!r}"
        return f"not owned by any role (deny-by-default){suffix}"

    def check(self, role: str, path: str | os.PathLike[str], *, grants: Iterable[str] = ()) -> PathDecision:
        """Decide whether *role* may write *path*."""
        requested = os.fspath(path)
        try:
            lexical, resolved = self.normalize(requested)
        except ScopeViolationError as exc:
            return PathDecision(requested, "", False, str(exc))
        grants = tuple(grants)
        for rel in dict.fromkeys((lexical, resolved)):  # the path and every ancestor, lexical and resolved
            through = self.protected_by(rel)
            if through is not None:
                inside = f" (it lies inside the protected path {through!r})" if through != rel else ""
                return PathDecision(requested, lexical, False, f"{rel!r} is protected{inside} (never writable)")
        if (self.root / lexical).is_symlink():
            return PathDecision(requested, lexical, False, "symlink entries are not allowed")
        if role != FULL_PROJECT and role not in self.config.roles:
            return PathDecision(requested, lexical, False, f"unknown role {role!r}")
        for rel in dict.fromkeys((lexical, resolved)):
            granted = _matches_any(grants, rel) and _matches_any(self.config.approval_required, rel)
            if not granted and (role == FULL_PROJECT or self.owner_of(rel) != role):
                reason = self._deny_reason(role, rel)
                if rel != lexical:
                    reason = f"resolves via symlink to {rel!r}, {reason}"
                return PathDecision(requested, lexical, False, reason, self.owner_of(rel))
        return PathDecision(requested, lexical, True, "allowed", self.owner_of(lexical))

    def require(self, role: str, paths: Iterable[str], *, grants: Iterable[str] = ()) -> list[str]:
        """Normalized paths if every one is allowed; otherwise ScopeViolationError listing denials."""
        grants = self.validate_grants(grants)
        decisions = [self.check(role, p, grants=grants) for p in paths]
        denied = [d for d in decisions if not d.allowed]
        if denied:
            details = "; ".join(f"{d.requested}: {d.reason}" for d in denied)
            raise ScopeViolationError(f"role {role!r} may not write: {details}")
        return list(dict.fromkeys(d.path for d in decisions))


class ScopedWorkspace:
    """File writes on behalf of one role, refused unless the policy allows them.

    Stands in for OS-level containment in Phase 2: mock runners never touch the file
    system directly.
    """

    def __init__(self, policy: PathPolicy, role: str, *, grants: Iterable[str] = ()) -> None:
        self.policy = policy
        self.role = role
        self.grants = policy.validate_grants(grants)
        self.changed: list[str] = []

    def _target(self, path: str) -> tuple[str, Path]:
        decision = self.policy.check(self.role, path, grants=self.grants)
        if not decision.allowed:
            raise ScopeViolationError(f"role {self.role!r} may not write {path!r}: {decision.reason}")
        return decision.path, self.policy.root / decision.path

    def write_text(self, path: str, content: str) -> str:
        """Write *content* to *path* (creating parent directories inside the scope)."""
        rel, target = self._target(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        self._record(rel)
        return rel

    def delete(self, path: str) -> str:
        """Delete *path* if it exists."""
        rel, target = self._target(path)
        if target.exists():
            target.unlink()
        self._record(rel)
        return rel

    def read_text(self, path: str) -> str:
        """Read a file inside the repository (reads are not scope-limited; `.env*` is refused — the file itself,
        anything inside a `.env*` directory, and a symlink that resolves to either)."""
        lexical, resolved = self.policy.normalize(path)
        for rel in dict.fromkeys((lexical, resolved)):
            if any(name == ".env" or name.startswith(".env.") for name in PurePosixPath(rel).parts):
                raise ScopeViolationError(f"{path!r} may contain secrets and is never read by agents")
        return (self.policy.root / lexical).read_text(encoding="utf-8")

    def _record(self, rel: str) -> None:
        if rel not in self.changed:
            self.changed.append(rel)
