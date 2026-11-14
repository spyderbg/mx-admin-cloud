"""OS-level containment with bubblewrap (Phase 3; architecture §11.6, layer 1; network W1, D-21).

Every real runner and every gate runs inside `bwrap`:

  * new mount, PID, IPC, UTS, cgroup, user AND network namespaces (`--unshare-all`). The
    host network is never shared (there is no `--share-net` path in this module): a sandbox
    has only its own loopback interface, so it cannot reach the internet, the LAN, services
    on the host's localhost, or host abstract Unix sockets such as the X11 display. A runner
    reaches its model only through the broker endpoint that netns_channel.py puts into its
    namespace; gates have no endpoint at all;
  * the sandbox dies with its supervisor (`--die-with-parent`) and gets its own session
    (`--new-session`, no terminal injection); the environment is cleared and rebuilt;
  * /usr and /etc read-only; a private HOME and private /tmp; the rest of the user's home
    — including the coordinator state directory and every credential (runner logins, SSH,
    GitHub, browser profiles) — is not mounted. Nothing is mounted to authenticate a runner
    (D-20): the upstream credential stays with the host-side broker;
  * the repository root is a private SHADOW directory. Every top-level entry of the real
    checkout is bind-mounted into it read-only; then only the role's writable roots are
    bind-mounted read-write on top; then everything inside those roots that the role must
    not write (exclusions, protected and approval-required paths, nested `.git`) is
    re-mounted read-only. `.git` is always read-only: only the Git coordinator mutates it;
  * secret files (e.g. `.env`) are not mounted at all (top level) or are masked with /dev/null
    (nested). Which files are secret is decided by the SECURITY POLICY alone (`runtime.secret_patterns`,
    fsguard) — never by whether Git tracks or stages the file, and never by the workspace baseline
    (workspace_hold.py): a staged, tracked or operator-accepted secret is masked like any other.
    (What a mask cannot hide: content that is in Git's object store — committed history, or a blob
    written by `git add` — stays readable through the read-only `.git`; see worker docs §25.)

The mount points are pre-created in the shadow on the host, so the shadow itself is mounted
read-only for Claude Code and for gates: any write outside the writable roots fails with
EROFS. Codex's own sandbox (itself bubblewrap) must create guard mount points in its working
root, so for Codex the shadow is writable; anything written there never reaches the checkout
and is reported as a stray write (the run is then rejected).

A protected path is protected with everything below it (PathPolicy judges every ancestor): an
EXISTING protected directory inside a writable root (`backend/src/.env/`, a nested `.git/`) — or a
directory at an excluded exact path (`scripts/deploy.sh/`) — is re-mounted read-only as a whole, so
its files can neither be changed nor joined by new ones. Which entries exist comes from the bounded
file-system inventory (fsguard.py), which also reads the dependency and build trees the role-specific
walk below skips for speed (PRUNE_DIRS): a protected directory or secret file below `node_modules/`,
`.venv/` or `dist/` is covered as well. Secret files are judged component by component: a file inside
an exempt directory (`backend/.env.example/.env`) is still masked.

FRESHNESS (Security S1 round 3). An inventory is a fact about one moment, so the builder keeps none:
every plan() takes its own verified scan and records it in the plan (`plan.inventory`), and that one
inventory decides the plan's overlays AND masks. Before the sandbox starts, the runner compares the
plan's inventory with another fresh scan (`stale()`); if a sensitive entry appeared or changed in
between, the plan is discarded and rebuilt, and after a few attempts the run is refused (fail closed).

The OS layer cannot stop a role from *creating* a NEW file or directory with a protected name
inside its own root (there is nothing to mount over yet). That is DETECTED only after the run, by
comparing the inventory before and after it (Git-ignored entries included; the run is rejected and
the checkout put on hold, see workspace_hold.py), and the commit gate (PathPolicy) refuses it.
Post-run detection does not prevent the agent from creating, reading or changing such a new path
while it runs.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from .config import ProjectConfig
from .errors import CoordinatorError
from .fsguard import Inventory, compare, scan
from .paths import PathPolicy, matches

PRUNE_DIRS = frozenset({"node_modules", ".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache", "dist"})
SYSTEM_DIRS = ("/bin", "/sbin", "/lib", "/lib32", "/lib64", "/libx32")


class SandboxUnavailableError(CoordinatorError):
    """bubblewrap is missing or cannot create namespaces on this host."""


@dataclass(frozen=True)
class Bind:
    """One mount inside the sandbox: `ro`, `rw`, or `devnull` (mask a file)."""

    source: str
    dest: str
    mode: str


@dataclass
class SandboxPlan:  # pylint: disable=too-many-instance-attributes
    """A fully resolved sandbox for one process."""

    bwrap: str
    repo_root: Path
    shadow: Path
    shadow_writable: bool
    home: Path
    binds: list[Bind]
    env: dict[str, str]
    cwd: Path
    top_level: frozenset[str]
    writable: tuple[str, ...]
    readonly_overlays: tuple[str, ...]
    masked: tuple[str, ...]
    hidden: tuple[str, ...]
    extra: list[Bind] = field(default_factory=list)
    hidden_dirs: tuple[str, ...] = ()
    inventory: Inventory | None = None   # the verified scan this plan's overlays and masks were built from

    def argv(self, command: Sequence[str]) -> list[str]:
        """The complete bwrap command line running *command*."""
        args = [self.bwrap, "--die-with-parent", "--new-session", "--unshare-all", "--ro-bind", "/usr", "/usr"]
        for path in SYSTEM_DIRS:
            if os.path.islink(path):
                args += ["--symlink", os.readlink(path), path]
            elif os.path.isdir(path):
                args += ["--ro-bind", path, path]
        args += ["--ro-bind", "/etc", "/etc"]
        args += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--dir", "/var", "--tmpfs", "/var/tmp"]
        args += ["--bind", str(self.home), os.environ.get("HOME", str(self.home))]
        for bind in self.extra:
            args += _bind_args(bind)
        for path in self.hidden_dirs:
            args += ["--tmpfs", path]
        args += ["--bind" if self.shadow_writable else "--ro-bind", str(self.shadow), str(self.repo_root)]
        for bind in self.binds:
            args += _bind_args(bind)
        args += ["--remount-ro", "/", "--chdir", str(self.cwd), "--clearenv"]
        for key, value in sorted(self.env.items()):
            args += ["--setenv", key, value]
        return args + list(command)

    def stray_writes(self) -> tuple[list[str], list[str]]:
        """(strays, residue) written into the shadow root, outside every writable root.

        `strays` are files with content (or non-empty directories): the run wrote outside its
        scope. `residue` are empty files/directories, e.g. mount points a nested sandbox left."""
        strays: list[str] = []
        residue: list[str] = []
        for entry in sorted(self.shadow.iterdir()):
            if entry.name in self.top_level:
                continue
            if entry.is_dir() and not entry.is_symlink():
                content = [str(p.relative_to(self.shadow)) for p in sorted(entry.rglob("*"))
                           if p.is_symlink() or not p.is_dir()]
                (strays if any((self.shadow / c).is_symlink() or (self.shadow / c).stat().st_size for c in content)
                 else residue).extend(content or [entry.name + "/"])
            elif entry.is_symlink() or entry.stat().st_size:
                strays.append(entry.name)
            else:
                residue.append(entry.name)
        return strays, residue

    def discard_shadow(self) -> None:
        """Remove the shadow directory (call only after the sandbox has exited)."""
        shutil.rmtree(self.shadow, ignore_errors=True)


def _bind_args(bind: Bind) -> list[str]:
    if bind.mode == "rw":
        return ["--bind", bind.source, bind.dest]
    if bind.mode == "ro":
        return ["--ro-bind", bind.source, bind.dest]
    if bind.mode == "ro-try":
        return ["--ro-bind-try", bind.source, bind.dest]
    if bind.mode == "devnull":
        return ["--ro-bind", "/dev/null", bind.dest]
    raise ValueError(f"unknown bind mode {bind.mode!r}")


def find_bwrap() -> str | None:
    """The bubblewrap executable, if installed."""
    return shutil.which("bwrap")


def bwrap_version(bwrap: str) -> str:
    """`bwrap --version` output."""
    result = subprocess.run([bwrap, "--version"], capture_output=True, text=True, check=False, timeout=10)
    return result.stdout.strip() or result.stderr.strip()


def check_bwrap(bwrap: str | None = None) -> str:
    """Return the bwrap path after proving it can create namespaces; raise otherwise (fail closed)."""
    bwrap = bwrap or find_bwrap()
    if not bwrap:
        raise SandboxUnavailableError("bubblewrap (bwrap) is not installed; real runners and gates refuse to start")
    probe = [bwrap, "--die-with-parent", "--unshare-all", "--ro-bind", "/usr", "/usr"]
    for path in SYSTEM_DIRS:
        if os.path.islink(path):
            probe += ["--symlink", os.readlink(path), path]
    try:
        result = subprocess.run([*probe, "--proc", "/proc", "--dev", "/dev", "/usr/bin/true"],
                                capture_output=True, text=True, check=False, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SandboxUnavailableError(f"bwrap cannot be executed: {exc}") from exc
    if result.returncode != 0:
        raise SandboxUnavailableError(f"bwrap cannot create namespaces here: {result.stderr.strip()}")
    return bwrap


class SandboxBuilder:
    """Builds sandbox plans for one checkout, from the same PathPolicy the commit gate uses."""

    def __init__(self, repo_root: Path, config: ProjectConfig, policy: PathPolicy | None = None,
                 *, bwrap: str | None = None) -> None:
        self.root = Path(repo_root).resolve()
        self.config = config
        self.policy = policy or PathPolicy(self.root, config)
        self.bwrap = bwrap or find_bwrap() or "bwrap"

    def inventory(self) -> Inventory:
        """A FRESH verified scan of the checkout's sensitive entries (fsguard). Never cached: an inventory is a
        fact about one moment (InventoryError: no complete scan — callers fail closed)."""
        return scan(self.root, self.config)

    def stale(self, plan: SandboxPlan) -> list[str]:
        """The sensitive entries that differ between *plan*'s inventory and the checkout NOW (a fresh scan): not
        empty means the plan's masks and overlays may no longer cover the checkout — rebuild it, never launch it."""
        if plan.inventory is None:
            return ["<the plan carries no inventory>"]
        return [change.path for change in compare(plan.inventory, self.inventory())]

    # -- what is writable -------------------------------------------------------

    def writable_roots(self, role: str, grants: Sequence[str] = ()) -> list[str]:
        """Existing repository-relative paths the role may write (owned roots + granted paths)."""
        role_cfg = self.config.role(role)
        roots: list[str] = []
        for pattern in (*role_cfg.owned, *grants):
            for rel in self._expand(pattern):
                if self.policy.check(role, rel if (self.root / rel).is_file() else f"{rel}/.probe",
                                     grants=grants).allowed:
                    roots.append(rel)
        return sorted(set(roots))

    def _expand(self, pattern: str) -> list[str]:
        base = pattern[:-3] if pattern.endswith("/**") else pattern
        if "*" not in base:
            return [base] if (self.root / base).exists() and not (self.root / base).is_symlink() else []
        return sorted(str(p.relative_to(self.root)) for p in self.root.glob(base) if not p.is_symlink())

    def _denied_subtree(self, role: str, rel_dir: str, grants: Sequence[str]) -> bool:
        role_cfg = self.config.role(role)
        granted = [g for g in grants if matches(g, rel_dir) or matches(g, f"{rel_dir}/.probe")]
        patterns = [*role_cfg.excluded, *(p for p in self.config.approval_required if not granted)]
        # A protected directory is read-only AS A WHOLE, whichever protected pattern names it (`**/.env` and
        # `**/.env.*` name directories too) and Git's own directory at any depth: everything below it is
        # protected (PathPolicy), so the role may neither change its files nor create new ones in it. Likewise a
        # directory an excluded or approval-required pattern names — `<dir>/**`, or an exact path used as a
        # directory (`scripts/deploy.sh/`): PathPolicy refuses everything inside it.
        return self.policy.protected_by(rel_dir) is not None or any(matches(p, rel_dir) for p in patterns)

    def readonly_overlays(self, role: str, roots: Iterable[str], grants: Sequence[str] = (),
                          inventory: Inventory | None = None) -> list[str]:
        """Paths inside *roots* that must be re-mounted read-only for *role* (*inventory*: the scan to use — the
        plan's own; none: a fresh one)."""
        roots = list(roots)
        inventory = inventory or self.inventory()
        overlays: list[str] = []
        for rel_root in roots:
            top = self.root / rel_root
            if top.is_file():
                continue
            for dirpath, dirnames, filenames in os.walk(top):
                rel_dir = os.path.relpath(dirpath, self.root)
                keep = []
                for name in sorted(dirnames):
                    rel = f"{rel_dir}/{name}"
                    if os.path.islink(os.path.join(dirpath, name)):
                        continue  # writes through a symlink hit the mount of its target
                    if self._denied_subtree(role, rel, grants):
                        overlays.append(rel)
                    elif name not in PRUNE_DIRS:
                        keep.append(name)
                dirnames[:] = keep
                for name in sorted(filenames):
                    rel = f"{rel_dir}/{name}"
                    if os.path.islink(os.path.join(dirpath, name)):
                        continue
                    if not self.policy.check(role, rel, grants=grants).allowed:
                        overlays.append(rel)
        return self._inventory_overlays(role, roots, grants, overlays, inventory)

    def _inventory_overlays(self, role: str, roots: Iterable[str], grants: Sequence[str],  # pylint: disable=too-many-arguments
                            overlays: list[str], inventory: Inventory) -> list[str]:
        """*overlays* plus every sensitive entry of the inventory inside *roots* the role may not write and no
        overlay covers yet — the walk above skips the PRUNE_DIRS trees (`backend/node_modules/x/.git/`,
        `backend/.venv/.env/`); the inventory does not. Parents sort before their children."""
        roots = tuple(r for r in roots if (self.root / r).is_dir())
        covered = set(overlays)
        for rel, entry in sorted(inventory.entries.items()):
            if entry.kind in ("symlink", "vanished") or not any(rel.startswith(f"{r}/") for r in roots):
                continue
            parts = rel.split("/")
            if any("/".join(parts[:i]) in covered for i in range(1, len(parts) + 1)):
                continue
            denied = (self._denied_subtree(role, rel, grants) if entry.kind == "dir"
                      else not self.policy.check(role, rel, grants=grants).allowed)
            if denied:
                covered.add(rel)
        return sorted(covered)

    # -- what is hidden ------------------------------------------------------------

    def secret_files(self, inventory: Inventory | None = None) -> list[str]:
        """Every secret file of the checkout — the file itself or a file inside a secret directory
        (`backend/.env/key`), anywhere, including the PRUNE_DIRS trees — never shown to agents (masked or hidden
        here; denied to hardened native agents). Each path component is judged on its own: an exempt suffix
        applies only to the name it matches, so `backend/.env.example` is readable but `backend/.env.example/.env`
        is masked (fsguard / paths.secret_prefix).

        SECURITY POLICY ONLY: the list depends on the secret patterns and on what exists in the file system —
        not on Git (a tracked or merely staged secret is listed like an untracked one; index membership is not
        history) and not on the workspace baseline (an operator-accepted entry stays listed). *inventory*: the
        scan to use (the plan's own); none: a fresh one — never a cached one."""
        return (inventory or self.inventory()).secret_files()

    # -- the plan ------------------------------------------------------------------

    def plan(self, *, run_dir: Path, role: str | None, grants: Sequence[str] = (),
             shadow_writable: bool = False, env: Mapping[str, str] | None = None,
             extra: Sequence[Bind] = (), cwd: Path | None = None, hide: Sequence[Path] = ()) -> SandboxPlan:
        """A sandbox for *role* (None: everything read-only, e.g. gates). Always network-isolated."""
        run_dir.mkdir(parents=True, exist_ok=True)
        home, shadow = run_dir / "home", run_dir / "shadow"
        home.mkdir(exist_ok=True)
        shadow.mkdir(exist_ok=False)
        inventory = self.inventory()   # fresh and verified: THIS plan's facts (overlays, masks, later baseline)
        roots = self.writable_roots(role, grants) if role else []
        overlays = self.readonly_overlays(role, roots, grants, inventory) if role else []
        secrets = self.secret_files(inventory)
        hidden = sorted(s for s in secrets if "/" not in s)
        masked = sorted(s for s in secrets if "/" in s)
        binds: list[Bind] = []
        top_level: set[str] = set()
        for entry in sorted(os.listdir(self.root)):
            source = self.root / entry
            if entry in hidden or (source.is_symlink() and not _inside(source.resolve(), self.root)):
                continue
            top_level.add(entry)
            if source.is_dir() and not source.is_symlink():
                (shadow / entry).mkdir()
            else:
                (shadow / entry).touch()
            binds.append(Bind(str(source), str(source), "ro"))
        binds += [Bind(str(self.root / rel), str(self.root / rel), "rw") for rel in roots]
        binds += [Bind(str(self.root / rel), str(self.root / rel), "ro") for rel in overlays]
        binds += [Bind("/dev/null", str(self.root / rel), "devnull") for rel in masked]
        base_env = {"HOME": os.environ.get("HOME", str(home)), "PATH": "/usr/local/bin:/usr/bin:/bin",
                    "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "TMPDIR": "/tmp", "TERM": "dumb",
                    "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "PYTHONDONTWRITEBYTECODE": "1"}
        for key in ("USER", "LOGNAME"):
            if key in os.environ:
                base_env[key] = os.environ[key]
        base_env.update(env or {})
        # Never expose the coordinator state (claims, tokens) through a bound toolchain prefix.
        hidden_dirs = [str(h) for h in hide if any(_inside(Path(h), Path(b.source)) for b in extra)]
        return SandboxPlan(bwrap=self.bwrap, repo_root=self.root, shadow=shadow, shadow_writable=shadow_writable,
                           home=home, binds=binds, env=base_env, cwd=cwd or self.root,
                           top_level=frozenset(top_level), writable=tuple(roots), readonly_overlays=tuple(overlays),
                           masked=tuple(masked), hidden=tuple(hidden), extra=list(extra),
                           hidden_dirs=tuple(hidden_dirs), inventory=inventory)


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def install_prefix(executable: str) -> Path:
    """The directory to bind read-only so *executable* (resolved) can run inside the sandbox:
    `<prefix>/bin/x` → `<prefix>`, otherwise the executable's own directory."""
    resolved = Path(shutil.which(executable) or executable).resolve()
    return resolved.parent.parent if resolved.parent.name == "bin" else resolved.parent


def _broad(path: Path) -> bool:
    home = Path(os.environ.get("HOME", "/nonexistent"))
    return path in (Path("/"), Path("/usr"), Path("/usr/local"), Path("/opt"), home, home / ".local")


def toolchain(executable: str) -> tuple[str, Path]:
    """(path to execute, prefix to bind read-only) for a host tool.

    A symlinked tool such as `<node>/bin/npm → ../lib/node_modules/npm/bin/npm-cli.js` needs its
    whole `<node>` prefix, so the unresolved `<prefix>/bin/x` wins when that prefix is narrow.
    A broad prefix (HOME, ~/.local, /usr/local, /opt) is never bound; the resolved target's own
    prefix is used instead (e.g. ~/.local/bin/claude → the Claude versions directory)."""
    found = Path(shutil.which(executable) or executable)
    unresolved = found.parent.parent if found.parent.name == "bin" else found.parent
    resolved = found.resolve()
    if found.is_symlink() and not _broad(unresolved) and _inside(resolved, unresolved):
        return str(found), unresolved
    return str(resolved), install_prefix(str(resolved))
