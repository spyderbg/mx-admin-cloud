"""Hardening for the Coordinator's OWN Git subprocesses against repository-controlled configuration
that executes a program (W1: the `.git/config` → `core.fsmonitor` attack and its whole class; Security S2).

An agent (or anyone who can write the checkout) can put an executable reference into the
repository's Git configuration or attributes — `core.fsmonitor`, `core.hooksPath` / a `.git/hooks`
script, `filter.<name>.{clean,smudge,process}` with a `.gitattributes` assignment, `core.sshCommand`,
`remote.<name>.uploadpack`, `diff.external`, a `credential.helper`, and others. The program then runs the
next time the Coordinator performs an ordinary Git operation (e.g. `git status`), OUTSIDE every restriction
placed on the agent. This module closes that path for both execution backends.

Three layers, all applied before the operation, none relying on after-the-fact detection:

1. Prevention on EVERY Coordinator Git call — `git_argv()` + `hardened_env()`:
   * `SAFE_CONFIG_ARGS` are command-line `-c` overrides, which sit at the highest configuration
     precedence — above the repository's `.git/config`, any `[include]`/`[includeIf]` it pulls, and
     the global/system files — so the repository can never switch them on:
       `core.fsmonitor=`            no filesystem-monitor hook program (builtin daemon stays off too),
       `core.hooksPath=/dev/null`   no hooks directory, so planted `.git/hooks/*` never run,
       `protocol.ext.allow=never`   no `ext::` remote-helper command,
       `submodule.recurse=false`, `push.recurseSubmodules=no`, `diff.submodule=short`
                                    no child Git inside an embedded repository, whose own configuration no scan of
                                    this checkout covers (S2; `status`: see guarded_argv),
       `gc.auto=0`, `maintenance.auto=false`
                                    no automatic maintenance: it runs repository-configured programs, detached, after
                                    the command — and its scan — are over,
       `--no-pager`                 no `pager.<command>` program on an operator's terminal.
     Verified on this host: `-c core.fsmonitor=` stops a planted `core.fsmonitor` during `git status`,
     and a `-c` value wins over an injected `GIT_CONFIG_PARAMETERS`. These keys have no
     legitimate Coordinator use from any scope, so forcing them never disturbs the workflow.
   * `hardened_env()` removes the Git environment variables that inject configuration
     (`GIT_CONFIG*`, `GIT_CONFIG_PARAMETERS`, `GIT_CONFIG_COUNT`/`GIT_CONFIG_KEY_*`/`_VALUE_*`) or name
     a program (`GIT_SSH*`, `GIT_PROXY_COMMAND`, `GIT_EXTERNAL_DIFF`, `GIT_PAGER`, `GIT_EDITOR`,
     `GIT_SEQUENCE_EDITOR`, `GIT_ASKPASS`, `GIT_ATTR_SOURCE`), so an inherited or injected value cannot
     reach the subprocess. The trusted global/system configuration (operator identity, credential
     helper) is kept, so pushes and signing keep working.

2. Fail-closed scan — `execution_findings()` / `assert_no_execution_config()`: everything the `-c`
   layer cannot force off from a single key (filters are keyed by an arbitrary name; `sshCommand`,
   `credential.helper`, `diff.external`, `*.textconv`, `gitProxy`, pager/editor are legitimately set
   in the operator's GLOBAL config) is a boundary violation only when it appears in the UNTRUSTED
   repository scope — `local` or `worktree`, which also covers whatever an `[include]` pulls in. The
   scan lists those entries, resolving includes through `git config --show-scope`, and the caller
   refuses the operation. Listing configuration runs no program, and the listing itself uses layer 1. A
   configuration that cannot be listed is refused too (S2). This is the ONE scanner: the integration jobs
   (integration_isolation.py) use it as well.

3. No Worker Git command without layer 2 (S2) — `guarded_argv()` / `run_git()`: every Git command the Worker runs on
   the checkout an agent can write is built by them, so the scan runs immediately before EACH command, not once at
   chosen places (`test_fc_gitsafe_s2.py` checks that no other module builds one). Transports are limited to
   ALLOWED_PROTOCOLS: a URL or `vcs` setting cannot select a `git-remote-<name>` program. Around an agent run,
   git_control.py additionally compares the Git control files before any Git command runs.

What stays out of scope by design: the global/system configuration is trusted (it is the operator's
machine, not the agent's); and a legitimate project may use git-lfs, whose `filter.lfs.*` lives in the
GLOBAL scope and is therefore never flagged — a repository-local `filter.lfs.*` that shadows it is, and so are the
repository-local git-lfs settings that name a program (`lfs.extension.*`, `lfs.customtransfer.*`).
Residual race: the scan and the command it precedes are two processes, and Git reads the configuration again in the
second. A process that rewrites `.git/config` in between (milliseconds) is not seen by that scan; agents are stopped
before their run is judged, and nothing else may write the checkout (feature-workflow.md §26).
"""

