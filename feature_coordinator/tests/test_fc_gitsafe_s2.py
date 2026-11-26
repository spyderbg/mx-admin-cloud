"""Security S2: repository-controlled Git configuration never runs a program through a Coordinator Git operation — the
one fail-closed scan, a scan in front of EVERY Worker Git command, and the Git control files checked before any Git
command judges an agent's run (whatever `runtime.native_hardening` says, in both execution backends).

Offline, disposable repositories, harmless marker programs (`touch`), the fake agent of the Phase 3 tests. Every attack
has a positive control: the same setting DOES run its program through gitsafe's first layer alone, so a test cannot
pass because the attack was never real. The active Worker's repository is never touched.
"""

# pylint: disable=missing-function-docstring,redefined-outer-name,too-many-arguments,too-many-lines,protected-access

from __future__ import annotations

import argparse
import ast
import dataclasses
import io
import json
import os
import pty
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pytest

from feature_coordinator import checkpoints, commands, git_control, gitsafe, launchers
from feature_coordinator import integration_isolation
from feature_coordinator import native_backend as nb
from feature_coordinator import publish, review, review_context
from feature_coordinator import workspace as workspace_mod
from feature_coordinator import workspace_hold as wh
from feature_coordinator.config import ProjectConfig
from feature_coordinator.demo import init_demo_repo
from feature_coordinator.errors import GitConfigExecutionError, LockBusyError, RemoteUnavailableError
from feature_coordinator.fsguard import scan
from feature_coordinator.gitops import CommitRequest, GitCoordinator, branch_contains, file_digest
from feature_coordinator.gitsafe import (
    assert_no_execution_config,
    execution_findings,
    git_argv,
    guarded_argv,
    guarded_env,
    hardened_env,
    is_execution_setting,
    run_git,
)
from feature_coordinator.launchers import RealCliRunner, role_dirty_paths, run_context
from feature_coordinator.lint_baseline import changed_python_files, export_snapshot
from feature_coordinator.locks import LockSet, ProcessIdentity
from feature_coordinator.model import Feature
from feature_coordinator.native_backend import NativeCliRunner
from feature_coordinator.native_hardening import NativeHardening
from feature_coordinator.operations import SELF_TEST_FILES
from feature_coordinator.paths import PathPolicy, ScopedWorkspace
from feature_coordinator.runners import RunRequest, RunResult
from feature_coordinator.store import StateStore
from feature_coordinator.supervisor import supervise
from feature_coordinator.workflow import TaskSpec
from feature_coordinator.workspace_commands import cmd_workspace_resolve, cmd_workspace_status
from feature_coordinator.workspace_hold import WorkspaceHoldError, hold_of, launch_gate, resolve

import test_fc_autonomy as autonomy
from fc_helpers import actions_prompt, child_env, config_with_agents, fake_broker_factory, git, install_fake_agent

PACKAGE = Path(gitsafe.__file__).parent
BRANCH = "feature/s2"
USER = ("-c", "user.name=T", "-c", "user.email=t@example.invalid")
REAL_GIT = shutil.which("git") or "git"


# =====================================================================================================================
# Building blocks
# =====================================================================================================================

@pytest.fixture
def ran(tmp_path: Path) -> Path:
    """The directory planted programs leave their markers in."""
    path = tmp_path / "ran"
    path.mkdir()
    return path


def names(ran: Path) -> list[str]:
    return sorted(p.name for p in ran.iterdir())


def program(ran: Path, name: str, then: str = "cat") -> str:
    """A harmless program: it records that it ran, then behaves (`cat`: a filter that changes nothing)."""
    script = ran.parent / f"planted-{name}"
    script.write_text(f"#!/bin/sh\ntouch '{ran}/{name}'\n{then}\n", encoding="utf-8")
    script.chmod(0o755)
    return str(script)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    init_demo_repo(path, SELF_TEST_FILES)
    return path


ORIGINAL = SELF_TEST_FILES["backend/src/app.py"]
EDITED = ORIGINAL[:-2] + ("X" if ORIGINAL[-2] != "X" else "Y") + "\n"   # the same size: a status must read the content


def edit(repo: Path, text: str = EDITED) -> None:
    """A dirty tracked file. Its size is unchanged, so Git cannot decide from stat data alone and every status runs
    the clean filter on the content (deterministic, not only for racily clean index entries)."""
    (repo / "backend/src/app.py").write_text(text, encoding="utf-8")


def attributes(repo: Path, line: str) -> None:
    info = repo / ".git/info"
    info.mkdir(exist_ok=True)
    (info / "attributes").write_text(line + "\n", encoding="utf-8")


def layer_1(repo: Path, *args: str, path: str = "", **env: str) -> subprocess.CompletedProcess[str]:
    """gitsafe's first layer only — the positive control."""
    environment = {**hardened_env(), **env}
    if path:
        environment["PATH"] = f"{path}:{environment['PATH']}"
    return subprocess.run(git_argv(repo, *args), capture_output=True, text=True, env=environment, check=False)


def embedded(repo: Path, ran: Path, name: str = "inner") -> Path:
    """An embedded repository recorded as a gitlink in the index, whose OWN configuration names a filter."""
    inner = repo / name
    init_demo_repo(inner, {"f.txt": "one\n"})
    head = git(inner, "rev-parse", "HEAD").strip()
    git(repo, "update-index", "--add", "--cacheinfo", f"160000,{head},{name}")
    git(inner, "config", "filter.ab.clean", program(ran, f"{name}-clean"))
    git(inner, "config", "filter.ab.smudge", program(ran, f"{name}-smudge"))
    attributes(inner, "*.txt filter=ab")
    (inner / "f.txt").write_text("two\n", encoding="utf-8")
    return inner


# =====================================================================================================================
# 1. One scan: it flags every program-naming setting, whatever the subsection, and only those
# =====================================================================================================================

PROGRAM_SETTINGS = [
    # dotted subsection names (the gap M2 documented)
    ("filter.a.b.clean", "x"), ("filter.a.b.smudge", "x"), ("filter.a.b.process", "x"), ("merge.a.b.driver", "x"),
    ("diff.a.b.command", "x"), ("diff.a.b.textconv", "x"), ("credential.https://host.example.helper", "!x"),
    ("difftool.a.b.cmd", "x"), ("mergetool.a.b.path", "x"), ("gpg.a.b.program", "x"), ("trailer.a.b.cmd", "x"),
    # executable remote configuration
    ("remote.origin.uploadpack", "x"), ("remote.origin.receivepack", "x"), ("remote.origin.vcs", "x"),
    ("remote.a.b.uploadpack", "x"),
    # keys the strict scanner already knew
    ("gpg.ssh.defaultKeyCommand", "x"), ("submodule.lib.update", "!x"), ("core.sshCommand", "x"),
    ("core.alternateRefsCommand", "x"), ("uploadpack.packObjectsHook", "x"), ("core.fsmonitor", "/x"),
    # found while fixing S2 (each executed through a Coordinator command on this host)
    ("gc.recentObjectsHook", "x"), ("lfs.extension.foo.clean", "x"), ("lfs.extension.foo.smudge", "x"),
    ("lfs.customtransfer.foo.path", "x"),
    # a value that looks like a boolean still names a program
    ("filter.ab.clean", "off"), ("filter.ab.clean", "false"), ("credential.helper", "no"), ("core.sshCommand", "0"),
]


@pytest.mark.parametrize("key,value", PROGRAM_SETTINGS, ids=[f"{k}={v}" for k, v in PROGRAM_SETTINGS])
def test_every_program_setting_is_found_and_refused(repo: Path, key: str, value: str) -> None:
    git(repo, "config", key, value)
    assert [f for f in execution_findings(repo) if f.startswith(f"local {key.lower()}=")]
    with pytest.raises(GitConfigExecutionError, match="would run a program"):
        assert_no_execution_config(repo)
    with pytest.raises(GitConfigExecutionError):
        guarded_argv(repo, "status")
    with pytest.raises(GitConfigExecutionError):
        run_git(repo, "rev-parse", "HEAD")


@pytest.mark.parametrize("key,value", [
    ("remote.origin.url", "git@github.com:a/b.git"), ("remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*"),
    ("branch.dev.merge", "refs/heads/dev"), ("branch.dev.vscode-merge-base", "origin/dev"), ("core.filemode", "false"),
    ("core.autocrlf", "input"), ("user.email", "a@example.invalid"), ("filter.ab.required", "true"),
    ("core.fsmonitor", "true"), ("core.fsmonitor", "false"), ("credential.helper", ""), ("filter.ab.clean", ""),
    ("submodule.lib.update", "checkout"), ("gc.auto", "0"), ("lfs.repositoryformatversion", "0"), ("merge.ff", "only"),
    ("lfs.https://host.example/info/lfs.access", "basic"), ("push.default", "simple"),
])
def test_ordinary_settings_are_not_refused(repo: Path, key: str, value: str) -> None:
    git(repo, "config", key, value)
    assert execution_findings(repo) == []
    assert run_git(repo, "rev-parse", "HEAD").returncode == 0


def test_included_and_worktree_settings_are_found(repo: Path) -> None:
    (repo / "team.inc").write_text('[filter "a.b"]\n\tclean = x\n', encoding="utf-8")
    git(repo, "config", "include.path", "../team.inc")          # inside the checkout: an agent can write it
    git(repo, "config", "extensions.worktreeConfig", "true")
    (repo / ".git/config.worktree").write_text('[remote "origin"]\n\tuploadpack = x\n', encoding="utf-8")
    assert {f.split("=")[0] for f in execution_findings(repo)} == {"local filter.a.b.clean",
                                                                  "worktree remote.origin.uploadpack"}


def test_the_operators_global_configuration_stays_trusted(repo: Path, fake_home: Path) -> None:
    (fake_home / ".gitconfig").write_text('[credential "https://host.example"]\n\thelper = store\n'
                                          '[filter "lfs"]\n\tclean = git-lfs clean -- %f\n', encoding="utf-8")
    assert "credential.https://host.example.helper" in git(repo, "config", "--list")
    assert execution_findings(repo) == []


def test_the_worker_and_the_integration_jobs_share_the_scan() -> None:
    assert integration_isolation.strict_execution_findings is gitsafe.execution_findings
    assert is_execution_setting("FILTER.A.B.Clean", "x")


# =====================================================================================================================
# 2. A configuration that cannot be listed is refused (fail closed), and nothing else is run
# =====================================================================================================================

def unlistable_malformed(repo: Path) -> None:
    with (repo / ".git/config").open("a", encoding="utf-8") as handle:
        handle.write("[core\n")


def unlistable_unreadable(repo: Path) -> None:
    if os.geteuid() == 0:
        pytest.skip("root reads everything")
    (repo / ".git/config").chmod(0)


def unlistable_fifo(repo: Path) -> None:
    git(repo, "config", "include.path", "pipe")
    os.mkfifo(repo / ".git/pipe")


def unlistable_gone(repo: Path) -> None:
    repo.rename(repo.parent / "moved")


UNLISTABLE = [unlistable_malformed, unlistable_unreadable, unlistable_fifo, unlistable_gone]


@pytest.mark.parametrize("damage", UNLISTABLE, ids=lambda f: f.__name__.removeprefix("unlistable_"))
def test_a_configuration_that_cannot_be_listed_fails_closed(repo: Path, monkeypatch: pytest.MonkeyPatch,
                                                            damage: Callable[[Path], None]) -> None:
    monkeypatch.setattr(gitsafe, "LIST_TIMEOUT", 1.0)
    real, seen = gitsafe.config_records, []
    monkeypatch.setattr(gitsafe, "config_records", lambda root, **_kw: real(root, timeout=1.0))
    damage(repo)
    started = subprocess.run

    def record(argv: list[str], *args: Any, **kwargs: Any) -> Any:
        seen.append(argv)
        return started(argv, *args, **kwargs)  # pylint: disable=subprocess-run-check
    monkeypatch.setattr(subprocess, "run", record)
    for call in (lambda: execution_findings(repo), lambda: assert_no_execution_config(repo),
                 lambda: run_git(repo, "status"), lambda: nb._status(repo), lambda: role_dirty_paths(repo, ["backend"]),
                 lambda: git_control.capture(repo)):
        with pytest.raises(GitConfigExecutionError):
            call()
    assert seen and all("--list" in argv for argv in seen), "only the listing itself ran"
    monkeypatch.undo()
    if damage is unlistable_unreadable:
        (repo / ".git/config").chmod(0o644)


@pytest.mark.parametrize("stdout", ["local\0core.bare\nfalse", "local\0", "local\0a\nb\0local\0"],
                         ids=["unterminated", "half-a-pair", "odd"])
def test_an_unexpected_listing_is_refused(repo: Path, monkeypatch: pytest.MonkeyPatch, stdout: str) -> None:
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout.encode(), b""))
    with pytest.raises(GitConfigExecutionError, match="cannot be listed"):
        execution_findings(repo)


def test_undecodable_settings_are_still_scanned(repo: Path) -> None:
    with (repo / ".git/config").open("ab") as handle:
        handle.write(b'[filter "\xff.b"]\n\tclean = /tmp/\xfe\n')
    found = execution_findings(repo)
    assert len(found) == 1 and found[0].isascii() and "clean" in found[0]


# =====================================================================================================================
# 3. The attacks are real (layer 1 alone runs them) and no Coordinator operation runs any of them
# =====================================================================================================================

def attack_dotted_filter(repo: Path, ran: Path) -> None:
    git(repo, "config", "filter.a.b.clean", program(ran, "dotted-filter"))
    git(repo, "config", "filter.a.b.smudge", program(ran, "dotted-smudge"))
    attributes(repo, "*.py filter=a.b")


def attack_filter_process(repo: Path, ran: Path) -> None:
    git(repo, "config", "filter.ab.process", program(ran, "filter-process", "exit 1"))
    attributes(repo, "*.py filter=ab")


def attack_boolean_looking(repo: Path, ran: Path) -> None:
    git(repo, "config", "filter.ab.clean", "off")
    attributes(repo, "*.py filter=ab")
    (ran.parent / "bin").mkdir(exist_ok=True)
    shutil.copy(program(ran, "named-off"), ran.parent / "bin/off")


def attack_tracked_attributes(repo: Path, ran: Path) -> None:
    git(repo, "config", "filter.a.b.clean", program(ran, "tracked-attributes"))
    (repo / "backend/.gitattributes").write_text("*.py filter=a.b\n", encoding="utf-8")   # a role may write this


STATUS_ATTACKS = [attack_dotted_filter, attack_filter_process, attack_boolean_looking, attack_tracked_attributes]


@pytest.mark.parametrize("attack", STATUS_ATTACKS, ids=lambda f: f.__name__.removeprefix("attack_"))
def test_positive_control_a_status_with_layer_1_alone_runs_the_planted_filter(repo: Path, ran: Path, attack) -> None:
    attack(repo, ran)
    edit(repo)
    layer_1(repo, "status", "--porcelain=v1", path=str(ran.parent / "bin"))
    assert names(ran), "attack precondition"


