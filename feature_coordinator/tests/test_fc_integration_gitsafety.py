"""W4.7 (M2): adversarial tests for the remaining Codex finding on M1 — "concurrent modification of Git control files
can affect Git subprocess execution after preflight" (integration_isolation.py).

The attacker controls the checkout's `.git` (config and everything it includes, hooks, attributes, promisor settings)
and changes it AFTER the preflight check — once, or continuously while intake and inspection run. Every planted
program only creates a marker file; the tests assert that no marker ever appears, that the checkout's `.git` is not
written by the coordinator, and (positive control) that the same planted setting DOES run a program when Git is
invoked the way M1 did — so the attack is real on this host. Disposable repositories only."""

# pylint: disable=missing-function-docstring,redefined-outer-name,unused-argument

from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable

import pytest

from feature_coordinator import gitsafe, integration_intake
from feature_coordinator.errors import GitConfigExecutionError, RemoteUnavailableError
from feature_coordinator.integration import JobState
from feature_coordinator.integration_isolation import (
    IsolatedRepository,
    is_execution_setting,
    isolated_remote_head,
    strict_execution_findings,
)
from feature_coordinator.issues import MockIssueTracker

from fc_helpers import git
from fc_integration_rig import (  # pylint: disable=unused-import
    BRANCH, REPOSITORY, OperatorKey, Rig, change_on, github_origin, head, origin, request_for, snapshot,
)

HOOKS = ("applypatch-msg", "pre-applypatch", "post-applypatch", "pre-commit", "pre-merge-commit", "prepare-commit-msg",
         "commit-msg", "post-commit", "pre-rebase", "post-checkout", "post-merge", "pre-push", "pre-receive", "update",
         "proc-receive", "post-receive", "post-update", "reference-transaction", "push-to-checkout", "pre-auto-gc",
         "post-rewrite", "sendemail-validate", "fsmonitor-watchman", "p4-changelist", "p4-prepare-changelist",
         "p4-post-changelist", "p4-pre-submit", "post-index-change")


@pytest.fixture
def key(tmp_path: Path) -> OperatorKey:
    return OperatorKey(tmp_path / "operator-home")


@pytest.fixture
def markers(tmp_path: Path) -> Path:
    path = tmp_path / "markers"
    path.mkdir()
    return path


def program(markers: Path, name: str, *, passthrough: str = "") -> str:
    """A script that records that it ran (and optionally execs the real program so the operation still works)."""
    script = markers.parent / f"evil-{name}.sh"
    tail = f'exec {passthrough} "$@"\n' if passthrough else "exit 0\n"
    script.write_text(f"#!/bin/sh\ntouch '{markers}/{name}'\n{tail}", encoding="utf-8")
    script.chmod(0o755)
    return str(script)


def ran(markers: Path) -> list[str]:
    return sorted(p.name for p in markers.iterdir())


EXECUTION_KEYS = ("core.sshCommand", "core.gitProxy", "core.pager", "core.editor", "core.askPass", "core.fsmonitor",
                  "core.alternateRefsCommand", "sequence.editor", "diff.external", "gpg.program",
                  "gpg.ssh.program", "credential.helper", "credential.https://github.com.helper",
                  "filter.evil.smudge", "filter.evil.clean", "filter.evil.process", "filter.a.b.smudge",
                  "diff.evil.textconv", "diff.evil.command", "merge.evil.driver", "merge.a.b.driver",
                  "remote.origin.uploadpack", "remote.origin.receivepack", "uploadpack.packObjectsHook",
                  "trailer.evil.command", "difftool.evil.cmd", "mergetool.evil.cmd")


def plant(repo: Path, markers: Path) -> Callable[[], None]:
    """Every known execution setting, every hook, attributes that use the planted drivers, an include with more of
    the same. Returns the function that removes it all again."""
    git_dir = repo / ".git"
    config = git_dir / "config"
    original = config.read_bytes()
    hooks = markers.parent / "evil-hooks"
    hooks.mkdir(exist_ok=True)
    for name in HOOKS:
        (hooks / name).write_text(f"#!/bin/sh\ntouch '{markers}/hook-{name}'\n", encoding="utf-8")
        (hooks / name).chmod(0o755)
        planted = git_dir / "hooks" / name
        planted.write_text(f"#!/bin/sh\ntouch '{markers}/hook-{name}'\n", encoding="utf-8")
        planted.chmod(0o755)
    include = markers.parent / "evil-include.cfg"
    include.write_text(f"[core]\n\tsshCommand = {program(markers, 'include-ssh')}\n", encoding="utf-8")
    lines = []
    for index, name in enumerate(EXECUTION_KEYS):
        passthrough = "git-upload-pack" if name.endswith("uploadpack") else ""
        lines.append((name, program(markers, f"k{index}-{name.replace('/', '_')}", passthrough=passthrough)))
    with config.open("a", encoding="utf-8") as out:
        for name, value in lines:
            section, _sep, rest = name.partition(".")
            sub, _sep2, leaf = rest.rpartition(".")
            out.write(f'[{section} "{sub}"]\n\t{leaf} = {value}\n' if sub else f"[{section}]\n\t{leaf} = {value}\n")
        out.write(f"[core]\n\thooksPath = {hooks}\n[include]\n\tpath = {include}\n")
        out.write("[remote \"origin\"]\n\tpromisor = true\n[extensions]\n\tpartialClone = origin\n")
    attributes = git_dir / "info" / "attributes"
    attributes.parent.mkdir(exist_ok=True)
    attributes.write_text("* filter=evil diff=evil merge=evil\n", encoding="utf-8")

    def remove() -> None:
        config.write_bytes(original)
        attributes.unlink(missing_ok=True)
        for name in HOOKS:
            (git_dir / "hooks" / name).unlink(missing_ok=True)
    return remove


