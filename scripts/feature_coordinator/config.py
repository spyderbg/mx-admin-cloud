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
    REMOTE_QWEN,
    OpenAICompatibleProfile,
    ProviderProfile,
    check_effective_input,
    profile_from_mapping,
)

ROLES = ("backend", "cli", "frontend", "automation", "coordination")
FULL_PROJECT = "full-project"
MAX_WRITERS_PER_ROLE = 1
SCHEMA_VERSION = 1
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._*-]+$")


@dataclass(frozen=True)
class RoleConfig:
    """One specialized role: its provider and write scope."""

    name: str
    provider: str
    owned: tuple[str, ...]
    excluded: tuple[str, ...] = ()
    max_writers: int = MAX_WRITERS_PER_ROLE


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


CONTAINMENT_MODES = ("bwrap",)  # write-capable runs are never unconfined


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

DEFAULT_UNSAFE_GATES: dict[str, str] = {
    "backend-tests": ("B-7/F-5: backend tests write registry lock files into the real content/ tree; "
                      "blocked against a real checkout until the Backend-owned fix lands"),
}

DEFAULT_ROLE_COMMANDS: dict[str, tuple[str, ...]] = {
    # Claude Code Bash permission rules (prefix matches). Anything not listed is denied.
    "backend": ("python -m pytest:*", "python -m pylint:*", "python -m compileall:*"),
    "cli": ("python -m pytest:*", "python -m pylint:*", "kiddo-cli --help:*"),
    "coordination": ("python scripts/check_docs_agents.py:*",),
}


def default_config() -> ProjectConfig:
    """The approved mx-admin-cloud configuration (architecture §1, D-12, D-14)."""
    roles = {
        "backend": RoleConfig("backend", "claude", ("backend/**", "docs_agents/backend/**"), ("backend/data/**",)),
        "cli": RoleConfig("cli", "claude", ("cli/**", "docs_agents/cli/**")),
        "frontend": RoleConfig("frontend", "codex", ("frontend/**", "docs_agents/frontend/**")),
        "automation": RoleConfig(
            "automation", "remote_qwen",
            ("scripts/**", "test_environment/**", "docs_agents/automation/**"),
            ("scripts/mxfix/**", "scripts/mxissues/**", "scripts/deploy.sh"),
        ),
        "coordination": RoleConfig("coordination", "claude", ("docs_agents/coordination/**",)),
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
