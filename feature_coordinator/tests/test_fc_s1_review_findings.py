"""Security S1 — the independent review's findings F1–F7 and workspace contamination, in disposable repositories.

F1 a secret nested below an exempt directory (`backend/.env.example/.env`) was not masked;
F2 the bwrap backend neither prevented nor detected a NEW Git-ignored protected path (`backend/new/.env/key`);
F3 protected entries below dependency/build trees (`backend/dist/.env/key`, `backend/.venv/.env/key`,
   `backend/node_modules/x/.git/config`) escaped the sandbox plan;
F4 native-cli accepted a nested Git directory (`backend/new/.git/config`) that `git status` never reports, and a
   second run took such residue as its clean baseline;
F5 the reviewer showed a protected file's content after a rename (`backend/src/.env/key.py` → `backend/src/public.py`);
F6 an excluded exact path used as a directory (`scripts/deploy.sh/`) was not read-only as a whole;
F7 the Worker's PathPolicy and the W4.7 integration inspection spelled `.git` aliases differently.

Real subprocesses where possible: the fake agent CLI as a host process (native-cli) and inside bubblewrap (bwrap,
skipped where the host cannot create namespaces — then OS enforcement is NOT verified by this run)."""

# pylint: disable=missing-function-docstring,redefined-outer-name,too-many-lines

from __future__ import annotations

import dataclasses
import json
import os
import subprocess
from pathlib import Path

import pytest

from feature_coordinator.config import ProjectConfig
from feature_coordinator.demo import init_demo_repo
from feature_coordinator.fsguard import (
    VANISHED,
    Change,
    Classifier,
    Entry,
    Inventory,
    InventoryError,
    compare,
    scan,
    violations,
)
from feature_coordinator.integration_paths import name_problem, path_class, symlink_problem
from feature_coordinator.launchers import ClaudeCodeLauncher, RealCliRunner
from feature_coordinator.model import TaskState
from feature_coordinator.native_backend import NativeCliRunner
from feature_coordinator.native_hardening import NativeHardening
from feature_coordinator.operations import SELF_TEST_FILES
from feature_coordinator.paths import PathPolicy, is_git_dir_name, matching_prefix, protected_prefix, secret_prefix
from feature_coordinator import review_context
from feature_coordinator.review_context import ChangeListError, build_context, changed_entries, entry_withheld
from feature_coordinator.sandbox import SandboxBuilder
from feature_coordinator.worker import FeatureWorker
from feature_coordinator.workflow import TaskSpec
from feature_coordinator.workspace_hold import (
    WorkspaceHoldError,
    guard_after_run,
    guard_before_run,
    hold_of,
    hold_ref,
    resolve,
    trusted_of,
    unexplained,
)

import test_fc_autonomy as autonomy
import test_fc_protected_descendants as s1
from fc_helpers import actions_prompt, config_with_agents, fake_broker_factory, git, install_fake_agent

EROFS, EACCES = s1.EROFS, s1.EACCES
GITIGNORE = ".env\n.env.*\n!.env.example\ndist/\n.venv/\nnode_modules/\n"


def write(repo: Path, rel: str, text: str = "SECRET=value-that-must-not-leak\n") -> str:
    return s1.write(repo, rel, text)


def init(path: Path, extra: dict | None = None) -> Path:
    init_demo_repo(path, {**SELF_TEST_FILES, ".gitignore": GITIGNORE, **(extra or {})})
    return path


def as_directory(repo: Path, *rels: str) -> None:
    """Replace a tracked exact path (e.g. `scripts/deploy.sh`) by nothing, so a test can make it a directory."""
    for rel in rels:
        if (repo / rel).is_file():
            git(repo, "rm", "-q", rel)


# =====================================================================================================================
# The shared primitives: one classification rule, a bounded walk that never follows a symlink
# =====================================================================================================================

PATHS = ["backend/.env.example/.env", "backend/.env.example", "backend/.env.example/README.md", ".env",
         "backend/.env/.env.local/key", "backend/.env.sample/.env.template/.env.local/key", "backend/src/app.py",
         "backend/node_modules/x/.git/config", "backend/GIT~1/hooks/x", "scripts/deploy.sh/run.sh",
         "scripts/deploy.sh", "scripts/deploy_tools.sh", "content/foods/a.csv", "backend/.g‌it/config",
         "test_environment/testenv.toml/x", "frontend/.env.local/config.ts", "docs/.github/x"]


@pytest.mark.parametrize("rel", PATHS)
def test_the_incremental_classifier_equals_the_path_rules(config: ProjectConfig, rel: str) -> None:
    excluded = [p for role in config.roles.values() for p in role.excluded]
    runtime = config.runtime
    assert Classifier(config).of(rel) == (protected_prefix(config.protected, rel),
                                          secret_prefix(runtime.secret_patterns, runtime.secret_exempt_suffixes, rel),
                                          matching_prefix(excluded, rel))