@pytest.fixture
def tracker() -> MockIssueTracker:
    return MockIssueTracker(repository=REPOSITORY)


@pytest.fixture
def rig(repo: Path, state_dir: Path, github_origin: Path, key: OperatorKey, tracker: MockIssueTracker) -> Rig:
    return Rig(repo, state_dir, key, client=tracker)


def attack_after_preflight(monkeypatch: pytest.MonkeyPatch, repo: Path, markers: Path, *,
                           clean_before_decision: bool) -> dict[str, int]:
    """The configuration scan passes, THEN everything is planted; optionally removed again right before the
    decision-time re-scan (an attacker who covers their tracks)."""
    real = integration_intake.assert_no_execution_config_strict
    # the planted promisor stays in place during every read: as if this Git could switch lazy fetching off, so M1's
    # partial-clone precheck lets the run continue (on Git < 2.44 it would refuse the checkout at once)
    monkeypatch.setattr(integration_intake, "lazy_fetch_switch_supported", lambda: True)
    state = {"scans": 0, "removed": 0}
    holder: dict[str, Callable[[], None]] = {}

    def scan(root: Any, **kwargs: Any) -> None:
        state["scans"] += 1
        if "remove" in holder and clean_before_decision:
            holder.pop("remove")()
            state["removed"] += 1
        real(root, **kwargs)
        if state["scans"] == 1:
            holder["remove"] = plant(repo, markers)

    monkeypatch.setattr(integration_intake, "assert_no_execution_config_strict", scan)
    return state


# -- the attack is real (positive control) and the strict scan sees what gitsafe misses ---------------------------

def test_positive_control_m1_style_invocation_runs_a_planted_uploadpack(repo: Path, origin: Path, markers: Path):
    """What M1 did after its single preflight check: `git -C <checkout> ls-remote origin`. gitsafe does not flag the
    setting, and Git runs it."""
    git(repo, "config", "remote.origin.uploadpack", program(markers, "uploadpack", passthrough="git-upload-pack"))
    assert gitsafe.execution_findings(repo) == []
    subprocess.run(gitsafe.git_argv(repo, "ls-remote", "origin", "refs/heads/dev"), capture_output=True,
                   env=gitsafe.hardened_env(), check=True)
    assert ran(markers) == ["uploadpack"]


def test_the_strict_scan_flags_what_gitsafe_misses(repo: Path, markers: Path):
    missed = {"credential.https://host.example.helper": "x", "filter.a.b.smudge": "x", "merge.a.b.driver": "x",
              "remote.origin.uploadpack": "x", "remote.origin.receivepack": "x", "remote.origin.vcs": "x",
              "difftool.a.b.cmd": "x", "submodule.lib.update": "!sh -c x"}
    for name, value in missed.items():
        git(repo, "config", name, value)
    assert gitsafe.execution_findings(repo) == []
    found = strict_execution_findings(repo)
    for name in missed:
        assert any(name.lower() in finding for finding in found), (name, found)


@pytest.mark.parametrize("name", sorted(gitsafe._EXACT_EXEC_KEYS | {  # pylint: disable=protected-access
    "filter.x.smudge", "filter.x.clean", "filter.x.process", "diff.x.command", "diff.x.textconv", "difftool.x.cmd",
    "mergetool.x.cmd", "merge.x.driver", "credential.helper", "credential.x.helper", "gpg.x.program",
    "trailer.x.command", "x.y.textconv", "x.y.hookspath", "core.fsmonitor"}))
def test_the_strict_scan_is_a_superset_of_gitsafe(name: str):
    assert gitsafe._is_execution(name, "/bin/evil")  # pylint: disable=protected-access
    assert is_execution_setting(name, "/bin/evil")
    for inert in ("", "false", "off"):
        assert is_execution_setting(name, inert) == gitsafe._is_execution(name, inert)  # pylint: disable=protected-access


