"""W4.7 (M1): the `github.integration` configuration section — integration jobs for an EXISTING branch or PR
(integration.py, integration_intake.py; design: docs_agents/coordination/existing-branch-integration-design.md).

Kept out of config.py, which loads it into `GitHubConfig.integration`; the section validates itself on construction
(it depends on no other section). Off by default: `python -m feature_coordinator integration intake --apply` refuses
to record a job until an operator enables it. M1 records and checks jobs only — no Git write, no PR, never a merge.

    enabled          false (default) | true
    known_authors    commit author e-mail addresses accepted at intake (compared case-insensitively). Every commit
                     the source adds to the base must be authored by one of them; anything else is REFUSED (design
                     §3 `provenance`). Empty while enabled is a configuration error (every intake would be refused).

The permitted source repository is `github.repository` itself (the M1 allowlist; refs are resolved through the
checkout's `origin`, which must be exactly that repository). Remote reads use `github.publish.git_timeout_seconds`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from .errors import ConfigError

_EMAIL_RE = re.compile(r"[^@\s<>\"',;]{1,64}@[^@\s<>\"',;]{1,190}")
MAX_KNOWN_AUTHORS = 100


@dataclass(frozen=True)
class GitHubIntegrationConfig:
    """W4.7: integration jobs of existing branches/PRs (integration.py). Off until an operator enables it."""

    enabled: bool = False
    known_authors: tuple[str, ...] = ()   # commit author e-mails (lowercase) accepted at intake

    def __post_init__(self) -> None:
        validate_integration(self)


def validate_integration(integration: GitHubIntegrationConfig) -> None:
    """ConfigError unless the `github.integration` section is valid."""
    where = "github.integration"
    if not isinstance(integration.enabled, bool):
        raise ConfigError(f"{where}.enabled must be true or false")
    if not isinstance(integration.known_authors, tuple):
        raise ConfigError(f"{where}.known_authors must be a list of e-mail addresses")
    bad = [a for a in integration.known_authors if not isinstance(a, str) or not _EMAIL_RE.fullmatch(a)
           or a != a.lower()]
    if bad:
        raise ConfigError(f"{where}.known_authors must be lowercase e-mail addresses; invalid: {bad[:5]}")
    if len(integration.known_authors) > MAX_KNOWN_AUTHORS:
        raise ConfigError(f"{where}.known_authors may list at most {MAX_KNOWN_AUTHORS} addresses")
    if len(set(integration.known_authors)) != len(integration.known_authors):
        raise ConfigError(f"{where}.known_authors lists an address more than once")
    if integration.enabled and not integration.known_authors:
        raise ConfigError(f"{where}.enabled needs at least one {where}.known_authors address (an intake with no "
                          "known author would refuse every job)")


def integration_from_mapping(data: Mapping[str, Any]) -> GitHubIntegrationConfig:
    """`github.integration` from YAML: strict, like `github.review`; absent → off."""
    known = set(GitHubIntegrationConfig.__dataclass_fields__)  # pylint: disable=no-member
    unknown = set(data) - known
    if unknown:
        raise ConfigError(f"unknown github.integration keys: {sorted(unknown)} (tokens and remotes never belong here)")
    values: dict[str, Any] = {}
    if "enabled" in data:
        if not isinstance(data["enabled"], bool):
            raise ConfigError("github.integration.enabled must be true or false")
        values["enabled"] = data["enabled"]
    if "known_authors" in data:
        raw = data["known_authors"] or ()
        if isinstance(raw, (str, bytes)) or not isinstance(raw, (list, tuple)):
            raise ConfigError("github.integration.known_authors must be a list of e-mail addresses")
        if not all(isinstance(item, str) for item in raw):
            raise ConfigError("github.integration.known_authors must be a list of e-mail addresses")
        values["known_authors"] = tuple(item.strip().lower() for item in raw)
    return GitHubIntegrationConfig(**values)
