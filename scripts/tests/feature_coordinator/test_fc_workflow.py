"""One feature, one branch, one PR — orchestration, dependencies, recovery and independence."""

# pylint: disable=missing-function-docstring,redefined-outer-name

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

from feature_coordinator.cli import main as cli_main
from feature_coordinator.demo import DEMO_SCRIPTS, RecordingGates, build_coordinator, demo_spec, run_demo
from feature_coordinator.errors import (
    CoordinatorError,
    DependencyError,
    EvidenceError,
    GitOperationError,
    ProviderUnavailableError,
    PullRequestPolicyError,
    UnstableFileError,
    WritersActiveError,
)
from feature_coordinator.gitops import CommitRequest, file_digest
from feature_coordinator.issues import OwnershipMarker
from feature_coordinator.locks import LockSet
from feature_coordinator.model import FeatureState, TaskState
from feature_coordinator.runners import FileEdit
from feature_coordinator.workflow import FeatureCoordinator, FeatureSpec, TaskSpec

from fc_helpers import git

PACKAGE = Path(__file__).resolve().parents[2] / "feature_coordinator"
REPO_ROOT = Path(__file__).resolve().parents[3]


def test_mock_feature_end_to_end_meets_the_phase2_exit_gate() -> None:
    report = run_demo()
    assert report["branches"] == ["dev", "feature/items-export"]  # exactly one feature branch
    assert report["pull_requests"] == 1  # exactly one PR, after all gates
    assert report["state"] == "review"  # not "done": that needs a human-reviewed merge
    assert report["all_verified"] and set(report["task_states"]) == {
        "items-export.view", "items-export.cmd", "items-export.e2e", "items-export.contract-items-export-endpoint"}
    assert len(report["commits"]) == 3  # backend, cli, frontend; the advisory Qwen task changed nothing
    assert report["second_backend_writer"] == "rejected (RoleBusyError)"
    assert report["foreign_issue_untouched"]
    assert "second PR refused" in report["steps"][-1]


