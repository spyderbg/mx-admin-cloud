"""Phase 3 exit gate: parallel Backend + Frontend writers on ONE shared checkout, in real sandboxes.

Two worker processes (Backend via the Claude Code launcher, Frontend via the Codex launcher;
both executables are the fake agent, so no model is called) run at the same time in a
disposable repository and an isolated state directory. While both are provably active:
a second Backend writer is rejected (in-process and from another process), a branch switch
is rejected, and a full-project session cannot start. Then both finish, and the Git
coordinator lands exactly one single-role commit each — without capturing an unrelated
dirty file of a third role.
"""

# pylint: disable=missing-function-docstring,redefined-outer-name

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from feature_coordinator.claims import ClaimManager
from feature_coordinator.config import ProjectConfig
from feature_coordinator.demo import RecordingGates, init_demo_repo
from feature_coordinator.errors import RoleBusyError, WritersActiveError
from feature_coordinator.model import TaskState
from feature_coordinator.operations import build_coordinator
from feature_coordinator.workflow import FeatureSpec, TaskSpec

from fc_helpers import WORKER_SCRIPT, actions_prompt, fake_runners, git, run_py, start_worker, worker_result

def barrier_actions(role: str, file: str, other: str) -> list[dict]:
    return [{"do": "write", "path": file, "text": f"// written by the {role} agent\n"},
            {"do": "write", "path": f"{role}/.started", "text": "1"},
            {"do": "wait_for", "path": f"{other}/.started", "timeout": 60},
            {"do": "wait_for", "path": ".git/fc-release", "timeout": 60},
            {"do": "delete", "path": f"{role}/.started"}]


def wait_until(predicate, timeout: float = 60.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


@pytest.mark.usefixtures("needs_bwrap")
def test_backend_and_frontend_write_concurrently_and_land_separately(  # pylint: disable=too-many-locals
        tmp_path: Path, state_dir: Path, agent_config: ProjectConfig, fake_agent: Path, fake_home: Path) -> None:
    del fake_home
    repo = tmp_path / "shared"
    init_demo_repo(repo)
    coord = build_coordinator(agent_config, repo, state_dir, runners=fake_runners(agent_config))
    coord.gate_runner = RecordingGates()
    feature = coord.activate(coord.plan(FeatureSpec("par", "Parallel roles", (
        TaskSpec("api", "backend", "API"), TaskSpec("ui", "frontend", "UI"), TaskSpec("db", "backend", "DB")))).id)
    base = coord.git.rev_parse(feature.branch)
    (repo / "cli/src/commands.py").write_text("COMMANDS = ['half-done by someone else']\n", encoding="utf-8")

    workers = {
        "par.api": barrier_actions("backend", "backend/src/api.py", "frontend"),
        "par.ui": barrier_actions("frontend", "frontend/src/ui.ts", "backend"),
    }
    procs = {task: start_worker(repo, state_dir, fake_agent, task, actions_prompt(actions))
             for task, actions in workers.items()}
    try:
        # Both sandboxes are running at the same time: each has seen the other's marker.
        assert wait_until(lambda: (repo / "backend/.started").exists() and (repo / "frontend/.started").exists())
        claims = ClaimManager(coord.store)
        assert {r["role"] for r in claims.live_claims().values()} == {"backend", "frontend"}
        # A second Backend writer is rejected — in this process and from another process.
        with pytest.raises(RoleBusyError):
            claims.claim("par.db", role="backend", branch=feature.branch, head_sha=base)
        other = run_py(WORKER_SCRIPT, str(repo), str(state_dir), str(fake_agent), "par.db", actions_prompt([]))
        assert json.loads(other.stdout.strip().splitlines()[-1])["exception"] == "RoleBusyError", other.stderr
        # Tree-changing Git operations and full-project sessions cannot overlap scoped writers.
        with pytest.raises(WritersActiveError):
            coord.git.switch_branch("dev")
        claims.request_full_project("needs the whole tree")
        with pytest.raises(WritersActiveError, match="backend.*frontend|frontend.*backend"):
            claims.claim_full_project()
        claims.withdraw_full_project_request("test: let the scoped writers finish")
    finally:
        (repo / ".git/fc-release").write_text("go", encoding="utf-8")
        results = {task: worker_result(proc) for task, proc in procs.items()}

    assert results["par.api"]["state"] == "verified", results
    assert results["par.ui"]["state"] == "verified", results
    commits = git(repo, "log", "--format=%H", f"{base}..{feature.branch}").split()
    assert len(commits) == 2
    files = sorted(git(repo, "show", "--name-only", "--format=", c).strip() for c in commits)
    assert files == ["backend/src/api.py", "frontend/src/ui.ts"]  # one role each; no markers, no foreign file
    assert git(repo, "status", "--porcelain").strip() == "M cli/src/commands.py"
    assert coord.store.load_task("par.db").state is TaskState.READY and not coord.store.list_claims()
    git(repo, "fsck", "--no-progress")