def test_secret_prefix_judges_every_component_on_its_own(config: ProjectConfig) -> None:
    def secret(rel: str) -> str | None:
        return secret_prefix(config.runtime.secret_patterns, config.runtime.secret_exempt_suffixes, rel)
    assert secret("backend/.env.example/.env") == "backend/.env.example/.env"       # F1: the inner secret
    assert secret("backend/.env.example") is None and secret("backend/.env.example/README.md") is None
    assert secret("backend/.env/.env.local/key") == "backend/.env"                  # the outermost secret
    assert secret("backend/.env.sample/.env.template/.env.local/key") == "backend/.env.sample/.env.template/.env.local"
    assert secret("backend/src/environment.py") is None


def test_scan_reads_dependency_trees_and_never_follows_symlinks(tmp_path: Path, config: ProjectConfig) -> None:
    repo = init(tmp_path / "repo")
    outside = tmp_path / "outside"
    write(tmp_path, "outside/.env")
    write(repo, "backend/node_modules/pkg/deep/x/.git/config", "[core]\n")
    write(repo, "backend/.env")
    os.symlink(outside, repo / "backend" / "elsewhere")          # a directory link: never entered
    os.symlink("../.env", repo / "backend" / "src" / "cfg")      # a link INTO a secret
    inventory = scan(repo, config)
    assert "backend/node_modules/pkg/deep/x/.git" in inventory.entries   # below node_modules too
    assert "backend/node_modules/pkg/deep/x/.git/config" in inventory.entries
    assert not any(p.startswith("backend/elsewhere/") for p in inventory.entries)
    assert "backend/elsewhere" not in inventory.entries   # it points outside the checkout, at nothing sensitive
    link = inventory.entries["backend/src/cfg"]
    assert link.kind == "symlink" and "symlink:backend/.env" in link.reasons
    assert ".git" not in inventory.entries and not any(p.startswith(".git/") for p in inventory.entries)
    assert scan(repo, config) == inventory   # deterministic


def test_scan_is_bounded_and_fails_closed(tmp_path: Path, config: ProjectConfig) -> None:
    repo = init(tmp_path / "repo")
    with pytest.raises(InventoryError, match="more than 5 entries"):
        scan(repo, config, max_entries=5)
    write(repo, "backend/" + "/".join(f"d{i}" for i in range(12)) + "/f.txt", "x\n")
    with pytest.raises(InventoryError, match="deeper than 4 levels"):
        scan(repo, config, max_depth=4)
    ticks = iter(range(10**9))
    with pytest.raises(InventoryError, match="did not finish in time"):
        scan(repo, config, max_seconds=0.5, clock=lambda: next(ticks) * 1000.0)


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads every directory")
def test_an_unreadable_directory_fails_the_scan_closed(tmp_path: Path, config: ProjectConfig) -> None:
    repo = init(tmp_path / "repo")
    locked = repo / "backend" / "locked"
    locked.mkdir()
    locked.chmod(0)
    try:
        with pytest.raises(InventoryError, match="backend/locked"):
            scan(repo, config)
    finally:
        locked.chmod(0o755)


def test_compare_counts_vanished_entries_and_respects_roots() -> None:
    reasons = ("protected:backend/.env",)
    before = Inventory({"backend/.env": Entry("file", "file:1", reasons), "cli/.env": Entry("file", "f", reasons)})
    after = Inventory({"backend/.env": Entry(VANISHED, VANISHED, reasons), "cli/.env": Entry("file", "f2", reasons)})
    assert [c.path for c in compare(before, after)] == ["backend/.env", "cli/.env"]
    assert [c.path for c in compare(before, after, within=["backend"])] == ["backend/.env"]
    same = Inventory({"backend/.env": Entry(VANISHED, VANISHED, reasons)})
    assert compare(same, same) == [Change("backend/.env", VANISHED, VANISHED, reasons)]  # never "unchanged"


# =====================================================================================================================
# F1 — nested secrets below exempt directories
# =====================================================================================================================

@pytest.fixture
def f1_repo(tmp_path: Path) -> Path:
    repo = init(tmp_path / "f1", {"frontend/.env.example": "EXAMPLE=1\n"})
    write(repo, "backend/.env.example/.env", "SECRET=inner-secret-f1\n")
    write(repo, "backend/.env.example/notes.md", "public notes\n")
    write(repo, "backend/.env.sample/.env.template/.env.local/key", "SECRET=deep-f1\n")
    write(repo, "backend/.env", "SECRET=backend-env\n")
    os.symlink("../.env", repo / "backend" / ".env.example" / "link")
    return repo


def test_f1_nested_secrets_are_masked_and_exempt_names_stay_readable(f1_repo: Path, config: ProjectConfig,
                                                                     tmp_path: Path) -> None:
    builder = SandboxBuilder(f1_repo, config)
    secrets = builder.secret_files()
    assert {"backend/.env.example/.env", "backend/.env.sample/.env.template/.env.local/key", "backend/.env"} <= \
        set(secrets)
    assert "backend/.env.example/notes.md" not in secrets and "frontend/.env.example" not in secrets
    assert "backend/.env.example/link" not in secrets   # the link itself: its TARGET (backend/.env) is masked
    plan = builder.plan(run_dir=tmp_path / "plan", role="backend")
    assert {"backend/.env.example/.env", "backend/.env.sample/.env.template/.env.local/key"} <= set(plan.masked)
    assert "backend/.env.example" in plan.readonly_overlays   # protected (`**/.env.*`): read-only, still readable
    plan.discard_shadow()
    assert "symlink:backend/.env" in builder.inventory().entries["backend/.env.example/link"].reasons


