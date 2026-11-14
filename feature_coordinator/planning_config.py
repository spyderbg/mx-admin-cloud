"""W4.5: the `github.planning` configuration section (label-driven Issue planning, intake_plan.py).

Kept out of config.py, which loads it into `GitHubConfig.planning` and calls `validate_planning()`.
Off by default; needs `github.intake.enabled` (an approved plan becomes an intake feature). Who may
request or approve: the `/feature` command rules (`command_authors`, `command_min_permission`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from .errors import ConfigError, ProviderError
from .providers import ProviderKind, check_model

_BOT_LOGIN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})\[bot\]$")
_WORKFLOW_PATH_RE = re.compile(r"^\.github/workflows/[A-Za-z0-9._-]{1,80}\.ya?ml$")
_FILE_RE = re.compile(r"^(?:[A-Za-z0-9._-]+/)*[A-Za-z0-9._-]+$")


@dataclass(frozen=True)
class GitHubPlanningConfig:  # pylint: disable=too-many-instance-attributes
    """W4.5: label-driven planning and approval of GitHub Issues (intake_plan.py). Off until an operator enables it."""

    enabled: bool = False
    provider: str = "claude"              # a Claude Code provider profile: its subscription login, never an API key
    model: str = "opus"                   # validated like a role model ("default": the CLI's own default)
    timeout_seconds: int = 900            # one planner invocation (no tools; outside the checkout)
    max_attempts: int = 2                 # planner invocations per request; a rejected plan is fed back once
    max_tasks: int = 4                    # at most one task per role, and at most this many tasks
    max_instructions_characters: int = 6000
    receipt_author: str = "github-actions[bot]"   # the only author whose approval receipts are read (evidence only)
    receipt_workflow: str = ".github/workflows/worker-issue-labels.yml"  # the run behind a receipt must be this
    receipt_wait_seconds: int = 900       # a `worker:approve` label without a receipt for this long is refused
    conventions_files: tuple[str, ...] = ("AGENTS.md", "CLAUDE.md")  # read from the checkout into the prompt
    max_conventions_characters: int = 6000


def validate_planning(config: Any) -> None:  # pylint: disable=too-many-branches
    """ConfigError unless `config.github.planning` is valid for this ProjectConfig."""
    planning, where = config.github.planning, "github.planning"
    if planning.enabled and not config.github.intake.enabled:
        raise ConfigError(f"{where}.enabled needs github.intake.enabled (an approved plan becomes an intake feature)")
    if planning.enabled:  # an existing configuration without this section keeps loading whatever it names
        profile = config.providers.get(planning.provider)
        if profile is None:
            raise ConfigError(f"{where}.provider: unknown provider {planning.provider!r}")
        if getattr(profile, "kind", None) is not ProviderKind.CLAUDE_CODE:
            raise ConfigError(f"{where}.provider must be a Claude Code provider (the subscription-authenticated CLI)")
        try:
            check_model(profile.kind, planning.model, where=where)
        except ProviderError as exc:
            raise ConfigError(str(exc)) from exc
    if not 60 <= planning.timeout_seconds <= 3600:
        raise ConfigError(f"{where}.timeout_seconds must be 60..3600")
    if not 1 <= planning.max_attempts <= 3:
        raise ConfigError(f"{where}.max_attempts must be 1..3 (each attempt is one subscription request)")
    if not 1 <= planning.max_tasks <= len(config.roles):
        raise ConfigError(f"{where}.max_tasks must be 1..{len(config.roles)} (at most one task per role)")
    if not 500 <= planning.max_instructions_characters <= 20000:
        raise ConfigError(f"{where}.max_instructions_characters must be 500..20000")
    if not _BOT_LOGIN_RE.fullmatch(planning.receipt_author):
        raise ConfigError(f"{where}.receipt_author must be a GitHub App bot login (`name[bot]`); an ordinary "
                          "account is never accepted as receipt author")
    if not _WORKFLOW_PATH_RE.fullmatch(planning.receipt_workflow):
        raise ConfigError(f"{where}.receipt_workflow must be a file under .github/workflows/")
    if not 60 <= planning.receipt_wait_seconds <= 86400:
        raise ConfigError(f"{where}.receipt_wait_seconds must be 60..86400")
    bad = [name for name in planning.conventions_files
           if not _FILE_RE.fullmatch(name) or any(seg in (".", "..") for seg in name.split("/"))]
    if bad:
        raise ConfigError(f"{where}.conventions_files must name repository files, got {bad}")
    if not 0 <= planning.max_conventions_characters <= 20000:
        raise ConfigError(f"{where}.max_conventions_characters must be 0..20000")


def planning_from_mapping(data: Mapping[str, Any]) -> GitHubPlanningConfig:
    """`github.planning` from YAML: strict, like `github.intake`; absent → off."""
    defaults = GitHubPlanningConfig()
    known = set(GitHubPlanningConfig.__dataclass_fields__)  # pylint: disable=no-member
    unknown = set(data) - known
    if unknown:
        raise ConfigError(f"unknown github.planning keys: {sorted(unknown)} (tokens and API keys never belong here)")
    values: dict[str, Any] = {}
    for key in known & set(data):
        default, raw = getattr(defaults, key), data[key]
        if isinstance(default, bool):
            if not isinstance(raw, bool):
                raise ConfigError(f"github.planning.{key} must be true or false")
            values[key] = raw
        elif isinstance(default, tuple):
            if not isinstance(raw, list) or not all(isinstance(v, str) for v in raw):
                raise ConfigError(f"github.planning.{key} must be a list of strings")
            values[key] = tuple(raw)
        elif isinstance(default, int):
            if isinstance(raw, bool) or not isinstance(raw, int):
                raise ConfigError(f"github.planning.{key} must be an integer")
            values[key] = raw
        else:
            if not isinstance(raw, str):
                raise ConfigError(f"github.planning.{key} must be a string")
            values[key] = raw
    return GitHubPlanningConfig(**values)