def test_positive_control_layer_1_alone_runs_an_uploadpack(repo: Path, ran: Path, tmp_path: Path) -> None:
    origin = tmp_path / "origin.git"
    git(repo, "clone", "-q", "--bare", str(repo), str(origin))
    git(repo, "remote", "add", "origin", str(origin))
    git(repo, "config", "remote.origin.uploadpack", program(ran, "uploadpack", 'exec git-upload-pack "$@"'))
    layer_1(repo, "ls-remote", "--heads", "origin")
    assert names(ran) == ["uploadpack"]


def test_positive_control_a_plain_fast_forward_runs_the_gc_hook_and_layer_1_does_not(repo: Path, ran: Path) -> None:
    git(repo, "switch", "-q", "-c", "tip")
    edit(repo)
    git(repo, "commit", "-qam", "tip")
    git(repo, "switch", "-q", "dev")
    blob = subprocess.run(["git", "-C", str(repo), "hash-object", "-w", "--stdin"], input="lost\n", text=True,
                          capture_output=True, check=True).stdout.strip()
    old = repo / ".git/objects" / blob[:2] / blob[2:]
    os.utime(old, (1, 1))
    git(repo, "repack", "-q", "-a")
    pack = subprocess.run(["git", "-C", str(repo), "hash-object", "-w", "--stdin"], input="pk\n", text=True,
                          capture_output=True, check=True).stdout
    subprocess.run(["git", "-C", str(repo), "pack-objects", "-q", str(repo / ".git/objects/pack/pack")], input=pack,
                   text=True, capture_output=True, check=True)
    for key, value in (("gc.recentObjectsHook", program(ran, "gc-hook", "true")), ("gc.auto", "1"),
                       ("gc.autoDetach", "false"), ("gc.pruneExpire", "1.hour.ago"), ("gc.autoPackLimit", "1")):
        git(repo, "config", key, value)
    if "recentObjectsHook" not in subprocess.run(["git", "help", "--config"], capture_output=True, text=True,
                                                 check=False).stdout:
        pytest.skip("this Git has no gc.recentObjectsHook")
    copy = repo.parent / "copy"
    shutil.copytree(repo, copy, symlinks=True)
    layer_1(copy, "merge", "--ff-only", "--no-stat", "--quiet", "tip")
    assert names(ran) == [], "no automatic gc"
    subprocess.run(["git", "-C", str(repo), "-c", "core.hooksPath=/dev/null", "merge", "--ff-only", "--quiet", "tip"],
                   capture_output=True, check=True)
    assert names(ran) == ["gc-hook"]


@pytest.mark.skipif(not shutil.which("git-lfs"), reason="git-lfs is not installed")
def test_positive_control_layer_1_alone_runs_a_git_lfs_extension(repo: Path, ran: Path, fake_home: Path) -> None:
    (fake_home / ".gitconfig").write_text('[filter "lfs"]\n\tclean = git-lfs clean -- %f\n\tsmudge = git-lfs smudge '
                                          '-- %f\n\tprocess = git-lfs filter-process\n\trequired = true\n',
                                          encoding="utf-8")   # the operator's global, trusted configuration
    git(repo, "config", "lfs.extension.foo.clean", program(ran, "lfs-extension"))
    git(repo, "config", "lfs.extension.foo.smudge", program(ran, "lfs-extension"))
    git(repo, "config", "lfs.extension.foo.priority", "0")
    attributes(repo, "*.py filter=lfs")
    edit(repo)
    layer_1(repo, "status", "--porcelain=v1")
    assert names(ran) == ["lfs-extension"]
    assert execution_findings(repo)


@pytest.fixture
def gc(repo: Path, state_dir: Path, config: ProjectConfig, tmp_path: Path) -> GitCoordinator:
    origin = tmp_path / "origin.git"
    git(repo, "clone", "-q", "--bare", str(repo), str(origin))
    git(repo, "remote", "add", "origin", str(origin))
    coordinator = GitCoordinator(repo, LockSet(state_dir / "locks"), PathPolicy(repo, config),
                                 tmp_dir=state_dir / "tmp")
    coordinator.create_feature_branch(BRANCH, "dev")
    return coordinator


def commit_request(gc: GitCoordinator, head: str, path: str = "backend/src/app.py") -> CommitRequest:
    return CommitRequest(feature_id="f", task_id="f.backend", role="backend", branch=BRANCH, expected_head=head,
                         files={path: file_digest(gc.root / path)}, message="work")


def operations_of(gc: GitCoordinator, config: ProjectConfig, store: StateStore, head: str,
                  tmp: Path) -> dict[str, Callable[[], Any]]:
    """Every kind of Git operation the Worker runs on the shared checkout."""
    root, lint = gc.root, config.lint_gates["backend-lint"]
    feature = Feature(id="f", slug="f", title="F", base_branch="dev", branch=BRANCH, base_sha=head)

    def fast_forward() -> Any:
        with gc.locks.project(shared=False).acquire() as project:
            return gc.fast_forward_branch(BRANCH, since=head, to=head, project=project)
    return {
        "commit": lambda: gc.commit(commit_request(gc, head)),
        "create_feature_branch": lambda: gc.create_feature_branch("feature/other", "dev"),
        "switch_branch": lambda: gc.switch_branch("dev"),
        "fast_forward": fast_forward,
        "push": lambda: gc.push_branch(BRANCH, head, destination=str(tmp / "origin.git")),
        "remote_head": lambda: gc.remote_head("dev"),
        "fetch": lambda: gc.fetch_branch("dev"),
        "remote_urls": gc.remote_urls,
        "tracked_changes": gc.tracked_changes,
        "untracked_files": gc.untracked_files,
        "staged_paths": gc.staged_paths,
        "current_branch": gc.current_branch,
        "rev_parse": lambda: gc.rev_parse("HEAD"),
        "has_commit": lambda: gc.has_commit(head),
        "branch_contains": lambda: branch_contains(root, BRANCH, head),
        "native status": lambda: nb._status(root),
        "native git state": lambda: nb.git_state(root),
        "native workspace state": lambda: nb.workspace_state(root, config),
        "role paths": lambda: role_dirty_paths(root, ["backend"]),
        "checkpoint status": lambda: checkpoints._status_paths(root),
        "unexplained": lambda: wh.unexplained(root, scan(root, config)),
        "staged sensitive": lambda: wh.staged_sensitive(root, config),
        "hold reports": lambda: wh._git_reports(root, ["backend/src/app.py"]),
        "admission": lambda: wh.guard_before_run(store, root, config),
        "finish": lambda: wh.finish_execution(store, root, config, execution_id="x", role="backend", grants=(),
                                              result=RunResult("r", "completed"), task_id="f.backend"),
        "identity": lambda: workspace_mod.identity(root),
        "ongoing": lambda: workspace_mod.git_operation_in_progress(root),
        "workspace problems": lambda: workspace_mod.workspace_problems(root, feature),
        "lint files": lambda: changed_python_files(root, head, head, lint),
        "lint export": lambda: export_snapshot(root, head, lint, tmp / "export"),
        "review entries": lambda: review_context.changed_entries(root, head, head),
        "review merge base": lambda: review_context.merge_base(root, head, head),
        "cli": lambda: commands._git(root, "rev-parse", "HEAD"),
    }


def test_operations_work_on_a_clean_checkout(gc: GitCoordinator, config: ProjectConfig, state_dir: Path,
                                             tmp_path: Path) -> None:
    """The same calls succeed without an attack: the refusals below are the scan's, nothing else's."""
    head = gc.rev_parse("HEAD")
    edit(gc.root)
    operations = operations_of(gc, config, StateStore(state_dir), head, tmp_path)
    for name in ("commit", "create_feature_branch", "switch_branch"):
        operations.pop(name)   # they move the branch: exercised by their own tests
    edit(gc.root, ORIGINAL)
    for name, call in operations.items():
        try:
            call()
        except WorkspaceHoldError:
            assert name == "admission"   # the demo files hold sensitive entries: the first inventory


@pytest.mark.parametrize("attack", STATUS_ATTACKS, ids=lambda f: f.__name__.removeprefix("attack_"))
def test_no_coordinator_operation_runs_a_planted_program(gc: GitCoordinator, config: ProjectConfig, state_dir: Path,
                                                         ran: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                         attack) -> None:
    head = gc.rev_parse("HEAD")
    operations = operations_of(gc, config, StateStore(state_dir), head, tmp_path)
    attack(gc.root, ran)
    for key in ("remote.origin.uploadpack", "remote.origin.receivepack", "core.sshCommand", "gc.recentObjectsHook",
                "diff.external", "credential.https://host.example.helper", "core.fsmonitor"):
        git(gc.root, "config", key, program(ran, key.replace("/", "_")))
    edit(gc.root)
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}:{os.environ['PATH']}")
    for name, call in operations.items():
        if name == "workspace problems":
            assert "would run a program" in call()[0]
        elif name == "finish":
            assert "would run a program" in call()["entries"][-1]["reason"]
        elif name == "admission":
            with pytest.raises(WorkspaceHoldError, match="would run a program"):
                call()
        else:
            with pytest.raises(GitConfigExecutionError):
                call()
        assert names(ran) == [], f"{name} ran a planted program"
    assert subprocess.run(["git", "-C", str(gc.root), "rev-parse", BRANCH], capture_output=True, text=True,
                          check=True).stdout.strip() == head, "the branch never moved"


def test_planted_hooks_never_run(gc: GitCoordinator, ran: Path, tmp_path: Path) -> None:
    """Hooks are files, not configuration: the operations go on, with no hook (layer 1)."""
    hooks = gc.root / ".git/hooks"
    hooks.mkdir(exist_ok=True)
    for name in ("pre-commit", "post-commit", "reference-transaction", "post-checkout", "post-merge", "pre-push",
                 "post-index-change", "pre-auto-gc", "post-rewrite", "commit-msg", "prepare-commit-msg"):
        shutil.copy(program(ran, name, "exit 0"), hooks / name)
    subprocess.run(["git", "-C", str(gc.root), "update-ref", "refs/heads/probe", "HEAD"], check=True)
    assert names(ran) == ["reference-transaction"], "attack precondition: a raw update-ref runs its hook"
    (ran / "reference-transaction").unlink()
    edit(gc.root)
    sha = gc.commit(commit_request(gc, gc.rev_parse("HEAD"))).sha
    gc.push_branch(BRANCH, sha, destination=str(tmp_path / "origin.git"))
    gc.fetch_branch(BRANCH)
    gc.switch_branch("dev")
    gc.switch_branch(BRANCH)
    gc.tracked_changes()
    nb.git_state(gc.root)
    assert names(ran) == []


@pytest.mark.parametrize("key,value", [("remote.origin.url", "planted::address"),
                                       ("remote.origin.url", "planted://host/x"),
                                       ("url.planted::.insteadOf", "ORIGIN")],
                         ids=["helper", "scheme", "insteadOf"])
def test_a_url_cannot_select_a_transport_program(gc: GitCoordinator, ran: Path, tmp_path: Path, key: str, value: str,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "bin").mkdir()
    shutil.copy(program(ran, "transport", "exit 1"), tmp_path / "bin/git-remote-planted")
    git(gc.root, "config", key, str(tmp_path / "origin.git") if value == "ORIGIN" else value)
    assert execution_findings(gc.root) == []      # a URL names no program by itself …
    layer_1(gc.root, "ls-remote", "--heads", "origin", path=str(tmp_path / "bin"))
    assert names(ran) == ["transport"], "attack precondition"
    (ran / "transport").unlink()
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}:{os.environ['PATH']}")
    monkeypatch.setenv("GIT_ALLOW_PROTOCOL", "planted")       # … and an inherited allowance does not count
    for call in (lambda: gc.remote_head("dev"), lambda: gc.fetch_branch("dev")):
        with pytest.raises(RemoteUnavailableError, match="not allowed"):
            call()
    assert run_git(gc.root, "ls-remote", "origin").returncode != 0
    assert names(ran) == []


def test_the_transports_the_worker_uses_stay_allowed(gc: GitCoordinator) -> None:
    assert gc.remote_head("dev") == gc.rev_parse("dev")           # file: the tests' origin
    for argv, env in ((guarded_argv(gc.root, "fetch"), guarded_env()), (gc._argv("fetch"), gc._env())):
        assert argv[0] == "git" and set(env["GIT_ALLOW_PROTOCOL"].split(":")) == {"file", "https", "ssh"}


# =====================================================================================================================
# 4. Embedded repositories: their own configuration is not part of the scan, so no child Git enters them
# =====================================================================================================================

def test_status_never_enters_an_embedded_repository(gc: GitCoordinator, config: ProjectConfig, ran: Path) -> None:
    embedded(gc.root, ran)
    git(gc.root, "config", "submodule.inner.ignore", "none")      # would override a configured default
    git(gc.root, "config", "diff.ignoreSubmodules", "none")
    assert execution_findings(gc.root) == []
    layer_1(gc.root, "status", "--porcelain=v1")
    assert names(ran) == ["inner-clean"], "attack precondition"
    (ran / "inner-clean").unlink()
    (gc.root / "inner/f.txt").write_text("six\n", encoding="utf-8")   # the same size as `two`: content must be read
    nb._status(gc.root)
    nb.git_state(gc.root)
    role_dirty_paths(gc.root, ["inner", "backend"])
    gc.tracked_changes()
    gc.staged_paths()
    checkpoints._status_paths(gc.root)
    wh.unexplained(gc.root, scan(gc.root, config))
    wh._git_reports(gc.root, ["inner"])
    run_git(gc.root, "status", "--porcelain=v1")
    assert names(ran) == []
    assert any(line.endswith("inner") for line in nb._status(gc.root)[0]), "the gitlink itself is still reported"


def test_switching_branches_never_recurses_into_an_embedded_repository(gc: GitCoordinator, ran: Path) -> None:
    inner = embedded(gc.root, ran)
    (gc.root / ".gitmodules").write_text('[submodule "inner"]\n\tpath = inner\n\turl = ./inner\n', encoding="utf-8")
    git(gc.root, "add", ".gitmodules")
    git(gc.root, "commit", "-qm", "gitlink")
    git(gc.root, "branch", "before")
    git(gc.root, "branch", "control")       # `before` itself is fast-forwarded below
    git(inner, "config", "--remove-section", "filter.ab")
    (inner / ".git/info/attributes").unlink()
    git(inner, "commit", "-qam", "two")
    git(gc.root, "update-index", "--cacheinfo", f"160000,{git(inner, 'rev-parse', 'HEAD').strip()},inner")
    git(gc.root, "commit", "-qm", "moved gitlink")
    git(inner, "config", "filter.ab.smudge", program(ran, "recursed"))
    git(inner, "config", "filter.ab.clean", program(ran, "recursed"))
    attributes(inner, "*.txt filter=ab")
    for key, value in (("submodule.recurse", "true"), ("submodule.inner.active", "true"),
                       ("submodule.inner.url", "./inner"), ("push.recurseSubmodules", "on-demand")):
        git(gc.root, "config", key, value)
    run_git(gc.root, "switch", "--quiet", "before")
    run_git(gc.root, "merge", "--ff-only", "--quiet", BRANCH)
    gc.switch_branch(BRANCH)
    assert names(ran) == []
    subprocess.run(["git", "-C", str(gc.root), "switch", "-q", "control"], capture_output=True, check=False)
    assert names(ran) == ["recursed"], "positive control: a plain switch recurses and runs the nested filter"