def test_f1_hardened_native_agents_are_denied_the_nested_secrets(f1_repo: Path, config: ProjectConfig) -> None:
    hardened = dataclasses.replace(config, runtime=dataclasses.replace(
        config.runtime, native_hardening=NativeHardening(enabled=True)))
    launcher = ClaudeCodeLauncher(hardened.providers["claude"], hardened, "claude", native=True)  # type: ignore[arg-type]
    denied = launcher.secrets(f1_repo)
    assert {"backend/.env.example/.env", "backend/.env.sample/.env.template/.env.local/key"} <= set(denied)
    assert "backend/.env.example/notes.md" not in denied


@pytest.mark.usefixtures("needs_bwrap")
def test_f1_inside_bwrap_the_nested_secret_is_never_readable(f1_repo: Path, config: ProjectConfig,
                                                             tmp_path: Path) -> None:
    out = s1.probe_in(SandboxBuilder(f1_repo, config).plan(run_dir=tmp_path / "probe", role="backend"), {
        "append": [], "create": [], "rename": [],
        "read": ["backend/.env.example/.env", "backend/.env.example/link", "backend/.env.example/notes.md",
                 "backend/.env.sample/.env.template/.env.local/key", "frontend/.env.example"]}, tmp_path)
    for rel in ("backend/.env.example/.env", "backend/.env.example/link",
                "backend/.env.sample/.env.template/.env.local/key"):
        assert out[f"read {rel}"] in ("ok:", EACCES), (rel, out[f"read {rel}"])   # masked: never the content
    assert out["read backend/.env.example/notes.md"] == "ok:public notes\n"       # legitimate access preserved
    assert out["read frontend/.env.example"] == "ok:EXAMPLE=1\n"


# =====================================================================================================================
# F3 — protected entries below pruned dependency and build trees
# =====================================================================================================================

PRUNED = ("backend/dist/.env/key", "backend/.venv/.env/key", "backend/node_modules/x/.git/config")


@pytest.fixture
def f3_repo(tmp_path: Path) -> Path:
    repo = init(tmp_path / "f3")
    for rel in PRUNED:
        write(repo, rel, "[core]\n" if rel.endswith("config") else "SECRET=pruned-f3\n")
    write(repo, "backend/node_modules/x/index.js", "module.exports = 1\n")
    return repo


def test_f3_the_plan_protects_entries_below_pruned_trees(f3_repo: Path, config: ProjectConfig,
                                                         tmp_path: Path) -> None:
    for rel in PRUNED:
        assert git(f3_repo, "check-ignore", rel).strip() == rel
    builder = SandboxBuilder(f3_repo, config)
    plan = builder.plan(run_dir=tmp_path / "plan", role="backend")
    assert {"backend/dist/.env", "backend/.venv/.env", "backend/node_modules/x/.git"} <= set(plan.readonly_overlays)
    assert {"backend/dist/.env/key", "backend/.venv/.env/key"} <= set(plan.masked)
    assert "backend/node_modules/x" not in plan.readonly_overlays   # the dependency tree itself stays writable
    order = list(plan.readonly_overlays)
    assert order == sorted(order)   # parents are mounted before their children
    plan.discard_shadow()


@pytest.mark.usefixtures("needs_bwrap")
def test_f3_real_mounts_keep_pruned_protected_entries_read_only(f3_repo: Path, config: ProjectConfig,
                                                                tmp_path: Path) -> None:
    out = s1.probe_in(SandboxBuilder(f3_repo, config).plan(run_dir=tmp_path / "probe", role="backend"), {
        "append": ["backend/node_modules/x/.git/config", "backend/dist/.env/key", "backend/node_modules/x/index.js"],
        "create": ["backend/.venv/.env/new", "backend/node_modules/x/.git/hooks/post-checkout", "backend/dist/ok.js"],
        "rename": [], "read": ["backend/.venv/.env/key", "backend/dist/.env/key"]}, tmp_path)
    assert out["append backend/node_modules/x/.git/config"] == EROFS
    assert out["append backend/dist/.env/key"] in (EROFS, EACCES)
    for rel in ("backend/.venv/.env/new", "backend/node_modules/x/.git/hooks/post-checkout"):
        assert out[f"create {rel}"] == EROFS, (rel, out[f"create {rel}"])
    for rel in ("backend/.venv/.env/key", "backend/dist/.env/key"):
        assert out[f"read {rel}"] in ("ok:", EACCES)
    assert out["append backend/node_modules/x/index.js"] == "ok:" and out["create backend/dist/ok.js"] == "ok:"
    assert (f3_repo / "backend/node_modules/x/.git/config").read_text(encoding="utf-8") == "[core]\n"


# =====================================================================================================================
# F6 — an excluded exact path used as a directory
# =====================================================================================================================

