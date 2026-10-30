"""Minimal .env-style file I/O, and the allowlist-based child environment builder.

`.testenv/test.env` is read by three different consumers: `docker compose --env-file`
(which has its own parser), and this module's `load_env_file` (native mode's `app.py`,
`stub_server.py` readiness checks, and `testenv.py`). Keep the format to plain
`KEY=VALUE` lines, one per line, no quoting, no export, no comments-after-value — the
lowest common denominator both parsers agree on.
"""

from __future__ import annotations

import os
from pathlib import Path

# Real provider keys live only in the repo-root .env, exported into the shell by
# scripts/app.sh / scripts/deploy.sh. Host environment variables beat Compose's
# --env-file (Compose v2 precedence), so a real key exported into this shell would
# otherwise silently win over the dummy value in .testenv/test.env. The allowlist is
# the fix: child processes (native mode) and `docker compose` invocations are built
# from *this* set, plus whatever .testenv/test.env supplies — never from a copy of
# os.environ. An allowlist, not a blocklist: a blocklist silently leaks whatever key
# gets added to the repo .env next month.
ALLOWED_HOST_ENV_KEYS = frozenset({
    "PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "TERM", "SSL_CERT_FILE",
})

# Key fragments that must never appear in a child environment — used by `doctor`'s
# secret scan, not by child_env() itself (child_env() already excludes them structurally
# by only ever merging the allowlist with .testenv/test.env's own dummy values).
SUSPICIOUS_KEY_PREFIXES = ("sk-", "sk_", "glpat-", "ghp_")


def parse_env_text(text: str) -> dict[str, str]:
    """Parse `KEY=VALUE` lines into a dict. Blank lines and `#` comments are skipped."""
    result: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        result[key] = value.strip()
    return result


def load_env_file(path: Path) -> dict[str, str]:
    """Return the KEY=VALUE mapping in *path*, or {} if it doesn't exist yet."""
    if not path.is_file():
        return {}
    return parse_env_text(path.read_text(encoding="utf-8"))


def write_env_file(path: Path, values: dict[str, str], *, header: str = "") -> None:
    """Write *values* as `KEY=VALUE` lines, sorted for a stable diff between reseeds."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    if header:
        lines.extend(f"# {line}" for line in header.splitlines())
        lines.append("")
    for key in sorted(values):
        lines.append(f"{key}={values[key]}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def child_env(test_env: dict[str, str]) -> dict[str, str]:
    """Build the environment for a native-mode child process or a Compose invocation.

    Only PATH/HOME/etc. (ALLOWED_HOST_ENV_KEYS) survive from the real host environment;
    every application setting comes from *test_env* (normally `.testenv/test.env`).
    A real OPENAI_API_KEY exported by a sourced scripts/app.sh in this shell is
    structurally excluded, not merely overridden — it is never read in the first place.
    """
    base = {k: v for k, v in os.environ.items() if k in ALLOWED_HOST_ENV_KEYS}
    return {**base, **test_env}


def find_leaked_secrets(env: dict[str, str], repo_dotenv_values: dict[str, str]) -> list[str]:
    """Return a list of human-readable problems: suspicious key formats or values that
    also appear in the repo's real .env. Used by `doctor`; empty means clean.
    """
    problems: list[str] = []
    repo_values = {v for v in repo_dotenv_values.values() if v}
    for key, value in env.items():
        if not value:
            continue
        if any(value.startswith(prefix) for prefix in SUSPICIOUS_KEY_PREFIXES):
            problems.append(f"{key} looks like a real credential (matches a known key prefix)")
        elif value in repo_values:
            problems.append(f"{key} has the same value as a key in the repo's real .env")
    return problems
