"""GitLab connection config: .env loading, host/project/token resolution.

Deliberately reads the repo-root .env — the opposite of scripts/testenvlib/envfile.py's
child_env(), which exists specifically to keep real secrets like this one *out* of the
test-environment's child processes. issues.py is not part of that isolation boundary:
it is the thing that legitimately needs GITLAB_TOKEN.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlsplit

from testenvlib.envfile import load_env_file
from testenvlib.paths import REPO_ROOT

ENV_FILE = REPO_ROOT / ".env"


class ConfigError(Exception):
    """Raised when required GitLab configuration is missing or invalid."""


@dataclass(frozen=True)
class GitLabConfig:
    host: str  # no trailing slash
    project: str  # "namespace/path" or a numeric project id (as a string)
    token: str


def load_config() -> GitLabConfig:
    """Resolve (host, project, token), preferring real process env over .env.

    Reading the real process environment first (rather than only the .env file) lets a
    CI job or a differently-configured shell inject these without editing the file, the
    same precedence python-dotenv-style tools generally use.
    """
    file_values = load_env_file(ENV_FILE)

    def resolve(key: str) -> str:
        return os.environ.get(key) or file_values.get(key, "")

    host = resolve("GITLAB_HOST").rstrip("/")
    project = resolve("GITLAB_PROJECT")
    token = resolve("GITLAB_TOKEN")

    missing = [name for name, value in (("GITLAB_HOST", host), ("GITLAB_PROJECT", project), ("GITLAB_TOKEN", token)) if not value]
    if missing:
        raise ConfigError(
            f"Missing from {ENV_FILE} (or the process environment): {', '.join(missing)}. "
            "See test_environment/README.md or implementation_plan.md Workstream 2 §9.",
        )

    return GitLabConfig(host=host, project=project, token=token)


def is_insecure_remote_host(host: str) -> bool:
    """True when *host* is plain HTTP and not loopback — a PAT would cross the network in
    the clear on every call. Used by `issues.py doctor` to warn, not to block: the plan's
    own finding is that the self-hosted instance has no TLS, so this is the expected
    state until that's fixed, not necessarily a misconfiguration.
    """
    parsed = urlsplit(host)
    if parsed.scheme != "http":
        return False
    hostname = (parsed.hostname or "").lower()
    return hostname not in ("localhost", "127.0.0.1", "::1")