def test_f6_excluded_exact_paths_used_as_directories_are_read_only_whole(tmp_path: Path, config: ProjectConfig) -> None:
    repo = init(tmp_path / "f6")
    as_directory(repo, "scripts/deploy.sh", "test_environment/testenv.toml")
    write(repo, "scripts/deploy.sh/run.sh", "echo deploy\n")
    write(repo, "test_environment/testenv.toml/x.toml", "a = 1\n")
    write(repo, "scripts/deploy_tools.sh", "echo tools\n")
    builder = SandboxBuilder(repo, config)
    overlays = builder.readonly_overlays("automation", builder.writable_roots("automation"))
    assert {"scripts/deploy.sh", "test_environment/testenv.toml"} <= set(overlays)
    assert "scripts/deploy_tools.sh" not in overlays and "scripts/deploy.sh/run.sh" not in overlays
    # post-run: an ignored file created inside it is an excluded entry the role may not write
    policy = PathPolicy(repo, config)
    before = scan(repo, config)
    write(repo, "scripts/deploy.sh/.venv/x", "x\n")
    assert git(repo, "check-ignore", "scripts/deploy.sh/.venv/x").strip()   # Git would never report it
    found = violations(compare(before, scan(repo, config)), policy, "automation")
    assert "scripts/deploy.sh/.venv/x" in [c.path for c in found]
    # a grant for the exact path is a grant for that file, never for what lies inside a directory of that name
    granted = violations(compare(before, scan(repo, config)), policy, "automation", ("scripts/deploy.sh",))
    assert "scripts/deploy.sh/.venv/x" in [c.path for c in granted]


@pytest.mark.usefixtures("needs_bwrap")
def test_f6_real_mounts_refuse_files_inside_an_excluded_directory(tmp_path: Path, config: ProjectConfig) -> None:
    repo = init(tmp_path / "f6")
    as_directory(repo, "scripts/deploy.sh")
    write(repo, "scripts/deploy.sh/run.sh", "echo deploy\n")
    out = s1.probe_in(SandboxBuilder(repo, config).plan(run_dir=tmp_path / "probe", role="automation"), {
        "append": ["scripts/deploy.sh/run.sh"], "create": ["scripts/deploy.sh/new.sh", "scripts/deploy_tools.sh"],
        "rename": [], "read": []}, tmp_path)
    assert out["append scripts/deploy.sh/run.sh"] == EROFS and out["create scripts/deploy.sh/new.sh"] == EROFS
    assert out["create scripts/deploy_tools.sh"] == "ok:"   # a sibling keeps its normal permission


# =====================================================================================================================
# F7 — one `.git` alias rule for the Worker and the integration inspection
# =====================================================================================================================

@pytest.mark.parametrize("name,alias", [
    (".git", True), (".GIT", True), (".git.", True), (".Git ", True), ("git~1", True), ("GIT~12", True),
    (".g‌it", True), ("﻿.git", True), (".git‍.", True),
    (".github", False), (".gitignore", False), ("git", False), (".gitx", False), ("xgit~1", False), ("git~", False)])
def test_f7_git_aliases(name: str, alias: bool) -> None:
    assert is_git_dir_name(name) is alias
    assert (name_problem(name.encode()) == "it names Git's own directory (.git, in some spelling)") is alias


@pytest.mark.parametrize("path", ["backend/GIT~1/config", "backend/.g‌it/hooks/pre-commit", "backend/sub/.git/x",
                                  "frontend/.Git./config"])
def test_f7_worker_and_integration_agree_on_nested_git_aliases(tmp_path: Path, config: ProjectConfig,
                                                               path: str) -> None:
    policy = PathPolicy(init(tmp_path / "f7"), config)
    assert policy.protected_by(path) is not None
    assert path_class(policy, config, path) == ("protected", None)
    for role in config.roles:
        assert not policy.check(role, path).allowed
    target = os.path.relpath(path, "backend/src")
    assert symlink_problem(config, "backend/src/link", target) is not None


def test_f7_the_classes_agree_for_every_probe_path(tmp_path: Path, config: ProjectConfig) -> None:
    policy = PathPolicy(init(tmp_path / "f7"), config)
    for path in PATHS:
        assert (path_class(policy, config, path)[0] == "protected") is (policy.protected_by(path) is not None), path


# =====================================================================================================================
# F2 — bwrap: new Git-ignored protected paths (real sandboxed runs of the fake agent)
# =====================================================================================================================

def bwrap_run(repo: Path, config: ProjectConfig, role: str, actions: list, tmp: Path):
    runner = RealCliRunner(config.providers[config.role(role).provider], config,  # type: ignore[arg-type]
                           broker_factory=fake_broker_factory())
    return runner.run(s1.request_for(repo, config, role, actions, tmp))