def test_the_reviewers_diff_never_enters_an_embedded_repository(gc: GitCoordinator, ran: Path) -> None:
    inner = embedded(gc.root, ran)
    git(gc.root, "commit", "-qm", "gitlink")
    git(inner, "config", "--remove-section", "filter.ab")
    git(inner, "commit", "-qam", "two")
    git(gc.root, "update-index", "--cacheinfo", f"160000,{git(inner, 'rev-parse', 'HEAD').strip()},inner")
    git(gc.root, "commit", "-qm", "moved gitlink")
    git(inner, "config", "diff.external", program(ran, "nested-diff", "exit 0"))
    git(gc.root, "config", "diff.submodule", "diff")
    diff = ("diff", "--no-ext-diff", "--no-textconv", "--no-color", "--unified=3", "HEAD~1", "HEAD", "--")
    subprocess.run(["git", "-C", str(gc.root), *diff], capture_output=True, check=False)
    assert names(ran) == ["nested-diff"], "attack precondition"
    (ran / "nested-diff").unlink()
    assert b"inner" in review_context._git(gc.root, *diff)
    assert names(ran) == []


# =====================================================================================================================
# 5. Git control files (file system only)
# =====================================================================================================================

def changes(root: Path, before: git_control.GitControl) -> list[str]:
    return git_control.verify(root, before).problems


def append(path: Path, text: str) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(text)


CONTROL_CHANGES: dict[str, Callable[[Path], Any]] = {
    "config": lambda r: append(r / ".git/config", "[core]\n\tx = y\n"),
    "config removed": lambda r: (r / ".git/config").unlink(),
    "config is a symlink": lambda r: ((r / ".git/config").rename(r / ".git/c2"), (r / ".git/config").symlink_to("c2")),
    "worktree config": lambda r: (r / ".git/config.worktree").write_text("[core]\n\tx = y\n", encoding="utf-8"),
    "commondir": lambda r: (r / ".git/commondir").write_text("/elsewhere\n", encoding="utf-8"),
    "hook added": lambda r: (r / ".git/hooks/post-index-change").write_text("#!/bin/sh\n", encoding="utf-8"),
    "hook made executable": lambda r: (r / ".git/hooks/plain").chmod(0o755),
    "nested hook": lambda r: ((r / ".git/hooks/d").mkdir(), (r / ".git/hooks/d/x").write_text("x", encoding="utf-8")),
    "hooks is a symlink": lambda r: (shutil.rmtree(r / ".git/hooks"), (r / ".git/hooks").symlink_to("objects")),
    "attributes": lambda r: attributes(r, "* filter=x"),
    "exclude": lambda r: append(r / ".git/info/exclude", "*.py\n"),
    "grafts": lambda r: (r / ".git/info/grafts").write_text("x\n", encoding="utf-8"),
    "legacy remote": lambda r: ((r / ".git/remotes").mkdir(), (r / ".git/remotes/origin").write_text("URL: x\n",
                                                                                                     encoding="utf-8")),
    "legacy branch": lambda r: ((r / ".git/branches").mkdir(exist_ok=True),
                                (r / ".git/branches/origin").write_text("x\n", encoding="utf-8")),
    "git directory replaced": lambda r: ((r / ".git").rename(r.parent / "real.git"),
                                         (r / ".git").write_text(f"gitdir: {r.parent / 'real.git'}\n",
                                                                 encoding="utf-8")),
    "git directory is a symlink": lambda r: ((r / ".git").rename(r.parent / "real.git"),
                                             (r / ".git").symlink_to(r.parent / "real.git")),
    "fifo": lambda r: ((r / ".git/info/exclude").unlink(), os.mkfifo(r / ".git/info/exclude")),
}


@pytest.mark.parametrize("change", list(CONTROL_CHANGES))
def test_a_control_file_change_is_reported_without_a_git_command(repo: Path, change: str,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    (repo / ".git/hooks").mkdir(exist_ok=True)
    (repo / ".git/hooks/plain").write_text("#!/bin/sh\n", encoding="utf-8")
    append(repo / ".git/info/exclude", "")
    before = git_control.capture(repo)
    assert not changes(repo, before)
    CONTROL_CHANGES[change](repo)
    started = subprocess.run
    seen: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run",
                        lambda argv, *a, **k: (seen.append(argv), started(argv, *a, **k))[1])  # pylint: disable=subprocess-run-check
    problems = changes(repo, before)
    assert problems and all(p.startswith(wh._UNVERIFIABLE) for p in problems), problems
    assert all("--list" in argv for argv in seen), "nothing but the configuration listing (it runs no program)"


def test_benign_control_entries_are_not_changes(repo: Path) -> None:
    (repo / ".git/hooks").mkdir(exist_ok=True)
    before = git_control.capture(repo)
    (repo / ".git/commondir").write_text(".\n", encoding="utf-8")           # Claude Code's own guards
    (repo / ".git/config.worktree").write_text("", encoding="utf-8")
    (repo / ".git/hooks/.claude-sandbox-noreplace").write_text("", encoding="utf-8")
    (repo / ".git/info/refs").write_text("x\n", encoding="utf-8")           # what a repack writes for HTTP servers
    assert not changes(repo, before)


def test_what_the_coordinator_itself_does_is_not_a_control_change(gc: GitCoordinator, tmp_path: Path) -> None:
    """A parallel role's commit, a push or a fetch while an agent runs must not look like a control change."""
    before = git_control.capture(gc.root)
    edit(gc.root)
    sha = gc.commit(commit_request(gc, gc.rev_parse("HEAD"))).sha
    gc.push_branch(BRANCH, sha, destination=str(tmp_path / "origin.git"))
    gc.fetch_branch(BRANCH)
    gc.create_feature_branch("feature/second", "dev")
    git(gc.root, "gc", "-q")
    assert not changes(gc.root, before)


def test_a_changed_included_file_is_a_change(repo: Path) -> None:
    (repo / "team.inc").write_text("[core]\n\tx = 1\n", encoding="utf-8")
    git(repo, "config", "include.path", "../team.inc")
    before = git_control.capture(repo)
    (repo / "team.inc").write_text("[core]\n\tx = 2\n", encoding="utf-8")    # outside .git, and names no program
    assert changes(repo, before) == ["Git control settings changed (an included configuration file)"]
    (repo / "team.inc").write_text('[filter "a.b"]\n\tclean = x\n', encoding="utf-8")
    assert "names a program: local filter.a.b.clean=x" in changes(repo, before)[0]


@pytest.mark.parametrize("limit", ["MAX_CONTROL_BYTES", "MAX_CONTROL_FILES"])
def test_control_files_that_cannot_all_be_compared_are_refused(repo: Path, monkeypatch: pytest.MonkeyPatch,
                                                               limit: str) -> None:
    (repo / ".git/hooks").mkdir(exist_ok=True)
    (repo / ".git/hooks/pre-commit").write_text("#!/bin/sh\n", encoding="utf-8")
    before = git_control.capture(repo)
    monkeypatch.setattr(git_control, limit, 0)
    with pytest.raises(GitConfigExecutionError, match="to be verified"):
        git_control.capture(repo)
    assert changes(repo, before)[0].startswith("Git control files cannot be verified")


def test_an_unreadable_control_file_is_compared_by_its_stamps(repo: Path) -> None:
    if os.geteuid() == 0:
        pytest.skip("root reads everything")
    exclude = repo / ".git/info/exclude"
    append(exclude, "x\n")
    exclude.chmod(0)
    before = git_control.capture(repo)
    assert not changes(repo, before)
    exclude.chmod(0o644)
    append(exclude, "y\n")
    exclude.chmod(0)
    assert changes(repo, before) == ["Git control file changed: .git/info/exclude"]


def test_a_linked_worktree_is_covered_too(repo: Path, tmp_path: Path) -> None:
    linked = tmp_path / "linked"
    git(repo, "worktree", "add", "-q", "--detach", str(linked))
    before = git_control.capture(linked)
    assert {".git", "commondir", "common/config"} <= set(before.files)
    append(repo / ".git/config", "[core]\n\tx = y\n")
    assert changes(linked, before)[0] == "Git control file changed: .git/common/config"


def test_capture_refuses_what_already_names_a_program(repo: Path) -> None:
    git(repo, "config", "remote.origin.uploadpack", "x")
    with pytest.raises(GitConfigExecutionError, match="remote.origin.uploadpack"):
        git_control.capture(repo)


# =====================================================================================================================
# 6. The end of an agent run: control files first, then — only when they are unchanged — Git
# =====================================================================================================================

@pytest.fixture
def agents(tmp_path: Path) -> Path:
    return install_fake_agent(tmp_path / "agents")


def native_config(config: ProjectConfig, agents: Path, hardened: bool) -> ProjectConfig:
    cfg = config_with_agents(config, agents)
    if hardened:
        cfg = dataclasses.replace(cfg, runtime=dataclasses.replace(cfg.runtime,
                                                                   native_hardening=NativeHardening(enabled=True)))
    return cfg


HARDENING = pytest.mark.parametrize("hardened", [False, True], ids=["hardening-off", "hardening-on"])


