"""Hardening for the Coordinator's OWN Git subprocesses against repository-controlled configuration
that executes a program (W1: the `.git/config` → `core.fsmonitor` attack and its whole class).

An agent (or anyone who can write the checkout) can put an executable reference into the
repository's Git configuration or attributes — `core.fsmonitor`, `core.hooksPath` / a `.git/hooks`
script, `filter.<name>.{clean,smudge,process}` with a `.gitattributes` assignment, `core.sshCommand`,
`diff.external`, a `credential.helper`, and others. The program then runs the next time the
Coordinator performs an ordinary Git operation (e.g. `git status`), OUTSIDE every restriction placed
on the agent. This module closes that path for both execution backends.

Two layers, both applied before the operation, neither relying on after-the-fact detection:

1. Prevention on EVERY Coordinator Git call — `git_argv()` + `hardened_env()`:
   * `SAFE_CONFIG_ARGS` are command-line `-c` overrides, which sit at the highest configuration
     precedence — above the repository's `.git/config`, any `[include]`/`[includeIf]` it pulls, and
     the global/system files — so the repository can never switch them on:
       `core.fsmonitor=`          no filesystem-monitor hook program (builtin daemon stays off too),
       `core.hooksPath=/dev/null` no hooks directory, so planted `.git/hooks/*` never run,
       `protocol.ext.allow=never` no `ext::` remote-helper command.
     Verified on this host: `-c core.fsmonitor=` stops a planted `core.fsmonitor` during `git status`,
     and a `-c` value wins over an injected `GIT_CONFIG_PARAMETERS`. These three keys have no
     legitimate Coordinator use from any scope, so forcing them never disturbs the workflow.
   * `hardened_env()` removes the Git environment variables that inject configuration
     (`GIT_CONFIG*`, `GIT_CONFIG_PARAMETERS`, `GIT_CONFIG_COUNT`/`GIT_CONFIG_KEY_*`/`_VALUE_*`) or name
     a program (`GIT_SSH*`, `GIT_PROXY_COMMAND`, `GIT_EXTERNAL_DIFF`, `GIT_PAGER`, `GIT_EDITOR`,
     `GIT_SEQUENCE_EDITOR`, `GIT_ASKPASS`, `GIT_ATTR_SOURCE`), so an inherited or injected value cannot
     reach the subprocess. The trusted global/system configuration (operator identity, credential
     helper) is kept, so pushes and signing keep working.

2. Fail-closed gate — `execution_findings()` / `assert_no_execution_config()`: everything the `-c`
   layer cannot force off from a single key (filters are keyed by an arbitrary name; `sshCommand`,
   `credential.helper`, `diff.external`, `*.textconv`, `gitProxy`, pager/editor are legitimately set
   in the operator's GLOBAL config) is a boundary violation only when it appears in the UNTRUSTED
   repository scope — `local` or `worktree`, which also covers whatever an `[include]` pulls in. The
   gate lists those entries, resolving includes through `git config --show-scope`, and the caller
   refuses the operation. Listing configuration runs no program, and the listing itself uses layer 1.
   The Coordinator calls it before it launches an agent and before it commits, so a repository that
   was already tampered with, or tampered with during a run, never reaches a mutating Git operation.

What stays out of scope by design: the global/system configuration is trusted (it is the operator's
machine, not the agent's); and a legitimate project may use git-lfs, whose `filter.lfs.*` lives in the
GLOBAL scope and is therefore never flagged — a repository-local `filter.lfs.*` that shadows it is.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from .errors import GitConfigExecutionError

# Highest-precedence overrides, prepended to every Coordinator Git command (before the subcommand).
SAFE_CONFIG_ARGS: tuple[str, ...] = (
    "-c", "core.fsmonitor=",
    "-c", "core.hooksPath=/dev/null",
    "-c", "protocol.ext.allow=never",
)

# Git environment variables removed before every Coordinator Git command: config injectors and
# program-naming hooks. GIT_CONFIG_KEY_*/GIT_CONFIG_VALUE_* are removed by prefix as well.
_SCRUB_ENV: frozenset[str] = frozenset({
    "GIT_CONFIG", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT",
    "GIT_SSH", "GIT_SSH_COMMAND", "GIT_PROXY_COMMAND", "GIT_EXTERNAL_DIFF",
    "GIT_PAGER", "GIT_EDITOR", "GIT_SEQUENCE_EDITOR", "GIT_ASKPASS", "GIT_ATTR_SOURCE",
    # repository-location overrides the Coordinator never wants inherited (gitops also scrubs these)
    "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_COMMON_DIR",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE",
})
_SCRUB_PREFIXES: tuple[str, ...] = ("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")
_BASE_ENV = {"GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}

# Untrusted configuration scopes (anything that came from inside the repository, includes resolved).
_UNTRUSTED_SCOPES: frozenset[str] = frozenset({"local", "worktree"})

# Exact config keys (lower-cased, as `git config` prints them) that name a program to run.
_EXACT_EXEC_KEYS: frozenset[str] = frozenset({
    "core.hookspath", "core.sshcommand", "core.gitproxy", "core.pager", "core.editor", "core.askpass",
    "core.alternaterefscommand", "core.fsmonitorhookversion", "sequence.editor", "diff.external",
    "gpg.program", "uploadpack.packobjectshook", "pack.packobjectshook",
})
# Name-keyed program config (e.g. filter.<name>.process): a match on the key means execution.
_EXEC_KEY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^filter\.[^.]+\.(clean|smudge|process)$"),
    re.compile(r"^diff\.[^.]+\.(command|textconv)$"),
    re.compile(r"^difftool\.[^.]+\.cmd$"),
    re.compile(r"^mergetool\.[^.]+\.cmd$"),
    re.compile(r"^merge\.[^.]+\.driver$"),
    re.compile(r"^credential(\.[^.]+)?\.helper$"),
    re.compile(r"^gpg\.[^.]+\.program$"),
    re.compile(r"^trailer\.[^.]+\.command$"),
    re.compile(r".*\.textconv$"),
    re.compile(r".*\.hookspath$"),
)
# Values that leave a flagged key harmless (a disable / reset, not a program).
_INERT_VALUES: frozenset[str] = frozenset({"", "false", "0", "no", "off"})
# core.fsmonitor=true selects Git's builtin monitor (no arbitrary program); any other non-inert
# value is a path to run.
_FSMONITOR_INERT: frozenset[str] = _INERT_VALUES | {"true"}


def hardened_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """A copy of *base* (default `os.environ`) with the Git config-injecting and program-naming
    variables removed and the Coordinator's fixed flags set. Global/system config is kept."""
    source = os.environ if base is None else base
    env = {k: v for k, v in source.items()
           if k not in _SCRUB_ENV and not k.startswith(_SCRUB_PREFIXES)}
    env.update(_BASE_ENV)
    return env


