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

This module is a *policy and detector*. Mock runners and the OpenAI-compatible runner write
only through ScopedWorkspace, which enforces the policy at write time. Real Claude Code and
Codex runners are contained by bubblewrap (sandbox.py), whose mounts are derived from this
same policy; the Git coordinator re-checks every committed path against it.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Iterable

from .config import FULL_PROJECT, ProjectConfig
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


@lru_cache(maxsize=None)
def _regex(pattern: str) -> re.Pattern[str]:
    any_depth = pattern.startswith("**/")
    body = pattern[3:] if any_depth else pattern
    subtree = body.endswith("/**")
    body = body[:-3] if subtree else body
    parts = [re.escape(seg).replace(r"\*", "[^/]*") for seg in body.split("/")]
    prefix = "(?:.*/)?" if any_depth else ""
    return re.compile(prefix + "/".join(parts) + ("(/.*)?" if subtree else "") + r"\Z")


def matches(pattern: str, rel: str) -> bool:
    """True if the repository-relative POSIX path *rel* matches *pattern*."""
    return bool(_regex(pattern).match(rel))


def _matches_any(patterns: Iterable[str], rel: str) -> bool:
    return any(matches(p, rel) for p in patterns)


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

    def owner_of(self, rel: str) -> str | None:
        """The role owning *rel*, or None (protected paths have no owner)."""
        if _matches_any(self.config.protected, rel):
            return None
        for name, role in self.config.roles.items():
            if _matches_any(role.owned, rel) and not _matches_any(role.excluded, rel):
                return name
        return None

    def validate_grants(self, grants: Iterable[str]) -> tuple[str, ...]:
        """Grants are only valid for approval-required paths, never for protected ones."""
        checked = []
        for grant in grants:
            if _matches_any(self.config.protected, grant.rstrip("/*") or grant):
                raise ScopeViolationError(f"grant {grant!r} targets a protected path")
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
            if name == role and _matches_any(cfg.excluded, rel):
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
        for rel in dict.fromkeys((lexical, resolved)):
            if _matches_any(self.config.protected, rel):
                return PathDecision(requested, lexical, False, f"{rel!r} is protected (never writable)")
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
        """Read a file inside the repository (reads are not scope-limited; `.env*` is refused)."""
        lexical, _resolved = self.policy.normalize(path)
        name = PurePosixPath(lexical).name
        if name == ".env" or name.startswith(".env."):
            raise ScopeViolationError(f"{path!r} may contain secrets and is never read by agents")
        return (self.policy.root / lexical).read_text(encoding="utf-8")

    def _record(self, rel: str) -> None:
        if rel not in self.changed:
            self.changed.append(rel)