@pytest.mark.usefixtures("needs_bwrap", "fake_home")
@pytest.mark.parametrize("path", ["backend/new/.env/key", "backend/new/.git/config", "backend/dist/.env/key"])
def test_f2_bwrap_detects_new_ignored_protected_paths(tmp_path: Path, agent_config: ProjectConfig, path: str) -> None:
    repo = init(tmp_path / "repo")
    result = bwrap_run(repo, agent_config, "backend", [{"do": "write", "path": "backend/src/ok.py", "text": "x = 1\n"},
                                                        {"do": "write", "path": path, "text": "S=1\n"}], tmp_path)
    assert result.status == "rejected" and path in result.error, (result.status, result.error)
    assert path in result.details["residue"] and path not in result.changed_paths
    assert "backend/src/ok.py" in result.changed_paths   # the legitimate change is still the role's
    evidence = json.loads((Path(result.details["run_dir"]) / "violations.json").read_text(encoding="utf-8"))
    assert path in [c["path"] for c in evidence["sensitive"]] and evidence["backend"] == "bwrap"
    assert (repo / path).exists()   # left in place for the operator — never deleted automatically


@pytest.mark.usefixtures("needs_bwrap", "fake_home")
def test_f2_a_clean_bwrap_run_is_still_accepted(tmp_path: Path, agent_config: ProjectConfig) -> None:
    repo = init(tmp_path / "repo")
    write(repo, "backend/dist/bundle.js", "built\n")   # ignored, not sensitive: ordinary build output
    result = bwrap_run(repo, agent_config, "backend", [{"do": "write", "path": "backend/src/ok.py", "text": "x\n"},
                                                        {"do": "write", "path": "backend/dist/more.js", "text": "x\n"}],
                       tmp_path)
    assert result.status == "completed", result.error
    assert result.details["residue"] == [] and result.changed_paths == ["backend/src/ok.py"]