def test_ordinary_settings_are_not_flagged():
    for name, value in (("remote.origin.url", "https://github.com/a/b"), ("filter.lfs.required", "true"),
                        ("core.fsmonitor", "true"), ("submodule.lib.update", "checkout"), ("merge.ff", "only"),
                        ("credential.helper", ""), ("user.email", "a@b.c"), ("remote.pushdefault", "origin")):
        assert not is_execution_setting(name, value), name


def test_the_preflight_queries_themselves_run_nothing_with_everything_planted(rig: Rig, repo: Path, markers: Path):
    """With the whole attack in place BEFORE the run, the checkout-side preflight (config, remote get-url,
    rev-parse and the configuration listing) runs and refuses — and nothing planted runs."""
    tip = change_on(repo, BRANCH, {"backend/src/x.py": "X = 1\n"})
    plant(repo, markers)
    with pytest.raises(GitConfigExecutionError):
        rig.intake(request_for(repo, tip))
    rig.git._checked = True  # pylint: disable=protected-access  # run the checkout queries past the scan
    for query in (("config", "--get-all", "remote.origin.url"), ("remote", "get-url", "--all", "origin"),
                  ("rev-parse", "--is-shallow-repository"), ("rev-parse", "--git-common-dir"),
                  ("rev-parse", "--show-object-format"), ("config", "--list", "-z")):
        rig.git._query(*query, checkout=True)  # pylint: disable=protected-access
    assert ran(markers) == []


# -- the race: planted after preflight --------------------------------------------------------------------------------

def test_settings_planted_after_preflight_never_run_during_intake_and_inspection(rig: Rig, repo: Path, markers: Path,
                                                                                 monkeypatch: pytest.MonkeyPatch):
    tip = change_on(repo, BRANCH, {"backend/src/x.py": "X = 1\n"})
    state = attack_after_preflight(monkeypatch, repo, markers, clean_before_decision=True)
    report = rig.intake(request_for(repo, tip))
    assert report.outcome == "accepted", (report.reasons, report.deferred)
    assert state["removed"] == 1 and ran(markers) == []
    job_id = report.job["job_id"]
    assert rig.approve(job_id)["outcome"] == "recorded"
    rig.git._checked = False  # pylint: disable=protected-access  # a fresh run: preflight again, then the attack
    state["scans"] = 0
    report = rig.inspect(job_id)
    assert report.outcome == "passed", (report.reasons, report.deferred)
    assert state["removed"] == 2 and ran(markers) == []


def test_settings_still_present_at_decision_time_refuse_the_run_and_record_nothing(rig: Rig, repo: Path,
                                                                                    markers: Path,
                                                                                    monkeypatch: pytest.MonkeyPatch):
    tip = change_on(repo, BRANCH, {"backend/src/x.py": "X = 1\n"})
    attack_after_preflight(monkeypatch, repo, markers, clean_before_decision=False)
    with pytest.raises(GitConfigExecutionError, match="changed during the run"):
        rig.intake(request_for(repo, tip))
    [job] = rig.jobs.list()
    assert job.state is JobState.INTAKE and ran(markers) == []  # written ahead, never decided; nothing ran


def test_continuous_flapping_of_the_checkout_configuration_never_runs_a_program(rig: Rig, repo: Path, markers: Path):
    tip = change_on(repo, BRANCH, {"backend/src/x.py": "X = 1\n"})
    config = repo / ".git" / "config"
    clean = config.read_bytes()
    remove = plant(repo, markers)
    evil = config.read_bytes()
    remove()
    stop = threading.Event()

    def flap() -> None:
        while not stop.is_set():
            for content in (evil, clean):
                tmp = config.with_suffix(".flap")
                tmp.write_bytes(content)
                os.replace(tmp, config)

    worker = threading.Thread(target=flap, daemon=True)
    worker.start()
    outcomes = []
    try:
        for _attempt in range(6):
            try:
                outcomes.append(rig.intake(request_for(repo, tip)).outcome)
            except (GitConfigExecutionError, integration_intake.IntegrationError) as exc:
                outcomes.append(type(exc).__name__)
    finally:
        stop.set()
        worker.join()
    config.write_bytes(clean)
    assert ran(markers) == [], outcomes
    assert set(outcomes) <= {"accepted", "existing", "deferred", "GitConfigExecutionError", "IntegrationError"}


# -- no Git write to the checkout, even from a promisor configuration --------------------------------------------

