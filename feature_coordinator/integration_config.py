"""W4.7 (M1, M2): the `github.integration` configuration section — integration jobs for an EXISTING branch or PR
(integration.py, integration_intake.py, integration_inspect.py; design:
docs_agents/coordination/existing-branch-integration-design.md).

Kept out of config.py, which loads it into `GitHubConfig.integration`; the section validates itself on construction
(it depends on no other section). Off by default: `python -m feature_coordinator integration intake --apply` refuses
to record a job until an operator enables it. M1/M2 record, check and inspect jobs only — no Git write, no PR, never a
merge.

    enabled          false (default) | true
    known_authors    commit author e-mail addresses accepted at intake (compared case-insensitively). Every commit
                     the source adds to the base must be authored by one of them; anything else is REFUSED (design
                     §3 `provenance`). Empty while enabled is a configuration error (every intake would be refused).
                     An e-mail is a CLAIM, never proof of identity (M2: see trusted_logins).
    operators        (M2) GitHub logins that may sign an operator authorization record (`integration approve`).
                     Empty: no approval can be recorded (approve refuses).
    allowed_signers  (M2) absolute path of the OpenSSH allowed-signers file (`ssh-keygen -Y verify -f`) that binds each
                     operator login to the public key(s) they sign authorization records with. It must live outside
                     every repository checkout, be a regular file owned by the operator and writable by nobody else.
                     Empty: no approval can be recorded.
    trusted_logins   (M2) GitHub logins whose GitHub attribution and GitHub-verified commit signatures are trusted for
                     provenance at inspection. A commit that GitHub attributes to, or that is signed by, any OTHER login
                     is REFUSED. Empty: `integration inspect` refuses to run.

The permitted source repository is `github.repository` itself (the M1 allowlist; refs are resolved through the
checkout's `origin`, which must be exactly that repository). Remote reads use `github.publish.git_timeout_seconds`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from .errors import ConfigError

_EMAIL_RE = re.compile(r"[^@\s<>\"',;]{1,64}@[^@\s<>\"',;]{1,190}")
_LOGIN_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")
MAX_KNOWN_AUTHORS = 100
MAX_LOGINS = 100
MAX_PATH = 4096


@dataclass(frozen=True)
class GitHubIntegrationConfig:
    """W4.7: integration jobs of existing branches/PRs (integration.py). Off until an operator enables it."""

    enabled: bool = False
    known_authors: tuple[str, ...] = ()   # commit author e-mails (lowercase) accepted at intake
    operators: tuple[str, ...] = ()       # M2: logins that may sign authorization records
    allowed_signers: str = ""             # M2: absolute path of the OpenSSH allowed-signers file
    trusted_logins: tuple[str, ...] = ()  # M2: logins trusted for GitHub attribution and verified signatures

    def __post_init__(self) -> None:
        validate_integration(self)


def _check_logins(value: Any, where: str) -> None:
    if not isinstance(value, tuple) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"{where} must be a list of GitHub logins")
    bad = [v for v in value if not _LOGIN_RE.fullmatch(v)]
    if bad:
        raise ConfigError(f"{where} must be GitHub logins (letters, digits, '-'); invalid: {bad[:5]}")
    if len(value) > MAX_LOGINS:
        raise ConfigError(f"{where} may list at most {MAX_LOGINS} logins")
    if len({v.lower() for v in value}) != len(value):
        raise ConfigError(f"{where} lists a login more than once (logins are case-insensitive)")


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
    _check_logins(integration.operators, f"{where}.operators")
    _check_logins(integration.trusted_logins, f"{where}.trusted_logins")
    signers = integration.allowed_signers
    if not isinstance(signers, str) or len(signers) > MAX_PATH or re.search(r"[\x00-\x1f\x7f]", signers) \
            or (signers and not signers.startswith("/")):
        raise ConfigError(f"{where}.allowed_signers must be an absolute file path (or empty)")


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
    for name in ("operators", "trusted_logins"):
        if name in data:
            raw = data[name] or ()
            if isinstance(raw, (str, bytes)) or not isinstance(raw, (list, tuple)) \
                    or not all(isinstance(item, str) for item in raw):
                raise ConfigError(f"github.integration.{name} must be a list of GitHub logins")
            values[name] = tuple(item.strip() for item in raw)
    if "allowed_signers" in data:
        raw = data["allowed_signers"]
        if raw is not None and not isinstance(raw, str):
            raise ConfigError("github.integration.allowed_signers must be an absolute file path")
        values["allowed_signers"] = (raw or "").strip()
    return GitHubIntegrationConfig(**values)
