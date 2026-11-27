"""W4.8.1: the `github.repair` configuration section — autonomous repair of an approved GitHub Issue's Draft PR after
its independent AI review requested changes (repair.py).

Kept out of config.py, which loads it into `GitHubConfig.repair` and calls `validate_repair()`. Off by default: an
operator enables it explicitly. It needs the AI review (`github.review.enabled`, which itself needs intake and
publication): a repair only ever answers a REQUEST_CHANGES review of the publisher's verified head. At most two
attempts per feature (a fixed ceiling, like the other v5 bounds); nothing here can merge.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .errors import ConfigError

MAX_REPAIR_ATTEMPTS = 2  # the ceiling: a configuration can lower it, never raise it


@dataclass(frozen=True)
class GitHubRepairConfig:
    """W4.8.1: the autonomous repair of a published Draft PR (repair.py). Off until an operator enables it."""

    enabled: bool = False
    max_attempts: int = 2              # repair attempts per feature, each counted BEFORE its agent starts (1..2)
    poll_interval_seconds: int = 60    # the Worker service's repair step at most this often (10..3600)


def validate_repair(config: Any) -> None:
    """ConfigError unless `config.github.repair` is valid for this ProjectConfig."""
    repair, where = config.github.repair, "github.repair"
    if repair.enabled and not config.github.review.enabled:
        raise ConfigError(f"{where}.enabled needs github.review.enabled (a repair answers the independent AI review of "
                          "an approved GitHub Issue's Draft PR, which needs github.intake and github.publish)")
    bounds = {"max_attempts": (1, MAX_REPAIR_ATTEMPTS), "poll_interval_seconds": (10, 3600)}
    for key, (low, high) in bounds.items():
        if not low <= getattr(repair, key) <= high:
            raise ConfigError(f"{where}.{key} must be {low}..{high}")


def repair_from_mapping(data: Mapping[str, Any]) -> GitHubRepairConfig:
    """`github.repair` from YAML: strict, like `github.review`; absent → off."""
    defaults = GitHubRepairConfig()
    known = set(GitHubRepairConfig.__dataclass_fields__)  # pylint: disable=no-member
    unknown = set(data) - known
    if unknown:
        raise ConfigError(f"unknown github.repair keys: {sorted(unknown)} (tokens and API keys never belong here)")
    values: dict[str, Any] = {}
    for key in known & set(data):
        default, raw = getattr(defaults, key), data[key]
        if isinstance(default, bool):
            if not isinstance(raw, bool):
                raise ConfigError(f"github.repair.{key} must be true or false")
        elif isinstance(raw, bool) or not isinstance(raw, int):
            raise ConfigError(f"github.repair.{key} must be an integer")
        values[key] = raw
    return GitHubRepairConfig(**values)
