"""Project configuration: roles, write scopes, protected paths, providers and gates.

`default_config()` is the mx-admin-cloud configuration. The documented example
docs_agents/coordination/project-config.example.yaml must load into exactly the same
object (a test enforces that), so the example can never drift from the code.

Fixed v5 safety ceilings are validated, not configurable: one writer per role, one
execution host, one shared checkout per feature, one final PR per feature, and a single
serialized Git mutation coordinator. A configuration that tries to relax them is rejected.

Path patterns are deliberately simple so that disjointness between roles is provable:
owned/excluded paths are either `<dir>/**` (a subtree) or an exact relative file path.
Protected and approval-required lists may additionally use `*` within one path segment
and a leading `**/` (any depth, e.g. `**/.env`).

Phase 3 adds the runtime section (OS containment, timeouts, heartbeats, per-role command
allowlists for Claude Code), baseline-aware lint gates (D-19) and the list of gates that
are unsafe to run against a real checkout (B-7).

Phase 4 adds the `github` section: the repository, the coordinator identity written into
every Issue it owns, the write scope (test features only during Phase 4), the operators
whose Issue-comment commands are accepted, and retry/rate-limit bounds. It holds no
credentials: the adapter uses the existing `gh` login.

The Phase 4 extension adds `github.project`: the GitHub Projects (v2) board that shows the
coordinator's Issues (owner, number and GraphQL node ID, the projects that must never be
written, the Status options and the Agent / Feature ID / Blocked Reason fields). It is
disabled by default; `default_config()` points it at the mx-admin-cloud board.

W2 (functional Worker) makes the routing role → agent → model configurable: a role names its
agent (`provider`) and may override that provider's model (`model`, validated per agent kind;
"default" = the CLI's own default), and a role can be disabled (`enabled: false`, Automation).
`runtime.execution_backend` selects how real runners execute: `native-cli` (the installed CLIs
with their existing subscription logins, no OS sandbox) or `bwrap` (the Phase 3 containment).
Role command allowlists use the `{python}` placeholder, resolved to a discovered or configured
interpreter (`runtime.python_interpreter`), never to a bare `python` on PATH.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .errors import ConfigError, ProviderError
from .providers import (
    CLAUDE_CODE,
    CODEX,
    MODEL_DEFAULT,
    REMOTE_QWEN,
    OpenAICompatibleProfile,
    ProviderProfile,
    check_effective_input,
    check_model,
    profile_from_mapping,
)

ROLES = ("backend", "cli", "frontend", "automation", "coordination")
FULL_PROJECT = "full-project"
MAX_WRITERS_PER_ROLE = 1
SCHEMA_VERSION = 1
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._*-]+$")


@dataclass(frozen=True)
class RoleConfig:  # pylint: disable=too-many-instance-attributes
    """One specialized role: its agent (provider), model, write scope and whether it runs."""

    name: str
    provider: str
    owned: tuple[str, ...]
    excluded: tuple[str, ...] = ()
    max_writers: int = MAX_WRITERS_PER_ROLE
    model: str = ""        # W2: "" = the provider's model; "default" = the CLI's own default; else validated
    enabled: bool = True   # W2: a disabled role's tasks are never run (the runner reports unavailable)


@dataclass(frozen=True)
class LintGate:
    """A baseline-aware Pylint gate (D-19): findings already present at the feature base are
    reported; findings the feature introduces block. Runs in `root` with `targets`."""

    root: str
    targets: tuple[str, ...]
    pythonpath: tuple[str, ...] = ()
    policy: str = "block-new"


LINT_POLICIES = ("block-new",)  # D-19: new findings always block; no "disabled" or "report-only"


@dataclass(frozen=True)
class RuntimeConfig:  # pylint: disable=too-many-instance-attributes
    """How real runners and gates execute (Phase 3)."""

    containment: str = "bwrap"
    execution_backend: str = "native-cli"   # W2: native-cli | bwrap (never a silent fallback between them)
    python_interpreter: str = "auto"        # W2: auto (workspace .venv, then python3) or an absolute path
    agent_network: bool = True
    gate_network: bool = False
    task_timeout_seconds: int = 3600
    heartbeat_interval_seconds: int = 10
    stale_heartbeat_seconds: int = 120
    terminate_grace_seconds: int = 10
    gate_timeout_seconds: int = 900
    claude_max_budget_usd: float = 5.0
    secret_patterns: tuple[str, ...] = ("**/.env", "**/.env.*")
    secret_exempt_suffixes: tuple[str, ...] = (".example", ".sample", ".template")
    role_commands: Mapping[str, tuple[str, ...]] = field(default_factory=dict)


CONTAINMENT_MODES = ("bwrap",)  # gates and the bwrap backend are never unconfined
# W2: how real runners execute. `native-cli` runs the installed CLIs directly with the operator's
# subscription logins and is NOT a security sandbox; `bwrap` is the Phase 3 OS containment.
EXECUTION_BACKENDS = ("native-cli", "bwrap")
PYTHON_PLACEHOLDER = "{python}"

GITHUB_TRANSPORTS = ("gh-cli",)
# Phase 4 ceiling: only features marked `test` may be written to GitHub. Widening the scope to
# real features is an operator decision (Phase 5).
GITHUB_WRITE_SCOPES = ("test-features-only", "all-features")
COMMAND_PERMISSIONS = ("admin", "maintain", "write")
_REPO_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}$")
_LOGIN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
_COORDINATOR_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")
PROJECT_OWNER_TYPES = ("user", "organization")
# The coordinator's board columns, in board order. Each key has a fixed meaning (project_sync.py);
# the configuration chooses only the option names.
STATUS_KEYS = ("backlog", "ready", "in_progress", "blocked", "review", "done")
DEFAULT_STATUS_NAMES = {"backlog": "Backlog", "ready": "Ready", "in_progress": "In Progress", "blocked": "Blocked",
                        "review": "Review", "done": "Done"}
# GitHub creates every new project with the Status options Todo / In Progress / Done. "Todo" is
# reused as Backlog (renamed, option ID kept), so items and workflows that point at it keep working.
DEFAULT_STATUS_REUSE = {"Todo": "backlog"}
DEFAULT_AGENT_NAMES = {"coordination": "Coordination", "backend": "Backend", "cli": "CLI", "frontend": "Frontend",
                       "automation": "Automation", "full-project": "Full Project"}
_PROJECT_NODE_RE = re.compile(r"^PVT_[A-Za-z0-9_-]{4,64}$")
_PROJECT_NAME_RE = re.compile(r"^[^\x00-\x1f\x7f\"\\]{1,50}$")
_BUILTIN_PROJECT_FIELDS = ("Title", "Assignees", "Labels", "Linked pull requests", "Milestone", "Repository",
                           "Reviewers", "Parent issue", "Sub-issues progress", "Type", "Tracks", "Tracked by")


@dataclass(frozen=True)
class GitHubProjectConfig:  # pylint: disable=too-many-instance-attributes
    """The GitHub Projects (v2) board that visualizes coordinator Issues (Phase 4 extension).

    The project is identified by owner, number AND node ID; all three must match before
    anything is written. `protected_numbers` lists projects of the same owner that are never
    written (for mx-admin-cloud: Project #1, owned by the independent mxissues workflow).
    The board is a projection of the local state: it never claims, locks or approves anything.
    """

    enabled: bool = False
    owner: str = ""
    owner_type: str = "user"
    number: int = 0
    node_id: str = ""
    title: str = ""                       # informational; a mismatch is reported, identity is the node ID
    protected_numbers: tuple[int, ...] = ()
    status_field: str = "Status"
    status_names: Mapping[str, str] = field(default_factory=lambda: dict(DEFAULT_STATUS_NAMES))
    status_reuse: Mapping[str, str] = field(default_factory=lambda: dict(DEFAULT_STATUS_REUSE))
    agent_field: str = "Agent"
    agent_names: Mapping[str, str] = field(default_factory=lambda: dict(DEFAULT_AGENT_NAMES))
    feature_id_field: str = "Feature ID"
    blocked_reason_field: str = "Blocked Reason"
    board_view: str = "Kanban"
    active_view: str = "Active Tasks"
    item_settle_seconds: float = 2.0      # after adding an item, before setting its fields (project workflows)
    max_field_attempts: int = 2           # field writes re-applied when a read-back differs

    def status(self, key: str) -> str:
        """The option name of a status key (see STATUS_KEYS)."""
        return self.status_names[key]


@dataclass(frozen=True)
class GitHubConfig:  # pylint: disable=too-many-instance-attributes
    """The GitHub Issues adapter (Phase 4). No credentials: `gh` uses its existing login."""

    repository: str = "spyderbg/mx-admin-cloud"
    coordinator_id: str = "mx-admin-cloud.feature-coordinator"
    transport: str = "gh-cli"
    gh_executable: str = "gh"
    hostname: str = "github.com"
    write_scope: str = "test-features-only"
    test_title_prefix: str = "[Feature Coordinator Test] "
    command_authors: tuple[str, ...] = ("spyderbg",)
    command_min_permission: str = "write"
    request_timeout_seconds: float = 30.0
    max_retries: int = 3
    backoff_base_seconds: float = 1.0
    max_backoff_seconds: float = 30.0
    max_rate_limit_wait_seconds: float = 120.0
    min_write_interval_seconds: float = 1.0
    sync_lock_timeout_seconds: float = 120.0
    create_settle_seconds: float = 5.0
    max_pages: int = 20
    project: GitHubProjectConfig = field(default_factory=GitHubProjectConfig)

    @property
    def owner(self) -> str:
        """The repository owner (user or organization)."""
        return self.repository.split("/", 1)[0]


@dataclass(frozen=True)
class ProjectConfig:  # pylint: disable=too-many-instance-attributes
    """The full coordinator configuration for one repository."""

    repository: str
    base_branch: str
    roles: Mapping[str, RoleConfig]
    protected: tuple[str, ...]
    approval_required: tuple[str, ...]
    providers: Mapping[str, ProviderProfile]
    gates: Mapping[str, tuple[str, ...]]
    branch_pattern: str = "feature/{slug}"
    pr_policy: str = "one-after-all-gates"
    state_app_dir: str = "mx-admin-agents"
    allowed_state_filesystems: tuple[str, ...] = ("ext4", "ext3", "ext2", "xfs", "btrfs", "f2fs", "tmpfs")
    lint_gates: Mapping[str, LintGate] = field(default_factory=dict)
    unsafe_gates: Mapping[str, str] = field(default_factory=dict)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    github: GitHubConfig = field(default_factory=GitHubConfig)
    extra: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate(self)

    def role(self, name: str) -> RoleConfig:
        """The role named *name*. Raises ConfigError for an unknown role."""
        try:
            return self.roles[name]
        except KeyError as exc:
            raise ConfigError(f"unknown role {name!r}; roles are {list(self.roles)}") from exc

    def provider_for(self, role: str) -> ProviderProfile:
        """The provider profile configured for *role*."""
        return self.providers[self.role(role).provider]

    def model_for(self, role: str) -> str:
        """The model *role* runs with: its own `model`, else its provider's; '' = the CLI default."""
        cfg = self.role(role)
        if cfg.model == MODEL_DEFAULT:
            return ""
        if cfg.model:
            return cfg.model
        profile = self.provider_for(role)
        model = getattr(profile, "model", "")
        return "" if model == MODEL_DEFAULT else model

    @property
    def gate_names(self) -> tuple[str, ...]:
        """Every configured gate: command gates and lint gates."""
        return (*self.gates, *self.lint_gates)

    def branch_for(self, slug: str) -> str:
        """The single feature branch name for *slug*."""
        if not _SLUG_RE.fullmatch(slug):
            raise ConfigError(f"invalid feature slug {slug!r} (lowercase letters, digits, '-'; max 48)")
        return self.branch_pattern.format(slug=slug)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _check_pattern(pattern: str, *, allow_glob: bool) -> None:
    if not pattern or pattern.startswith("/") or "\\" in pattern or "\x00" in pattern:
        raise ConfigError(f"path pattern {pattern!r} must be a non-empty relative POSIX path")
    segments = pattern.split("/")
    if any(seg in ("", ".", "..") for seg in segments):
        raise ConfigError(f"path pattern {pattern!r} must not contain empty, '.' or '..' segments")
    if allow_glob and len(segments) > 1 and segments[0] == "**":
        segments = segments[1:]  # leading "**/": the rest may match at any depth
    body = segments[:-1] if segments[-1] == "**" else segments
    for seg in body:
        if not _SEGMENT_RE.fullmatch(seg) or "**" in seg:
            raise ConfigError(f"path pattern {pattern!r} has an unsupported segment {seg!r}")
        if "*" in seg and not allow_glob:
            raise ConfigError(f"owned/excluded pattern {pattern!r} may only be '<dir>/**' or an exact path")


def _prefix(pattern: str) -> tuple[str, bool]:
    """(path, is_subtree) for an owned pattern."""
    if pattern.endswith("/**"):
        return pattern[:-3], True
    return pattern, False


def _overlaps(a: str, b: str) -> bool:
    (pa, tree_a), (pb, tree_b) = _prefix(a), _prefix(b)
    if pa == pb:
        return True
    if tree_a and pb.startswith(pa + "/"):
        return True
    return tree_b and pa.startswith(pb + "/")


def _scope_overlap(a: RoleConfig, b: RoleConfig) -> list[tuple[str, str]]:
    """Owned-pattern pairs of two roles that overlap. Role scopes must be strictly disjoint."""
    return [(pa, pb) for pa in a.owned for pb in b.owned if _overlaps(pa, pb)]


def _validate_roles(config: ProjectConfig) -> None:
    if tuple(config.roles) != ROLES:
        raise ConfigError(f"roles must be exactly {ROLES} in this order, got {tuple(config.roles)}")
    for name, role in config.roles.items():
        if role.name != name:
            raise ConfigError(f"role key {name!r} does not match its name {role.name!r}")
        if role.max_writers != MAX_WRITERS_PER_ROLE:
            raise ConfigError(f"role {name!r}: max_writers is fixed to {MAX_WRITERS_PER_ROLE} (v5 ceiling)")
        if role.provider not in config.providers:
            raise ConfigError(f"role {name!r}: unknown provider {role.provider!r}")
        try:
            check_model(config.providers[role.provider].kind, role.model, where=f"role {name!r}")
        except ProviderError as exc:
            raise ConfigError(str(exc)) from exc
        if not role.owned:
            raise ConfigError(f"role {name!r}: owned paths must not be empty")
        for pattern in (*role.owned, *role.excluded):
            _check_pattern(pattern, allow_glob=False)
    names = list(config.roles)
    for i, first in enumerate(names):
        for second in names[i + 1:]:
            clashes = _scope_overlap(config.roles[first], config.roles[second])
            if clashes:
                raise ConfigError(f"roles {first!r} and {second!r} have overlapping write scopes: {clashes}")


def validate(config: ProjectConfig) -> None:
    """Raise ConfigError unless *config* respects every structural rule and v5 ceiling."""
    _validate_roles(config)
    if config.pr_policy != "one-after-all-gates":
        raise ConfigError("pr_policy is fixed to 'one-after-all-gates' (one final PR per feature)")
    if "{slug}" not in config.branch_pattern or not config.branch_pattern.startswith("feature/"):
        raise ConfigError("branch_pattern must be 'feature/...{slug}...'")
    for pattern in (*config.protected, *config.approval_required):
        _check_pattern(pattern, allow_glob=True)
    for name, argv in config.gates.items():
        if not argv or not all(isinstance(arg, str) and arg for arg in argv):
            raise ConfigError(f"gate {name!r} must be a non-empty argv list of strings")
    _validate_gates_and_runtime(config)
    _validate_github(config.github)
    if not config.allowed_state_filesystems or any(fs.startswith("fuse") for fs in config.allowed_state_filesystems):
        raise ConfigError("allowed_state_filesystems must be non-empty and must not include FUSE filesystems")


def _validate_gates_and_runtime(config: ProjectConfig) -> None:
    clash = set(config.gates) & set(config.lint_gates)
    if clash:
        raise ConfigError(f"gate names must be unique across command and lint gates: {sorted(clash)}")
    for name, lint in config.lint_gates.items():
        if lint.policy not in LINT_POLICIES:
            raise ConfigError(f"lint gate {name!r}: policy must be one of {LINT_POLICIES} (D-19: new findings block)")
        for rel in (lint.root, *(f"{lint.root}/{t}" for t in lint.targets)):
            _check_pattern(rel, allow_glob=False)
        if not lint.targets:
            raise ConfigError(f"lint gate {name!r} needs at least one target")
    unknown = set(config.unsafe_gates) - set(config.gate_names)
    if unknown:
        raise ConfigError(f"unsafe_gates names unknown gates: {sorted(unknown)}")
    _validate_runtime(config)


def _validate_runtime(config: ProjectConfig) -> None:
    runtime = config.runtime
    if runtime.containment not in CONTAINMENT_MODES:
        raise ConfigError(f"runtime.containment must be one of {CONTAINMENT_MODES}; write-capable runs are "
                          "never unconfined")
    if runtime.gate_network:
        raise ConfigError("runtime.gate_network is fixed to false: gates run offline")
    for name in ("task_timeout_seconds", "heartbeat_interval_seconds", "stale_heartbeat_seconds",
                 "terminate_grace_seconds", "gate_timeout_seconds"):
        if getattr(runtime, name) <= 0:
            raise ConfigError(f"runtime.{name} must be positive")
    if runtime.stale_heartbeat_seconds <= runtime.heartbeat_interval_seconds:
        raise ConfigError("runtime.stale_heartbeat_seconds must exceed heartbeat_interval_seconds")
    for pattern in runtime.secret_patterns:
        _check_pattern(pattern, allow_glob=True)
    unknown_roles = set(runtime.role_commands) - set(config.roles)
    if unknown_roles:
        raise ConfigError(f"runtime.role_commands names unknown roles: {sorted(unknown_roles)}")
    if runtime.execution_backend not in EXECUTION_BACKENDS:
        raise ConfigError(f"runtime.execution_backend must be one of {EXECUTION_BACKENDS}")
    interpreter = runtime.python_interpreter
    if interpreter != "auto" and (not interpreter.startswith("/") or any(c.isspace() for c in interpreter)):
        raise ConfigError("runtime.python_interpreter must be 'auto' or an absolute path without whitespace")
    for role, commands in runtime.role_commands.items():
        for command in commands:
            _check_role_command(role, command)


def _check_role_command(role: str, command: str) -> None:
    """A Claude Code Bash prefix rule: no bare `python`/`python3` (W2: it may not exist, e.g. on
    Ubuntu 24.04) and no placeholder other than a leading {python}."""
    body = command[len(PYTHON_PLACEHOLDER):] if command.startswith(PYTHON_PLACEHOLDER + " ") else command
    first = command.split(" ", 1)[0]
    if not command.strip() or "{" in body or "}" in body:
        raise ConfigError(f"runtime.role_commands[{role!r}]: {command!r} may only use a leading {PYTHON_PLACEHOLDER}")
    if first in ("python", "python3") or first.startswith("python3."):
        raise ConfigError(f"runtime.role_commands[{role!r}]: {command!r} names a bare interpreter; use "
                          f"{PYTHON_PLACEHOLDER} (resolved from runtime.python_interpreter)")


def _validate_github(github: GitHubConfig) -> None:
    if not _REPO_RE.fullmatch(github.repository):
        raise ConfigError(f"github.repository must be 'owner/name', got {github.repository!r}")
    if not _COORDINATOR_ID_RE.fullmatch(github.coordinator_id):
        raise ConfigError("github.coordinator_id must be 3-64 characters: lowercase letters, digits, '.', '_', '-'")
    if github.transport not in GITHUB_TRANSPORTS:
        raise ConfigError(f"github.transport must be one of {GITHUB_TRANSPORTS}")
    if github.write_scope not in GITHUB_WRITE_SCOPES:
        raise ConfigError(f"github.write_scope must be one of {GITHUB_WRITE_SCOPES}")
    if github.command_min_permission not in COMMAND_PERMISSIONS:
        raise ConfigError(f"github.command_min_permission must be one of {COMMAND_PERMISSIONS}")
    bad = [login for login in github.command_authors if not _LOGIN_RE.fullmatch(login)]
    if bad:
        raise ConfigError(f"github.command_authors has invalid logins: {bad}")
    if not re.fullmatch(r"[A-Za-z0-9.-]{1,253}", github.hostname):
        raise ConfigError("github.hostname must be a plain host name")
    if not github.gh_executable or "\x00" in github.gh_executable:
        raise ConfigError("github.gh_executable must be a program name or path")
    for name in ("request_timeout_seconds", "backoff_base_seconds", "max_backoff_seconds",
                 "max_rate_limit_wait_seconds", "sync_lock_timeout_seconds"):
        if getattr(github, name) <= 0:
            raise ConfigError(f"github.{name} must be positive")
    if github.min_write_interval_seconds < 0 or github.create_settle_seconds < 0:
        raise ConfigError("github.min_write_interval_seconds and create_settle_seconds must not be negative")
    if not 0 <= github.max_retries <= 10 or not 1 <= github.max_pages <= 100:
        raise ConfigError("github.max_retries must be 0..10 and github.max_pages 1..100")
    _validate_project(github.project)


def _validate_project(project: GitHubProjectConfig) -> None:  # pylint: disable=too-many-branches
    if not project.enabled:
        return
    where = "github.project"
    if not _LOGIN_RE.fullmatch(project.owner):
        raise ConfigError(f"{where}.owner must be a GitHub login, got {project.owner!r}")
    if project.owner_type not in PROJECT_OWNER_TYPES:
        raise ConfigError(f"{where}.owner_type must be one of {PROJECT_OWNER_TYPES}")
    if project.number < 1 or not _PROJECT_NODE_RE.fullmatch(project.node_id):
        raise ConfigError(f"{where}.number must be positive and {where}.node_id a ProjectV2 node ID (PVT_…)")
    if project.number in project.protected_numbers:
        raise ConfigError(f"{where}.number {project.number} is listed in protected_numbers; it is never written")
    if any(n < 1 for n in project.protected_numbers):
        raise ConfigError(f"{where}.protected_numbers must be positive project numbers")
    if tuple(project.status_names) != STATUS_KEYS:
        raise ConfigError(f"{where}.status_names must map exactly {STATUS_KEYS}, in this order")
    if set(project.agent_names) != {*ROLES, FULL_PROJECT}:
        raise ConfigError(f"{where}.agent_names must map exactly {(*ROLES, FULL_PROJECT)}")
    for group in (project.status_names, project.agent_names):
        names = list(group.values())
        if len(set(names)) != len(names) or not all(_PROJECT_NAME_RE.fullmatch(n) for n in names):
            raise ConfigError(f"{where}: option names must be unique, 1-50 printable characters")
    for name, key in project.status_reuse.items():
        if key not in STATUS_KEYS or not _PROJECT_NAME_RE.fullmatch(name) or name in project.status_names.values():
            raise ConfigError(f"{where}.status_reuse: {name!r} → {key!r} is not a reusable status option")
    fields = [project.status_field, project.agent_field, project.feature_id_field, project.blocked_reason_field]
    if len(set(fields)) != 4 or not all(_PROJECT_NAME_RE.fullmatch(f) for f in fields) \
            or set(fields[1:]) & set(_BUILTIN_PROJECT_FIELDS):
        raise ConfigError(f"{where}: field names must be distinct, printable and not built-in project fields")
    views = (project.board_view, project.active_view)
    if len(set(views)) != 2 or not all(_PROJECT_NAME_RE.fullmatch(v) for v in views):
        raise ConfigError(f"{where}: board_view and active_view must be distinct, printable names")
    if project.item_settle_seconds < 0 or not 1 <= project.max_field_attempts <= 5:
        raise ConfigError(f"{where}.item_settle_seconds must not be negative; max_field_attempts must be 1..5")


# ---------------------------------------------------------------------------
# The mx-admin-cloud configuration
# ---------------------------------------------------------------------------

DEFAULT_PROTECTED = (
    ".git/**",
    ".env",
    ".env.*",
    "**/.env",
    "**/.env.*",
    "content/**",
    "data/**",
    "backend/data/**",
    "scripts/mxfix/**",
    "scripts/mxissues/**",
)

DEFAULT_APPROVAL_REQUIRED = (
    "CLAUDE.md",
    "AGENTS.md",
    "README.md",
    ".gitignore",
    ".gitlab-ci.yml",
    "testenv.toml",
    "scripts/deploy.sh",
    ".claude/**",
    ".cursor/**",
    ".opencode/**",
    ".aiassistant/**",
    ".agents/**",
    ".github/**",
    "docker/**",
    "docs_agents/architecture/**",
    "docs_agents/README.md",
)

DEFAULT_GATES: dict[str, tuple[str, ...]] = {
    "backend-tests": ("python", "-m", "pytest", "-q", "-p", "no:cacheprovider"),
    "contract-check": ("python", "scripts/export_openapi.py", "--docs-agents", "--check"),
    "docs-check": ("python", "scripts/check_docs_agents.py"),
    "frontend-type-check": ("npm", "run", "type-check"),
    # The build output goes to the sandbox's private /tmp: gates never write the checkout.
    "frontend-build": ("npm", "run", "build", "--", "--outDir", "/tmp/fc-frontend-dist", "--emptyOutDir"),
    "frontend-tests": ("npm", "run", "test:voices"),
    "frontend-tests-content": ("npm", "run", "test:content"),
    "frontend-tests-auth": ("npm", "run", "test:auth"),
    "coordinator-tests": ("python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "scripts/tests"),
}

DEFAULT_LINT_GATES: dict[str, LintGate] = {
    "backend-lint": LintGate(root="backend", targets=("src",), pythonpath=("src",)),
    "cli-lint": LintGate(root="cli", targets=("src/kiddo_cli",), pythonpath=("src",)),
}

# None since the B-7 fix: the backend tests no longer reach the real content/ tree.
DEFAULT_UNSAFE_GATES: dict[str, str] = {}

DEFAULT_ROLE_COMMANDS: dict[str, tuple[str, ...]] = {
    # Claude Code Bash permission rules (prefix matches). Anything not listed is denied.
    # {python} is the discovered or configured interpreter (W2), never a bare `python`.
    "backend": ("{python} -m pytest:*", "{python} -m pylint:*", "{python} -m compileall:*"),
    "cli": ("{python} -m pytest:*", "{python} -m pylint:*", "kiddo-cli --help:*"),
    "coordination": ("{python} scripts/check_docs_agents.py:*",),
}


# The mx-admin-cloud board (Phase 4 extension). Project #1 ("mx-admin-cloud issues") belongs to the
# independent mxissues bug workflow and is never written.
MX_ADMIN_CLOUD_PROJECT = GitHubProjectConfig(
    enabled=True, owner="spyderbg", owner_type="user", number=2, node_id="PVT_kwHOACEIPc4BndH-",
    title="mx-admin-cloud automation", protected_numbers=(1,))


def default_config() -> ProjectConfig:
    """The approved mx-admin-cloud configuration (architecture §1, D-12, D-14)."""
    # W2 routing: Backend → Claude Code (Opus), CLI → Claude Code (Sonnet), Frontend → Codex (CLI
    # default), Automation → remote Qwen, disabled. Changing an agent or model is configuration only.
    roles = {
        "backend": RoleConfig("backend", "claude", ("backend/**", "docs_agents/backend/**"), ("backend/data/**",),
                              model="opus"),
        "cli": RoleConfig("cli", "claude", ("cli/**", "docs_agents/cli/**"), model="sonnet"),
        "frontend": RoleConfig("frontend", "codex", ("frontend/**", "docs_agents/frontend/**"), model=MODEL_DEFAULT),
        "automation": RoleConfig(
            "automation", "remote_qwen",
            ("scripts/**", "test_environment/**", "docs_agents/automation/**"),
            ("scripts/mxfix/**", "scripts/mxissues/**", "scripts/deploy.sh"), enabled=False,
        ),
        "coordination": RoleConfig("coordination", "claude", ("docs_agents/coordination/**",), model="sonnet"),
    }
    return ProjectConfig(
        repository="mx-admin-cloud",
        base_branch="dev",
        roles=roles,
        protected=DEFAULT_PROTECTED,
        approval_required=DEFAULT_APPROVAL_REQUIRED,
        providers={"claude": CLAUDE_CODE, "codex": CODEX, "remote_qwen": REMOTE_QWEN},
        gates=dict(DEFAULT_GATES),
        lint_gates=dict(DEFAULT_LINT_GATES),
        unsafe_gates=dict(DEFAULT_UNSAFE_GATES),
        runtime=RuntimeConfig(role_commands=dict(DEFAULT_ROLE_COMMANDS)),
        github=GitHubConfig(project=MX_ADMIN_CLOUD_PROJECT),
    )


# ---------------------------------------------------------------------------
# Loading the documented YAML example
# ---------------------------------------------------------------------------

def _require(data: Mapping[str, Any], key: str, expected: Any = None) -> Any:
    if key not in data:
        raise ConfigError(f"missing configuration key {key!r}")
    if expected is not None and data[key] != expected:
        raise ConfigError(f"{key!r} is fixed to {expected!r}, got {data[key]!r}")
    return data[key]


def config_from_mapping(data: Mapping[str, Any]) -> ProjectConfig:
    """Build and validate a ProjectConfig from the YAML structure of the example file."""
    _require(data, "schema_version", SCHEMA_VERSION)
    _require(data, "execution_host", "single-linux")
    _require(data, "checkout_mode", "shared-single-feature")
    git = _require(data, "git")
    for flag in ("single_mutation_coordinator", "serialize_commits_push_branch_and_pr", "no_agent_branch_switches",
                 "no_per_agent_worktrees"):
        if git.get(flag) is not True:
            raise ConfigError(f"git.{flag} is a fixed v5 ceiling and must be true")
    feature = _require(data, "feature")
    roles = {
        name: RoleConfig(
            name=name,
            provider=str(spec["provider"]),
            owned=tuple(spec["owned_paths"]),
            excluded=tuple(spec.get("excluded_paths", ())),
            max_writers=int(spec.get("max_writers", MAX_WRITERS_PER_ROLE)),
            model=str(spec.get("model", "") or ""),
            enabled=_bool(spec.get("enabled", True), f"roles.{name}.enabled"),
        )
        for name, spec in dict(_require(data, "roles")).items()
    }
    providers: dict[str, ProviderProfile] = {}
    try:
        for name, spec in dict(_require(data, "providers")).items():
            profile = profile_from_mapping(name, spec)
            if isinstance(profile, OpenAICompatibleProfile):
                check_effective_input(profile, spec.get("effective_max_input_tokens"))
                if "base_url" in spec or "api_key" in spec:
                    raise ConfigError(f"provider {name!r}: endpoints and keys come from the environment only")
            providers[name] = profile
    except ProviderError as exc:
        raise ConfigError(str(exc)) from exc
    state = dict(data.get("state", {}))
    return ProjectConfig(
        lint_gates={name: LintGate(root=str(spec["root"]), targets=tuple(spec["targets"]),
                                   pythonpath=tuple(spec.get("pythonpath", ())),
                                   policy=str(spec.get("policy", "block-new")))
                    for name, spec in dict(data.get("lint_gates", {})).items()},
        unsafe_gates={str(k): str(v) for k, v in dict(data.get("unsafe_gates", {})).items()},
        runtime=_runtime_from_mapping(dict(data.get("runtime", {}))),
        github=_github_from_mapping(dict(data.get("github", {}))),
        repository=str(_require(data, "repository")),
        base_branch=str(_require(feature, "base_branch")),
        branch_pattern=str(_require(feature, "branch_pattern")),
        pr_policy=str(_require(feature, "pr_policy")),
        roles=roles,
        protected=tuple(_require(data, "protected_paths")),
        approval_required=tuple(_require(data, "approval_required_paths")),
        providers=providers,
        gates={name: tuple(argv) for name, argv in dict(_require(data, "gates")).items()},
        state_app_dir=str(state.get("app_dir", "mx-admin-agents")),
        allowed_state_filesystems=tuple(state.get("allowed_filesystems", ProjectConfig.allowed_state_filesystems)),
    )


def _bool(value: Any, where: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"{where} must be true or false")
    return value


def _runtime_from_mapping(data: Mapping[str, Any]) -> RuntimeConfig:
    defaults = RuntimeConfig()
    known = set(RuntimeConfig.__dataclass_fields__)  # pylint: disable=no-member
    unknown = set(data) - known
    if unknown:
        raise ConfigError(f"unknown runtime keys: {sorted(unknown)}")
    values: dict[str, Any] = {}
    for key in known:
        if key not in data:
            continue
        default = getattr(defaults, key)
        raw = data[key]
        if key == "role_commands":
            values[key] = {str(r): tuple(str(c) for c in cmds) for r, cmds in dict(raw).items()}
        elif isinstance(default, tuple):
            values[key] = tuple(str(v) for v in raw)
        elif isinstance(default, bool):
            values[key] = bool(raw)
        else:
            values[key] = type(default)(raw)
    return RuntimeConfig(**values)


def _github_from_mapping(data: Mapping[str, Any]) -> GitHubConfig:
    defaults = GitHubConfig()
    known = set(GitHubConfig.__dataclass_fields__)  # pylint: disable=no-member
    unknown = set(data) - known
    if unknown:
        raise ConfigError(f"unknown github keys: {sorted(unknown)} (tokens and endpoints never belong here)")
    values: dict[str, Any] = {}
    for key in known & set(data):
        default = getattr(defaults, key)
        raw = data[key]
        if key == "project":
            values[key] = _project_from_mapping(dict(raw or {}))
        else:
            values[key] = tuple(str(v) for v in raw) if isinstance(default, tuple) else type(default)(raw)
    return GitHubConfig(**values)


def _project_from_mapping(data: Mapping[str, Any]) -> GitHubProjectConfig:
    defaults = GitHubProjectConfig()
    known = set(GitHubProjectConfig.__dataclass_fields__)  # pylint: disable=no-member
    unknown = set(data) - known
    if unknown:
        raise ConfigError(f"unknown github.project keys: {sorted(unknown)} (tokens never belong here)")
    values: dict[str, Any] = {}
    for key in known & set(data):
        default, raw = getattr(defaults, key), data[key]
        if key == "protected_numbers":
            values[key] = tuple(int(v) for v in raw)
        elif isinstance(default, Mapping):
            values[key] = {str(k): str(v) for k, v in dict(raw).items()}
        elif isinstance(default, bool):
            if not isinstance(raw, bool):
                raise ConfigError(f"github.project.{key} must be true or false")
            values[key] = raw
        else:
            values[key] = type(default)(raw)
    return GitHubProjectConfig(**values)


def load_config(path: Path) -> ProjectConfig:
    """Load a project configuration from YAML. Requires PyYAML."""
    try:
        import yaml  # pylint: disable=import-outside-toplevel
    except ImportError as exc:  # pragma: no cover - PyYAML is installed in the project venv
        raise ConfigError("PyYAML is required to load a YAML configuration") from exc
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"cannot read configuration {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"configuration {path} must be a mapping")
    return config_from_mapping(data)
