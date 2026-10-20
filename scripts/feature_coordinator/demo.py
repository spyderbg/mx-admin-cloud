"""A complete mock feature, end to end, in a throwaway Git repository (Phase 2 exit gate).

Nothing here touches the real checkout, the real state directory, GitHub or any model
endpoint: the repository and state live in a temporary directory, runners and the
tracker are mocks, gates are recorded fakes, and the remote-Qwen transport is a scripted
mock behind a loopback URL.

Scenario (one feature, one branch, one PR):
  1. plan `items-export` with Frontend, CLI and Automation tasks;
  2. activate → the single branch feature/items-export;
  3. the CLI task finds a missing API → ONE Backend contract task is created (a duplicate
     request reuses it); only the CLI step is blocked, the Frontend task proceeds;
  4. a second Backend writer is rejected while the Backend slot is held, while Backend
     and Frontend slots can be held at the same time;
  5. Backend contract verified on the shared branch → CLI resumes and is verified;
  6. Automation (remote Qwen, tool calling not validated) runs read-only/advisory;
  7. integration gates on the final HEAD → exactly one PR candidate → one mock PR;
     a second PR is refused.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Any, Sequence

from .config import ProjectConfig, default_config
from .errors import PullRequestPolicyError, RoleBusyError
from .issues import MockIssueTracker
from .model import TaskState
from .runners import FileEdit, MockChatTransport, MockClaudeCodeRunner, MockCodexRunner, OpenAICompatibleRunner, \
    chat_response
from .store import StateStore
from .statedir import ensure_state_dir
from .workflow import FeatureCoordinator, FeatureSpec, TaskSpec

DEMO_AUTHOR = ("Feature Coordinator Demo", "feature-coordinator@example.invalid")
SEED_FILES = {
    "backend/src/app.py": "ROUTES = []\n",
    "cli/src/commands.py": "COMMANDS = []\n",
    "frontend/src/view.ts": "export const views: string[] = []\n",
    "scripts/e2e_check.py": "print('ok')\n",
    "docs_agents/coordination/README.md": "# Coordination\n",
}


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


def init_demo_repo(repo: Path, files: dict[str, str] | None = None, base_branch: str = "dev") -> str:
    """Create a small repository with one commit on *base_branch*; returns its SHA."""
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "--quiet", f"--initial-branch={base_branch}")
    _git(repo, "config", "user.name", DEMO_AUTHOR[0])
    _git(repo, "config", "user.email", DEMO_AUTHOR[1])
    _git(repo, "config", "commit.gpgsign", "false")
    for rel, content in (files or SEED_FILES).items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    _git(repo, "add", "--all")
    _git(repo, "commit", "--quiet", "-m", "Seed")
    return _git(repo, "rev-parse", "HEAD").strip()


class RecordingGates:  # pylint: disable=too-few-public-methods
    """Fake gate runner: every gate passes unless listed in *failing*; calls are recorded."""

    def __init__(self, failing: Sequence[str] = ()) -> None:
        self.failing = set(failing)
        self.calls: list[str] = []

    def __call__(self, name: str, context: object) -> int:
        del context
        self.calls.append(name)
        return 1 if name in self.failing else 0


def build_coordinator(repo: Path, state_dir: Path, *, config: ProjectConfig | None = None,
                      qwen_script: Sequence[Any] = (), gates: RecordingGates | None = None,
                      scripts: dict[str, dict[str, list[FileEdit]]] | None = None) -> FeatureCoordinator:
    """A coordinator wired with mock runners, a mock tracker and fake gates."""
    config = config or default_config()
    ensure_state_dir(state_dir, allowed=config.allowed_state_filesystems)
    scripts = scripts or {}
    claude = config.providers["claude"]
    codex = config.providers["codex"]
    runners = {
        "backend": MockClaudeCodeRunner(claude, scripts.get("backend")),  # type: ignore[arg-type]
        "cli": MockClaudeCodeRunner(claude, scripts.get("cli")),  # type: ignore[arg-type]
        "frontend": MockCodexRunner(codex, scripts.get("frontend")),  # type: ignore[arg-type]
        "coordination": MockClaudeCodeRunner(claude, scripts.get("coordination")),  # type: ignore[arg-type]
        "automation": OpenAICompatibleRunner(
            config.providers["remote_qwen"],  # type: ignore[arg-type]
            env={"AGENT_QWEN_BASE_URL": "http://127.0.0.1:8080/v1"},  # loopback mock, never contacted
            transport=MockChatTransport(list(qwen_script) or [chat_response("E2E plan reviewed: no blockers.")]),
            sleep=lambda _s: None,
        ),
    }
    return FeatureCoordinator(config, repo, StateStore(state_dir), tracker=MockIssueTracker(), runners=runners,
                              gate_runner=gates or RecordingGates(), author=DEMO_AUTHOR)


def demo_spec() -> FeatureSpec:
    """The demo feature."""
    return FeatureSpec(
        slug="items-export", title="Export category items as CSV",
        acceptance=("Backend exposes the export endpoint", "CLI command calls it over HTTP",
                    "Frontend offers an export button", "End-to-end check passes"),
        tasks=(
            TaskSpec("view", "frontend", "Export button in the category view",
                     required_gates=("frontend-type-check", "frontend-tests")),
            TaskSpec("cmd", "cli", "kiddo-cli content export-items", required_gates=("coordinator-tests",)),
            TaskSpec("e2e", "automation", "End-to-end verification plan", depends_on=("view", "cmd"),
                     required_gates=("docs-check",)),
        ),
    )


DEMO_SCRIPTS = {
    "backend": {"items-export.contract-items-export-endpoint": [
        FileEdit("backend/src/app.py", "ROUTES = ['/api/v1/content/categories/{id}/items/export']\n"),
        FileEdit("docs_agents/backend/services/content/export.md", "# Export endpoint\n")]},
    "cli": {"items-export.cmd": [FileEdit("cli/src/commands.py", "COMMANDS = ['content export-items']\n")]},
    "frontend": {"items-export.view": [FileEdit("frontend/src/view.ts", "export const views = ['export']\n")]},
}


def run_demo(workdir: Path | None = None) -> dict[str, Any]:  # pylint: disable=too-many-locals
    """Run the scenario; returns a report dict. Uses a temporary directory unless *workdir* is given."""
    with tempfile.TemporaryDirectory(prefix="feature-coordinator-demo-") as tmp:
        base = workdir or Path(tmp)
        repo, state_dir = base / "repo", base / "state"
        init_demo_repo(repo)
        gates = RecordingGates()
        coord = build_coordinator(repo, state_dir, gates=gates, scripts=DEMO_SCRIPTS)
        foreign = coord.tracker.add_foreign_issue(  # type: ignore[attr-defined]
            "bug: flaky test", ["mx-auto", "source::pytest"])
        report: dict[str, Any] = {"steps": []}
        step = report["steps"].append

        feature = coord.plan(demo_spec())
        step(f"planned {feature.id} with {len(feature.task_ids)} tasks; parent issue #{feature.issue_number}")
        feature = coord.activate(feature.id)
        step(f"activated: branch {feature.branch} from {feature.base_sha[:12]}")

        contract = coord.request_contract("items-export.cmd", key="items-export-endpoint",
                                          title="Items export endpoint",
                                          contract="GET /api/v1/content/categories/{id}/items/export → text/csv; "
                                                   "auth: require_write_access read path")
        again = coord.request_contract("items-export.cmd", key="items-export-endpoint", title="dup", contract="dup")
        step(f"CLI requested a contract → backend task {contract.id} "
             f"(duplicate request reused: {again.id == contract.id}); "
             f"cli is {coord.store.load_task('items-export.cmd').state.value}")

        head = coord.git.rev_parse(f"refs/heads/{feature.branch}")
        backend_claim = coord.claims.claim(contract.id, role="backend", branch=feature.branch, head_sha=head)
        frontend_claim = coord.claims.claim("items-export.view", role="frontend", branch=feature.branch, head_sha=head)
        step("backend and frontend slots held concurrently (different roles)")
        probe = coord.store.locks.role("backend", busy_error=RoleBusyError)
        try:
            probe.acquire()
            second_backend = "ACCEPTED (unexpected)"
            probe.release()
        except RoleBusyError:
            second_backend = "rejected (RoleBusyError)"
        report["second_backend_writer"] = second_backend
        step(f"a second backend writer is {second_backend}")
        coord.claims.release(backend_claim, yield_task=True)
        coord.claims.release(frontend_claim, yield_task=True)

        runs = [coord.run_task("items-export.view"), coord.run_task(contract.id)]
        step(f"frontend view → {runs[0].state.value}; backend contract → {runs[1].state.value}, "
             f"unblocked {runs[1].unblocked}")
        runs.append(coord.run_task("items-export.cmd"))
        runs.append(coord.run_task("items-export.e2e"))
        step(f"cli → {runs[2].state.value}; automation (remote Qwen, advisory) → {runs[3].state.value} "
             f"[{runs[3].runner.status}]")

        feature = coord.integrate(feature.id)
        pr = coord.open_pull_request(feature.id, approved=True)
        try:
            coord.open_pull_request(feature.id, approved=True)
            second_pr = "created"
        except PullRequestPolicyError:
            second_pr = "refused"
        step(f"integrated at {feature.pull_request.head_sha[:12]}; PR #{pr.number} opened; second PR {second_pr}")

        log = _git(repo, "log", "--format=%h %s", f"{feature.base_sha}..{feature.branch}").splitlines()
        tasks = coord.store.load_tasks(coord.store.load_feature(feature.id))
        report.update(
            feature=feature.id, branch=feature.branch, state=coord.store.load_feature(feature.id).state.value,
            commits=log, pull_requests=len(coord.tracker.pull_requests()),  # type: ignore[attr-defined]
            task_states={t.id: t.state.value for t in tasks.values()},
            all_verified=all(t.state is TaskState.VERIFIED for t in tasks.values()),
            foreign_issue_untouched=coord.tracker.get(foreign.number).labels == ["mx-auto", "source::pytest"],
            gates_run=gates.calls, branches=_git(repo, "branch", "--format=%(refname:short)").split(),
        )
        return report
