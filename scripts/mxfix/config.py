"""testenv.toml loading: this file -> testenv.local.toml -> CLI overrides.

tomllib is stdlib on Python 3.11+ (the repo venv is newer still) — no new dependency.
See testenv.toml's own comments for what each setting means; this module is only the
loader + the typed shape the rest of scripts/mxfix/ consumes.
"""

from __future__ import annotations

import tomllib
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from testenvlib.paths import REPO_ROOT

TESTENV_TOML = REPO_ROOT / "testenv.toml"
TESTENV_LOCAL_TOML = REPO_ROOT / "testenv.local.toml"

STRATEGIES = ("report", "heal", "batch")
FIXER_ADAPTERS = ("codex", "claude")
SEVERITIES = ("high", "medium", "low")


class ConfigError(Exception):
    """Raised for a missing/malformed testenv.toml, or an unknown key in it."""


def _from_dict(cls: type, data: dict[str, Any], *, path: str) -> Any:
    known = {f.name for f in fields(cls)}
    unknown = set(data) - known
    if unknown:
        raise ConfigError(f"Unknown key(s) in [{path}]: {', '.join(sorted(unknown))} (expected one of {sorted(known)})")
    defaults = cls()
    return cls(**{**asdict(defaults), **data})


@dataclass(frozen=True)
class LimitsConfig:
    max_fix_attempts: int = 3
    concurrency: int = 3
    severity_threshold: str = "medium"
    max_new_issues: int = 20


@dataclass(frozen=True)
class FixerConfig:
    adapter: str = "codex"
    timeout_seconds: int = 1800


@dataclass(frozen=True)
class GitConfig:
    base_branch: str = "dev"
    branch_prefix: str = "ai/"
    push: bool = True
    open_pr: bool = True


@dataclass(frozen=True)
class RunConfig:
    strategy: str = "report"
    limits: LimitsConfig = field(default_factory=LimitsConfig)
    fixer: FixerConfig = field(default_factory=FixerConfig)
    git: GitConfig = field(default_factory=GitConfig)

    def validate(self) -> None:
        if self.strategy not in STRATEGIES:
            raise ConfigError(f"strategy must be one of {STRATEGIES}, got {self.strategy!r}")
        if self.fixer.adapter not in FIXER_ADAPTERS:
            raise ConfigError(f"[fixer] adapter must be one of {FIXER_ADAPTERS}, got {self.fixer.adapter!r}")
        if self.limits.severity_threshold not in SEVERITIES:
            raise ConfigError(f"[limits] severity_threshold must be one of {SEVERITIES}, got {self.limits.severity_threshold!r}")
        if self.limits.max_fix_attempts < 1:
            raise ConfigError("[limits] max_fix_attempts must be >= 1")
        if self.limits.concurrency < 1:
            raise ConfigError("[limits] concurrency must be >= 1")
        if self.limits.max_new_issues < 0:
            raise ConfigError("[limits] max_new_issues must be >= 0 (0 disables issue creation entirely)")
        if self.fixer.timeout_seconds < 1:
            raise ConfigError("[fixer] timeout_seconds must be >= 1")


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_raw(*, local_path: Path = TESTENV_LOCAL_TOML) -> dict[str, Any]:
    if not TESTENV_TOML.is_file():
        raise ConfigError(f"{TESTENV_TOML} not found — it should be tracked at the repo root. See testenv.toml's own comments.")
    try:
        data = tomllib.loads(TESTENV_TOML.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{TESTENV_TOML}: {exc}") from exc

    if local_path.is_file():
        try:
            local_data = tomllib.loads(local_path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"{local_path}: {exc}") from exc
        data = _deep_merge(data, local_data)

    return data


def build_config(raw: dict[str, Any]) -> RunConfig:
    limits = _from_dict(LimitsConfig, raw.get("limits", {}), path="limits")
    fixer = _from_dict(FixerConfig, raw.get("fixer", {}), path="fixer")
    git = _from_dict(GitConfig, raw.get("git", {}), path="git")

    known_top = {"strategy", "limits", "fixer", "git"}
    unknown_top = set(raw) - known_top
    if unknown_top:
        raise ConfigError(f"Unknown top-level key(s) in testenv.toml: {', '.join(sorted(unknown_top))}")

    config = RunConfig(strategy=raw.get("strategy", "report"), limits=limits, fixer=fixer, git=git)
    config.validate()
    return config


def load_config(*, strategy_override: str | None = None) -> RunConfig:
    """The one entry point the rest of scripts/mxfix/ and testenv.py should use."""
    raw = load_raw()
    if strategy_override is not None:
        raw = {**raw, "strategy": strategy_override}
    return build_config(raw)


def render_config(config: RunConfig) -> str:
    lines = [f"strategy = {config.strategy!r}", "", "[limits]"]
    for f in fields(config.limits):
        lines.append(f"  {f.name} = {getattr(config.limits, f.name)!r}")
    lines.append("\n[fixer]")
    for f in fields(config.fixer):
        lines.append(f"  {f.name} = {getattr(config.fixer, f.name)!r}")
    lines.append("\n[git]")
    for f in fields(config.git):
        lines.append(f"  {f.name} = {getattr(config.git, f.name)!r}")
    return "\n".join(lines)