class GitLog:
    """A `git` first on PATH that records every Git process, and whether the agent had finished by then."""

    def __init__(self, tmp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.done, self.log = tmp / "agent-done", tmp / "git.log"
        shim = tmp / "shim"
        shim.mkdir()
        (shim / "git").write_text(f"#!/bin/sh\n[ -e '{self.done}' ] && w=after || w=before\n"
                                  f"echo \"$w $*\" >> '{self.log}'\nexec '{REAL_GIT}' \"$@\"\n", encoding="utf-8")
        (shim / "git").chmod(0o755)
        monkeypatch.setenv("PATH", f"{shim}:{os.environ['PATH']}")

    def lines(self, when: str) -> list[str]:
        text = self.log.read_text(encoding="utf-8") if self.log.exists() else ""
        return [line.split(" ", 1)[1] for line in text.splitlines() if line.startswith(when + " ")]

    @property
    def action(self) -> dict[str, str]:
        return {"do": "write", "path": str(self.done), "text": "done\n"}


@pytest.fixture
def git_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> GitLog:
    return GitLog(tmp_path, monkeypatch)


def native_run(config: ProjectConfig, repo: Path, tmp: Path, actions: list[dict[str, Any]]) -> RunResult:
    runner = NativeCliRunner(config.provider_for("backend"), config, hardening_check=lambda *a, **k: "",  # type: ignore[arg-type]
                             enforcement_check=lambda *a, **k: "")
    request = RunRequest("f.backend", "backend", actions_prompt(actions),
                         ScopedWorkspace(PathPolicy(repo, config), "backend"),
                         context=run_context(tmp / "runs", "f.backend"))
    return runner.run(request)


WORK = {"do": "write", "path": "backend/src/app.py", "text": EDITED}


def planted_filter(ran: Path, key: str = "a.b") -> list[dict[str, Any]]:
    """What an agent would do: name a program, assign it to the files it edits, and finish normally."""
    return [WORK, {"do": "append", "path": ".git/config",
                   "text": f'[filter "{key}"]\n\tclean = {program(ran, "agent-filter")}\n'},
            {"do": "write", "path": ".git/info/attributes", "text": f"*.py filter={key}\n"}]


@HARDENING
@pytest.mark.parametrize("key", ["a.b", "ab"], ids=["dotted", "plain"])
def test_a_filter_planted_by_the_agent_never_runs(config: ProjectConfig, agents: Path, repo: Path, tmp_path: Path,
                                                  ran: Path, git_log: GitLog, fake_home: Path, hardened: bool,
                                                  key: str) -> None:
    """The demonstrated gap: before S2 the post-run `git status` ran the program, whatever its name, with hardening
    off and on."""
    del fake_home
    result = native_run(native_config(config, agents, hardened), repo, tmp_path,
                        [*planted_filter(ran, key), git_log.action])
    assert result.status == "rejected" and "Git control files" in result.error
    assert names(ran) == []
    after = git_log.lines("after")
    assert after and all(line.endswith("config --list --show-scope -z") for line in after), after
    # evidence: what changed and which setting names a program; everything is left in place
    residue = result.details["residue"]
    assert residue[0] == git_control.RESIDUE and "Git control file changed: .git/config" in residue
    assert "Git control file added: .git/info/attributes" in residue
    assert any(f"filter.{key}.clean" in item for item in residue)
    evidence = json.loads((Path(result.details["run_dir"]) / "violations.json").read_text(encoding="utf-8"))
    assert evidence["git"] == result.details["git_control"]
    assert "agent-filter" in (repo / ".git/config").read_text(encoding="utf-8")
    assert (repo / "backend/src/app.py").read_text(encoding="utf-8") == EDITED
    assert not result.changed_paths, "nothing is offered for a commit"
    # positive control: with layer 1 alone the same checkout runs it
    layer_1(repo, "status", "--porcelain=v1")
    assert names(ran) == ["agent-filter"]


@HARDENING
@pytest.mark.parametrize("path,text", [
    (".git/hooks/post-index-change", "#!/bin/sh\n"), (".git/info/attributes", "* filter=evil\n"),
    (".git/config", '[remote "origin"]\n\tuploadpack = /tmp/x\n'), (".git/config", "[core\n"),
    (".git/config", '[user]\n\tname = somebody else\n'), (".git/remotes/origin", "URL: ext::x\n"),
    (".git/config.worktree", "[core]\n\tfsmonitor = /tmp/x\n")],
    ids=["hook", "attributes", "uploadpack", "unlistable", "harmless setting", "legacy remote", "worktree config"])
def test_every_control_change_rejects_the_run(config: ProjectConfig, agents: Path, repo: Path, tmp_path: Path,
                                              git_log: GitLog, fake_home: Path, hardened: bool, path: str,
                                              text: str) -> None:
    """Settings that name a program or cannot be listed: nothing but the listing runs. Otherwise Git may look at the
    rest of the run (as evidence: M1), after the listing — and the run is rejected all the same."""
    del fake_home
    result = native_run(native_config(config, agents, hardened), repo, tmp_path,
                        [WORK, {"do": "write" if path.endswith("remotes/origin") else "append", "path": path,
                                "text": text}, git_log.action])
    assert result.status == "rejected" and result.details["residue"][0] == git_control.RESIDUE
    after = git_log.lines("after")
    assert after[0].endswith("config --list --show-scope -z") and text in (repo / path).read_text(encoding="utf-8")
    if "uploadpack" in text or text == "[core\n":   # (config.worktree is not read without its extension)
        assert all(line.endswith("config --list --show-scope -z") for line in after)
        assert git_control.NOT_JUDGED in result.details["residue"]


@HARDENING
@pytest.mark.parametrize("status", ["timeout", "failed"])
def test_a_control_change_outranks_a_timeout_and_a_failure(config: ProjectConfig, agents: Path, repo: Path,
                                                           tmp_path: Path, ran: Path, fake_home: Path, hardened: bool,
                                                           status: str) -> None:
    del fake_home
    cfg = native_config(config, agents, hardened)
    cfg = dataclasses.replace(cfg, runtime=dataclasses.replace(cfg.runtime, task_timeout_seconds=2,
                                                               terminate_grace_seconds=1))
    end = {"do": "sleep", "seconds": 30} if status == "timeout" else {"do": "exit", "code": 3}
    result = native_run(cfg, repo, tmp_path, [*planted_filter(ran), end])
    assert result.status == "rejected" and names(ran) == []


@HARDENING
def test_a_clean_run_still_completes(config: ProjectConfig, agents: Path, repo: Path, tmp_path: Path,
                                     git_log: GitLog, fake_home: Path, hardened: bool) -> None:
    del fake_home
    result = native_run(native_config(config, agents, hardened), repo, tmp_path, [WORK, git_log.action])
    assert result.status == "completed", result.error
    assert result.changed_paths == ["backend/src/app.py"] and "git_control" not in result.details
    after = git_log.lines("after")
    assert after[0].endswith("config --list --show-scope -z"), "the settings are verified before the first status"
    assert any(" status " in line for line in after)


@HARDENING
def test_settings_planted_after_the_check_are_refused_by_the_next_command(config: ProjectConfig, agents: Path,
                                                                          repo: Path, tmp_path: Path, ran: Path,
                                                                          fake_home: Path, hardened: bool,
                                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    """The race between the check and the commands that follow: each command is checked again."""
    del fake_home
    real = nb.verify_git_control

    def check_then_plant(root: Path, before: Any) -> Any:
        verdict = real(root, before)
        git(root, "config", "filter.a.b.clean", program(ran, "late"))
        attributes(root, "*.py filter=a.b")
        return verdict
    monkeypatch.setattr(nb, "verify_git_control", check_then_plant)
    result = native_run(native_config(config, agents, hardened), repo, tmp_path, [WORK])
    assert result.status == "rejected" and "filter.a.b.clean" in result.error and names(ran) == []
    assert result.details["residue"][0] == git_control.RESIDUE


@HARDENING
def test_settings_planted_while_the_run_is_prepared_never_start_the_agent(config: ProjectConfig, agents: Path,
                                                                          repo: Path, tmp_path: Path, ran: Path,
                                                                          fake_home: Path, hardened: bool,
                                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    del fake_home
    real = nb.capture_git_control

    def capture_then_plant(root: Path) -> Any:
        control = real(root)
        git(root, "config", "filter.a.b.clean", program(ran, "early"))
        attributes(root, "*.py filter=a.b")
        edit(root)
        return control
    monkeypatch.setattr(nb, "capture_git_control", capture_then_plant)
    result = native_run(native_config(config, agents, hardened), repo, tmp_path,
                        [{"do": "write", "path": "backend/src/started.py", "text": "x = 1\n"}])
    assert result.status == "rejected" and not (repo / "backend/src/started.py").exists() and names(ran) == []


def test_sensitive_entries_are_still_recorded_without_git(config: ProjectConfig, agents: Path, repo: Path,
                                                          tmp_path: Path, ran: Path, fake_home: Path) -> None:
    """S1's evidence (an entry a run created is never acceptable) comes from the file system, not from Git."""
    del fake_home
    created = "backend/new/.git/config"
    result = native_run(native_config(config, agents, False), repo, tmp_path,
                        [*planted_filter(ran), {"do": "write", "path": created, "text": "[core]\n"}])
    assert result.status == "rejected" and created in result.details["residue"] and names(ran) == []
    assert created in [c["path"] for c in result.details["sensitive_changes"] if c["before"] is None]


# -- the whole workflow: failed task, hold, operator -----------------------------------------------------------------

def workflow_rig(tmp: Path, state_dir: Path, agents: Path, ran: Path, *,
                 actions: list[dict[str, Any]] | None = None) -> autonomy.Rig:
    repo = tmp / "rig"
    init_demo_repo(repo, SELF_TEST_FILES)
    cfg = config_with_agents(autonomy.autonomous(), agents)
    assert not cfg.runtime.native_hardening.enabled       # the production setting
    rig = autonomy.Rig(repo, state_dir, config=cfg)
    for role in ("backend", "cli"):
        rig.coord.runners[role] = NativeCliRunner(cfg.provider_for(role), cfg)  # type: ignore[arg-type]
    rig.activate(TaskSpec("one", "backend", actions_prompt(planted_filter(ran) if actions is None else actions),
                          required_gates=("backend-tests",)),
                 TaskSpec("two", "cli", actions_prompt([{"do": "write", "path": "cli/src/x.py", "text": "x = 1\n"}]),
                          required_gates=("cli-lint",)))
    return rig


def test_a_rejected_run_fails_the_task_and_holds_the_checkout_until_an_operator_decides(
        tmp_path: Path, state_dir: Path, agents: Path, ran: Path, fake_home: Path) -> None:
    del fake_home
    rig = workflow_rig(tmp_path, state_dir, agents, ran)
    repo, store = rig.coord.root, rig.coord.store
    run = rig.coord.run_task("w4.one")
    assert run.state.value == "failed" and run.runner.status == "rejected" and run.commit is None
    hold = hold_of(store, repo)
    assert hold is not None and git_control.RESIDUE in hold["entries"][0]["paths"]
    # every Worker operation is refused while the setting is there; nothing was repaired or discarded
    with pytest.raises(GitConfigExecutionError):
        rig.coord.run_task("w4.two")
    with pytest.raises(GitConfigExecutionError):
        resolve(store, repo, rig.config, apply=True, accept=True, reason="not looked at")
    assert "would run a program" in workspace_mod.workspace_problems(repo, store.load_feature("w4"))[0]
    assert "agent-filter" in (repo / ".git/config").read_text(encoding="utf-8")
    assert names(ran) == []
    # the operator removes what the run planted: still held, resolved only by an explicit decision
    git(repo, "config", "--remove-section", "filter.a.b")
    (repo / ".git/info/attributes").unlink()
    with pytest.raises(WorkspaceHoldError):
        rig.coord.run_task("w4.two")
    assert not resolve(store, repo, rig.config, apply=True).applied
    assert resolve(store, repo, rig.config, apply=True, accept=True, reason="config and attributes restored").applied
    assert rig.coord.run_task("w4.two").runner.status == "completed"
    assert names(ran) == []


def test_the_service_shows_needs_attention_and_starts_nothing(tmp_path: Path, state_dir: Path, agents: Path, ran: Path,
                                                              fake_home: Path) -> None:
    del fake_home
    rig = workflow_rig(tmp_path, state_dir, agents, ran)
    git(rig.coord.root, "config", "filter.a.b.clean", program(ran, "before-the-service"))
    attributes(rig.coord.root, "*.py filter=a.b")
    edit(rig.coord.root)
    rig.service(until=autonomy.state_is("NEEDS_ATTENTION")).run()
    state, reason = autonomy.last_state(rig)
    assert state == "NEEDS_ATTENTION" and reason.startswith("workspace: the Git configuration of the checkout")
    assert not rig.events("worker-cycle-failed"), "a refusal, not an error"
    assert all(rig.task(t).state.value == "ready" for t in ("w4.one", "w4.two"))
    assert names(ran) == []


def test_admission_refuses_and_holds_a_checkout_whose_settings_name_a_program(gc: GitCoordinator, state_dir: Path,
                                                                             config: ProjectConfig, ran: Path) -> None:
    store = StateStore(state_dir)
    inventory = scan(gc.root, config)
    git(gc.root, "config", "filter.a.b.clean", program(ran, "admission"))
    attributes(gc.root, "*.py filter=a.b")
    edit(gc.root)
    started = []
    with pytest.raises(WorkspaceHoldError, match="would run a program"):
        with launch_gate(store, gc.root, config, execution={"id": "x"}, inventory=inventory):
            started.append(True)
    assert not started and hold_of(store, gc.root) is not None and names(ran) == []
    git(gc.root, "config", "--remove-section", "filter.a.b")
    with pytest.raises(WorkspaceHoldError):        # removing it does not lift the hold
        wh.require_no_hold(store, gc.root)


# -- the bwrap backend ------------------------------------------------------------------------------------------------

def bwrap_run(config: ProjectConfig, repo: Path, tmp: Path, actions: list[dict[str, Any]]) -> RunResult:
    runner = RealCliRunner(config.providers["claude"], config, broker_factory=fake_broker_factory())  # type: ignore[arg-type]
    request = RunRequest("f.backend", "backend", actions_prompt(actions),
                         ScopedWorkspace(PathPolicy(repo, config), "backend"),
                         context=run_context(tmp / "runs", "f.backend"))
    return runner.run(request)


@pytest.mark.usefixtures("needs_bwrap", "fake_home")
def test_bwrap_refuses_a_checkout_whose_settings_name_a_program(config: ProjectConfig, agents: Path, repo: Path,
                                                                tmp_path: Path, ran: Path) -> None:
    git(repo, "config", "filter.a.b.clean", program(ran, "bwrap-before"))
    attributes(repo, "*.py filter=a.b")
    edit(repo)
    result = bwrap_run(config_with_agents(config, agents), repo, tmp_path,
                       [{"do": "write", "path": "backend/src/started.py", "text": "x = 1\n"}])
    assert result.status == "rejected" and "filter.a.b.clean" in result.error
    assert not (repo / "backend/src/started.py").exists() and names(ran) == []


@pytest.mark.usefixtures("needs_bwrap", "fake_home")
def test_bwrap_agents_cannot_change_the_control_files_and_complete(config: ProjectConfig, agents: Path, repo: Path,
                                                                   tmp_path: Path, git_log: GitLog) -> None:
    before = git_control.capture(repo)
    result = bwrap_run(config_with_agents(config, agents), repo, tmp_path, [
        WORK, {"do": "try_write", "path": ".git/config", "text": "x"},
        {"do": "try_write", "path": ".git/hooks/post-commit", "text": "x"}])
    assert result.status == "completed", result.error
    assert not changes(repo, before) and result.changed_paths == ["backend/src/app.py"]
    assert git_log.lines("before")


@pytest.mark.usefixtures("needs_bwrap", "fake_home")
@pytest.mark.parametrize("late", [False, True], ids=["during the run", "after the check"])
def test_bwrap_judges_nothing_with_git_after_a_control_change(config: ProjectConfig, agents: Path, repo: Path,
                                                              tmp_path: Path, ran: Path, late: bool,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    """The sandbox mounts .git read-only; another process of the operator's account still can write it."""
    def plant() -> None:
        git(repo, "config", "filter.a.b.clean", program(ran, "bwrap-during"))
        attributes(repo, "*.py filter=a.b")
    real_supervise, real_verify = launchers.supervise, launchers.verify_git_control
    if late:
        monkeypatch.setattr(launchers, "verify_git_control", lambda r, b: (real_verify(r, b), plant())[0])
    else:
        monkeypatch.setattr(launchers, "supervise", lambda *a, **k: (real_supervise(*a, **k), plant())[0])
    result = bwrap_run(config_with_agents(config, agents), repo, tmp_path, [WORK])
    assert result.status == "rejected" and names(ran) == []
    assert result.details["residue"][0] == git_control.RESIDUE
    assert any("filter.a.b.clean" in item for item in result.details["residue"])
    assert (Path(result.details["run_dir"]) / "violations.json").exists()


# =====================================================================================================================
# 7. The structure: no other way to run Git on the checkout
# =====================================================================================================================

# Modules that run Git on repositories the coordinator owns, or (integration_intake) only non-executing queries.
OWN_REPOSITORIES = {"gitsafe.py", "integration_isolation.py", "integration_intake.py", "integration_candidate.py",
                    "integration_review_context.py"}
DEMO = {"demo.py"}      # builds and inspects its own temporary repository with scripted agents
RAW = {"SAFE_CONFIG_ARGS", "git_argv", "hardened_env", "config_records", "untrusted_records"}
ALLOWED_RAW = {"git_control.py": {"untrusted_records"}}     # the listing itself


def test_every_other_module_runs_git_only_through_the_guard() -> None:
    offenders = []
    for path in sorted(PACKAGE.glob("*.py")):
        if path.name in OWN_REPOSITORIES:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("gitsafe"):
                offenders += [f"{path.name}:{node.lineno} imports {a.name}" for a in node.names
                              if a.name in RAW - ALLOWED_RAW.get(path.name, set())]
            elif isinstance(node, (ast.Import, ast.ImportFrom)) and any(
                    a.name.endswith("gitsafe") for a in node.names):
                offenders.append(f"{path.name}:{node.lineno} imports the module (its raw names would be reachable)")
            elif isinstance(node, ast.Attribute) and node.attr in RAW:
                offenders.append(f"{path.name}:{node.lineno} uses .{node.attr}")
            elif isinstance(node, (ast.List, ast.Tuple)) and node.elts and isinstance(node.elts[0], ast.Constant) \
                    and node.elts[0].value == "git" and path.name not in DEMO:
                offenders.append(f"{path.name}:{node.lineno} builds a git command line")
    # push_plan only RENDERS a command line (never executed).
    assert [o for o in offenders if not o.startswith("gitops.py:") or "builds" not in o] == []
    assert len([o for o in offenders if o.startswith("gitops.py:")]) == 1


def test_every_gitops_command_is_scanned(gc: GitCoordinator, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Each Git process of a commit and a push is preceded by its own scan."""
    log = GitLog(tmp_path, monkeypatch)
    edit(gc.root)
    sha = gc.commit(commit_request(gc, gc.rev_parse("HEAD"))).sha
    gc.push_branch(BRANCH, sha, destination=str(tmp_path / "origin.git"))
    lines = log.lines("before")
    commands_run = [line for line in lines if not line.endswith("config --list --show-scope -z")]
    assert len(commands_run) >= 20
    previous = ""
    for line in lines:
        if "receive-pack" not in line and "upload-pack" not in line and "pack-objects" not in line \
                and line.startswith("-C ") and not line.endswith("config --list --show-scope -z"):
            assert previous.endswith("config --list --show-scope -z"), line
        previous = line if line.startswith("-C ") else previous


def test_concurrent_readers_do_not_disturb_each_other(gc: GitCoordinator) -> None:
    errors: list[BaseException] = []

    def work() -> None:
        try:
            for _ in range(5):
                gc.tracked_changes()
        except BaseException as exc:  # pylint: disable=broad-except
            errors.append(exc)
    threads = [threading.Thread(target=work) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors


# =====================================================================================================================
# 8. The independent review's findings
# =====================================================================================================================

# -- H1: git-lfs settings that choose or parameterise a program -------------------------------------------------------

@pytest.mark.parametrize("key,value", [
    ("lfs.customtransfer.xet.args", "transfer $(touch x)"), ("lfs.customtransfer.xet.path", "x"),
    ("lfs.customtransfer.a.b.direction", "both"), ("lfs.extension.x.clean", "x"), ("lfs.extension.x.priority", "0"),
    ("lfs.standalonetransferagent", "xet"), ("lfs.https://host.example/x.standalonetransferagent", "xet"),
    ("hook.x.command", "x"),      # L10: configuration-based hooks of newer Git versions
])
def test_git_lfs_program_settings_are_refused(repo: Path, key: str, value: str) -> None:
    git(repo, "config", key, value)
    assert execution_findings(repo)


@pytest.mark.skipif(not shutil.which("git-lfs"), reason="git-lfs is not installed")
def test_a_git_lfs_transfer_argument_never_runs(gc: GitCoordinator, ran: Path, fake_home: Path) -> None:
    """The operator's GLOBAL configuration defines the transfer program; the repository supplies only its arguments,
    which git-lfs passes through a shell."""
    (fake_home / ".gitconfig").write_text('[filter "lfs"]\n\tclean = git-lfs clean -- %f\n\tsmudge = git-lfs smudge '
                                          '-- %f\n\tprocess = git-lfs filter-process\n\trequired = true\n'
                                          '[lfs "customtransfer.xet"]\n\tpath = /bin/true\n\targs = transfer\n'
                                          '\tconcurrent = false\n', encoding="utf-8")
    root, base = gc.root, gc.rev_parse("HEAD")

    def blob(text: str) -> str:
        return subprocess.run(["git", "-C", str(root), "hash-object", "-w", "--stdin"], input=text, text=True,
                              capture_output=True, check=True).stdout.strip()
    pointer = blob(f"version https://git-lfs.github.com/spec/v1\noid sha256:{'a' * 64}\nsize 12\n")
    for sha, path in ((pointer, "backend/src/l.bin"), (blob("*.bin filter=lfs -text\n"), "backend/.gitattributes")):
        git(root, "update-index", "--add", "--cacheinfo", f"100644,{sha},{path}")
    tip = git(root, *USER, "commit-tree", git(root, "write-tree").strip(), "-p", base, "-m", "lfs").strip()
    git(root, "read-tree", base)
    git(root, "update-index", "-q", "--refresh")
    for key, value in (("lfs.url", "https://127.0.0.1:1/x"), ("lfs.standalonetransferagent", "xet"),
                       ("lfs.customtransfer.xet.args", f"transfer $(touch '{ran}/lfs-args')")):
        git(root, "config", key, value)
    with gc.locks.project(shared=False).acquire() as project:
        with pytest.raises(GitConfigExecutionError):
            gc.fast_forward_branch(BRANCH, since=base, to=tip, project=project)
    assert names(ran) == []
    layer_1(root, "merge", "--ff-only", "--quiet", tip)
    assert names(ran) == ["lfs-args"], "positive control"


# -- H2: no pager, also on an operator's terminal ---------------------------------------------------------------------

PAGER_PROBE = """
import sys
from pathlib import Path
from feature_coordinator.config import default_config
from feature_coordinator.gitops import GitCoordinator
from feature_coordinator.gitsafe import git_argv, hardened_env
from feature_coordinator.locks import LockSet
from feature_coordinator.paths import PathPolicy
import subprocess
repo, state, mode = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
gc = GitCoordinator(repo, LockSet(state / "locks"), PathPolicy(repo, default_config()), tmp_dir=state / "tmp")
head = gc.rev_parse("HEAD")
if mode == "raw":
    subprocess.run([a for a in git_argv(repo, "log", "-1") if a != "--no-pager"], env=hardened_env(), check=False)
else:
    gc.branch_exists("dev"); gc._check_head(gc.rev_parse("HEAD~1"), head, ["cli"])
    subprocess.run(git_argv(repo, "log", "-1"), env=hardened_env(), check=False)
"""


@pytest.mark.parametrize("mode", ["raw", "coordinator"])
def test_a_repository_pager_never_runs_on_a_terminal(gc: GitCoordinator, ran: Path, state_dir: Path,
                                                     mode: str) -> None:
    edit(gc.root)
    git(gc.root, "commit", "-qam", "second")
    for command in ("log", "show-ref", "merge-base"):
        git(gc.root, "config", f"pager.{command}", program(ran, f"pager-{command}", "cat >/dev/null"))
    main_fd, terminal = pty.openpty()
    try:
        proc = subprocess.run([sys.executable, "-c", PAGER_PROBE, str(gc.root), str(state_dir), mode], stdout=terminal,
                              stderr=subprocess.PIPE, text=True, env=child_env() | {"TERM": "xterm"}, check=False,
                              timeout=60)
    finally:
        os.close(terminal)
        os.close(main_fd)
    assert proc.returncode == 0, proc.stderr
    assert names(ran) == (["pager-log"] if mode == "raw" else [])


# -- H3: a control check that cannot be completed rejects and holds; it never lets the run go unjudged ----------------

BROKEN_CONTROL: dict[str, list[dict[str, Any]]] = {
    "unreadable .git": [{"do": "exec", "argv": ["/bin/chmod", "000", ".git"]}],
    "gitfile with NUL": [{"do": "exec", "argv": ["/bin/mv", ".git", "../real.git"]},
                         {"do": "write", "path": ".git", "text": "gitdir: a\0b\n"}],
    "dangling gitfile": [{"do": "exec", "argv": ["/bin/mv", ".git", "../real.git"]},
                         {"do": "write", "path": ".git", "text": "gitdir: /nonexistent\n"}],
    "no .git": [{"do": "exec", "argv": ["/bin/mv", ".git", "../real.git"]}],
    "no HEAD": [{"do": "exec", "argv": ["/bin/mv", ".git/HEAD", ".git/HEAD.moved"]}],
    "no objects": [{"do": "exec", "argv": ["/bin/mv", ".git/objects", ".git/objects.moved"]}],
}


@HARDENING
@pytest.mark.parametrize("damage", list(BROKEN_CONTROL))
def test_a_control_check_that_cannot_be_completed_rejects_the_run(config: ProjectConfig, agents: Path, repo: Path,
                                                                  tmp_path: Path, fake_home: Path, hardened: bool,
                                                                  damage: str) -> None:
    del fake_home
    if damage == "unreadable .git" and os.geteuid() == 0:
        pytest.skip("root reads everything")
    try:
        result = native_run(native_config(config, agents, hardened), repo, tmp_path, [WORK, *BROKEN_CONTROL[damage]])
    finally:
        if (repo / ".git").is_dir():
            (repo / ".git").chmod(0o755)
    assert result.status == "rejected", result.error
    assert result.details["residue"][0] == git_control.RESIDUE
    assert (Path(result.details["run_dir"]) / "violations.json").exists()


def test_a_run_whose_control_files_could_not_be_checked_is_held_not_forgotten(
        tmp_path: Path, state_dir: Path, agents: Path, ran: Path, fake_home: Path) -> None:
    del fake_home
    if os.geteuid() == 0:
        pytest.skip("root reads everything")
    rig = workflow_rig(tmp_path, state_dir, agents, ran, actions=[
        WORK, {"do": "append", "path": ".git/config", "text": "[user]\n\tname = somebody\n"},
        {"do": "exec", "argv": ["/bin/chmod", "000", ".git"]}])
    repo, store = rig.coord.root, rig.coord.store
    try:
        run = rig.coord.run_task("w4.one")
    finally:
        (repo / ".git").chmod(0o755)
    assert run.state.value == "failed" and hold_of(store, repo) is not None
    with pytest.raises(WorkspaceHoldError):     # usable again — and still held: nothing is re-baselined silently
        rig.coord.run_task("w4.two")


@HARDENING
def test_an_unexpected_error_while_judging_rejects_the_run(config: ProjectConfig, agents: Path, repo: Path,
                                                           tmp_path: Path, fake_home: Path, hardened: bool,
                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    del fake_home

    def broken(*_args: Any, **_kwargs: Any) -> Any:
        raise subprocess.CalledProcessError(128, ["git", "status"])
    real = nb.NativeCliRunner._state
    monkeypatch.setattr(nb.NativeCliRunner, "_state",
                        lambda self, root, ctx, attempts=None: broken() if attempts else real(self, root, ctx))
    result = native_run(native_config(config, agents, hardened), repo, tmp_path, [WORK])
    assert result.status == "rejected" and any(p.startswith("unverified workspace:") for p in result.details["residue"])


# -- H4 / M5: the service records the launch and recovers once the operator has decided -------------------------------

def test_the_service_records_a_rejected_launch_and_waits_for_the_operator(
        tmp_path: Path, state_dir: Path, agents: Path, ran: Path, fake_home: Path) -> None:
    del fake_home
    rig = workflow_rig(tmp_path, state_dir, agents, ran)
    rig.service(max_cycles=6).run()
    sched = rig.sched("w4.one")
    assert sched.in_flight is None and sched.launches == 1 and sched.history, "the launch was settled"
    assert sched.attention and "would run a program" in sched.attention["reason"]
    assert not rig.events("worker-cycle-failed")
    assert autonomy.last_state(rig)[0] == "NEEDS_ATTENTION" and hold_of(rig.coord.store, rig.coord.root)
    # the operator removes the setting and lifts the hold: the other task runs, without a restart of the checkout
    git(rig.coord.root, "config", "--remove-section", "filter.a.b")
    (rig.coord.root / ".git/info/attributes").unlink()
    assert resolve(rig.coord.store, rig.coord.root, rig.config, apply=True, accept=True, reason="restored").applied
    rig.service(max_cycles=8).run()
    assert rig.task("w4.two").state.value == "verified" and names(ran) == []


def test_the_service_settles_a_launch_in_the_same_instance(tmp_path: Path, state_dir: Path, agents: Path, ran: Path,
                                                           fake_home: Path) -> None:
    del fake_home
    rig = workflow_rig(tmp_path, state_dir, agents, ran)
    service = rig.service(max_cycles=14)
    idle, seen = service.idle_sleep, []

    def sleep(seconds: float) -> None:
        seen.append(seconds)
        if len(seen) == 3:
            git(rig.coord.root, "config", "--remove-section", "filter.a.b")
            (rig.coord.root / ".git/info/attributes").unlink()
            assert resolve(rig.coord.store, rig.coord.root, rig.config, apply=True, accept=True, reason="r").applied
        idle(seconds)
    service.idle_sleep = sleep
    service.run()
    assert rig.sched("w4.one").in_flight is None and rig.task("w4.two").state.value == "verified"


def test_a_flagged_setting_at_startup_leaves_no_trace_on_a_task(repo: Path, state_dir: Path) -> None:
    rig = autonomy.Rig(repo, state_dir)
    rig.activate(TaskSpec("one", "backend", "API", steps=autonomy.STEPS))
    git(repo, "config", "filter.a.b.clean", "x")
    assert rig.service(max_cycles=3).run() == 0, "not a startup failure"
    assert autonomy.last_state(rig)[0] == "NEEDS_ATTENTION" and rig.sched("w4.one").attention is None
    git(repo, "config", "--remove-section", "filter.a.b")
    rig.service(max_cycles=3).run()
    assert rig.sched("w4.one").attention is None and rig.sched("w4.one").launches >= 1


# -- M1: a control change never hides the run's other unauthorized changes --------------------------------------------

@HARDENING
def test_a_harmless_control_change_does_not_hide_other_violations(config: ProjectConfig, agents: Path, repo: Path,
                                                                  tmp_path: Path, fake_home: Path,
                                                                  hardened: bool) -> None:
    del fake_home
    result = native_run(native_config(config, agents, hardened), repo, tmp_path, [
        WORK, {"do": "write", "path": "cli/src/evil2.py", "text": "x = 1\n"},
        {"do": "git", "args": ["-c", "user.name=a", "-c", "user.email=a@e.invalid", "commit", "-q", "--allow-empty",
                               "-m", "by the agent"]},
        {"do": "append", "path": ".git/config", "text": "[user]\n\tname = somebody\n"}])
    residue = result.details["residue"]
    assert result.status == "rejected" and residue[0] == git_control.RESIDUE
    assert "cli/src/evil2.py" in residue
    assert any("was not made by the Git coordinator" in item for item in residue)
    evidence = json.loads((Path(result.details["run_dir"]) / "violations.json").read_text(encoding="utf-8"))
    assert "cli/src/evil2.py" in evidence["denied"]


@HARDENING
def test_when_git_may_not_judge_the_operator_is_told_and_given_the_state_before(config: ProjectConfig, agents: Path,
                                                                                repo: Path, tmp_path: Path, ran: Path,
                                                                                fake_home: Path,
                                                                                hardened: bool) -> None:
    del fake_home
    head = git(repo, "rev-parse", "HEAD").strip()
    (repo / "cli/src").mkdir(parents=True, exist_ok=True)
    (repo / "cli/src/earlier.py").write_text("x = 0\n", encoding="utf-8")
    result = native_run(native_config(config, agents, hardened), repo, tmp_path, [
        *planted_filter(ran), {"do": "write", "path": "cli/src/evil2.py", "text": "x = 1\n"}])
    assert git_control.NOT_JUDGED in result.details["residue"] and names(ran) == []
    evidence = json.loads((Path(result.details["run_dir"]) / "violations.json").read_text(encoding="utf-8"))
    assert evidence["before"]["head"] == head and "cli/src/earlier.py" in evidence["before"]["dirty"]


def test_a_late_refusal_keeps_the_evidence_already_written(config: ProjectConfig, agents: Path, repo: Path,
                                                           tmp_path: Path, ran: Path, fake_home: Path,
                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    """L3: the judgement had already recorded what it found when a later command was refused."""
    del fake_home
    real = launchers.role_dirty_paths

    def plant(root: Path, roots: Any) -> list[str]:
        git(root, "config", "filter.a.b.clean", program(ran, "late"))
        return real(root, roots)
    monkeypatch.setattr(launchers, "role_dirty_paths", plant)
    result = native_run(native_config(config, agents, False), repo, tmp_path,
                        [WORK, {"do": "write", "path": "cli/src/evil2.py", "text": "x = 1\n"}])
    assert result.status == "rejected" and names(ran) == []
    run_dir = Path(result.details["run_dir"])
    assert "cli/src/evil2.py" in (run_dir / "violations-judged.json").read_text(encoding="utf-8")


# -- M2: no automatic maintenance outlives a command ------------------------------------------------------------------

def test_no_automatic_gc_runs_in_the_background(gc: GitCoordinator, tmp_path: Path,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in (("gc.auto", "1"), ("gc.autoPackLimit", "1"), ("gc.autoDetach", "true"),
                       ("maintenance.auto", "true"), ("fetch.writeCommitGraph", "false")):
        git(gc.root, "config", key, value)
    git(gc.root, "repack", "-q", "-a")
    for text in ("one", "two"):      # more packs than gc.autoPackLimit: an automatic gc has work to do
        sha = subprocess.run(["git", "-C", str(gc.root), "hash-object", "-w", "--stdin"], input=text, text=True,
                             capture_output=True, check=True).stdout
        subprocess.run(["git", "-C", str(gc.root), "pack-objects", "-q", str(gc.root / ".git/objects/pack/pack")],
                       input=sha, text=True, capture_output=True, check=True)
    trace = tmp_path / "trace"
    monkeypatch.setenv("GIT_TRACE", str(trace))
    gc.fetch_branch("dev")
    head = gc.rev_parse("HEAD")
    with gc.locks.project(shared=False).acquire() as project:
        gc.fast_forward_branch(BRANCH, since=head, to=head, project=project)
    run_git(gc.root, "merge", "--ff-only", "--quiet", "dev")
    text = trace.read_text(encoding="utf-8")
    assert "fetch" in text and "git gc" not in text and "pack-objects" not in text


# -- M3: an interrupted run's control changes are found ---------------------------------------------------------------

def test_a_failing_heartbeat_never_leaves_the_agent_running(tmp_path: Path) -> None:
    marker = tmp_path / "still-alive"

    def heartbeat() -> None:
        raise OSError("the state directory is gone")
    with pytest.raises(OSError):
        supervise(["/bin/sh", "-c", f"sleep 2; touch '{marker}'"], cwd=tmp_path, env=None,
                  stdout_path=tmp_path / "out", stderr_path=tmp_path / "err", timeout_seconds=30, heartbeat=heartbeat,
                  heartbeat_interval=0.1)
    time.sleep(2.5)
    assert not marker.exists()


@pytest.mark.parametrize("how", ["runner raised", "worker died"])
def test_an_interrupted_runs_control_change_holds_the_checkout(tmp_path: Path, state_dir: Path, agents: Path,
                                                               ran: Path, fake_home: Path, how: str,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    del fake_home
    rig = workflow_rig(tmp_path, state_dir, agents, ran, actions=[
        WORK, {"do": "append", "path": ".git/config", "text": "[user]\n\tname = somebody\n"}])
    repo, store = rig.coord.root, rig.coord.store
    if how == "runner raised":
        real = nb.kill_tagged
        monkeypatch.setattr(nb, "kill_tagged", lambda *a: (real(*a), (_ for _ in ()).throw(OSError("interrupted")))[0])
        with pytest.raises(OSError):
            rig.coord.run_task("w4.one")
        monkeypatch.undo()
    else:       # what a killed Worker leaves: a registered execution whose processes are gone
        monkeypatch.setattr(wh, "finish_execution", lambda *a, **k: None)
        monkeypatch.setattr("feature_coordinator.workflow.finish_execution", lambda *a, **k: None)
        rig.coord.run_task("w4.one")
        monkeypatch.undo()
        flag = store.get_flag(wh.GUARD_FLAG)
        for execution in flag[wh.checkout_key(repo)]["executions"].values():
            execution["worker"] = {"pid": 2 ** 22 - 1, "start_ticks": 1, "boot_id": "x"}
            execution["agent"] = None
        with store.transaction():
            store.set_flag(wh.GUARD_FLAG, flag)
    with pytest.raises(WorkspaceHoldError, match="interrupted"):
        rig.coord.run_task("w4.two")
    assert git_control.RESIDUE in hold_of(store, repo)["entries"][0]["paths"]


def test_an_interrupted_run_without_control_changes_is_not_held(tmp_path: Path, state_dir: Path, agents: Path,
                                                               ran: Path, fake_home: Path,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    del fake_home
    rig = workflow_rig(tmp_path, state_dir, agents, ran, actions=[WORK])
    real = nb.kill_tagged
    monkeypatch.setattr(nb, "kill_tagged", lambda *a: (real(*a), (_ for _ in ()).throw(OSError("interrupted")))[0])
    with pytest.raises(OSError):
        rig.coord.run_task("w4.one")
    monkeypatch.undo()
    assert hold_of(rig.coord.store, rig.coord.root) is None
    assert rig.coord.run_task("w4.two").runner.status == "completed"


# -- M4: the operator can always see the hold -------------------------------------------------------------------------

@pytest.mark.parametrize("as_json", [False, True], ids=["text", "json"])
def test_workspace_status_shows_the_hold_while_git_is_refused(tmp_path: Path, state_dir: Path, agents: Path, ran: Path,
                                                             fake_home: Path, capsys: pytest.CaptureFixture[str],
                                                             as_json: bool) -> None:
    del fake_home
    rig = workflow_rig(tmp_path, state_dir, agents, ran)
    rig.coord.run_task("w4.one")
    (rig.coord.root / ".git/hooks").mkdir(exist_ok=True)
    args = argparse.Namespace(state_dir=str(state_dir), repo=str(rig.coord.root), json=as_json)
    assert cmd_workspace_status(args, rig.config) == 1
    out = capsys.readouterr().out
    ref = wh.hold_ref(hold_of(rig.coord.store, rig.coord.root))
    assert ref in out and git_control.RESIDUE in out and "would run a program" in out
    if as_json:
        assert json.loads(out)["needs_operator"] is True


# -- M6 / L7: what an editor or git-lfs writes by itself --------------------------------------------------------------

@HARDENING
@pytest.mark.parametrize("text", ['[branch "feature/s2"]\n\tvscode-merge-base = origin/dev\n',
                                  '[lfs]\n\trepositoryformatversion = 0\n', "# a comment\n"],
                         ids=["vscode", "git-lfs", "comment"])
def test_settings_that_tools_write_by_themselves_do_not_reject_a_run(config: ProjectConfig, agents: Path, repo: Path,
                                                                     tmp_path: Path, fake_home: Path, hardened: bool,
                                                                     text: str) -> None:
    del fake_home
    result = native_run(native_config(config, agents, hardened), repo, tmp_path,
                        [WORK, {"do": "append", "path": ".git/config", "text": text}])
    assert result.status == "completed", result.error


@pytest.mark.parametrize("text", ['[branch "dev"]\n\tvscode-merge-base = x\n\tremote = evil\n',
                                  '[branch "dev"]\n\tpushRemote = evil\n', "[include]\n\tpath = ../x.inc\n",
                                  '[includeIf "onbranch:feature/**"]\n\tpath = x.inc\n',
                                  "[lfs]\n\trepositoryformatversion = 0\n\turl = https://evil.example\n"])
def test_every_other_setting_still_does(repo: Path, text: str) -> None:
    before = git_control.capture(repo)
    append(repo / ".git/config", text)
    assert changes(repo, before) == ["Git control file changed: .git/config"]


# -- LOW findings -----------------------------------------------------------------------------------------------------

def test_l2_names_and_values_are_escaped_everywhere(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    before = git_control.capture(repo)
    (repo / ".git/hooks").mkdir(exist_ok=True)
    (repo / os.fsdecode(b".git/hooks/\xff\x1b[31m")).write_text("x", encoding="utf-8")
    append(repo / ".git/config", '[filter "a.b"]\n\tclean = "x\\tred"\n')
    problems = changes(repo, before)
    assert all(p.isascii() and p.isprintable() for p in problems), problems
    print("\n".join(problems))
    assert "\\xff" in capsys.readouterr().out or "udcff" in "".join(problems)


def test_l5_a_fast_forward_never_stashes(gc: GitCoordinator, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    git(gc.root, "config", "merge.autoStash", "true")
    git(gc.root, "branch", "tip")
    edit(gc.root)
    git(gc.root, "commit", "-qam", "next")
    tip, base = gc.rev_parse("HEAD"), gc.rev_parse("HEAD~1")
    git(gc.root, "reset", "-q", "--hard", base)
    log = GitLog(tmp_path, monkeypatch)
    with gc.locks.project(shared=False).acquire() as project:
        assert gc.fast_forward_branch(BRANCH, since=base, to=tip, project=project)
    assert any("merge --ff-only --no-autostash" in line for line in log.lines("before"))


def test_the_environment_cannot_configure_a_worker_command(gc: GitCoordinator, ran: Path,
                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    """hardened_env / gitops._env: an inherited variable never reaches a Worker Git command."""
    attributes(gc.root, "*.py filter=env")
    edit(gc.root)
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "filter.env.clean")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", program(ran, "env-filter"))
    subprocess.run(["git", "-C", str(gc.root), "status", "--porcelain"], capture_output=True, check=True)
    assert names(ran) == ["env-filter"], "attack precondition"
    (ran / "env-filter").unlink()
    edit(gc.root, EDITED)
    gc.tracked_changes()
    nb._status(gc.root)
    assert branch_contains(gc.root, BRANCH, gc.rev_parse("HEAD"))
    assert names(ran) == []
    for env in (hardened_env(), guarded_env(), gc._env()):
        assert not [k for k in env if k.startswith("GIT_CONFIG")]


def test_a_push_never_recurses(gc: GitCoordinator) -> None:
    git(gc.root, "config", "push.recurseSubmodules", "on-demand")
    git(gc.root, "config", "submodule.recurse", "true")
    for key, value in (("push.recurseSubmodules", "no"), ("submodule.recurse", "false"), ("gc.auto", "0"),
                       ("maintenance.auto", "false"), ("diff.submodule", "short")):
        assert run_git(gc.root, "config", "--get", key).stdout.strip() == value


def test_l1_a_refused_command_is_not_reported_as_a_missing_branch(gc: GitCoordinator) -> None:
    """publish._head_problem: a refusal is deferred, never recorded as a fact about the PR (review: section 9)."""
    publisher = publish.PullRequestPublisher.__new__(publish.PullRequestPublisher)
    publisher.git = gc
    git(gc.root, "config", "filter.a.b.clean", "x")
    feature = Feature(id="f", slug="f", title="F", base_branch="dev", branch=BRANCH)
    with pytest.raises(GitConfigExecutionError):
        publisher._head_problem(feature, SimpleNamespace(head_sha="0" * 40))


@pytest.mark.usefixtures("needs_bwrap", "fake_home")
def test_bwrap_evidence_is_limited_to_the_roles_roots(config: ProjectConfig, agents: Path, repo: Path, tmp_path: Path,
                                                      ran: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The sandbox cannot write elsewhere: a sensitive entry another process created there is not this run's."""
    def plant() -> None:
        git(repo, "config", "filter.a.b.clean", program(ran, "bwrap-within"))
        for path in ("cli/.env", "backend/.env"):
            (repo / path).parent.mkdir(exist_ok=True)
            (repo / path).write_text("KEY=1\n", encoding="utf-8")
    real = launchers.supervise
    monkeypatch.setattr(launchers, "supervise", lambda *a, **k: (real(*a, **k), plant())[0])
    result = bwrap_run(config_with_agents(config, agents), repo, tmp_path, [WORK])
    assert "backend/.env" in result.details["residue"] and "cli/.env" not in result.details["residue"]


# -- is Git allowed to look at the rest of the run? ----------------------------------------------------------------

NOT_THE_SAME_REPOSITORY: dict[str, Callable[[Path], Any]] = {
    "HEAD": lambda r: (r / ".git/HEAD").rename(r / ".git/HEAD.moved"),
    "HEAD is a symlink": lambda r: ((r / ".git/HEAD").rename(r / ".git/h"), (r / ".git/HEAD").symlink_to("h")),
    "objects": lambda r: (r / ".git/objects").rename(r / ".git/objects.moved"),
    "refs": lambda r: (r / ".git/refs").rename(r / ".git/refs.moved"),
    "commondir": lambda r: (r / ".git/commondir").write_text("../../elsewhere\n", encoding="utf-8"),
    "gitfile": CONTROL_CHANGES["git directory replaced"],
    "symlink": CONTROL_CHANGES["git directory is a symlink"],
}


@pytest.mark.parametrize("change", list(NOT_THE_SAME_REPOSITORY))
def test_git_never_judges_what_may_be_another_repository(repo: Path, change: str) -> None:
    before = git_control.capture(repo)
    assert git_control.verify(repo, before) == git_control.Verdict([], git_safe=True)
    NOT_THE_SAME_REPOSITORY[change](repo)
    verdict = git_control.verify(repo, before)
    assert verdict.problems and not verdict.git_safe


def test_git_never_judges_an_outer_repository(tmp_path: Path) -> None:
    """Without its HEAD the checkout's .git is no repository and Git finds the one around it, whose settings are the
    same: only the file comparison can tell (L8)."""
    outer, inner = tmp_path / "outer", tmp_path / "outer/inner"
    init_demo_repo(outer, {"a.txt": "a\n"})
    init_demo_repo(inner, SELF_TEST_FILES)
    before = git_control.capture(inner)
    (inner / ".git/HEAD").rename(inner / ".git/HEAD.moved")
    assert git(inner, "rev-parse", "--show-toplevel").strip() == str(outer), "precondition"
    verdict = git_control.verify(inner, before)
    assert "Git control file removed: .git/HEAD" in verdict.problems and not verdict.git_safe


@pytest.mark.parametrize("text,safe", [("[user]\n\tname = x\n", True), ('[filter "a.b"]\n\tclean = x\n', False),
                                       ("[core\n", False)], ids=["harmless", "program", "unlistable"])
def test_git_judges_the_rest_only_while_the_settings_are_safe(repo: Path, text: str, safe: bool) -> None:
    before = git_control.capture(repo)
    append(repo / ".git/config", text)
    verdict = git_control.verify(repo, before)
    assert verdict.problems and verdict.git_safe is safe


def test_an_inherited_git_dir_never_redirects_a_branch_check(gc: GitCoordinator, tmp_path: Path,
                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    other = tmp_path / "other"
    init_demo_repo(other, {"a.txt": "a\n"})
    head = gc.rev_parse("HEAD")
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    assert branch_contains(gc.root, BRANCH, head)       # github_commands: no environment of its own


# =====================================================================================================================
# 9. The second review round
# =====================================================================================================================

# -- N1: git-lfs reads `git config -l` line by line, split at the first `=`; the scan must see what it sees ---------

LFS_GLOBAL = ('[filter "lfs"]\n\tclean = git-lfs clean -- %f\n\tsmudge = git-lfs smudge -- %f\n'
              '\tprocess = git-lfs filter-process\n\trequired = true\n')


def lfs_lines(ran: Path) -> str:
    planted = program(ran, "lfs-line")
    return f"x\nlfs.extension.foo.clean={planted}\nlfs.extension.foo.smudge={planted}\nlfs.extension.foo.priority=0"


@pytest.mark.parametrize("key", ["branch.dev.vscode-merge-base", "user.note", "lfs.repositoryformatversion"])
def test_a_setting_hidden_behind_a_newline_is_found(repo: Path, ran: Path, key: str) -> None:
    git(repo, "config", key, lfs_lines(ran))
    assert execution_findings(repo)
    with pytest.raises(GitConfigExecutionError):
        run_git(repo, "status")


@pytest.mark.parametrize("key,value", [
    ("core.sshcommand=touch M #.x", "1"), ("lfs.extension.foo.clean=touch M #.x", "1"),
    ("user.note", "a\rb"), ("user.note", "a\x1bb"),
])
def test_a_setting_that_reads_differently_by_line_is_found(repo: Path, key: str, value: str) -> None:
    git(repo, "config", key, value)
    assert execution_findings(repo)


@pytest.mark.parametrize("key,value", [
    ("branch.a=b.merge", "refs/heads/a=b"), ("remote.origin.url", "https://host.example/x?a=b"),
    ("url.https://host.example/?a=b.insteadof", "x="), ("user.name", "Plámen Č"), ("alias.x", "log --format=%H"),
    ("core.excludesfile", "a\tb"),
])
def test_equal_signs_and_ordinary_text_are_not_refused(repo: Path, key: str, value: str) -> None:
    git(repo, "config", key, value)
    assert execution_findings(repo) == []


@pytest.mark.skipif(not shutil.which("git-lfs"), reason="git-lfs is not installed")
@HARDENING
@pytest.mark.parametrize("key", ['[branch "dev"]\n\tvscode-merge-base', "[user]\n\tnote"], ids=["ignored", "other"])
def test_a_git_lfs_program_hidden_in_a_value_never_runs(config: ProjectConfig, agents: Path, repo: Path,
                                                        tmp_path: Path, ran: Path, fake_home: Path, hardened: bool,
                                                        key: str) -> None:
    (fake_home / ".gitconfig").write_text(LFS_GLOBAL, encoding="utf-8")
    value = lfs_lines(ran).replace("\n", "\\n")
    result = native_run(native_config(config, agents, hardened), repo, tmp_path, [
        WORK, {"do": "append", "path": ".git/config", "text": f'{key} = "{value}"\n'},
        {"do": "write", "path": "backend/.gitattributes", "text": "*.py filter=lfs\n"}])
    assert result.status == "rejected" and names(ran) == [] and git_control.NOT_JUDGED in result.details["residue"]
    layer_1(repo, "status", "--porcelain=v1")
    assert names(ran) == ["lfs-line"], "positive control"


@pytest.mark.parametrize("text", ["[lfs]\n\trepositoryformatversion = 99\n",
                                  '[branch "x"]\n\tvscode-merge-base = "a b"\n',
                                  '[branch "x"]\n\tvscode-merge-base = -x\n'])
def test_only_plain_values_of_the_ignored_settings_are_ignored(repo: Path, text: str) -> None:
    before = git_control.capture(repo)
    append(repo / ".git/config", text)
    assert changes(repo, before) == ["Git control file changed: .git/config"]


def test_a_configuration_that_is_no_plain_file_is_never_tolerated(repo: Path) -> None:
    (repo / ".git/config").rename(repo / ".git/one")
    shutil.copy(repo / ".git/one", repo / ".git/two")
    (repo / ".git/config").symlink_to("one")
    before = git_control.capture(repo)
    (repo / ".git/config").unlink()
    (repo / ".git/config").symlink_to("two")        # the same settings, from another file
    assert changes(repo, before) == ["Git control file changed: .git/config"]


def test_a_changed_mode_of_the_configuration_is_a_change(repo: Path) -> None:
    before = git_control.capture(repo)
    (repo / ".git/config").chmod(0o777)
    assert changes(repo, before) == ["Git control file changed: .git/config"]


# -- N2: an ordinary error is not a verdict about the run -----------------------------------------------------------

def failing_state(monkeypatch: pytest.MonkeyPatch, error: BaseException) -> None:
    real = nb.NativeCliRunner._state

    def state(self: Any, root: Path, ctx: Any, attempts: int | None = None) -> Any:
        if attempts:    # the judgement's snapshot, not the one before the run
            raise error
        return real(self, root, ctx)
    monkeypatch.setattr(nb.NativeCliRunner, "_state", state)


@HARDENING
@pytest.mark.parametrize("error", [LockBusyError("git.lock is busy"), OSError(24, "Too many open files")],
                         ids=["lock", "EMFILE"])
def test_a_transient_error_while_judging_is_not_turned_into_a_rejection(
        config: ProjectConfig, agents: Path, repo: Path, tmp_path: Path, fake_home: Path, hardened: bool,
        error: Exception, monkeypatch: pytest.MonkeyPatch) -> None:
    del fake_home
    failing_state(monkeypatch, error)
    with pytest.raises(type(error)):
        native_run(native_config(config, agents, hardened), repo, tmp_path, [WORK])


def test_a_transient_error_leaves_the_task_ready_and_the_checkout_free(tmp_path: Path, state_dir: Path, agents: Path,
                                                                      ran: Path, fake_home: Path,
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    del fake_home
    rig = workflow_rig(tmp_path, state_dir, agents, ran, actions=[WORK])
    with monkeypatch.context() as patch:
        failing_state(patch, LockBusyError("git.lock is busy"))
        with pytest.raises(LockBusyError):
            rig.coord.run_task("w4.one")
    assert rig.task("w4.one").state.value == "ready" and hold_of(rig.coord.store, rig.coord.root) is None
    assert rig.coord.run_task("w4.one").runner.status == "completed"


@HARDENING
def test_a_run_that_git_cannot_judge_is_rejected_with_the_true_reason(config: ProjectConfig, agents: Path, repo: Path,
                                                                      tmp_path: Path, fake_home: Path,
                                                                      hardened: bool) -> None:
    del fake_home
    result = native_run(native_config(config, agents, hardened), repo, tmp_path,
                        [WORK, {"do": "write", "path": ".git/index", "text": "garbage"}])
    assert result.status == "rejected" and "could not be judged" in result.error
    assert "Git control files" not in result.error and git_control.RESIDUE not in result.details["residue"]
    assert git_control.NOT_JUDGED in result.details["residue"]


def test_a_control_change_is_rejected_whatever_else_fails(config: ProjectConfig, agents: Path, repo: Path,
                                                          tmp_path: Path, fake_home: Path,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    del fake_home
    failing_state(monkeypatch, LockBusyError("git.lock is busy"))
    result = native_run(native_config(config, agents, False), repo, tmp_path,
                        [WORK, {"do": "append", "path": ".git/config", "text": "[user]\n\tname = x\n"}])
    assert result.status == "rejected" and result.details["residue"][0] == git_control.RESIDUE


# -- N3: the service settles every launch it cannot inspect ----------------------------------------------------------

@pytest.mark.parametrize("damage", [
    [{"do": "write", "path": ".git/index", "text": "garbage"}],
    [{"do": "exec", "argv": ["/bin/rm", "-rf", ".git/refs/heads/feature"]}],
    [{"do": "exec", "argv": ["/bin/chmod", "000", ".git"]}],
], ids=["index", "branch", "unreadable"])
def test_the_service_settles_a_launch_it_cannot_inspect(tmp_path: Path, state_dir: Path, agents: Path, ran: Path,
                                                        fake_home: Path, damage: list[dict[str, Any]]) -> None:
    del fake_home
    if os.geteuid() == 0:
        pytest.skip("root reads everything")
    rig = workflow_rig(tmp_path, state_dir, agents, ran, actions=[WORK, *damage])
    try:
        rig.service(max_cycles=4).run()
        sched = rig.sched("w4.one")
    finally:
        (rig.coord.root / ".git").chmod(0o755)
    assert sched.in_flight is None and sched.launches == 1 and sched.attention
    assert rig.task("w4.two").state.value in {"ready", "failed"}


# -- N4 / N5 / N9: an interrupted run is compared before anything else, and never under the Coordinator lock ---------

def dead_execution(store: StateStore, repo: Path, **changed: Any) -> None:
    """What a killed Worker leaves: a registered execution whose processes are gone."""
    with store.transaction():
        flag = store.get_flag(wh.GUARD_FLAG) or {}
        record = flag.setdefault(wh.checkout_key(repo), {})
        record.setdefault("executions", {})["dead"] = {
            "task": "w4.one", "role": "backend", "runner": "claude", "run_dir": "", "agent": None,
            "worker": {"pid": 2 ** 22 - 1, "start_ticks": 1, "boot_id": "x"}, "admitted_at": "2026-01-01T00:00:00Z",
            "generation": 0, "git_control": git_control.capture(repo).to_dict(), **changed}
        store.set_flag(wh.GUARD_FLAG, flag)


@pytest.mark.parametrize("check", ["guard", "service", "launch"])
def test_an_interrupted_run_is_compared_before_anything_else(gc: GitCoordinator, state_dir: Path,
                                                             config: ProjectConfig, check: str) -> None:
    store = StateStore(state_dir)
    dead_execution(store, gc.root)
    append(gc.root / ".git/config", "[user]\n\tname = somebody\n")
    if check == "service":
        assert "interrupted" in wh.hold_problems(store, gc.root)[0]
    else:
        with pytest.raises(WorkspaceHoldError, match="interrupted"):
            if check == "guard":
                wh.guard_before_run(store, gc.root, config)
            else:
                with launch_gate(store, gc.root, config, execution={"id": "x"}):
                    pytest.fail("no agent may start")
    assert "dead" not in store.get_flag(wh.GUARD_FLAG)[wh.checkout_key(gc.root)]["executions"]


def test_the_comparison_never_holds_the_coordinator_lock(gc: GitCoordinator, state_dir: Path,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    store = StateStore(state_dir)
    dead_execution(store, gc.root)
    real, free = wh.verify, []

    def slow(root: Path, before: Any) -> Any:
        other = StateStore(state_dir)
        with other.transaction():       # another process could work now
            free.append(True)
        return real(root, before)
    monkeypatch.setattr(wh, "verify", slow)
    wh.hold_problems(store, gc.root)
    wh.abandon_execution(store, gc.root, "dead")
    dead_execution(store, gc.root)
    wh.abandon_execution(store, gc.root, "dead")
    assert len(free) == 2


@pytest.mark.parametrize("broken", [{}, {"files": 1, "settings": 2}, {"files": {}}, "x"])
def test_an_unreadable_recorded_state_holds_the_checkout(gc: GitCoordinator, state_dir: Path, config: ProjectConfig,
                                                         broken: Any) -> None:
    store = StateStore(state_dir)
    dead_execution(store, gc.root, git_control=broken or {"files": None})
    with pytest.raises(WorkspaceHoldError, match="interrupted"):
        wh.guard_before_run(store, gc.root, config)


def test_records_from_before_the_upgrade_are_dropped_as_before(gc: GitCoordinator, state_dir: Path) -> None:
    store = StateStore(state_dir)
    dead_execution(store, gc.root, git_control=None)
    append(gc.root / ".git/config", "[user]\n\tname = somebody\n")
    assert not wh.hold_problems(store, gc.root)
    assert "dead" not in store.get_flag(wh.GUARD_FLAG)[wh.checkout_key(gc.root)]["executions"]


# -- N6 / L8 / L2 / N7 / N12 -------------------------------------------------------------------------------------------

def test_a_symlinked_control_directory_is_still_compared(repo: Path, tmp_path: Path) -> None:
    (repo / ".git/info").rename(tmp_path / "info")
    (repo / ".git/info").symlink_to(tmp_path / "info")
    before = git_control.capture(repo)
    append(tmp_path / "info/attributes", "*.py filter=x\n")
    assert changes(repo, before) == ["Git control file added: .git/info/attributes"]


def test_an_unreadable_control_directory_is_not_skipped(repo: Path) -> None:
    if os.geteuid() == 0:
        pytest.skip("root reads everything")
    nested = repo / ".git/hooks/nested"
    nested.mkdir(parents=True)
    before = git_control.capture(repo)
    nested.chmod(0)
    try:
        assert changes(repo, before)[0].startswith("Git control files cannot be verified")
    finally:
        nested.chmod(0o755)


def test_a_head_that_is_no_head_is_another_repository(tmp_path: Path) -> None:
    outer, inner = tmp_path / "outer", tmp_path / "outer/inner"
    init_demo_repo(outer, {"a.txt": "a\n"})
    init_demo_repo(inner, SELF_TEST_FILES)
    before = git_control.capture(inner)
    (inner / ".git/HEAD").write_text("garbage\n", encoding="utf-8")
    verdict = git_control.verify(inner, before)
    assert verdict.problems == ["Git control file changed: .git/HEAD"] and not verdict.git_safe
    (inner / ".git/HEAD").write_text("ref: refs/heads/feature/other\n", encoding="utf-8")
    assert not git_control.verify(inner, before).problems, "a branch switch is judged as Git state, not here"
    (inner / ".git/HEAD").write_text("0" * 40 + "\n", encoding="utf-8")
    assert not git_control.verify(inner, before).problems


def test_a_listing_error_is_printable(repo: Path) -> None:
    name = b"bad-\xff\x1b.inc"         # Git quotes the name of the included file it cannot parse
    (repo / os.fsdecode(b".git/" + name)).write_text("[core\n", encoding="utf-8")
    with (repo / ".git/config").open("ab") as handle:
        handle.write(b"[include]\n\tpath = \"" + name + b"\"\n")
    with pytest.raises(GitConfigExecutionError) as caught:
        execution_findings(repo)
    assert str(caught.value).isascii() and str(caught.value).isprintable()


@pytest.mark.parametrize("unsafe", [False, True], ids=["judged", "not judged"])
def test_evidence_planted_by_the_agent_is_never_taken_for_ours(config: ProjectConfig, agents: Path, repo: Path,
                                                              tmp_path: Path, ran: Path, fake_home: Path, unsafe: bool,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    del fake_home
    run_dir = tmp_path / "fixed-run"

    def fixed(root: Path, task: str, **kwargs: Any) -> Any:
        del root, task
        run_dir.mkdir(mode=0o700)
        return launchers.RunContext(run_dir=run_dir, **kwargs)
    monkeypatch.setitem(globals(), "run_context", fixed)
    actions = planted_filter(ran) if unsafe else [WORK, {"do": "append", "path": ".git/config",
                                                        "text": "[user]\n\tname = x\n"}]
    result = native_run(native_config(config, agents, False), repo, tmp_path,
                        [*actions, {"do": "write", "path": str(run_dir / "violations.json"), "text": "not json"}])
    assert result.status == "rejected"
    ours = json.loads((run_dir / "violations.json").read_text(encoding="utf-8"))
    assert "Git control file changed: .git/config" in ours["git"]
    assert not (run_dir / "violations-judged.json").exists()


def test_workspace_status_does_not_claim_what_git_was_not_asked(tmp_path: Path, state_dir: Path, agents: Path,
                                                               ran: Path, fake_home: Path,
                                                               capsys: pytest.CaptureFixture[str]) -> None:
    del fake_home
    rig = workflow_rig(tmp_path, state_dir, agents, ran)
    rig.coord.run_task("w4.one")
    cmd_workspace_status(argparse.Namespace(state_dir=str(state_dir), repo=str(rig.coord.root), json=False), rig.config)
    assert "not explained by Git" not in capsys.readouterr().out
    git(rig.coord.root, "config", "--remove-section", "filter.a.b")
    dead_execution(rig.coord.store, rig.coord.root, worker=ProcessIdentity.current().to_dict())     # alive: us
    cmd_workspace_status(argparse.Namespace(state_dir=str(state_dir), repo=str(rig.coord.root), json=True), rig.config)
    out = json.loads(capsys.readouterr().out)
    assert list(out["executions"]) == ["dead"] and "git_control" not in out["executions"]["dead"]


# -- the mutations that survived ------------

@pytest.mark.usefixtures("needs_bwrap", "fake_home")
def test_bwrap_rejects_a_harmless_control_change_and_keeps_the_judgement(config: ProjectConfig, agents: Path,
                                                                         repo: Path, tmp_path: Path,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    def plant() -> None:
        append(repo / ".git/config", "[user]\n\tname = somebody\n")
        (repo / "backend/.env").write_text("KEY=1\n", encoding="utf-8")
    real = launchers.supervise
    monkeypatch.setattr(launchers, "supervise", lambda *a, **k: (real(*a, **k), plant())[0])
    result = bwrap_run(config_with_agents(config, agents), repo, tmp_path, [WORK])
    assert result.status == "rejected" and not result.changed_paths
    assert result.details["residue"][0] == git_control.RESIDUE and "backend/.env" in result.details["residue"]
    evidence = json.loads((Path(result.details["run_dir"]) / "violations.json").read_text(encoding="utf-8"))
    assert evidence["git"] == ["Git control file changed: .git/config"] and "backend/.env" in evidence["denied"]


def test_a_failed_supervision_leaves_no_tagged_process(config: ProjectConfig, agents: Path, repo: Path, tmp_path: Path,
                                                       fake_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    del fake_home
    killed: list[str] = []
    monkeypatch.setattr(nb, "kill_tagged", lambda name, tag: (killed.append(tag), [])[1])
    monkeypatch.setattr(nb, "supervise", lambda *a, **k: (_ for _ in ()).throw(OSError("supervision failed")))
    with pytest.raises(OSError, match="supervision failed"):
        native_run(native_config(config, agents, False), repo, tmp_path, [WORK])
    assert len(killed) == 1


def test_a_state_that_cannot_be_captured_at_admission_holds_the_checkout(gc: GitCoordinator, state_dir: Path,
                                                                        config: ProjectConfig,
                                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    store = StateStore(state_dir)
    monkeypatch.setattr(wh, "capture", lambda root: (_ for _ in ()).throw(GitConfigExecutionError("too large")))
    with pytest.raises(WorkspaceHoldError, match="too large"):
        with launch_gate(store, gc.root, config, execution={"id": "x"}):
            pytest.fail("no agent may start")
    assert hold_of(store, gc.root) is not None


def test_a_review_is_deferred_not_refused_when_git_is(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """review.py: `no merge base` would be recorded as a fact about the PR."""
    runner = review.ReviewRunner.__new__(review.ReviewRunner)
    runner.coord = SimpleNamespace(root=tmp_path)
    runner.git = SimpleNamespace(rev_parse=lambda _ref: "1" * 40)
    monkeypatch.setattr(review, "merge_base", lambda *a: (_ for _ in ()).throw(GitConfigExecutionError("refused")))
    with pytest.raises(GitConfigExecutionError, match="refused"):
        runner._bind(SimpleNamespace(base_branch="dev", base_sha="2" * 40), None, SimpleNamespace(head_sha="3" * 40),
                     False)


# =====================================================================================================================
# 10. The third review round
# =====================================================================================================================

def native_run_in(run_dir: Path, config: ProjectConfig, repo: Path, actions: list[dict[str, Any]]) -> RunResult:
    run_dir.mkdir(mode=0o700)
    runner = NativeCliRunner(config.provider_for("backend"), config, hardening_check=lambda *a, **k: "",  # type: ignore[arg-type]
                             enforcement_check=lambda *a, **k: "")
    return runner.run(RunRequest("f.backend", "backend", actions_prompt(actions),
                                 ScopedWorkspace(PathPolicy(repo, config), "backend"),
                                 context=launchers.RunContext(run_dir=run_dir)))


@HARDENING
@pytest.mark.parametrize("sabotage", ["read-only", "no output", "both"])
@pytest.mark.parametrize("control", [False, True], ids=["role violation", "and a control change"])
def test_an_agent_cannot_escape_judgement_by_breaking_its_run_directory(
        config: ProjectConfig, agents: Path, repo: Path, tmp_path: Path, fake_home: Path, hardened: bool,
        sabotage: str, control: bool) -> None:
    del fake_home
    if os.geteuid() == 0:
        pytest.skip("root writes everywhere")
    run_dir = tmp_path / "run"
    actions = [WORK, {"do": "write", "path": "cli/src/evil2.py", "text": "x = 1\n"}]
    if control:
        actions.append({"do": "append", "path": ".git/config", "text": "[user]\n\tname = x\n"})
    if sabotage in ("no output", "both"):
        actions.append({"do": "exec", "argv": ["/bin/rm", "-f", str(run_dir / "stdout.jsonl")]})
    if sabotage in ("read-only", "both"):
        actions.append({"do": "exec", "argv": ["/bin/chmod", "500", str(run_dir)]})
    try:
        result = native_run_in(run_dir, native_config(config, agents, hardened), repo, actions)
    finally:
        run_dir.chmod(0o700)
    assert result.status == "rejected", result.error
    residue = result.details["residue"]
    assert (residue[0] == git_control.RESIDUE) is control
    if sabotage == "read-only":
        assert "cli/src/evil2.py" in residue and any("evidence could not be written" in item for item in residue)
    else:
        assert "cli/src/evil2.py" in residue or git_control.NOT_JUDGED in residue


@HARDENING
@pytest.mark.parametrize("error", [ValueError("bad"), RuntimeError("bad"), PermissionError(13, "denied"),
                                   FileNotFoundError(2, "gone"), OSError(28, "no space left")],
                         ids=lambda e: f"{type(e).__name__}{getattr(e, 'errno', None) or ''}")
def test_an_error_an_agent_could_cause_never_returns_the_run_unjudged(
        config: ProjectConfig, agents: Path, repo: Path, tmp_path: Path, fake_home: Path, hardened: bool,
        error: Exception, monkeypatch: pytest.MonkeyPatch) -> None:
    del fake_home
    failing_state(monkeypatch, error)
    result = native_run(native_config(config, agents, hardened), repo, tmp_path, [WORK])
    assert result.status == "rejected" and "could not be judged" in result.error
    assert git_control.NOT_JUDGED in result.details["residue"]


@pytest.mark.parametrize("error", [KeyboardInterrupt(), SystemExit(1), MemoryError(), OSError(12, "no memory")],
                         ids=["ctrl-c", "exit", "memory", "ENOMEM"])
def test_what_says_nothing_about_the_run_is_raised(config: ProjectConfig, agents: Path, repo: Path, tmp_path: Path,
                                                   fake_home: Path, error: BaseException,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    del fake_home
    failing_state(monkeypatch, error)
    with pytest.raises(type(error)):
        native_run(native_config(config, agents, False), repo, tmp_path, [WORK])


def test_a_branch_description_is_not_refused(repo: Path) -> None:
    git(repo, "config", "branch.dev.description", "What this is for.\n\nIt = a description, over several lines.\n")
    git(repo, "config", "alias.x", "!f() {\n  git status\n}; f")
    assert execution_findings(repo) == []
    assert run_git(repo, "status").returncode == 0


@pytest.mark.parametrize("line", ["lfs.extension.foo.clean=x", "  LFS.Extension.foo.Clean = x", "core.sshcommand=x"])
def test_a_line_of_a_description_that_is_a_setting_is_refused(repo: Path, line: str) -> None:
    git(repo, "config", "branch.dev.description", f"Text.\n{line}\nMore text.")
    assert execution_findings(repo)


@HARDENING
def test_a_file_name_that_is_not_utf8_is_judged(config: ProjectConfig, agents: Path, repo: Path, tmp_path: Path,
                                                fake_home: Path, hardened: bool) -> None:
    del fake_home
    name = os.fsdecode(b"cli/src/\xff.py")
    result = native_run(native_config(config, agents, hardened), repo, tmp_path, [
        WORK, {"do": "exec", "argv": ["/bin/mkdir", "-p", "cli/src"]}, {"do": "write", "path": name, "text": "x\n"}])
    assert result.status == "rejected" and name in result.details["residue"]
    json.dumps(result.details)


def test_the_service_settles_a_launch_that_left_an_undecodable_name(tmp_path: Path, state_dir: Path, agents: Path,
                                                                    ran: Path, fake_home: Path) -> None:
    del fake_home
    rig = workflow_rig(tmp_path, state_dir, agents, ran, actions=[
        WORK, {"do": "write", "path": os.fsdecode(b"backend/src/\xff.py"), "text": "x = 1\n"}])
    rig.service(max_cycles=4).run()
    assert rig.sched("w4.one").in_flight is None and not rig.events("worker-cycle-failed")


@pytest.mark.parametrize("name", ["objects", "refs"])
def test_an_unreadable_repository_is_never_mistaken_for_an_outer_one(tmp_path: Path, name: str) -> None:
    if os.geteuid() == 0:
        pytest.skip("root reads everything")
    outer, inner = tmp_path / "outer", tmp_path / "outer/inner"
    init_demo_repo(outer, {"a.txt": "a\n"})
    init_demo_repo(inner, SELF_TEST_FILES)
    before = git_control.capture(inner)
    (inner / ".git" / name).chmod(0)
    try:
        verdict = git_control.verify(inner, before)
        assert verdict.problems and not verdict.git_safe
    finally:
        (inner / ".git" / name).chmod(0o755)


@pytest.mark.parametrize("worker", [{}, {"pid": "x"}, "x", {"pid": 1}], ids=repr)
def test_an_unreadable_identity_counts_as_alive(gc: GitCoordinator, state_dir: Path, worker: Any) -> None:
    """Fail closed: nothing is dropped on a record nobody can read, and the service's cycle goes on."""
    store = StateStore(state_dir)
    dead_execution(store, gc.root, worker=worker)
    wh.hold_problems(store, gc.root)
    assert ("dead" in wh.executions_of(store, gc.root)) is bool(worker)


# =====================================================================================================================
# 11. The fourth review round: ordinary shortages and races are no verdict
# =====================================================================================================================

def emfile(*_args: Any, **_kwargs: Any) -> Any:
    raise OSError(24, "Too many open files")


def test_a_momentary_shortage_at_the_listing_is_retried(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real, failed = subprocess.run, []

    def once(*args: Any, **kwargs: Any) -> Any:
        if not failed:
            failed.append(True)
            emfile()
        return real(*args, **kwargs)  # pylint: disable=subprocess-run-check
    monkeypatch.setattr(subprocess, "run", once)
    assert execution_findings(repo) == [] and failed


def test_a_lasting_shortage_is_not_called_a_bad_configuration(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    before = git_control.capture(repo)
    monkeypatch.setattr(subprocess, "run", emfile)
    for call in (lambda: execution_findings(repo), lambda: git_control.capture(repo),
                 lambda: git_control.verify(repo, before), lambda: run_git(repo, "status")):
        with pytest.raises(OSError) as caught:
            call()
        assert caught.value.errno == 24
    monkeypatch.undo()
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError(2, "no git")))
    with pytest.raises(GitConfigExecutionError):        # not a shortage: fail closed
        execution_findings(repo)


def test_a_shortage_while_the_control_files_are_read_is_no_change(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    before = git_control.capture(repo)
    monkeypatch.setattr(git_control.os, "open", emfile)
    with pytest.raises(OSError):
        git_control.verify(repo, before)


@pytest.mark.parametrize("where", ["verify", "judgement", "admission"])
def test_a_shortage_around_a_run_neither_rejects_nor_holds(tmp_path: Path, state_dir: Path, agents: Path, ran: Path,
                                                           fake_home: Path, where: str,
                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    del fake_home
    rig = workflow_rig(tmp_path, state_dir, agents, ran, actions=[WORK])
    with monkeypatch.context() as patch:
        if where == "verify":
            patch.setattr(nb, "verify_git_control", emfile)
        elif where == "judgement":
            real = nb.NativeCliRunner._judge
            patch.setattr(nb.NativeCliRunner, "_judge",
                          lambda self, *a: (patch.setattr(gitsafe, "_list_config", emfile), real(self, *a))[1])
        else:
            patch.setattr(wh, "unexplained", emfile)
        with pytest.raises(OSError):
            rig.coord.run_task("w4.one")
    assert rig.task("w4.one").state.value == "ready" and hold_of(rig.coord.store, rig.coord.root) is None
    assert rig.coord.run_task("w4.one").runner.status == "completed"


def test_a_symlink_that_vanishes_is_absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "link").symlink_to("x")
    monkeypatch.setattr(launchers.os, "readlink", lambda _p: (_ for _ in ()).throw(FileNotFoundError(2, "gone")))
    assert launchers.snapshot(tmp_path, ["link"]) == {"link": None}


def test_an_event_that_is_just_being_written_is_not_read(state_dir: Path) -> None:
    store = StateStore(state_dir)
    with store.transaction():
        store.append_event("one", value=1)
    with (state_dir / "events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write('{"kind": "tw')
    assert [e["kind"] for e in store.events()] == ["one"]
    with (state_dir / "events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write('o"}\n')
    assert [e["kind"] for e in store.events()] == ["one", "two"]


@HARDENING
def test_a_name_that_is_not_utf8_is_never_committed(config: ProjectConfig, agents: Path, repo: Path, tmp_path: Path,
                                                    fake_home: Path, hardened: bool) -> None:
    """It could not be shown in a pull request: rejected like any other path the role may not write."""
    del fake_home
    name = os.fsdecode(b"backend/src/\xff.py")
    result = native_run(native_config(config, agents, hardened), repo, tmp_path,
                        [WORK, {"do": "write", "path": name, "text": "x = 1\n"}])
    assert result.status == "rejected" and name in result.details["residue"] and name not in result.changed_paths


@pytest.mark.usefixtures("needs_bwrap", "fake_home")
def test_a_name_that_is_not_utf8_is_never_committed_by_bwrap(config: ProjectConfig, agents: Path, repo: Path,
                                                             tmp_path: Path) -> None:
    name = os.fsdecode(b"backend/src/\xff.py")
    result = bwrap_run(config_with_agents(config, agents), repo, tmp_path,
                       [WORK, {"do": "write", "path": name, "text": "x = 1\n"}])
    assert result.status == "rejected" and name in result.details["residue"] and name not in result.changed_paths


def test_a_hold_with_such_a_name_can_be_shown_and_resolved(tmp_path: Path, state_dir: Path, agents: Path, ran: Path,
                                                           fake_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    del fake_home
    rig = workflow_rig(tmp_path, state_dir, agents, ran, actions=[
        WORK, {"do": "write", "path": os.fsdecode(b"backend/src/\xff.py"), "text": "x = 1\n"}])
    rig.coord.run_task("w4.one")
    hold = hold_of(rig.coord.store, rig.coord.root)
    assert hold is not None
    wh.describe(hold).encode("utf-8")
    strict = io.TextIOWrapper(io.BytesIO(), encoding="utf-8", errors="strict")
    monkeypatch.setattr(sys, "stdout", strict)
    cmd_workspace_status(argparse.Namespace(state_dir=str(state_dir), repo=str(rig.coord.root), json=False), rig.config)
    cmd_workspace_resolve(argparse.Namespace(state_dir=str(state_dir), repo=str(rig.coord.root), apply=False,
                                             accept=False, reason="", hold=""), rig.config)
    monkeypatch.undo()
    (rig.coord.root / os.fsdecode(b"backend/src/\xff.py")).unlink()
    assert resolve(rig.coord.store, rig.coord.root, rig.config, apply=True, accept=True, reason="removed").applied


def test_a_stash_message_that_is_not_utf8_is_compared(repo: Path) -> None:
    edit(repo)
    subprocess.run(["git", "-C", os.fsencode(repo), *USER, "stash", "push", "-q", "-m", b"\xff"], check=True)
    assert nb.git_state(repo).stash


def test_gitops_reads_a_name_that_is_not_utf8(gc: GitCoordinator) -> None:
    name = os.fsdecode(b"backend/\xff.py")
    (gc.root / name).write_text("x = 1\n", encoding="utf-8")
    assert name in gc.untracked_files()


def test_evidence_is_never_written_through_a_symlink(config: ProjectConfig, agents: Path, repo: Path, tmp_path: Path,
                                                     fake_home: Path) -> None:
    del fake_home
    if os.geteuid() == 0:
        pytest.skip("root writes everywhere")
    run_dir, outside = tmp_path / "run", tmp_path / "outside.txt"
    outside.write_text("precious\n", encoding="utf-8")
    try:
        result = native_run_in(run_dir, native_config(config, agents, False), repo, [
            WORK, {"do": "write", "path": "cli/src/evil2.py", "text": "x = 1\n"},
            {"do": "symlink", "target": str(outside), "link": str(run_dir / "violations.json")},
            {"do": "exec", "argv": ["/bin/chmod", "500", str(run_dir)]}])
    finally:
        run_dir.chmod(0o700)
    assert outside.read_text(encoding="utf-8") == "precious\n"
    assert result.status == "rejected" and "cli/src/evil2.py" in result.details["residue"]
