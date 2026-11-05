"""W4.6.1 (R1): the `github.review` configuration section — the advisory, independent AI review of an approved
GitHub Issue's Draft PR (review.py).

Kept out of config.py, which loads it into `GitHubConfig.review` and calls `validate_review()`. Off by default.
R1 is deliberately small: a Claude Code reviewer (no tools, the operator's subscription) started only by an
operator with `python -m feature_coordinator github review run FEATURE --apply`. The Worker service never
starts a review, there is no fallback provider and no Codex reviewer yet, and nothing here can merge.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .errors import ConfigError, ProviderError
from .providers import ProviderKind, check_model

INDEPENDENCE_MODES = ("prefer", "require")


@dataclass(frozen=True)
class GitHubReviewConfig:  # pylint: disable=too-many-instance-attributes
    """W4.6.1: the independent AI review of a published Draft PR (review.py). Off until an operator enables it."""

    enabled: bool = False
    provider: str = "claude"              # a Claude Code provider profile: its subscription login, never an API key
    model: str = "opus"                   # validated like a role model ("default": the CLI's own default)
    independence: str = "prefer"          # prefer | require: require refuses a reviewer no more independent than
                                          # the implementing agents (same provider kind and model)
    timeout_seconds: int = 600            # one reviewer invocation (no tools; outside the checkout)
    max_attempts: int = 1                 # reviewer invocations per review entry (each one subscription request)
    unavailable_wait_seconds: int = 3600  # a reviewer unavailable for this long makes the review ERROR
    max_diff_characters: int = 150000     # a larger diff is not reviewed (ERROR), never truncated
    max_findings: int = 30                # more findings than this is invalid reviewer output


def validate_review(config: Any) -> None:
    """ConfigError unless `config.github.review` is valid for this ProjectConfig."""
    review, where = config.github.review, "github.review"
    if review.enabled and not (config.github.intake.enabled and config.github.publish.enabled):
        raise ConfigError(f"{where}.enabled needs github.intake.enabled and github.publish.enabled (only the "
                          "published Draft PR of an approved GitHub Issue is reviewed)")
    if review.enabled:  # an existing configuration without this section keeps loading whatever it names
        profile = config.providers.get(review.provider)
        if profile is None:
            raise ConfigError(f"{where}.provider: unknown provider {review.provider!r}")
        if getattr(profile, "kind", None) is not ProviderKind.CLAUDE_CODE:
            raise ConfigError(f"{where}.provider must be a Claude Code provider (R1: the subscription-authenticated "
                              "Claude Code CLI without tools; a Codex reviewer is not implemented)")
        try:
            check_model(profile.kind, review.model, where=where)
        except ProviderError as exc:
            raise ConfigError(str(exc)) from exc
    if review.independence not in INDEPENDENCE_MODES:
        raise ConfigError(f"{where}.independence must be one of {list(INDEPENDENCE_MODES)}")
    bounds = {"timeout_seconds": (60, 1800), "max_attempts": (1, 3), "unavailable_wait_seconds": (60, 86400),
              "max_diff_characters": (1000, 400000), "max_findings": (1, 100)}
    for key, (low, high) in bounds.items():
        if not low <= getattr(review, key) <= high:
            raise ConfigError(f"{where}.{key} must be {low}..{high}")


def review_from_mapping(data: Mapping[str, Any]) -> GitHubReviewConfig:
    """`github.review` from YAML: strict, like `github.planning`; absent → off."""
    defaults = GitHubReviewConfig()
    known = set(GitHubReviewConfig.__dataclass_fields__)  # pylint: disable=no-member
    unknown = set(data) - known
    if unknown:
        raise ConfigError(f"unknown github.review keys: {sorted(unknown)} (tokens and API keys never belong here)")
    values: dict[str, Any] = {}
    for key in known & set(data):
        default, raw = getattr(defaults, key), data[key]
        if isinstance(default, bool):
            if not isinstance(raw, bool):
                raise ConfigError(f"github.review.{key} must be true or false")
        elif isinstance(default, int):
            if isinstance(raw, bool) or not isinstance(raw, int):
                raise ConfigError(f"github.review.{key} must be an integer")
        elif not isinstance(raw, str):
            raise ConfigError(f"github.review.{key} must be a string")
        values[key] = raw
    return GitHubReviewConfig(**values)