def test_a_promisor_configuration_cannot_make_git_write_the_checkout(rig: Rig, repo: Path, github_origin: Path,
                                                                    tmp_path: Path,
                                                                    monkeypatch: pytest.MonkeyPatch):
    """M1 showed that Git's promisor fetch writes `remote.origin.partialclonefilter` into .git/config before it fails
    (that is why M1 refuses partial clones without --no-lazy-fetch). In the private repository there is no promisor,
    so even with GIT_NO_LAZY_FETCH ignored, a missing object leaves the checkout byte-identical."""
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(github_origin), str(other)], check=True)
    change_on(other, BRANCH, {"remote_only.py": "R = 1\n"})
    tip = head(other, BRANCH)
    git(repo, "config", "remote.origin.promisor", "true")
    monkeypatch.setattr(integration_intake, "lazy_fetch_switch_supported", lambda: True)
    monkeypatch.setattr(integration_intake, "LOCAL_GIT_ENV", {
        k: v for k, v in integration_intake.LOCAL_GIT_ENV.items() if k != "GIT_NO_LAZY_FETCH"})
    before = snapshot(repo, github_origin)
    report = rig.intake(request_for(repo, tip))
    assert report.outcome == "deferred" and "not present in this checkout" in report.deferred
    assert snapshot(repo, github_origin) == before


# -- the private repository ------------------------------------------------------------------------------------

def test_object_and_remote_queries_run_only_in_a_private_repository_that_is_removed(rig: Rig, repo: Path,
                                                                                   monkeypatch: pytest.MonkeyPatch):
    tip = change_on(repo, BRANCH, {"backend/src/x.py": "X = 1\n"})
    seen: list[list[str]] = []
    private: list[set[str]] = []
    real_run, real_popen = subprocess.run, subprocess.Popen

    def note(argv: Any) -> None:
        if isinstance(argv, (list, tuple)) and argv and Path(str(argv[0])).name == "git":
            seen.append([str(a) for a in argv])
            for arg in argv:
                if str(arg).startswith("--git-dir="):
                    root = Path(str(arg).split("=", 1)[1])
                    private.append({str(p.relative_to(root)) for p in root.rglob("*")})

    def run(argv: Any, *args: Any, **kwargs: Any):
        note(argv)
        return real_run(argv, *args, **kwargs)  # pylint: disable=subprocess-run-check

    class Popen(real_popen):  # type: ignore[misc,valid-type]  # pylint: disable=too-few-public-methods
        """Records every git argv."""

        def __init__(self, argv: Any, *args: Any, **kwargs: Any) -> None:
            note(argv)
            super().__init__(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(subprocess, "Popen", Popen)
    assert rig.intake(request_for(repo, tip)).outcome == "accepted"
    monkeypatch.undo()
    from test_fc_integration_intake import git_subcommand  # pylint: disable=import-outside-toplevel
    for argv in seen:
        sub = git_subcommand(argv)
        isolated = any(a.startswith("--git-dir=") for a in argv)
        if sub in ("cat-file", "rev-list", "merge-base", "ls-remote"):
            assert isolated and argv[2] != str(repo), argv
        else:
            assert sub in ("config", "remote", "rev-parse") and not isolated, argv
    assert private and all(layout <= {"HEAD", "config", "refs", "objects", "objects/info",
                                      "objects/info/alternates"} for layout in private)
    assert not list((rig.state / "tmp").glob("integration-git-*"))  # removed when the run ended


def test_the_private_repository_and_remote_reader_refuse_unsafe_inputs(tmp_path: Path):
    with pytest.raises(GitConfigExecutionError):
        IsolatedRepository(Path("relative/objects"))
    with pytest.raises(GitConfigExecutionError):
        IsolatedRepository(Path(str(tmp_path) + "/a\nb"))
    objects = tmp_path / "objects"
    objects.mkdir()
    with IsolatedRepository(objects, scratch=tmp_path) as isolated:
        assert isolated.path is not None and isolated.path.parent == tmp_path
        assert (isolated.path / "objects/info/alternates").read_text(encoding="utf-8") == f"{objects}\n"
        assert oct(isolated.path.stat().st_mode & 0o777) == "0o700"
        for url in ("-uhack", "--upload-pack=touch x", "a\nb", ""):
            with pytest.raises(RemoteUnavailableError, match="refusing"):
                isolated_remote_head(isolated, url, "dev", timeout=5)
        with pytest.raises(RemoteUnavailableError, match="refusing"):
            isolated_remote_head(isolated, "https://github.com/a/b", "--all", timeout=5)
        with pytest.raises(RemoteUnavailableError, match="not allowed|failed"):
            isolated_remote_head(isolated, "ext::sh -c touch% /tmp/x", "dev", timeout=5)
        path = isolated.path
    assert not path.exists()


def test_gpg_key_commands_are_flagged_too():
    assert is_execution_setting("gpg.ssh.defaultKeyCommand", "/bin/evil")
    assert not is_execution_setting("gpg.ssh.allowedSignersFile", "/home/op/allowed")
