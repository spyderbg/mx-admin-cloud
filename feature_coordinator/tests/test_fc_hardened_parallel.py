"""W1: regression tests for hardened PARALLEL native execution (the bounded smoke test's failure).

Two hardened runs sharing one checkout were falsely rejected because the trusted CLI materialises inert
credential-shadow anchors (``.claude``, ``.mcp.json``, …) in the workspace to carry its own permission
rules, and the per-run workspace diff attributed them — and a finished sibling's own file — to the agent.
These tests reproduce that offline: the fake agent (no model, no network) plants the same anchors via
``FAKE-ACTIONS``. They assert the trusted anchors are excused, a real write to a protected path is still
rejected, two simultaneous hardened runs both reach VERIFIED, and the concurrency-attribution fix counts a
sibling that was running when a task started even if it has since finished. Reuses the autonomy harness
(``test_fc_autonomy.Rig``) and the installed fake agent.
"""

# pylint: disable=missing-function-docstring,redefined-outer-name

from __future__ import annotations

import dataclasses
import subprocess
from pathlib import Path

import pytest

from feature_coordinator.config import ProjectConfig
from feature_coordinator.errors import PullRequestPolicyError
from feature_coordinator.model import FeatureState, TaskState
from feature_coordinator.native_backend import NativeCliRunner
from feature_coordinator.native_hardening import NativeHardening, agent_config_anchors, inert_launcher_anchor
from feature_coordinator.workflow import TaskSpec

import test_fc_autonomy as autonomy
from fc_helpers import actions_prompt, config_with_agents, install_fake_agent

# The two inert anchors the hardened Codex profile plants in the shared checkout (empty files); the fake
# agent plants them here to stand in for the real CLI's rule placeholders.
ANCHORS = [{"do": "write", "path": ".claude", "text": ""}, {"do": "write", "path": ".mcp.json", "text": ""}]


@pytest.fixture
def hardened_cfg(tmp_path: Path) -> ProjectConfig:
    agent = install_fake_agent(tmp_path / "agents")
    cfg = config_with_agents(autonomy.autonomous(), agent)
    return dataclasses.replace(cfg, runtime=dataclasses.replace(
        cfg.runtime, native_hardening=NativeHardening(enabled=True)))


def _rig_with_native(repo: Path, state_dir: Path, cfg: ProjectConfig, roles) -> autonomy.Rig:
    """A Rig whose *roles* run the real hardened NativeCliRunner (driving the fake agent)."""
    rig = autonomy.Rig(repo, state_dir, config=cfg)
    for role in roles:
        rig.coord.runners[role] = NativeCliRunner(cfg.provider_for(role), cfg)  # type: ignore[arg-type]
    return rig


def _tracked(repo: Path, *paths: str) -> list[str]:
    out = subprocess.run(["git", "-C", str(repo), "ls-files", "--", *paths],
                         capture_output=True, text=True, check=True).stdout
    return [p for p in out.splitlines() if p]


def test_inert_launcher_anchors_do_not_fail_a_hardened_run(hardened_cfg, repo, state_dir, fake_home) -> None:
    """A single hardened run whose CLI planted the inert anchors still verifies, commits its real change,
    and never commits the anchors (points: launcher artifacts handled; normal commit + gate still work)."""
    del fake_home
    rig = _rig_with_native(repo, state_dir, hardened_cfg, ("backend",))
    actions = [{"do": "write", "path": "backend/app.py", "text": "ROUTES = ['/health']\n"}, *ANCHORS]
    rig.activate(TaskSpec("api", "backend", actions_prompt(actions), required_gates=("backend-tests",)))
    run = rig.coord.run_task("w4.api")

    task = rig.task("w4.api")
    assert task.state is TaskState.VERIFIED, (task.state, run.runner.error)
    assert (repo / "backend/app.py").read_text(encoding="utf-8") == "ROUTES = ['/health']\n"
    anchors = set(run.runner.details.get("launcher_anchors_ignored", []))
    assert anchors == {".claude", ".mcp.json"}
    assert anchors <= agent_config_anchors()
    assert _tracked(repo, ".claude", ".mcp.json") == []       # never staged or committed
    assert _tracked(repo, "backend/app.py") == ["backend/app.py"]