from __future__ import annotations

import errno
import os
import subprocess
import time
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from .errors import GitConfigExecutionError

# Highest-precedence overrides, prepended to every Coordinator Git command (before the subcommand).
SAFE_CONFIG_ARGS: tuple[str, ...] = (
    "--no-pager",
    "-c", "core.fsmonitor=",
    "-c", "core.hooksPath=/dev/null",
    "-c", "protocol.ext.allow=never",
    "-c", "submodule.recurse=false",
    "-c", "push.recurseSubmodules=no",
    "-c", "diff.submodule=short",
    "-c", "gc.auto=0",
    "-c", "maintenance.auto=false",
)

# The transports a Worker Git command may use (GIT_ALLOW_PROTOCOL overrides every protocol.* setting): the origin is
# an https or ssh GitHub URL (publish.py); `file` is for local remotes. Integration jobs set their own, stricter limits.
ALLOWED_PROTOCOLS = "file:https:ssh"

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

# Exact config keys (lower-cased) whose value names a program to run.
_EXACT_EXEC_KEYS: frozenset[str] = frozenset({
    "core.hookspath", "core.sshcommand", "core.gitproxy", "core.pager", "core.editor", "core.askpass",
    "core.alternaterefscommand", "core.fsmonitorhookversion", "sequence.editor", "diff.external",
    "gpg.program", "uploadpack.packobjectshook", "pack.packobjectshook",
    "gc.recentobjectshook",     # run by a gc
})
# Name-keyed program settings: section → final key names, for ANY subsection (one that contains dots included).
_EXEC_KEYS_BY_SECTION: Mapping[str, frozenset[str]] = {
    "filter": frozenset({"clean", "smudge", "process"}),
    "diff": frozenset({"command", "textconv"}),
    "difftool": frozenset({"cmd", "path"}),
    "mergetool": frozenset({"cmd", "path"}),
    "merge": frozenset({"driver"}),
    "credential": frozenset({"helper"}),
    "gpg": frozenset({"program", "defaultkeycommand"}),
    "trailer": frozenset({"command", "cmd"}),
    "remote": frozenset({"uploadpack", "receivepack", "vcs"}),  # run by ls-remote, fetch and push
    "hook": frozenset({"command"}),  # configuration-based hooks (newer Git)
}
# git-lfs (run by the operator's global filter): these name a program, or choose and parameterise one the operator's
# global configuration names (the arguments go through a shell), whatever their last component.
_LFS_PROGRAM_PREFIXES = ("lfs.customtransfer.", "lfs.extension.")
_EXEC_KEYS_ANY_SECTION: frozenset[str] = frozenset({"textconv", "hookspath"})
# core.fsmonitor is a boolean or a program: these values run no program (`true` is Git's builtin monitor). For every
# other flagged key only the EMPTY value is a reset: `filter.x.clean = off` runs a program called `off`.
_FSMONITOR_INERT: frozenset[str] = frozenset({"", "false", "0", "no", "off", "true", "1", "yes", "on"})
# The host is short of something for a moment: says nothing about a configuration or a run, and no agent that has ended
# causes it (a full disk can be a run's doing: it is not one of them).
TRANSIENT_ERRNO = frozenset({errno.EMFILE, errno.ENFILE, errno.ENOMEM, errno.EINTR, errno.EAGAIN})
LIST_ATTEMPTS, LIST_PAUSE = 4, 0.1
LIST_TIMEOUT = 30.0   # seconds to list a checkout's configuration (an include may name a FIFO) before it is refused