def test_cli_demo_and_dry_run_commands(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    assert cli_main(["demo"]) == 0
    assert "pull requests: 1" in capsys.readouterr().out
    assert cli_main(["config"]) == 0
    assert cli_main(["providers"]) == 0
    assert "110592" in capsys.readouterr().out
    assert cli_main(["doctor"]) == 0  # dry run: nothing is created
    assert not (tmp_path / "xdg-state" / "mx-admin-agents").exists()
    assert cli_main(["status"]) == 0
    spec = tmp_path / "spec.json"
    spec.write_text('{"slug": "x-feature", "title": "X", "tasks": ['
                    '{"key": "api", "role": "backend", "title": "API", "required_gates": ["contract-check"]},'
                    '{"key": "cmd", "role": "cli", "title": "CMD", "depends_on": ["api"]}]}', encoding="utf-8")
    assert cli_main(["plan", str(spec)]) == 0
    assert "feature/x-feature" in capsys.readouterr().out
    spec.write_text('{"slug": "x", "title": "X", "tasks": [{"key": "a", "role": "cli", "title": "A", '
                    '"depends_on": ["a"]}]}', encoding="utf-8")
    assert cli_main(["plan", str(spec)]) == 1


def test_doctor_apply_creates_the_state_directory_and_proves_flock(tmp_path: Path) -> None:
    assert cli_main(["doctor", "--apply"]) == 0
    assert (tmp_path / "xdg-state" / "mx-admin-agents" / "locks").is_dir()


def test_planning_validates_the_feature(coord: FeatureCoordinator) -> None:
    with pytest.raises(CoordinatorError, match="unknown role"):
        coord.plan(FeatureSpec("bad", "B", (TaskSpec("a", "devops", "A"),)))
    with pytest.raises(CoordinatorError, match="unknown gates"):
        coord.plan(FeatureSpec("bad", "B", (TaskSpec("a", "cli", "A", required_gates=("nope",)),)))
    with pytest.raises(DependencyError, match="cycle"):
        coord.plan(FeatureSpec("bad", "B", (TaskSpec("a", "cli", "A", depends_on=("b",)),
                                            TaskSpec("b", "backend", "B", depends_on=("a",)))))
    with pytest.raises(CoordinatorError, match="unique"):
        coord.plan(FeatureSpec("bad", "B", (TaskSpec("a", "cli", "A"), TaskSpec("a", "cli", "A2"))))
    coord.plan(demo_spec())
    with pytest.raises(CoordinatorError, match="already exists"):
        coord.plan(demo_spec())


def test_one_active_feature_per_checkout(coord: FeatureCoordinator) -> None:
    coord.activate(coord.plan(demo_spec()).id)
    queued = coord.plan(FeatureSpec("second-feature", "Second", (TaskSpec("a", "cli", "A"),)))
    with pytest.raises(CoordinatorError, match="one active feature"):
        coord.activate(queued.id)
    assert coord.store.load_feature(queued.id).state is FeatureState.PLANNED


def test_existing_branch_is_never_reused(coord: FeatureCoordinator, repo: Path) -> None:
    git(repo, "branch", "feature/items-export")
    with pytest.raises(GitOperationError, match="already exists"):
        coord.plan(demo_spec())


def test_blocked_consumer_does_not_freeze_unrelated_work(coord: FeatureCoordinator) -> None:
    feature = coord.activate(coord.plan(demo_spec()).id)
    contract = coord.request_contract("items-export.cmd", key="endpoint", title="Endpoint", contract="GET /x")
    assert coord.store.load_task("items-export.cmd").state is TaskState.BLOCKED
    assert coord.tracker.get(coord.store.load_task("items-export.cmd").issue_number).labels[-1] == "feature:blocked"
    with pytest.raises(CoordinatorError, match="not ready"):
        coord.run_task("items-export.cmd")
    assert coord.run_task("items-export.view").state is TaskState.VERIFIED  # unrelated frontend work proceeds
    assert coord.store.load_task("items-export.cmd").state is TaskState.BLOCKED
    run = coord.run_task(contract.id)  # the contract task has no scripted edits: gates on the shared HEAD
    assert run.state is TaskState.VERIFIED and run.unblocked == ["items-export.cmd"]
    assert "feature:blocked" not in coord.tracker.get(coord.store.load_task("items-export.cmd").issue_number).labels
    assert feature.branch == coord.git.current_branch()


def test_contract_requests_are_deduplicated(coord: FeatureCoordinator) -> None:
    coord.activate(coord.plan(demo_spec()).id)
    first = coord.request_contract("items-export.cmd", key="endpoint", title="Endpoint", contract="GET /x")
    second = coord.request_contract("items-export.cmd", key="endpoint", title="dup", contract="dup")
    other = coord.request_contract("items-export.view", key="endpoint", title="dup", contract="dup")
    assert first.id == second.id == other.id
    issues = [i for i in coord.tracker.list_issues()
              if (m := OwnershipMarker.parse(i.body)) is not None and m.key == f"items-export/{first.id}"]
    assert len(issues) == 1
    assert coord.store.load_task("items-export.cmd").depends_on == (first.id,)


def test_failing_gate_fails_the_task_and_keeps_dependents_blocked(repo: Path, state_dir: Path) -> None:
    coord = build_coordinator(repo, state_dir, scripts=DEMO_SCRIPTS, gates=RecordingGates(failing=["frontend-tests"]))
    coord.activate(coord.plan(demo_spec()).id)
    assert coord.run_task("items-export.view").state is TaskState.FAILED
    assert coord.store.load_task("items-export.e2e").state is TaskState.BLOCKED


def test_unavailable_remote_model_only_affects_automation(repo: Path, state_dir: Path) -> None:
    coord = build_coordinator(repo, state_dir, scripts=DEMO_SCRIPTS,
                              qwen_script=[ProviderUnavailableError("down")] * 3)
    spec = FeatureSpec("qwen-down", "Q", (TaskSpec("e2e", "automation", "E2E"), TaskSpec("view", "frontend", "View")))
    feature = coord.activate(coord.plan(spec).id)
    run = coord.run_task("qwen-down.e2e")
    assert run.runner.status == "unavailable" and run.state is TaskState.READY
    assert coord.store.load_task("qwen-down.e2e").state is TaskState.READY  # claim released, retry later
    assert coord.store.load_claim("qwen-down.e2e") is None
    assert coord.run_task("qwen-down.view").state is TaskState.VERIFIED  # Codex role unaffected
    assert feature.state is FeatureState.ACTIVE


def _verified_feature(coord: FeatureCoordinator) -> str:
    feature = coord.activate(coord.plan(demo_spec()).id)
    contract = coord.request_contract("items-export.cmd", key="items-export-endpoint", title="E", contract="GET /x")
    for task_id in ("items-export.view", contract.id, "items-export.cmd", "items-export.e2e"):
        assert coord.run_task(task_id).state is TaskState.VERIFIED, task_id
    return feature.id


def test_commit_messages_carry_a_short_subject_never_the_task_prompt(coord: FeatureCoordinator, repo: Path) -> None:
    prompt = ("Create the file backend/src/app.py containing exactly one line: ROUTES = ['/x']\n"
              "Do not create, change or delete anything else. Do not run any command. Then reply 'done'.")
    feature = coord.activate(coord.plan(FeatureSpec("subjects", "Commit subjects", (
        TaskSpec("api", "backend", prompt), TaskSpec("view", "frontend", "Export button in the category view"),
    ))).id)
    coord.runners["backend"].script["subjects.api"] = [FileEdit("backend/src/app.py", "ROUTES = ['/x']\n")]
    coord.runners["frontend"].script["subjects.view"] = [FileEdit("frontend/src/view.ts", "export const v = 1\n")]
    for task_id in ("subjects.api", "subjects.view"):
        assert coord.run_task(task_id).state is TaskState.VERIFIED
    coord.reopen_task("subjects.api", "the route changes")  # a second run of the task: a second commit
    coord.runners["backend"].script["subjects.api"] = [FileEdit("backend/src/app.py", "ROUTES = ['/y']\n")]
    assert coord.run_task("subjects.api").state is TaskState.VERIFIED
    commits = git(repo, "rev-list", "--reverse", f"{feature.base_sha}..{feature.branch}").split()
    assert coord.store.load_task("subjects.api").commits == [commits[0], commits[2]]
    messages = [git(repo, "log", "-1", "--format=%B", sha) for sha in commits]
    trailers = "\n\nFeature: subjects\nFeature-Task: subjects.{key}\nFeature-Role: {role}\n\n"
    assert messages == ["backend: task subjects.api" + trailers.format(key="api", role="backend"),
                        "frontend: Export button in the category view" + trailers.format(key="view", role="frontend"),
                        "backend: task subjects.api" + trailers.format(key="api", role="backend")]
    assert {git(repo, "log", "-1", "--format=%an <%ae>", sha).strip() for sha in commits} == {
        "Feature Coordinator Demo <feature-coordinator@example.invalid>"}


def test_integration_requires_everything_verified_and_a_clean_checkout(coord: FeatureCoordinator,
                                                                       repo: Path) -> None:
    feature = coord.activate(coord.plan(demo_spec()).id)
    with pytest.raises(PullRequestPolicyError, match="not verified"):
        coord.integrate(feature.id)
    for task_id in ("items-export.view", "items-export.cmd", "items-export.e2e"):
        coord.run_task(task_id)
    (repo / "frontend/src/stray.ts").write_text("// unfinished\n", encoding="utf-8")
    with pytest.raises(PullRequestPolicyError, match="uncommitted or foreign"):
        coord.integrate(feature.id)
    (repo / "frontend/src/stray.ts").unlink()
    assert coord.integrate(feature.id).state is FeatureState.REVIEW


def test_single_pr_needs_approval_and_done_needs_a_merge(coord: FeatureCoordinator) -> None:
    feature_id = _verified_feature(coord)
    feature = coord.integrate(feature_id)
    body = feature.pull_request.body
    assert "backend/src/app.py" in body and "frontend/src/view.ts" in body and "Remote CI: pending" in body
    with pytest.raises(PullRequestPolicyError, match="approval"):
        coord.open_pull_request(feature_id, approved=False)
    pr = coord.open_pull_request(feature_id, approved=True)
    with pytest.raises(PullRequestPolicyError):
        coord.open_pull_request(feature_id, approved=True)
    assert len(coord.tracker.pull_requests()) == 1 and pr.head == "feature/items-export"  # type: ignore[attr-defined]
    with pytest.raises(EvidenceError):
        coord.complete(feature_id, merged_sha="")
    done = coord.complete(feature_id, merged_sha="d" * 40)
    assert done.state is FeatureState.DONE
    assert all(i.state == "closed" for i in coord.tracker.list_issues())  # type: ignore[attr-defined]


def test_pr_is_refused_when_the_branch_moved_after_integration(coord: FeatureCoordinator, repo: Path) -> None:
    feature_id = _verified_feature(coord)
    coord.integrate(feature_id)
    (repo / "cli/src/commands.py").write_text("late change\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "commit", "-qam", "late"], check=True)
    with pytest.raises(PullRequestPolicyError, match="moved"):
        coord.open_pull_request(feature_id, approved=True)


def test_reopened_contract_invalidates_its_consumer(coord: FeatureCoordinator) -> None:
    _verified_feature(coord)
    blocked = coord.reopen_task("items-export.contract-items-export-endpoint", "contract must change")
    assert "items-export.cmd" in blocked and "items-export.e2e" in blocked
    assert coord.store.load_task("items-export.cmd").state is TaskState.BLOCKED


def test_crash_during_a_task_is_recovered_and_the_task_reruns(coord: FeatureCoordinator) -> None:
    coord.activate(coord.plan(demo_spec()).id)
    head = coord.git.rev_parse("refs/heads/feature/items-export")
    claim = coord.claims.claim("items-export.view", role="frontend", branch="feature/items-export", head_sha=head)
    record = coord.store.load_claim("items-export.view")
    with coord.store.transaction():  # simulate the worker dying: forge a dead identity, drop its locks
        record["identity"]["start_ticks"] += 1
        coord.store.save_claim("items-export.view", record)
    claim.release_locks()
    report = coord.claims.recover()
    assert [r["task_id"] for r in report.recovered] == ["items-export.view"]
    assert coord.run_task("items-export.view").state is TaskState.VERIFIED


def test_the_coordinator_is_independent_of_the_bug_workflow_and_the_backend() -> None:
    forbidden = ("mxfix", "mxissues", "src", "backend", "testenvlib")
    for source in PACKAGE.glob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                [node.module or ""] if isinstance(node, ast.ImportFrom) and node.level == 0 else []
            for name in names:
                assert name.split(".")[0] not in forbidden, f"{source.name} imports {name}"
    status = subprocess.run(["git", "-C", str(REPO_ROOT), "status", "--porcelain", "--", "scripts/mxfix",
                             "scripts/mxissues"], capture_output=True, text=True, check=True).stdout
    assert status == ""


# -- every run_task exit leaves the task in a defined, claimable state --------------------

def _no_claims_left(coord: FeatureCoordinator) -> bool:
    return not coord.store.list_claims()


def test_branch_moving_after_the_gates_reruns_them(coord: FeatureCoordinator, repo: Path,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    feature = coord.activate(coord.plan(demo_spec()).id)
    original, calls = coord.gate_evidence, []

    def gates_then_another_role_commits(feat, gates, attempts=3):
        evidence = original(feat, gates, attempts)
        calls.append(evidence.head_sha)
        if len(calls) == 1:  # the CLI role lands a disjoint commit right after the frontend's gates
            (repo / "cli/src/commands.py").write_text("COMMANDS = ['concurrent']\n", encoding="utf-8")
            coord.git.commit(CommitRequest(feature.id, "items-export.cmd", "cli", feature.branch,
                                           coord.git.rev_parse(feature.branch),
                                           {"cli/src/commands.py": file_digest(repo / "cli/src/commands.py")},
                                           "concurrent cli work"))
        return evidence

    monkeypatch.setattr(coord, "gate_evidence", gates_then_another_role_commits)
    run = coord.run_task("items-export.view")
    assert run.state is TaskState.VERIFIED and len(calls) == 2 and calls[0] != calls[1]
    assert coord.store.load_task("items-export.view").evidence.head_sha == coord.git.rev_parse(feature.branch)


def test_commit_refusal_fails_the_task_instead_of_stranding_it(coord: FeatureCoordinator, repo: Path) -> None:
    coord.activate(coord.plan(demo_spec()).id)
    (repo / "cli/src/commands.py").write_text("staged by hand\n", encoding="utf-8")
    git(repo, "add", "cli/src/commands.py")  # foreign staged content in the shared index
    run = coord.run_task("items-export.view")
    assert run.state is TaskState.FAILED and "commit refused" in coord.store.load_task(
        "items-export.view").history[-1].reason
    assert _no_claims_left(coord)


def test_deferred_commit_and_unexpected_errors_return_the_task_to_ready(coord: FeatureCoordinator,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    coord.activate(coord.plan(demo_spec()).id)

    def unstable(*_args, **_kwargs):
        raise UnstableFileError("frontend/src/view.ts changed after it was declared")

    monkeypatch.setattr(coord.git, "commit", unstable)
    assert coord.run_task("items-export.view").state is TaskState.READY
    assert coord.store.load_task("items-export.view").state is TaskState.READY and _no_claims_left(coord)
    assert any(e["kind"] == "commit-deferred" for e in coord.store.events())

    def crash(_request):
        raise RuntimeError("runner bug")

    monkeypatch.setattr(coord.runners["frontend"], "run", crash)
    with pytest.raises(RuntimeError):
        coord.run_task("items-export.view")
    assert coord.store.load_task("items-export.view").state is TaskState.READY and _no_claims_left(coord)


def test_contract_request_during_a_run_supersedes_its_result(coord: FeatureCoordinator,
                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    coord.activate(coord.plan(demo_spec()).id)
    runner = coord.runners["cli"]
    original = runner.run

    def run_and_discover_missing_api(request):
        result = original(request)
        coord.request_contract("items-export.cmd", key="endpoint", title="Endpoint", contract="GET /x")
        return result

    monkeypatch.setattr(runner, "run", run_and_discover_missing_api)
    run = coord.run_task("items-export.cmd")
    task = coord.store.load_task("items-export.cmd")
    assert run.state is TaskState.BLOCKED and task.state is TaskState.BLOCKED
    assert task.commits == [run.commit]  # its committed work is kept on the branch and recorded
    assert any(e["kind"] == "task-result-superseded" for e in coord.store.events())


def test_integration_is_a_quiescent_barrier(coord: FeatureCoordinator, state_dir: Path) -> None:
    feature_id = _verified_feature(coord)
    writer = LockSet(state_dir / "locks").project(shared=True).acquire()  # e.g. a writer on a new task
    with pytest.raises(WritersActiveError):
        coord.integrate(feature_id)
    writer.release()
    assert coord.integrate(feature_id).state is FeatureState.REVIEW


def test_failing_integration_gate_returns_the_feature_to_active(repo: Path, state_dir: Path) -> None:
    gates = RecordingGates()
    coord = build_coordinator(repo, state_dir, scripts=DEMO_SCRIPTS, gates=gates)
    feature_id = _verified_feature(coord)
    gates.failing.add("docs-check")  # the combined tree breaks a check that passed per task
    with pytest.raises(EvidenceError, match="docs-check"):
        coord.integrate(feature_id)
    feature = coord.store.load_feature(feature_id)
    assert feature.state is FeatureState.ACTIVE and feature.pull_request is None
    with pytest.raises(PullRequestPolicyError):
        coord.open_pull_request(feature_id, approved=True)
    assert not coord.tracker.pull_requests()  # type: ignore[attr-defined]