def test_a_real_write_to_a_protected_path_is_still_rejected(hardened_cfg, repo, state_dir, fake_home) -> None:
    """A non-inert `.claude` (real content) is an actual modification, not a placeholder: still rejected,
    and nothing from the run is committed (points: path-policy preserved; not globally ignored)."""
    del fake_home
    rig = _rig_with_native(repo, state_dir, hardened_cfg, ("backend",))
    actions = [{"do": "write", "path": "backend/app.py", "text": "x\n"},
               {"do": "write", "path": ".claude", "text": "stolen-content\n"}]  # NOT inert
    rig.activate(TaskSpec("api", "backend", actions_prompt(actions)))
    run = rig.coord.run_task("w4.api")

    task = rig.task("w4.api")
    assert task.state is TaskState.FAILED, (task.state, run.runner.error)
    assert "may not write" in (run.runner.error or "") and ".claude" in (run.runner.error or "")
    assert run.runner.details.get("launcher_anchors_ignored") == []   # real content is not an inert anchor
    assert _tracked(repo, "backend/app.py", ".claude") == []   # the rejected run commits nothing


def test_an_unowned_path_write_is_still_rejected_under_hardening(hardened_cfg, repo, state_dir, fake_home) -> None:
    """A real file outside the role's scope (another role's tree) is still rejected — the anchor rule does
    not loosen ordinary path-policy enforcement."""
    del fake_home
    rig = _rig_with_native(repo, state_dir, hardened_cfg, ("backend",))
    actions = [{"do": "write", "path": "frontend/sneaky.py", "text": "print('x')\n"}, *ANCHORS]
    rig.activate(TaskSpec("api", "backend", actions_prompt(actions)))
    run = rig.coord.run_task("w4.api")

    task = rig.task("w4.api")
    assert task.state is TaskState.FAILED, (task.state, run.runner.error)
    assert "frontend/sneaky.py" in (run.runner.error or "")   # the real out-of-scope write is still denied
    # the inert anchors were still excused even though the run was (correctly) rejected for the real write
    assert set(run.runner.details.get("launcher_anchors_ignored", [])) == {".claude", ".mcp.json"}


def test_two_concurrent_hardened_runs_are_not_falsely_rejected(hardened_cfg, repo, state_dir, fake_home,
                                                               tmp_path) -> None:
    """The smoke-test scenario offline: two hardened roles run at the same time in the one shared checkout,
    each planting the inert anchors and meeting at a barrier so their windows overlap. Both reach VERIFIED
    and commit their own file; neither is rejected for the other's anchors or file."""
    del fake_home
    rig = _rig_with_native(repo, state_dir, hardened_cfg, ("backend", "cli"))
    barrier = tmp_path / "barrier"
    barrier.mkdir()

    def acts(role: str, other: str) -> list[dict]:
        return [{"do": "write", "path": f"{role}/app.py", "text": f"# {role}\n"}, *ANCHORS,
                {"do": "write", "path": str(barrier / f"{role}.ready"), "text": "1"},
                {"do": "wait_for", "path": str(barrier / f"{other}.ready"), "timeout": 30}]

    rig.activate(TaskSpec("api", "backend", actions_prompt(acts("backend", "cli"))),
                 TaskSpec("ui", "cli", actions_prompt(acts("cli", "backend"))))
    handles = [rig.spawn("w4.api", 0), rig.spawn("w4.ui", 0)]
    for handle in handles:
        handle.wait(120)

    for task_id, path in (("w4.api", "backend/app.py"), ("w4.ui", "cli/app.py")):
        task = rig.task(task_id)
        assert task.state is TaskState.VERIFIED, (task_id, task.state, task.blocked_reason)
        assert _tracked(repo, path) == [path]
    assert _tracked(repo, ".claude", ".mcp.json") == []        # anchors committed by neither run