def hardened_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """A copy of *base* (default `os.environ`) with the Git config-injecting and program-naming
    variables removed and the Coordinator's fixed flags set. Global/system config is kept."""
    source = os.environ if base is None else base
    env = {k: v for k, v in source.items()
           if k not in _SCRUB_ENV and not k.startswith(_SCRUB_PREFIXES)}
    env.update(_BASE_ENV)
    return env


def guarded_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """hardened_env() with the transports limited to ALLOWED_PROTOCOLS (the environment of a guarded_argv command)."""
    return {**hardened_env(base), "GIT_ALLOW_PROTOCOL": ALLOWED_PROTOCOLS}


def git_argv(root: Path | str, *args: str) -> list[str]:
    """`git -C <root> <safe -c overrides> <args...>` — layer 1 only. For a checkout that an agent can write use
    guarded_argv()/run_git(); this one is for the scan itself and for coordinator-owned repositories."""
    return ["git", "-C", str(root), *SAFE_CONFIG_ARGS, *args]


def guarded_argv(root: Path | str, *args: str) -> list[str]:
    """The argv of one Worker Git command on a checkout an agent can write. Raises GitConfigExecutionError — and the
    command must not run — when the checkout's own configuration names a program or cannot be listed. `status` never
    starts a child Git in an embedded repository (a gitlink in the index): that repository's own configuration is not
    part of the scan, and a `submodule.<name>.ignore` setting would override a configured default."""
    assert_no_execution_config(root, where=f"the checkout {root}")
    if args and args[0] == "status":
        args = ("status", "--ignore-submodules=dirty", *args[1:])
    return git_argv(root, *args)


def _list_config(root: Path | str, timeout: float) -> subprocess.CompletedProcess[bytes]:
    for attempt in range(LIST_ATTEMPTS):
        try:
            return subprocess.run(git_argv(root, "config", "--list", "--show-scope", "-z"), capture_output=True,
                                  env=hardened_env(), timeout=timeout, check=False)
        except OSError as exc:
            if exc.errno not in TRANSIENT_ERRNO or attempt == LIST_ATTEMPTS - 1:
                raise
            time.sleep(LIST_PAUSE * 2 ** attempt)
    raise AssertionError("unreachable")


def config_records(root: Path | str, *, timeout: float = LIST_TIMEOUT) -> list[tuple[str, str, str]]:
    """(scope, key, value) for every effective config entry, via a listing that runs no program. Includes are
    resolved by Git and reported at the including file's scope. Fails CLOSED: a configuration that cannot be listed
    (unreadable, malformed, a listing that does not finish) is a GitConfigExecutionError. A host that stays short of
    resources (TRANSIENT_ERRNO) is an OSError: nothing runs either, but nobody is blamed for it."""
    try:
        proc = _list_config(root, timeout)
    except subprocess.TimeoutExpired as exc:
        raise GitConfigExecutionError(f"listing the Git configuration of {root} did not finish within "
                                      f"{timeout:.0f}s; nothing is run on it") from exc
    except OSError as exc:
        if exc.errno in TRANSIENT_ERRNO:
            raise
        raise GitConfigExecutionError(f"the Git configuration of {root} cannot be listed: "
                                      f"{printable(str(exc))}") from exc
    if proc.returncode != 0:
        raise GitConfigExecutionError(f"the Git configuration of {root} cannot be listed: "
                                      f"{printable(' '.join(os.fsdecode(proc.stderr).split())[:200])}")
    fields = os.fsdecode(proc.stdout).split("\0")      # bytes: text mode would turn a CR into a line break
    if fields.pop() or len(fields) % 2:   # every field is NUL-terminated and they come in pairs
        raise GitConfigExecutionError(f"the Git configuration of {root} cannot be listed: unexpected output")
    records: list[tuple[str, str, str]] = []
    for i in range(0, len(fields), 2):
        key, _, value = fields[i + 1].partition("\n")
        records.append((fields[i], key.strip(), value))
    return records


def is_execution_setting(key: str, value: str) -> bool:
    """True when the configuration entry *key*=*value* names a program Git (or git-lfs) may run."""
    key = key.lower()
    value = value.strip()
    if key == "core.fsmonitor":
        return value.lower() not in _FSMONITOR_INERT
    section, _, rest = key.partition(".")
    name = key.rsplit(".", 1)[-1]
    if section == "submodule" and name == "update":
        return value.startswith("!")
    flagged = (key in _EXACT_EXEC_KEYS
               or key.startswith(_LFS_PROGRAM_PREFIXES)
               or (section == "lfs" and name == "standalonetransferagent")
               or (section == "credential" and name == "helper")
               or ("." in rest and name in _EXEC_KEYS_BY_SECTION.get(section, frozenset()))
               or (key != name and name in _EXEC_KEYS_ANY_SECTION))
    return flagged and bool(value)