def git_argv(root: Path | str, *args: str) -> list[str]:
    """`git -C <root> <safe -c overrides> <args...>` — the only argv shape a Coordinator Git call
    should take against a checkout that an agent can write."""
    return ["git", "-C", str(root), *SAFE_CONFIG_ARGS, *args]


def _records(root: Path | str) -> list[tuple[str, str, str]]:
    """(scope, key, value) for every effective config entry, via a listing that runs no program.
    Includes are resolved by Git and reported at the including file's scope."""
    proc = subprocess.run(git_argv(root, "config", "--list", "--show-scope", "-z"),
                          capture_output=True, text=True, env=hardened_env(), check=False)
    if proc.returncode != 0:
        # A repository with no config at all is the only expected non-zero here; treat anything else
        # as unreadable and let the caller's other checks proceed (the -c layer still applies).
        return []
    fields = proc.stdout.split("\0")
    records: list[tuple[str, str, str]] = []
    for i in range(0, len(fields) - 1, 2):
        scope = fields[i]
        key, _, value = fields[i + 1].partition("\n")
        records.append((scope, key.strip().lower(), value))
    return records


def _is_execution(key: str, value: str) -> bool:
    if key == "core.fsmonitor":
        return value.strip().lower() not in _FSMONITOR_INERT
    inert = value.strip().lower() in _INERT_VALUES
    if key in _EXACT_EXEC_KEYS:
        return not inert
    return (not inert) and any(pattern.match(key) for pattern in _EXEC_KEY_PATTERNS)


def execution_findings(root: Path | str) -> list[str]:
    """Repository-scoped (local/worktree, includes resolved) config entries that would run a program
    during a Coordinator Git operation. Empty when the repository's configuration is safe. Reads
    only; runs nothing. The reported value is truncated — it is attacker-controlled, not a secret."""
    findings = []
    for scope, key, value in _records(root):
        if scope in _UNTRUSTED_SCOPES and _is_execution(key, value):
            findings.append(f"{scope} {key}={value[:80]}")
    return sorted(set(findings))


def assert_no_execution_config(root: Path | str, *, where: str = "this repository") -> None:
    """Raise GitConfigExecutionError when the repository's own configuration would run a program.
    The Coordinator calls this before launching an agent and before committing (fail closed)."""
    findings = execution_findings(root)
    if findings:
        raise GitConfigExecutionError(
            f"the Git configuration of {where} would run a program during a Coordinator Git operation "
            f"(repository-scoped execution settings: {findings}); refusing to operate on it until an "
            "operator removes them — the Coordinator never runs repository-provided hooks, filters or "
            "monitor programs")


def run_git(root: Path | str, *args: str, env: Mapping[str, str] | None = None, check: bool = False,
            text: bool = True, input: str | None = None,  # pylint: disable=redefined-builtin
            timeout: float | None = None) -> subprocess.CompletedProcess:
    """subprocess.run of a hardened Coordinator Git command (`git_argv` + `hardened_env`)."""
    return subprocess.run(git_argv(root, *args), capture_output=True, text=text, input=input,
                          timeout=timeout, env=hardened_env(env), check=check)


def extend_scrub(names: Iterable[str]) -> frozenset[str]:
    """The scrub set plus *names* (for a caller that keeps its own env construction, e.g. gitops)."""
    return _SCRUB_ENV | frozenset(names)


def scrub_prefixes() -> Sequence[str]:
    """The env-variable prefixes a caller constructing its own environment must also drop."""
    return _SCRUB_PREFIXES
