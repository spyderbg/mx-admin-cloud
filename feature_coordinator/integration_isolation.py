"""W4.7 (M2): Git process isolation for integration jobs — the answer to the remaining finding of the independent
Codex review of M1: "concurrent modification of Git control files can affect Git subprocess execution after
preflight".

The problem. M1 checked the checkout's Git configuration ONCE (gitsafe) and then ran many Git processes with the
checkout as their repository (`git -C <checkout> …`). Every one of those processes re-reads, when it starts, the
checkout's `.git/config` and everything it includes, `hooks/`, `info/attributes`, `info/grafts`, `shallow`,
`refs/replace/`, the commit-graph and the promisor settings. Whoever can write the checkout's `.git` between the check
and a later process changes what that process does — e.g. `core.sshCommand` or a `credential.helper` added after the
check runs during `ls-remote origin`. Re-checking before each process only narrows the window. Two further gaps make
the single check weaker than it looks (both reproduced on this host, tests in test_fc_integration_gitsafety.py):
  * `remote.<name>.uploadpack` (and `receivepack`, `vcs`) name a program that `ls-remote` runs for a local or ssh
    remote, and gitsafe does not flag them;
  * gitsafe's name-keyed patterns (`^credential(\\.[^.]+)?\\.helper$`, `^filter\\.[^.]+\\.(clean|smudge|process)$`, …)
    do not match a subsection name that contains a dot (`credential.https://host.example.helper`,
    `filter.a.b.smudge`), which Git accepts and uses.

The mechanism (closes the race instead of narrowing it). No integration Git process that reads objects or talks to a
remote ever has the checkout as its repository:

  * IsolatedRepository — a fresh private bare repository per command run, created with mkdtemp (mode 0700) under the
    coordinator state directory's `tmp/` and written by this module (no `git init`): `HEAD`, `refs/`, a `config`
    written here, and `objects/info/alternates` naming the checkout's object directory (resolved once). Every
    `rev-list`, `cat-file`, `merge-base` and `ls-remote` runs as `git --git-dir=<private>`. Git then reads only the
    system and global configuration (trusted, gitsafe's model) and the private config — never the checkout's config,
    includes, hooks, attributes, grafts, shallow file, replace refs or commit-graph — so no change to those files, at
    any time, reaches these processes. The checkout contributes object DATA only, through the alternate, and that
    data is hash-verified before it is believed (commit objects: M1; trees and blobs: M2, integration_paths.py).
  * Remote reads use the URL validated at preflight, passed on the command line (never read from the checkout again),
    with every transport refused except https and ssh (and `file`, which only the tests' local remotes use).
  * Lazy fetching cannot happen: the private repository has no promisor remote. A partial clone can therefore no
    longer make Git write the checkout's `.git/config` (the M1 promisor finding) — M1's refusal of partial clones on Git
    < 2.44 is kept as defence in depth.

What still reads the checkout's configuration: the preflight queries that inspect it (`config --get-all`, `remote
get-url`, `rev-parse --is-shallow-repository|--git-common-dir|--show-object-format`, `config --list`). These commands
run no program whatever the configuration says and never write; the adversarial tests plant every known execution
key and every hook and assert that nothing runs. Their output is validated input.

strict_execution_findings() is a superset of gitsafe's check (the keys above included). It runs at preflight and again
right before a decision is recorded: a checkout whose configuration was changed to run programs during a run is
refused — not because a program could have run (none can), but as evidence of tampering.

Residual risks (integration-jobs.md §9): the Git binary, the system/global configuration and the operator account
are trusted; Git parses untrusted object files (pack/loose) from the checkout (parser bugs; data is hash-verified
afterwards, and reads are bounded by timeouts); M3's write-capable operations need the same isolation plus
content-triggered execution controls (filters and merge drivers assigned by the source's `.gitattributes`).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .errors import GitConfigExecutionError, RemoteUnavailableError
from .gitsafe import git_argv, hardened_env

# Written by this module into every private repository (no `git init`, no template, no hooks directory).
PRIVATE_CONFIG = (
    "[core]\n"
    "\trepositoryformatversion = 0\n"
    "\tbare = true\n"
    "\tcommitGraph = false\n"
    "\tfsmonitor = false\n"
    "\thooksPath = /dev/null\n"
    "[gc]\n"
    "\tauto = 0\n"
    "[maintenance]\n"
    "\tauto = false\n"
)
# Remote reads: only https and ssh (github.com); `file` stays at Git's default `user` level for the tests' local remotes
# (refused when GIT_PROTOCOL_FROM_USER=0); `ext`, `git`, `fd` and every other transport are refused.
NETWORK_ARGS: tuple[str, ...] = ("-c", "protocol.allow=never", "-c", "protocol.https.allow=always",
                                 "-c", "protocol.ssh.allow=always", "-c", "protocol.file.allow=user",
                                 "-c", "credential.interactive=false")
_SHA_RE = re.compile(r"[0-9a-f]{40}")
_REF_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


class IsolatedRepository:
    """A private bare repository whose only link to the untrusted checkout is `objects/info/alternates` (see the
    module docstring). Create with open(), remove with close(); usable as a context manager."""

    def __init__(self, objects_dir: Path, *, scratch: Path | None = None) -> None:
        text = os.fspath(objects_dir)
        if not os.path.isabs(text) or _CONTROL_RE.search(text):
            raise GitConfigExecutionError(f"the checkout's object directory {text!r} cannot be used as an alternate")
        self.objects_dir = Path(text)
        self.scratch = scratch
        self.path: Path | None = None

    def open(self) -> Path:
        """Create the private repository (idempotent while open) and return its path."""
        if self.path is not None:
            return self.path
        if not self.objects_dir.is_dir():
            raise GitConfigExecutionError(f"the checkout's object directory {self.objects_dir} is not a directory")
        base = self.scratch if self.scratch is not None and self.scratch.is_dir() else None
        path = Path(tempfile.mkdtemp(prefix="integration-git-", dir=base))  # mode 0700, unpredictable name
        try:
            (path / "refs").mkdir(mode=0o700)
            (path / "objects" / "info").mkdir(mode=0o700, parents=True)
            (path / "HEAD").write_text("ref: refs/heads/isolated\n", encoding="ascii")
            (path / "config").write_text(PRIVATE_CONFIG, encoding="ascii")
            (path / "objects" / "info" / "alternates").write_text(f"{self.objects_dir}\n", encoding="utf-8")
        except BaseException:
            shutil.rmtree(path, ignore_errors=True)
            raise
        self.path = path
        return path

    def close(self) -> None:
        """Remove the private repository (idempotent)."""
        if self.path is not None:
            shutil.rmtree(self.path, ignore_errors=True)
            self.path = None

    def argv(self, *args: str) -> list[str]:
        """`git -C <private> --git-dir=<private> <gitsafe -c overrides> <args>` — never the checkout."""
        path = self.open()
        return git_argv(path, f"--git-dir={path}", *args)

    def __enter__(self) -> "IsolatedRepository":
        self.open()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()


def isolated_remote_head(repo: IsolatedRepository, url: str, branch: str, *, timeout: float) -> str | None:
    """The commit of refs/heads/<branch> at *url* (None: no such branch), read by `ls-remote` in the private
    repository (bounded; the process group is killed on timeout). RemoteUnavailableError when it cannot be read."""
    if not url or url.startswith("-") or _CONTROL_RE.search(url):
        raise RemoteUnavailableError(f"refusing to read refs from the remote URL {url[:80]!r}")
    if not _REF_RE.fullmatch(branch):
        raise RemoteUnavailableError(f"refusing to read the ref {branch[:80]!r}")
    ref = f"refs/heads/{branch}"
    argv = repo.argv(*NETWORK_ARGS, "ls-remote", "--heads", "--", url, ref)
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,  # pylint: disable=consider-using-with
                            stdin=subprocess.DEVNULL, text=True, env=hardened_env(), start_new_session=True)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, 9)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        proc.communicate()
        raise RemoteUnavailableError(f"git ls-remote did not finish within {timeout:.0f}s") from None
    if proc.returncode != 0:
        detail = " ".join((err or out or "").split())[-300:]
        raise RemoteUnavailableError(f"git ls-remote failed: {detail}")
    for line in out.splitlines():
        sha, _sep, name = line.partition("\t")
        if name.strip() == ref and _SHA_RE.fullmatch(sha):
            return sha
    return None


# ---------------------------------------------------------------------------
# The strict configuration scan (a superset of gitsafe.execution_findings)
# ---------------------------------------------------------------------------

_UNTRUSTED_SCOPES = frozenset({"local", "worktree"})
_INERT = frozenset({"", "false", "0", "no", "off"})
# Exact keys (gitsafe's list): the value names a program.
_EXACT = frozenset({"core.hookspath", "core.sshcommand", "core.gitproxy", "core.pager", "core.editor", "core.askpass",
                    "core.alternaterefscommand", "core.fsmonitorhookversion", "sequence.editor", "diff.external",
                    "gpg.program", "uploadpack.packobjectshook", "pack.packobjectshook"})
# Name-keyed program settings: section → final key names, for ANY subsection (dots included — gitsafe misses those).
_BY_SECTION: dict[str, frozenset[str]] = {
    "filter": frozenset({"clean", "smudge", "process"}),
    "diff": frozenset({"command", "textconv"}),
    "difftool": frozenset({"cmd", "path"}),
    "mergetool": frozenset({"cmd", "path"}),
    "merge": frozenset({"driver"}),
    "credential": frozenset({"helper"}),
    "gpg": frozenset({"program", "defaultkeycommand"}),
    "trailer": frozenset({"command", "cmd"}),
    "remote": frozenset({"uploadpack", "receivepack", "vcs"}),  # not in gitsafe: run by ls-remote/fetch/push
}
_ANY_SECTION = frozenset({"textconv", "hookspath"})


def is_execution_setting(key: str, value: str) -> bool:
    """True when the configuration entry *key*=*value* names a program Git may run."""
    key = key.lower()
    inert = value.strip().lower() in _INERT
    if key == "core.fsmonitor":
        return value.strip().lower() not in (_INERT | {"true"})  # `true` is Git's builtin monitor, no program
    section, _sep, rest = key.partition(".")
    name = key.rsplit(".", 1)[-1]
    if section == "submodule" and name == "update":
        return value.strip().startswith("!")
    if key in _EXACT:
        return not inert
    has_subsection = "." in rest
    if section == "credential" and name == "helper":
        return not inert
    if has_subsection and name in _BY_SECTION.get(section, frozenset()):
        return not inert
    return (not inert) and name in _ANY_SECTION and key != name


def config_records(root: Path | str, *, timeout: float = 30.0) -> list[tuple[str, str, str]]:
    """(scope, key, value) of every effective configuration entry of *root* (includes resolved by Git), read by a
    command that runs no program. Unlike gitsafe's listing it fails CLOSED: a configuration that cannot be listed is
    a GitConfigExecutionError (an unreadable configuration also breaks every other Git command)."""
    try:
        proc = subprocess.run(git_argv(root, "config", "--list", "--show-scope", "-z"), capture_output=True,
                              text=True, env=hardened_env(), timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise GitConfigExecutionError(f"listing the Git configuration of {root} did not finish within "
                                      f"{timeout:.0f}s") from exc
    if proc.returncode != 0:
        raise GitConfigExecutionError(f"the Git configuration of {root} cannot be listed: "
                                      f"{' '.join(proc.stderr.split())[:200]}")
    fields = proc.stdout.split("\0")
    records = []
    for index in range(0, len(fields) - 1, 2):
        key, _sep, value = fields[index + 1].partition("\n")
        records.append((fields[index], key.strip(), value))
    return records


def strict_execution_findings(root: Path | str, *, timeout: float = 30.0) -> list[str]:
    """Repository-scoped (local/worktree, includes resolved) configuration entries that name a program. Empty when
    there are none. A superset of gitsafe.execution_findings (see the module docstring)."""
    findings = {f"{scope} {key.lower()}={value[:80]}" for scope, key, value in config_records(root, timeout=timeout)
                if scope in _UNTRUSTED_SCOPES and is_execution_setting(key, value)}
    return sorted(findings)


def assert_no_execution_config_strict(root: Path | str, *, where: str, timeout: float = 30.0) -> None:
    """GitConfigExecutionError when the repository's own configuration names a program (strict scan)."""
    findings = strict_execution_findings(root, timeout=timeout)
    if findings:
        raise GitConfigExecutionError(
            f"the Git configuration of {where} names a program Git could run (repository-scoped execution settings: "
            f"{findings}); refusing to operate on it until an operator removes them — integration jobs never run "
            "repository-provided hooks, filters, helpers or transport programs")