def untrusted_records(root: Path | str, *, timeout: float = LIST_TIMEOUT) -> list[tuple[str, str, str]]:
    """config_records() of the repository's own scopes only (local/worktree, includes resolved)."""
    return [record for record in config_records(root, timeout=timeout) if record[0] in _UNTRUSTED_SCOPES]


def printable(text: str) -> str:
    """*text* with every byte that is not printable ASCII escaped (names and values here are attacker-controlled)."""
    return text.encode("unicode_escape", "backslashreplace").decode("ascii")


def displayable(text: str) -> str:
    """*text* for a terminal, a log or an Issue: a byte of a name that is not UTF-8 is shown escaped."""
    return text.encode("utf-8", "backslashreplace").decode("utf-8")


def _runs_a_program(key: str, value: str) -> bool:
    """is_execution_setting(), also as a program sees the entry that reads `git config --list` by LINE, split at the
    first `=` (git-lfs does): a value may span lines (a branch description does) and a subsection may contain a `=`.
    Any other control character is refused as such: nobody writes one, and readers differ about it."""
    if any(ord(char) < 32 and char not in "\t\n" or ord(char) == 127 for char in key + value):
        return True
    lines = (line.partition("=") for line in f"{key}={value}".split("\n"))
    return is_execution_setting(key, value) or any(is_execution_setting(k.strip(), v) for k, _, v in lines)


def findings_in(records: Iterable[tuple[str, str, str]]) -> list[str]:
    """The execution settings among *records* (and those that do not read unambiguously), printable. The reported
    value is truncated — it is attacker-controlled, not a secret."""
    return sorted({printable(f"{scope} {key.lower()[:200]}={value[:80]}")
                   for scope, key, value in records
                   if scope in _UNTRUSTED_SCOPES and _runs_a_program(key, value)})


def execution_findings(root: Path | str, *, timeout: float = LIST_TIMEOUT) -> list[str]:
    """Repository-scoped (local/worktree, includes resolved) config entries that would run a program
    during a Coordinator Git operation. Empty when the repository's configuration is safe. Reads
    only; runs nothing. GitConfigExecutionError when the configuration cannot be listed."""
    return findings_in(config_records(root, timeout=timeout))


def assert_no_execution_config(root: Path | str, *, where: str = "this repository",
                               timeout: float = LIST_TIMEOUT) -> None:
    """Raise GitConfigExecutionError when the repository's own configuration would run a program, or cannot be
    listed (fail closed)."""
    findings = execution_findings(root, timeout=timeout)
    if findings:
        raise GitConfigExecutionError(
            f"the Git configuration of {where} would run a program during a Coordinator Git operation "
            f"(repository-scoped execution settings: {findings}); refusing to operate on it until an "
            "operator removes them — the Coordinator never runs repository-provided hooks, filters or "
            "monitor programs")


def run_git(root: Path | str, *args: str, env: Mapping[str, str] | None = None, check: bool = False,
            text: bool = True, input: str | None = None,  # pylint: disable=redefined-builtin
            timeout: float | None = None) -> subprocess.CompletedProcess:
    """subprocess.run of a guarded Worker Git command (guarded_argv + guarded_env of *env*, default `os.environ`). Text
    is decoded as file names are (a name that is not UTF-8 is a name like any other)."""
    return subprocess.run(guarded_argv(root, *args), capture_output=True, text=text, input=input, timeout=timeout,
                          errors="surrogateescape" if text else None, env=guarded_env(env), check=check)


def extend_scrub(names: Iterable[str]) -> frozenset[str]:
    """The scrub set plus *names* (for a caller that keeps its own env construction, e.g. gitops)."""
    return _SCRUB_ENV | frozenset(names)


def scrub_prefixes() -> Sequence[str]:
    """The env-variable prefixes a caller constructing its own environment must also drop."""
    return _SCRUB_PREFIXES
