"""Issue-tracker configuration: which tracker and where (testenv.toml [issues]), and its
token (.env).

Two halves, deliberately kept apart:
  - IssuesConfig / GitLabIssuesConfig / GitHubIssuesConfig — the non-secret shape of
    testenv.toml's [issues] section (tracker choice, GitLab host/project, GitHub
    repo/project settings). Parsed by scripts/mxfix/config.py along with the rest of
    testenv.toml, testenv.local.toml and CLI overrides included. testenv.toml is tracked
    in git, so nothing secret ever goes there.
  - load_gitlab_token() / load_github_token() — the tokens, from the repo-root .env.

Deliberately reads the repo-root .env — the opposite of scripts/testenvlib/envfile.py's
child_env(), which exists specifically to keep real secrets like these *out* of the
test-environment's child processes. issues.py is not part of that isolation boundary:
it is the thing that legitimately needs these tokens.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from testenvlib.envfile import load_env_file
from testenvlib.paths import REPO_ROOT

from .tracker import STATUSES

ENV_FILE = REPO_ROOT / ".env"

TRACKERS = ("gitlab", "github")


class ConfigError(Exception):
    """Raised when required issue-tracker configuration is missing or invalid."""


# ---------------------------------------------------------------------------
# testenv.toml [issues]
# ---------------------------------------------------------------------------

DEFAULT_GITHUB_STATUS_OPTIONS: dict[str, str] = {
    "open": "Open",
    "in-progress": "In progress",
    "needs-human": "Needs human",
    "done": "Done",
}


@dataclass(frozen=True)
class GitLabIssuesConfig:
    host: str = ""  # e.g. "http://82.103.77.45", or "http://127.0.0.1:8080" via an SSH tunnel
    project: str = ""  # "namespace/path" or a numeric project id (as a string)


@dataclass(frozen=True)
class GitHubIssuesConfig:
    repo: str = ""  # "owner/repo"; empty = parse the origin remote
    project_owner: str = ""  # user or org login owning the Project; empty = the repo's owner
    project_number: int = 0  # from the project URL (.../projects/<N>); 0 = not configured
    status_field: str = "mx-status"
    status_options: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_GITHUB_STATUS_OPTIONS))


@dataclass(frozen=True)
class IssuesConfig:
    tracker: str = "gitlab"
    gitlab: GitLabIssuesConfig = field(default_factory=GitLabIssuesConfig)
    github: GitHubIssuesConfig = field(default_factory=GitHubIssuesConfig)

    def validate(self) -> None:
        if self.tracker not in TRACKERS:
            raise ConfigError(f"[issues] tracker must be one of {TRACKERS}, got {self.tracker!r}")
        gl = self.gitlab
        if gl.host and urlsplit(gl.host).scheme not in ("http", "https"):
            raise ConfigError(f"[issues.gitlab] host must be an http:// or https:// URL, got {gl.host!r}")
        gh = self.github
        if gh.repo and gh.repo.count("/") != 1:
            raise ConfigError(f"[issues.github] repo must look like 'owner/repo', got {gh.repo!r}")
        if gh.project_number < 0:
            raise ConfigError("[issues.github] project_number must be >= 1 (0 = not configured)")
        if not gh.status_field.strip():
            raise ConfigError("[issues.github] status_field must not be empty")
        missing = [s for s in STATUSES if not gh.status_options.get(s, "").strip()]
        unknown = sorted(set(gh.status_options) - set(STATUSES))
        if missing or unknown:
            raise ConfigError(
                f"[issues.github.status_options] needs exactly the keys {STATUSES}"
                + (f"; missing/empty: {missing}" if missing else "")
                + (f"; unknown: {unknown}" if unknown else ""),
            )
        names = [gh.status_options[s] for s in STATUSES]
        if len(set(names)) != len(names):
            raise ConfigError(f"[issues.github.status_options] option names must be distinct, got {names}")


# ---------------------------------------------------------------------------
# .env secrets
# ---------------------------------------------------------------------------

def _resolver():
    """Prefer real process env over .env — lets a CI job or a differently-configured
    shell inject these without editing the file, the same precedence python-dotenv-style
    tools generally use.
    """
    file_values = load_env_file(ENV_FILE)

    def resolve(key: str) -> str:
        return os.environ.get(key) or file_values.get(key, "")

    return resolve


@dataclass(frozen=True)
class GitLabConfig:
    """Everything the GitLab backend needs to connect, resolved from both halves."""

    host: str  # no trailing slash
    project: str
    token: str


# Keys that used to live in .env before [issues.gitlab] existed — only ever read to
# point a stale setup at the new home, never used as values.
_MOVED_GITLAB_KEYS = {"GITLAB_HOST": "host", "GITLAB_PROJECT": "project"}


def load_gitlab_config(settings: GitLabIssuesConfig) -> GitLabConfig:
    """host/project from testenv.toml's [issues.gitlab]; the token from .env's GITLAB_TOKEN."""
    resolve = _resolver()
    host = settings.host.rstrip("/")
    project = settings.project

    missing_settings = [key for key, value in (("host", host), ("project", project)) if not value]
    if missing_settings:
        stale = [f"{env_key}={resolve(env_key)}" for env_key, key in _MOVED_GITLAB_KEYS.items() if key in missing_settings and resolve(env_key)]
        hint = (
            f" Your environment still sets {', '.join(stale)} — GitLab's host/project moved from .env to "
            "testenv.toml's [issues.gitlab]; copy the value(s) there and delete them from .env."
            if stale else ""
        )
        raise ConfigError(
            f"[issues.gitlab] {' and '.join(missing_settings)} not set in testenv.toml (or testenv.local.toml).{hint} "
            "See test_environment/README.md's \"GitLab setup\" section.",
        )

    token = resolve("GITLAB_TOKEN")
    if not token:
        raise ConfigError(
            f"GITLAB_TOKEN is missing from {ENV_FILE} (or the process environment). "
            "See test_environment/README.md's \"GitLab setup\" section.",
        )
    return GitLabConfig(host=host, project=project, token=token)


def load_github_token() -> str:
    """GITHUB_PAT — the same token scripts/mxfix/git_target.py opens PRs with."""
    token = _resolver()("GITHUB_PAT")
    if not token:
        raise ConfigError(
            f"GITHUB_PAT is missing from {ENV_FILE} (or the process environment) — the GitHub issue "
            "tracker needs it. See test_environment/README.md's \"Issue tracking\" section for the "
            "scopes it needs.",
        )
    return token


def is_insecure_remote_host(host: str) -> bool:
    """True when *host* is plain HTTP and not loopback — a token would cross the network
    in the clear on every call. Used by `issues.py doctor` to warn, not to block: the
    plan's own finding is that the self-hosted GitLab has no TLS, so this is the expected
    state until that's fixed, not necessarily a misconfiguration.
    """
    parsed = urlsplit(host)
    if parsed.scheme != "http":
        return False
    hostname = (parsed.hostname or "").lower()
    return hostname not in ("localhost", "127.0.0.1", "::1")