def test_integration_is_not_blocked_by_inert_anchors(hardened_cfg, repo, state_dir, fake_home) -> None:
    """The integration cleanliness guard ignores the inert anchors left in the shared checkout, so a
    hardened feature can still render its PR candidate (they are never committed either)."""
    del fake_home
    rig = _rig_with_native(repo, state_dir, hardened_cfg, ("backend",))
    actions = [{"do": "write", "path": "backend/app.py", "text": "ROUTES = []\n"}, *ANCHORS]
    feature = rig.activate(TaskSpec("api", "backend", actions_prompt(actions)))
    rig.coord.run_task("w4.api")
    assert rig.task("w4.api").state is TaskState.VERIFIED
    assert (repo / ".claude").exists() and (repo / ".mcp.json").exists()  # anchors still physically present

    integrated = rig.coord.integrate(feature.id, gates=())
    assert integrated.state is FeatureState.REVIEW
    assert _tracked(repo, ".claude", ".mcp.json") == []        # never committed into the candidate


def test_integration_still_blocks_on_a_real_foreign_file(hardened_cfg, repo, state_dir, fake_home) -> None:
    """A real untracked file (not an anchor) still blocks integration — the guard is not weakened."""
    del fake_home
    rig = _rig_with_native(repo, state_dir, hardened_cfg, ("backend",))
    actions = [{"do": "write", "path": "backend/app.py", "text": "ROUTES = []\n"}, *ANCHORS]
    feature = rig.activate(TaskSpec("api", "backend", actions_prompt(actions)))
    rig.coord.run_task("w4.api")
    assert rig.task("w4.api").state is TaskState.VERIFIED
    (repo / "stray.txt").write_text("foreign\n", encoding="utf-8")  # a real uncommitted file appears

    with pytest.raises(PullRequestPolicyError, match="stray.txt"):
        rig.coord.integrate(feature.id, gates=())


def test_inert_launcher_anchor_predicate(tmp_path) -> None:
    """The provenance predicate: only the known agent-config anchor paths, and only in inert form."""
    root = tmp_path
    (root / ".claude").write_text("", encoding="utf-8")           # empty file -> inert anchor
    (root / ".codex").mkdir()                                     # directory -> inert anchor
    (root / ".mcp.json").write_text("x", encoding="utf-8")        # non-empty -> NOT inert
    (root / "notes.txt").write_text("", encoding="utf-8")         # empty but not an anchor path
    assert inert_launcher_anchor(root, ".claude") is True
    assert inert_launcher_anchor(root, ".codex") is True
    assert inert_launcher_anchor(root, ".agents") is True          # absent anchor is inert
    assert inert_launcher_anchor(root, ".mcp.json") is False       # real content is a real change
    assert inert_launcher_anchor(root, "notes.txt") is False       # not an anchor path
    assert inert_launcher_anchor(root, ".git") is False            # the real Git dir is never an anchor


def test_concurrent_roles_counts_a_sibling_that_finished_before_judgment(hardened_cfg, repo, state_dir) -> None:
    """The attribution fix: a role that was already running when this task claimed stays a concurrent role
    even after it finishes (so its paths are never blamed on this agent), where the old event-window and
    live-claims checks alone would have missed it."""
    coord = autonomy.Rig(repo, state_dir, config=hardened_cfg).coord
    mark = len(coord.store.events())
    assert "frontend" in coord._concurrent_roles(mark, "backend", {"frontend"})  # pylint: disable=protected-access
    assert "frontend" not in coord._concurrent_roles(mark, "backend", set())     # pylint: disable=protected-access