@pytest.mark.usefixtures("needs_bwrap", "fake_home")
def test_f2_an_incomplete_inventory_never_starts_the_agent(tmp_path: Path, agent_config: ProjectConfig,
                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    repo = init(tmp_path / "repo")

    def refuse(*_args, **_kwargs):
        raise InventoryError("the checkout has more than 1 entries")
    monkeypatch.setattr("feature_coordinator.sandbox.scan", refuse)   # the plan's own (fresh) inventory
    result = bwrap_run(repo, agent_config, "backend", [{"do": "write", "path": "backend/src/ok.py"}], tmp_path)
    assert result.status == "failed" and "was not started" in result.error
    assert not (repo / "backend/src/ok.py").exists()


# =====================================================================================================================
# F4 — native-cli: nested Git directories and other ignored protected descendants (real host processes)
# =====================================================================================================================

def native_run(repo: Path, config: ProjectConfig, role: str, actions: list, tmp: Path):
    runner = NativeCliRunner(config.provider_for(role), config)  # type: ignore[arg-type]
    return runner.run(s1.request_for(repo, config, role, actions, tmp))


@pytest.mark.parametrize("path", ["backend/new/.git/config", "backend/new/.env/key", "backend/node_modules/x/.git/HEAD",
                                  "frontend/GIT~1/config"])
def test_f4_native_detects_nested_git_and_ignored_protected_paths(tmp_path: Path, agent_config: ProjectConfig,
                                                                  fake_home: Path, path: str) -> None:
    del fake_home
    repo = init(tmp_path / "repo")
    role = path.split("/", 1)[0]
    result = native_run(repo, agent_config, role, [{"do": "write", "path": path, "text": "[core]\n"}], tmp_path)
    assert git(repo, "status", "--porcelain", "--untracked-files=all").strip() == "" or path.startswith("frontend")
    assert result.status == "rejected" and path in result.error, (result.status, result.error)
    assert path in result.details["residue"] and path not in result.changed_paths
    evidence = json.loads((Path(result.details["run_dir"]) / "violations.json").read_text(encoding="utf-8"))
    assert path in [c["path"] for c in evidence["sensitive"]] and evidence["backend"] == "native-cli"
    assert (repo / path).exists()


def test_f4_an_unsafe_symlink_created_by_the_agent_is_a_violation(tmp_path: Path, agent_config: ProjectConfig,
                                                                   fake_home: Path) -> None:
    del fake_home
    repo = init(tmp_path / "repo")
    write(repo, ".env", "SECRET=root\n")
    write(repo, "backend/dist/keep.js", "x\n")
    result = native_run(repo, agent_config, "backend",
                        [{"do": "symlink", "link": "backend/dist/env", "target": "../../.env"}], tmp_path)
    assert result.status == "rejected" and "backend/dist/env" in result.details["residue"], result.error


# =====================================================================================================================
# Workspace contamination across consecutive Worker runs (native-cli, real fake-agent processes)
# =====================================================================================================================

@pytest.fixture
def native_rig(tmp_path: Path, state_dir: Path, fake_home: Path):
    del fake_home
    repo = init(tmp_path / "rig")
    agent = install_fake_agent(tmp_path / "agents")
    cfg = config_with_agents(autonomy.autonomous(), agent)
    rig = autonomy.Rig(repo, state_dir, config=cfg)
    for role in ("backend", "cli"):
        rig.coord.runners[role] = NativeCliRunner(cfg.provider_for(role), cfg)  # type: ignore[arg-type]
    return rig


def tasks_for(rig, first: list, second: list):
    rig.activate(TaskSpec("one", "backend", actions_prompt(first), required_gates=("backend-tests",)),
                 TaskSpec("two", "cli", actions_prompt(second), required_gates=("cli-lint",)))


def test_a_rejected_run_holds_the_checkout_until_an_operator_resolves_it(native_rig) -> None:
    rig, repo = native_rig, native_rig.coord.root
    marker = "cli/src/second_run_started.txt"
    tasks_for(rig, [{"do": "write", "path": "backend/new/.git/config", "text": "[core]\n"}],
              [{"do": "write", "path": marker, "text": "ran\n"}])
    first = rig.coord.run_task("w4.one")
    assert first.runner.status == "rejected" and rig.task("w4.one").state is TaskState.FAILED
    hold = hold_of(rig.coord.store, repo)
    assert hold is not None and "backend/new/.git/config" in hold["entries"][0]["paths"]
    assert any(e["kind"] == "workspace-hold" for e in rig.events())
    # the second, unrelated task never starts: no claim, no agent, still ready
    with pytest.raises(WorkspaceHoldError, match="on hold"):
        rig.coord.run_task("w4.two")
    assert rig.task("w4.two").state is TaskState.READY and not (repo / marker).exists()
    assert rig.coord.store.list_claims() == {}
    # resolving without remediation is refused; nothing is ever removed automatically
    attempt = resolve(rig.coord.store, repo, rig.config, apply=True)
    assert not attempt.applied and "backend/new/.git/config" in attempt.remaining
    assert (repo / "backend/new/.git/config").exists() and hold_of(rig.coord.store, repo) is not None
    # … and what a run created in a protected location can never be accepted, whatever the reason (round 3, F8)
    refused = resolve(rig.coord.store, repo, rig.config, apply=True, accept=True, reason="keep it")
    assert not refused.applied and "backend/new/.git/config" in refused.forbidden
    # the operator removes the residue; the hold lifts; the next task runs normally
    (repo / "backend/new/.git/config").unlink()
    (repo / "backend/new/.git").rmdir()
    done = resolve(rig.coord.store, repo, rig.config, apply=True)
    assert done.applied and hold_of(rig.coord.store, repo) is None
    second = rig.coord.run_task("w4.two")
    assert rig.task("w4.two").state is TaskState.VERIFIED, second.runner.error
    assert (repo / marker).exists()


def test_residue_of_an_interrupted_run_is_never_the_next_runs_clean_baseline(native_rig) -> None:
    """No run judged it (e.g. the Worker died mid-run): the next run's pre-run inventory check catches it."""
    rig, repo = native_rig, native_rig.coord.root
    tasks_for(rig, [{"do": "write", "path": "backend/src/ok.py", "text": "x = 1\n"}],
              [{"do": "write", "path": "cli/src/ok.py", "text": "y = 1\n"}])
    assert rig.coord.run_task("w4.one").runner.status == "completed"
    assert trusted_of(rig.coord.store, repo) is not None   # recorded by the first check (trust on first use)
    write(repo, "backend/new/.git/config", "[core]\n")     # left behind outside any judged run
    with pytest.raises(WorkspaceHoldError, match="outside an accepted run"):
        rig.coord.run_task("w4.two")
    hold = hold_of(rig.coord.store, repo)
    assert hold is not None and "backend/new/.git/config" in hold["entries"][0]["paths"]
    assert rig.task("w4.two").state is TaskState.READY
    # the operator may explicitly accept it instead (recorded with the reason); then it is trusted
    accepted = resolve(rig.coord.store, repo, rig.config, apply=True, accept=True, reason="vendored test repo")
    assert accepted.applied and hold_of(rig.coord.store, repo) is None
    event = [e for e in rig.events() if e["kind"] == "workspace-hold-resolved"][-1]
    assert event["reason"] == "vendored test repo" and "backend/new/.git/config" in event["accepted"]
    assert rig.coord.run_task("w4.two").runner.status == "completed"


def test_the_service_waits_needs_attention_without_counting_a_launch(native_rig) -> None:
    rig, repo = native_rig, native_rig.coord.root
    tasks_for(rig, [{"do": "write", "path": "backend/src/ok.py", "text": "x = 1\n"}],
              [{"do": "write", "path": "cli/src/ok.py", "text": "y = 1\n"}])
    guard_before_run(rig.coord.store, repo, rig.config)   # trust on first use
    write(repo, "backend/stray/.env", "SECRET=1\n")
    rig.service(until=autonomy.state_is("NEEDS_ATTENTION"), max_cycles=10).run()
    state, reason = autonomy.last_state(rig)
    assert state == "NEEDS_ATTENTION" and "on hold" in reason, reason
    assert rig.sched("w4.one").launches == 0 and rig.sched("w4.two").launches == 0
    assert rig.task("w4.one").state is TaskState.READY


def test_the_feature_worker_starts_nothing_while_held(native_rig) -> None:
    rig, repo = native_rig, native_rig.coord.root
    tasks_for(rig, [{"do": "write", "path": "backend/src/ok.py", "text": "x = 1\n"}],
              [{"do": "write", "path": "cli/src/ok.py", "text": "y = 1\n"}])
    guard_before_run(rig.coord.store, repo, rig.config)
    write(repo, "cli/vendor/.git/HEAD", "ref: refs/heads/main\n")
    with pytest.raises(WorkspaceHoldError):
        guard_before_run(rig.coord.store, repo, rig.config)
    report = FeatureWorker(rig.coord, rig.spawn, max_parallel=2, echo=lambda _line: None).run(
        rig.coord.store.list_features()[0].id)
    assert not report.launches and "on hold" in report.held


# =====================================================================================================================
# The trusted inventory
# =====================================================================================================================

def test_git_explains_tracked_clean_protected_content_only(tmp_path: Path, config: ProjectConfig) -> None:
    repo = init(tmp_path / "repo", {"content/foods/a.csv": "id\n1\n", "frontend/.env.local/config.ts": "tracked\n"})
    write(repo, ".env", "SECRET=1\n")
    found = unexplained(repo, scan(repo, config))
    assert ".env" in found
    assert "content/foods/a.csv" not in found and "content" not in found and "content/foods" not in found
    write(repo, "content/foods/a.csv", "id\n2\n")   # modified: no longer what the history says
    assert "content/foods/a.csv" in unexplained(repo, scan(repo, config))


def test_after_a_clean_run_only_allowed_sensitive_changes_become_trusted(tmp_path: Path, state_dir: Path,
                                                                         config: ProjectConfig) -> None:
    from feature_coordinator.runners import RunResult  # pylint: disable=import-outside-toplevel
    from feature_coordinator.store import StateStore  # pylint: disable=import-outside-toplevel
    repo = init(tmp_path / "repo")
    store = StateStore(state_dir)
    guard_before_run(store, repo, config)
    toml = "test_environment/testenv.toml"
    write(repo, toml, "changed = true\n")      # excluded for automation, allowed with its approval grant
    write(repo, "backend/oops/.env", "SECRET=1\n")   # protected: never allowed
    clean = RunResult("fake", "completed", details={"residue": []})
    assert guard_after_run(store, repo, config, role="automation", grants=(toml,), result=clean,
                           task_id="t") is None
    trusted = trusted_of(store, repo)["entries"]
    assert toml in trusted and "backend/oops/.env" not in trusted
    with pytest.raises(WorkspaceHoldError, match="backend/oops/.env"):
        guard_before_run(store, repo, config)


# =====================================================================================================================
# F5 — the reviewer never shows protected content through a rename, a copy, a deletion or a repeated line
# =====================================================================================================================

SECRET_LINE = "API_TOKEN=s1-review-secret-value-0123456789"


def history(tmp_path: Path, change) -> tuple[Path, str, str]:
    root = tmp_path / "r"
    subprocess.run(["git", "init", "-q", "--initial-branch=dev", str(root)], check=True)
    user = ("-c", "user.name=T", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false")
    write(root, "backend/src/.env/key.py", f"{SECRET_LINE}\nOTHER=1\n")
    write(root, "backend/src/app.py", "ROUTES = []\n")
    write(root, "old.txt", "one\ntwo\n")
    git(root, "add", "-A")
    git(root, *user, "commit", "-q", "-m", "base")
    base = git(root, "rev-parse", "HEAD").strip()
    change(root)
    git(root, "add", "-A")
    git(root, *user, "commit", "-q", "-m", "head")
    return root, base, git(root, "rev-parse", "HEAD").strip()


def context_for(config: ProjectConfig, root: Path, base: str, head: str):
    from feature_coordinator.intake import IntakeRecord  # pylint: disable=import-outside-toplevel
    from feature_coordinator.model import Feature  # pylint: disable=import-outside-toplevel
    feature = Feature(id="issue-9", slug="issue-9", title="X", base_branch="dev", branch="feature/issue-9",
                      base_sha=base)
    record = IntakeRecord(id="issue-9", issue_number=9, issue_id=909, node_id="I_9", url="mock://issues/9",
                          issue_author="someone", title="X", body="Do it.", digest="d" * 64, role="backend",
                          gates=["backend-tests"], task_id="issue-9.backend", approved_by="spyderbg",
                          approval_comment_id=1, approval_comment_url="mock://c/1", approved_at="2026-11-13T10:00:00Z")
    binding = {"repository": "spyderbg/mx-admin-cloud", "pr": 9, "head_branch": "feature/issue-9",
               "base_branch": "dev", "head_sha": head, "merge_base": base}
    return build_context(config, root, feature=feature, record=record, tasks=[], binding=binding)


def mv(src: str, dst: str):
    return lambda root: git(root, "mv", src, dst)


def copy_to(dst: str, extra: str = ""):
    return lambda root: write(root, dst, (root / "backend/src/.env/key.py").read_text(encoding="utf-8") + extra)


@pytest.mark.parametrize("change,shown_as", [
    (mv("backend/src/.env/key.py", "backend/src/public.py"), "backend/src/public.py"),                  # the repro
    (copy_to("backend/src/copy.py"), "backend/src/copy.py"),                                           # verbatim copy
    (lambda root: write(root, "backend/src/partial.py", f"import os\n{SECRET_LINE}\nprint(1)\n"),
     "backend/src/partial.py"),                                                                         # a line of it
    (lambda root: (root / "backend/src/.env/key.py").unlink(), "backend/src/.env/key.py"),             # deleted
    (lambda root: write(root, "backend/src/.env/key.py", f"{SECRET_LINE}\nOTHER=2\n"), "backend/src/.env/key.py"),
], ids=["renamed", "copied", "repeated-line", "deleted", "modified"])
def test_f5_protected_content_is_withheld_however_it_moves(tmp_path: Path, config: ProjectConfig, change,
                                                           shown_as: str) -> None:
    root, base, head = history(tmp_path, change)
    ctx = context_for(config, root, base, head)
    assert not ctx.problem, ctx.problem
    assert "s1-review-secret-value" not in ctx.prompt
    assert f"- {shown_as} (" in ctx.prompt and "content withheld" in ctx.prompt.split(f"- {shown_as} (", 1)[1][:40]
    assert ctx.files[shown_as].lines is None and any("withheld" in n for n in ctx.notes)


def test_f5_renames_keep_both_paths_and_ordinary_renames_stay_reviewable(tmp_path: Path,
                                                                         config: ProjectConfig) -> None:
    def change(root: Path) -> None:
        git(root, "mv", "backend/src/.env/key.py", "backend/src/public.py")
        git(root, "mv", "old.txt", "new.txt")
    root, base, head = history(tmp_path, change)
    entries = {e.path: e for e in changed_entries(root, base, head)}
    assert entries["backend/src/public.py"].source == "backend/src/.env/key.py"
    assert entries["backend/src/public.py"].status == "R" and entry_withheld(config, entries["backend/src/public.py"])
    assert entries["new.txt"].source == "old.txt" and not entry_withheld(config, entries["new.txt"])
    ctx = context_for(config, root, base, head)
    assert "rename from old.txt" in ctx.prompt and "s1-review-secret-value" not in ctx.prompt
    assert any("backend/src/public.py ← backend/src/.env/key.py" in note for note in ctx.notes), ctx.notes


@pytest.mark.parametrize("raw", [b"R100\0only-source\0", b"M\0", b"Q\0x\0", b"\0", b"M\0/etc/passwd\0",
                                 b"R100\0\0dst\0"])
def test_f5_malformed_change_lists_are_never_reviewed(tmp_path: Path, config: ProjectConfig,
                                                      monkeypatch: pytest.MonkeyPatch, raw: bytes) -> None:
    root, base, head = history(tmp_path, mv("old.txt", "new.txt"))
    real = getattr(review_context, "_git")

    def fake(repo: Path, *args: str) -> bytes:
        return raw if "--name-status" in args else real(repo, *args)
    monkeypatch.setattr("feature_coordinator.review_context._git", fake)
    with pytest.raises(ChangeListError):
        changed_entries(root, base, head)
    ctx = context_for(config, root, base, head)
    assert ctx.problem.startswith("the change list could not be read safely") and not ctx.prompt


# =====================================================================================================================
# Operator commands
# =====================================================================================================================

def test_the_workspace_commands_show_and_resolve_a_hold(tmp_path: Path, state_dir: Path, config: ProjectConfig,
                                                        capsys: pytest.CaptureFixture[str]) -> None:
    from feature_coordinator.cli import main  # pylint: disable=import-outside-toplevel
    from feature_coordinator.store import StateStore  # pylint: disable=import-outside-toplevel
    repo = init(tmp_path / "repo")
    store = StateStore(state_dir)
    guard_before_run(store, repo, config)
    write(repo, "backend/new/.git/config", "[core]\n")
    with pytest.raises(WorkspaceHoldError):
        guard_before_run(store, repo, config)
    base = ["--repo", str(repo), "--state-dir", str(state_dir)]
    assert main([*base, "workspace", "status"]) == 1
    out = capsys.readouterr().out
    assert "ON HOLD" in out and "untrusted: backend/new/.git/config" in out
    assert main([*base, "status"]) == 0 and "WORKSPACE HOLD" in capsys.readouterr().out
    ref = hold_ref(hold_of(store, repo))
    assert main([*base, "workspace", "resolve", "--apply"]) == 2          # --apply must name the hold
    assert "--apply needs --hold" in capsys.readouterr().out
    assert main([*base, "workspace", "resolve", "--apply", "--hold", ref]) == 1   # not remediated: refused
    assert "still untrusted (sensitive): backend/new/.git/config" in capsys.readouterr().out
    assert (repo / "backend/new/.git/config").exists() and hold_of(store, repo) is not None
    assert main([*base, "workspace", "resolve", "--apply", "--hold", ref, "--accept"]) == 1   # needs a reason
    assert "needs --reason" in capsys.readouterr().err
    assert main([*base, "workspace", "resolve", "--apply", "--hold", "ffffffff.9", "--accept", "--reason", "x"]) == 3
    assert "STALE" in capsys.readouterr().out and hold_of(store, repo) is not None   # another hold: nothing changed
    assert main([*base, "workspace", "resolve", "--apply", "--hold", ref, "--accept", "--reason", "fixture repo"]) == 0
    assert "ACCEPTED" in capsys.readouterr().out and hold_of(store, repo) is None
    assert main([*base, "workspace", "status", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["untrusted"] == []
