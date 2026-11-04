"""W4.5: GitHub-native Issue planning and approval (worker:* labels) — offline acceptance tests.

The persisted mock tracker stands in for GitHub (Issues, comments, label events, Actions runs), a fake
planner for the Desktop Worker's Claude Code, and the GitHub Action's REAL receipt code
(.github/scripts/worker_issue_labels.py) runs against the mock through a small REST adapter, so the
coordinator validates exactly what the workflow would post. Mock runners and recorded gates execute
the approved tasks. No model, no real GitHub, no network."""

# pylint: disable=missing-function-docstring,redefined-outer-name,protected-access,too-many-lines

from __future__ import annotations

import calendar
import copy
import dataclasses
import importlib.util
import itertools
import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import pytest
import yaml

from feature_coordinator import plan_digest as pd
from feature_coordinator.cli import EXAMPLE_CONFIG
from feature_coordinator.cli import main as cli_main
from feature_coordinator.config import GitHubPlanningConfig, config_from_mapping, default_config
from feature_coordinator.errors import ClaimError, ConfigError, TrackerError
from feature_coordinator.intake import IntakeRecord, feature_id_for, load_intake, spec_digest
from feature_coordinator.intake_poll import IntakePoller
from feature_coordinator.issue_sync import SimulatedCrash
from feature_coordinator.issues import MockIssueTracker, normalize_body, parse_op
from feature_coordinator.model import FeatureState, TaskState
from feature_coordinator.native_backend import SubscriptionCheck
from feature_coordinator.planner import ClaudeCodePlanner, PlannerRequest, PlannerResult, planner_argv
from feature_coordinator.planning import INTEGRATION_GATES, canonical_plan, load_plan, path_problem
from feature_coordinator.launchers import RunnerProbe
from feature_coordinator.providers import ProviderKind
from feature_coordinator.runners import FileEdit
from feature_coordinator.supervisor import Supervision
from feature_coordinator.workflow import DEFAULT_INTEGRATION_GATES

from test_fc_intake import BODY, OPERATOR, TITLE, Rig, intake_config
from test_fc_project_sync import values

REPO_ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location("worker_issue_labels",
                                               REPO_ROOT / ".github/scripts/worker_issue_labels.py")
assert _SPEC is not None and _SPEC.loader is not None
ACTION = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ACTION)
WORKFLOW_FILE = REPO_ROOT / ".github/workflows/worker-issue-labels.yml"

REPO_NAME = default_config().github.repository
BOT = "github-actions[bot]"
WORKFLOW = ".github/workflows/worker-issue-labels.yml"
EPOCH = calendar.timegm(time.strptime("2026-01-01T00:00:00", "%Y-%m-%dT%H:%M:%S"))


def planning_config(**planning: Any):
    config = intake_config()
    return dataclasses.replace(config, github=dataclasses.replace(config.github, planning=dataclasses.replace(
        config.github.planning, **{"enabled": True, **planning})))


def task(task_id: str, role: str, paths: list[str], depends: tuple[str, ...] = (), gates: tuple[str, ...] = ()):
    return {"id": task_id, "title": f"The {role} part", "role": role,
            "instructions": f"Implement the {role} part of the export and test it.",
            "acceptance_criteria": [f"the {role} part works"], "paths": paths, "depends_on": list(depends),
            "gates": list(gates)}


SINGLE = {"feature": {"title": TITLE, "summary": "One CLI command.",
                      "acceptance_criteria": ["one CSV line per item", "exit code 0"]},
          "tasks": [task("cli-export", "cli", ["cli/src/commands.py"])], "attention": None}
MULTI = {"feature": {"title": "Category export across the stack", "summary": "API, UI, CLI and docs.",
                     "acceptance_criteria": ["the API returns CSV", "the UI and the CLI use it"]},
         "tasks": [task("api", "backend", ["backend/src/**"]),
                   task("ui", "frontend", ["frontend/src/**"], depends=("api",)),
                   task("cli", "cli", ["cli/src/commands.py"], depends=("api",)),
                   task("docs", "coordination", ["docs_agents/coordination/README.md"], depends=("api", "ui", "cli"))],
         "attention": None}
EDITS = {"backend": FileEdit("backend/src/app.py", "ROUTES = ['/export']\n"),
         "frontend": FileEdit("frontend/src/view.ts", "export const views: string[] = ['export']\n"),
         "cli": FileEdit("cli/src/commands.py", "COMMANDS = ['content export-items']\n"),
         "coordination": FileEdit("docs_agents/coordination/README.md", "# Coordination\n\nExport.\n")}


class FakePlanner:  # pylint: disable=too-few-public-methods
    """Scripted proposals (the last one repeats); records every request."""

    def __init__(self, *results: Any) -> None:
        self.results = list(results)
        self.requests: list[PlannerRequest] = []

    def plan(self, request: PlannerRequest) -> PlannerResult:
        self.requests.append(request)
        result = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        if callable(result):
            return result(request)
        if isinstance(result, PlannerResult):
            return result
        return PlannerResult("ok", proposal=copy.deepcopy(result))


class MockApi:
    """The Action's REST client (worker_issue_labels.Api) over the mock tracker, acting as github-actions[bot]."""

    def __init__(self, tracker: MockIssueTracker) -> None:
        self.tracker = tracker
        self.sleep = lambda _seconds: None
        self.calls: list[str] = []

    def request(self, method: str, path: str, body: Any = None, *, ok: tuple[int, ...] = ()) -> tuple[int, Any]:
        self.calls.append(f"{method} {path.split('?')[0]}")
        rest = path.split("?")[0].split("/")[3:]
        if rest[0] == "collaborators":
            permission = self.tracker.permissions.get(unquote(rest[1]))
            if permission is None:
                assert 404 in ok
                return 404, None
            return 200, {"permission": permission}
        number = int(rest[1])
        if rest[2] == "comments" and method == "POST":
            return 201, {"id": self.tracker.add_comment_as(number, body["body"], BOT).id}
        if rest[2] == "labels" and method == "DELETE":
            self.tracker.unlabel_as(number, unquote(rest[3]), BOT)
            return 200, None
        if rest[2] == "events":
            return 200, [{"id": e.id, "event": e.event, "actor": {"login": e.actor}, "label": {"name": e.label},
                          "created_at": e.created_at} for e in self.tracker.list_issue_events(number)]
        if rest[2] == "comments":
            return 200, [{"id": c.id, "user": {"login": c.author}, "body": c.body, "created_at": c.created_at}
                         for c in self.tracker.list_comments(number)]
        raise AssertionError(path)

    def pages(self, path: str) -> list[Any]:
        return self.request("GET", path)[1]


class PlanRig(Rig):
    """A Rig with planning enabled, a fake planner and the Action's receipt step."""

    def __init__(self, repo: Path, state: Path, *, config=None, planner: FakePlanner | None = None) -> None:
        super().__init__(repo, state, config=config or planning_config())
        self.planner = planner or FakePlanner(SINGLE)
        self.coord.planner = self.planner          # what the Worker service's polls use
        self.now = EPOCH + 30.0                    # the coordinator's clock (receipt wait)
        self.run_ids = itertools.count(7001)
        self.events_written = itertools.count(1)

    def make(self, faults: tuple[str, ...] = (), config=None):
        coord = super().make(faults, config)
        if getattr(self, "planner", None) is not None:
            coord.planner = self.planner
        return coord

    def poll(self, apply: bool = True, faults: tuple[str, ...] = ()):
        return IntakePoller(self.coord, faults=faults, planner=self.planner, clock=lambda: self.now).poll(apply=apply)

    def request_plan(self, number: int, actor: str = OPERATOR) -> dict[str, Any]:
        return self.tracker.label_as(number, pd.LABEL_PLAN, actor)

    def approve_label(self, number: int, actor: str = OPERATOR, *, action: bool = True, **kwargs: Any) -> dict:
        event = self.tracker.label_as(number, pd.LABEL_APPROVE, actor)
        if action:
            assert self.action(number, pd.LABEL_APPROVE, actor, **kwargs) == 0
        return event

    def action(self, number: int, label: str, actor: str, *, snapshot=None, run: dict | None = None) -> int:
        """What the workflow does for this label event: the real script, the mock as GitHub."""
        remote = snapshot or self.remote(number)
        payload = {"action": "labeled", "label": {"name": label},
                   "issue": {"number": remote.number, "id": remote.id, "node_id": remote.node_id, "title": remote.title,
                             "body": remote.body, "state": remote.state},
                   "sender": {"login": actor},
                   "repository": {"full_name": REPO_NAME, "id": self.tracker.repository_id,
                                  "owner": {"login": OPERATOR}}}
        path = self.state / f"event-{next(self.events_written)}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        run_id = next(self.run_ids)
        self.tracker.add_workflow_run({"id": run_id, "event": "issues", "path": WORKFLOW, "head_branch": "main",
                                       "actor": actor, "triggering_actor": actor, "repository": REPO_NAME,
                                       "run_attempt": 1, **(run or {})})
        env = {"GITHUB_EVENT_PATH": str(path), "GITHUB_RUN_ID": str(run_id), "GITHUB_RUN_ATTEMPT": "1",
               "WORKER_PLAN_AUTHORS": self.tracker.viewer, "WORKER_MIN_PERMISSION": "write"}
        return ACTION.run(env, MockApi(self.tracker), out=self.out.append)

    def plan_record(self, number: int):
        return load_plan(self.store, feature_id_for(number))

    def labels(self, number: int) -> set[str]:
        return set(self.remote(number).labels)

    def bot_comments(self, number: int) -> list[str]:
        return [c.body for c in self.tracker.list_comments(number) if c.author == BOT]

    def plan_comments(self, number: int) -> list[Any]:
        return [c for c in self.tracker.list_comments(number) if pd.decode_marker(pd.PLAN_MARKER, c.body)]

    def published(self, number: int, planner_result: Any = None) -> Any:
        """Issue → plan request → poll: plan v1 published."""
        if planner_result is not None:
            self.planner.results = [planner_result]
        self.request_plan(number)
        report = self.poll()
        assert report.ok and report.plans, report.lines()
        return report

    def forge(self, number: int, change) -> None:
        """Post a receipt as github-actions[bot] built by the Action, then altered by *change*."""
        receipt_bodies = self.bot_comments(number)
        payload = pd.decode_marker(pd.RECEIPT_MARKER, receipt_bodies[-1])
        assert payload is not None
        change(payload)
        self.tracker.add_comment_as(number, "forged\n" + pd.encode_marker(pd.RECEIPT_MARKER, payload), BOT)


@pytest.fixture
def prig(repo: Path, state_dir: Path) -> PlanRig:
    return PlanRig(repo, state_dir)


def multi_rig(repo: Path, state_dir: Path) -> PlanRig:
    rig = PlanRig(repo, state_dir, planner=FakePlanner(MULTI))
    for role, edit in EDITS.items():
        rig.runners[role].script.update({f"issue-1.{t['id']}": [edit] for t in MULTI["tasks"] if t["role"] == role})
    return rig


# -- 1. single-role planning ---------------------------------------------------------------------------------

def test_a_simple_issue_is_planned_as_one_task_and_published_for_review(prig: PlanRig) -> None:
    number = prig.issue()
    report = prig.published(number)
    feature_id = feature_id_for(number)
    assert [p["version"] for p in report.plans] == [1] and report.plans[0]["tasks"] == ["cli-export (cli)"]
    record = prig.plan_record(number)
    assert record is not None and record.status == "published" and len(record.versions) == 1
    plan = record.latest["plan"]
    assert plan["schema"] == pd.PLAN_SCHEMA and plan["version"] == 1 and plan["repository"] == REPO_NAME.lower()
    assert plan["issue"] == {"number": number, "id": prig.remote(number).id, "node_id": prig.remote(number).node_id}
    assert plan["spec_sha256"] == spec_digest(TITLE, BODY)
    assert [(t["id"], t["role"], t["gates"]) for t in plan["tasks"]] == [
        ("cli-export", "cli", ["cli-lint", "docs-check"])]  # the role's intake gates are always required
    assert plan["integration_gates"] == list(DEFAULT_INTEGRATION_GATES)
    # published: one readable comment carrying exactly the recorded plan, labels updated
    comments = prig.plan_comments(number)
    assert len(comments) == 1 and pd.decode_marker(pd.PLAN_MARKER, comments[0].body) == plan
    text = comments[0].body
    for needle in ("Execution plan v1", "`cli-export`", "| cli |", "`cli-lint`", "one CSV line per item",
                   "`worker:approve`", "Integration gates", record.latest["sha256"]):
        assert needle in text
    assert prig.labels(number) == {pd.LABEL_READY}
    # planning wrote nothing else: no feature, task, intake record or claim
    assert not prig.store.list_features() and not prig.store.list_records("intake") and not prig.store.list_claims()
    assert load_intake(prig.store, feature_id) is None
    # the planner saw the Issue as data, the roles and their policies, and the schema of the answer
    request = prig.planner.requests[0]
    assert TITLE in request.prompt and "it prints one CSV line per item" in request.prompt
    assert "ISSUE_BODY" in request.prompt and "`cli`: owns cli/**" in request.prompt and "automation" in request.prompt
    assert request.schema["properties"]["tasks"]["items"]["properties"]["role"]["enum"] == \
        ["backend", "cli", "coordination", "frontend"]


# -- 2. multi-role planning, 3. dependencies, 12. nothing before approval ----------------------------------

def test_a_complex_issue_becomes_one_feature_with_dependent_role_tasks(repo: Path, state_dir: Path) -> None:
    rig = multi_rig(repo, state_dir)
    number = rig.issue(title="Category export across the stack", body="Backend API, UI, CLI and docs.")
    assert number == 1
    rig.published(number)
    plan = rig.plan_record(number).latest["plan"]
    assert [(t["id"], t["role"], t["depends_on"]) for t in plan["tasks"]] == [
        ("api", "backend", []), ("ui", "frontend", ["api"]), ("cli", "cli", ["api"]),
        ("docs", "coordination", ["api", "cli", "ui"])]
    assert plan["tasks"][0]["gates"] == ["backend-tests", "backend-lint", "docs-check"]
    assert not rig.store.list_features()
    rig.approve_label(number)
    report = rig.poll()
    feature_id = feature_id_for(number)
    assert report.ok and [a["feature"] for a in report.approved] == [feature_id]
    assert report.planned == [feature_id] and report.activated == [feature_id]
    feature = rig.store.load_feature(feature_id)
    assert feature.state is FeatureState.ACTIVE and feature.task_ids == [f"{feature_id}.{t}" for t in
                                                                          ("api", "ui", "cli", "docs")]
    assert feature.acceptance == ("the API returns CSV", "the UI and the CLI use it")
    tasks = rig.store.load_tasks(feature)
    states = {t.split(".")[1]: v.state for t, v in tasks.items()}
    assert states == {"api": TaskState.READY, "ui": TaskState.BLOCKED, "cli": TaskState.BLOCKED,
                      "docs": TaskState.BLOCKED}
    assert tasks[f"{feature_id}.docs"].depends_on == tuple(f"{feature_id}.{d}" for d in ("api", "cli", "ui"))
    api_prompt = tasks[f"{feature_id}.api"].acceptance[0]
    assert "execution plan v1" in api_prompt and "Backend API, UI, CLI and docs." in api_prompt
    # the established lifecycle runs them in dependency order (claims, gates, readiness)
    assert rig.coord.run_task(f"{feature_id}.api").state is TaskState.VERIFIED
    tasks = rig.store.load_tasks(feature)
    assert tasks[f"{feature_id}.ui"].state is TaskState.READY and tasks[f"{feature_id}.cli"].state is TaskState.READY
    assert tasks[f"{feature_id}.docs"].state is TaskState.BLOCKED
    for name in ("ui", "cli", "docs"):
        assert rig.coord.run_task(f"{feature_id}.{name}").state is TaskState.VERIFIED
    assert rig.gates.calls[:3] == ["backend-tests", "backend-lint", "docs-check"]
    # the approved Issue is the FEATURE's: one acknowledgement there, its card shows the feature
    acks = [c for c in rig.own_comments(number) if "Approved for the Worker" in c]
    assert len(acks) == 1 and "execution plan v1" in acks[0] and f"`{feature_id}.docs`" in acks[0]
    assert rig.store.load_feature(feature_id).issue_number == number
    board = rig.coord.board.sync(feature_id, apply=True)
    assert board.ok and "feature's card" in board.skipped[f"{feature_id}.api"]
    assert values(rig.coord, feature_id)["Status"] == "In Progress"
    assert len(rig.tracker.list_issues()) == 1  # no sub-Issue was created


def test_nothing_runs_before_an_explicit_approval(prig: PlanRig) -> None:
    number = prig.issue()
    prig.script(number, "cli")
    prig.request_plan(number)
    service = prig.service(until=lambda s: s.cycles >= 4)
    assert service.run() == 0
    assert prig.plan_record(number).status == "published"          # the service planned and published ...
    assert service.launched == [] and not prig.runners["cli"].requests  # ... and started nothing
    assert not prig.store.list_features() and not prig.store.list_records("intake")
    assert prig.planner.requests[0].heartbeat is not None and prig.planner.requests[0].cancelled is not None
    prig.approve_label(number)
    task_id = f"{feature_id_for(number)}.cli-export"
    prig.runners["cli"].script[task_id] = [FileEdit("cli/src/commands.py", "COMMANDS = ['x']\n")]
    service = prig.service(until=lambda s: prig.store.list_features() and
                           prig.store.load_task(task_id).state is TaskState.VERIFIED)
    assert service.run() == 0
    assert service.launched == [(task_id, 1)]
    kinds = [e["kind"] for e in prig.store.events()]
    assert kinds.index("plan-published") < kinds.index("intake-approved") < kinds.index("worker-agent-started")


@pytest.mark.parametrize("deps, problem", [
    ({"b": ["a"], "a": ["b"]}, "cycle"),
    ({"a": ["a"]}, "depends on itself"),
    ({"a": ["zzz"]}, "unknown tasks"),
])
def test_invalid_dependencies_are_refused(deps: dict, problem: str) -> None:
    proposal = {"feature": SINGLE["feature"], "attention": None,
                "tasks": [task("a", "backend", ["backend/src/app.py"], depends=tuple(deps.get("a", ()))),
                          task("b", "cli", ["cli/src/commands.py"], depends=tuple(deps.get("b", ())))]}
    outcome = _validate(proposal)
    assert outcome.plan is None and any(problem in p for p in outcome.problems), outcome.problems


# -- 4. roles and protected paths ----------------------------------------------------------------------------

def _validate(proposal: Any, config=None):
    return canonical_plan(proposal, config=config or planning_config(), repository=REPO_NAME,
                          issue={"number": 7, "id": 900007, "node_id": "I_mock7"}, spec_sha="0" * 64, version=1)


@pytest.mark.parametrize("change, problem", [
    (lambda p: p["tasks"][0].update(role="automation"), "role 'automation' is disabled"),
    (lambda p: p["tasks"][0].update(role="full-project"), "is unknown"),
    (lambda p: p["tasks"][0].update(paths=["content/en/items.json"]), "is protected"),
    (lambda p: p["tasks"][0].update(paths=[".github/workflows/x.yml"]), "approval-required"),
    (lambda p: p["tasks"][0].update(paths=["frontend/src/view.ts"]), "outside the cli role's owned paths"),
    (lambda p: p["tasks"][0].update(role="backend", paths=["backend/data/x.json"]), "protected"),
    (lambda p: p["tasks"][0].update(paths=["cli/src/*.py"]), "no `..`, no wildcards"),
    (lambda p: p["tasks"][0].update(paths=["cli/../backend/x.py"]), "no `..`"),
    (lambda p: p["tasks"][0].update(grants=[".github/**"]), "not allowed"),
    (lambda p: p["tasks"][0].update(gates=["deploy"]), "unknown gates"),
    (lambda p: p.update(tasks=[task("a", "cli", ["cli/x.py"]), task("b", "cli", ["cli/y.py"])]), "one task per role"),
    (lambda p: p.update(tasks=[]), "1..4 tasks"),
    (lambda p: p["tasks"][0].update(id="Bad_ID"), "must match"),
    (lambda p: p["tasks"][0].update(instructions="x" * 7000), "at most 6000"),
    (lambda p: p.update(extra=True), "not allowed"),
])
def test_invalid_roles_paths_and_permission_expansions_are_refused(change, problem: str) -> None:
    proposal = copy.deepcopy(SINGLE)
    change(proposal)
    outcome = _validate(proposal)
    assert outcome.plan is None and any(problem in p for p in outcome.problems), outcome.problems


def test_required_gates_cannot_be_removed_and_extra_gates_keep_configuration_order() -> None:
    proposal = copy.deepcopy(SINGLE)
    proposal["tasks"][0]["gates"] = ["coordinator-tests", "cli-lint"]
    plan = _validate(proposal).plan
    assert plan is not None and plan["tasks"][0]["gates"] == ["cli-lint", "docs-check", "coordinator-tests"]
    assert path_problem(planning_config(), "backend", "backend/src/**") is None
    assert INTEGRATION_GATES == DEFAULT_INTEGRATION_GATES


def test_an_unassignable_issue_needs_an_operator_and_the_refusal_is_fed_back(prig: PlanRig) -> None:
    bad = copy.deepcopy(SINGLE)
    bad["tasks"][0]["paths"] = ["content/en/items.json"]
    prig.planner.results = [bad, bad]
    number = prig.issue()
    prig.request_plan(number)
    report = prig.poll()
    assert not report.plans and len(prig.planner.requests) == 2      # max_attempts, with feedback
    assert "is protected" in prig.planner.requests[1].prompt and "REFUSED" in prig.planner.requests[1].prompt
    record = prig.plan_record(number)
    assert record.status == "attention" and "is protected" in record.attention["reason"] and not record.versions
    notices = [c for c in prig.own_comments(number) if "Plan needs an operator" in c]
    assert len(notices) == 1 and pd.LABEL_PLAN not in prig.labels(number)
    prig.poll()
    assert len(prig.planner.requests) == 2                            # answered: not planned again
    prig.planner.results = [{"attention": "needs the disabled automation role", "feature": SINGLE["feature"],
                             "tasks": []}]
    prig.request_plan(number)                                         # a new request: planned again
    prig.poll()
    assert "automation" in prig.plan_record(number).attention["reason"] and len(prig.planner.requests) == 3


# -- 5. deterministic digests ----------------------------------------------------------------------------------

def test_plan_and_approval_digests_are_deterministic_and_bind_every_field() -> None:
    plan = _validate(copy.deepcopy(MULTI)).plan
    assert plan is not None
    shuffled = json.loads(json.dumps(plan), object_pairs_hook=lambda pairs: dict(reversed(pairs)))
    assert list(shuffled) != list(plan) and pd.plan_sha256(shuffled) == pd.plan_sha256(plan)
    assert pd.canonical_json(plan) == pd.canonical_json(shuffled)
    repo, issue = {"full_name": REPO_NAME, "id": 4242}, {"number": 7, "id": 900007, "node_id": "I_mock7"}
    base = pd.approval_sha256(pd.approval_statement(repository=repo, issue=issue, title=TITLE, body=BODY, plan=plan))
    assert base == pd.approval_sha256(pd.approval_statement(repository={"full_name": REPO_NAME.upper(), "id": 4242},
                                                            issue=issue, title=f"  {TITLE} ",
                                                            body=BODY.replace("\r\n", "\n"), plan=shuffled))

    def digest(**changes: Any) -> str:
        args = {"repository": repo, "issue": issue, "title": TITLE, "body": BODY, "plan": plan, **changes}
        return pd.approval_sha256(pd.approval_statement(**args))

    def replanned(mutate) -> dict:
        other = copy.deepcopy(plan)
        mutate(other)
        return other
    variants = [digest(title=TITLE + "!"), digest(body=BODY + "x"), digest(issue={**issue, "id": 1}),
                digest(repository={**repo, "id": 1}), digest(plan=replanned(lambda p: p.update(version=2))),
                digest(plan=replanned(lambda p: p["tasks"][1].update(role="cli"))),
                digest(plan=replanned(lambda p: p["tasks"][1].update(depends_on=[]))),
                digest(plan=replanned(lambda p: p["tasks"][0]["gates"].pop())),
                digest(plan=replanned(lambda p: p["tasks"][0].update(instructions="other"))),
                digest(plan=replanned(lambda p: p.update(integration_gates=["docs-check"])))]
    assert len({base, *variants}) == len(variants) + 1
    assert pd.spec_sha256(TITLE, BODY) == spec_digest(TITLE, BODY)
    for text in (BODY, " a \r\n\r\n b\t \n", "", "x\r\ny  "):
        assert pd.normalize_text(text) == normalize_body(text)
    with pytest.raises(ValueError, match="floats"):
        pd.canonical_json({"a": 1.5})


def test_markers_carry_the_exact_object_and_detect_damage() -> None:
    plan = _validate(copy.deepcopy(SINGLE)).plan
    marker = pd.encode_marker(pd.PLAN_MARKER, plan)
    assert pd.decode_marker(pd.PLAN_MARKER, f"text\n{marker}\nmore") == plan
    assert pd.decode_marker(pd.RECEIPT_MARKER, marker) is None
    assert pd.decode_marker(pd.PLAN_MARKER, marker + "\n" + marker) is None              # ambiguous
    sha = marker.split("sha256=")[1][:64]
    assert pd.decode_marker(pd.PLAN_MARKER, marker.replace(sha, "0" * 64)) is None       # digest
    data = marker.split("data=")[1][:-4]
    tampered = json.loads(__import__("base64").b64decode(data))
    tampered["version"] = 9
    forged = marker.replace(data, __import__("base64").b64encode(json.dumps(tampered).encode()).decode())
    assert pd.decode_marker(pd.PLAN_MARKER, forged) is None                              # sha / canonical form


# -- 6. approval of the exact plan version ---------------------------------------------------------------------

def test_the_label_approves_exactly_the_published_plan_version(prig: PlanRig) -> None:
    number = prig.issue()
    prig.published(number)
    event = prig.approve_label(number)
    receipts = prig.bot_comments(number)
    assert len(receipts) == 1 and "Approval receipt" in receipts[0]
    payload = pd.decode_marker(pd.RECEIPT_MARKER, receipts[0])
    assert payload["label_event"] == {"id": event["id"], "actor": OPERATOR, "created_at": event["created_at"]}
    report = prig.poll()
    feature_id = feature_id_for(number)
    record = load_intake(prig.store, feature_id)
    plan_record = prig.plan_record(number)
    latest = plan_record.latest
    remote = prig.remote(number)
    statement = pd.approval_statement(repository={"full_name": REPO_NAME, "id": prig.tracker.repository_id},
                                      issue={"number": number, "id": remote.id, "node_id": remote.node_id},
                                      title=remote.title, body=remote.body, plan=latest["plan"])
    assert record is not None and record.planned and record.status == "active"
    assert record.plan["version"] == 1 and record.plan["sha256"] == latest["sha256"]
    assert record.plan["approval_sha256"] == pd.approval_sha256(statement) == payload["approval"]["sha256"]
    assert record.plan["canonical"] == latest["plan"] and record.anchor == feature_id
    assert record.task_ids == [f"{feature_id}.cli-export"] and record.digest == spec_digest(TITLE, BODY)
    assert record.plan["evidence"]["label_event_id"] == event["id"] and record.approved_by == OPERATOR
    assert plan_record.status == "approved" and report.activated == [feature_id]
    assert prig.labels(number) == {pd.LABEL_APPROVED}
    assert json.loads((prig.state / "intake" / f"{feature_id}.json").read_text())["schema_version"] == 2


# -- 7. modification after planning ----------------------------------------------------------------------------

def test_an_issue_edited_after_planning_is_refused_by_the_action(prig: PlanRig) -> None:
    number = prig.issue()
    prig.published(number)
    prig.tracker.tamper(number, body=BODY + "\nAlso delete the database.\n")
    prig.approve_label(number)
    assert "changed after plan v1" in prig.bot_comments(number)[0] and pd.LABEL_APPROVE not in prig.labels(number)
    prig.poll()
    assert load_intake(prig.store, feature_id_for(number)) is None and not prig.store.list_features()
    assert prig.plan_record(number).status == "stale"   # and the plan is marked stale for review
    assert pd.LABEL_READY not in prig.labels(number)


def test_an_issue_edited_after_the_receipt_is_refused_by_the_coordinator(prig: PlanRig) -> None:
    number = prig.issue()
    prig.published(number)
    prig.approve_label(number)                                   # the Action saw the planned text
    prig.tracker.tamper(number, title=TITLE + " and more")       # edited before the Worker polled
    report = prig.poll()
    assert any("changed after plan v1" in r["reason"] for r in report.rejected)
    assert any("approval digest does not match" in r["reason"] for r in report.rejected)
    assert load_intake(prig.store, feature_id_for(number)) is None and not prig.store.list_features()
    notices = [c for c in prig.own_comments(number) if "Approval not accepted" in c]
    assert len(notices) == 1 and pd.LABEL_APPROVE not in prig.labels(number)


def test_a_revised_plan_invalidates_an_earlier_approval(prig: PlanRig) -> None:
    number = prig.issue()
    prig.published(number)
    prig.approve_label(number)                                   # receipt for v1 ...
    prig.request_plan(number)                                    # ... then a revision is requested
    report = prig.poll()
    assert any("revised plan was requested" in r["reason"] for r in report.rejected)
    assert load_intake(prig.store, feature_id_for(number)) is None
    record = prig.plan_record(number)
    assert [v["version"] for v in record.versions] == [1, 2] and record.status == "published"
    assert prig.labels(number) == {pd.LABEL_READY}
    prig.approve_label(number)                                   # approving v2 works
    prig.poll()
    approved = load_intake(prig.store, feature_id_for(number))
    assert approved is not None and approved.plan["version"] == 2


def test_a_tampered_plan_comment_is_refused(prig: PlanRig) -> None:
    number = prig.issue()
    prig.published(number)
    prig.approve_label(number)
    comment = prig.plan_comments(number)[0]
    rec = prig.tracker._record(number)  # an edit of the published comment (same author, same ID)
    for item in rec["comments"]:
        if item["id"] == comment.id:
            item["body"] = item["body"].replace("Execution plan v1", "Execution plan v1 (edited)") + "\n<!-- x -->"
            item["body"] = item["body"].split("<!-- feature-coordinator:plan")[0]
    prig.tracker._save()
    report = prig.poll()
    assert any("no longer shows the recorded plan" in r["reason"] for r in report.rejected)
    assert load_intake(prig.store, feature_id_for(number)) is None


# -- 8. duplicate events and repeated approvals -------------------------------------------------------------------

def test_duplicate_events_repeated_polls_and_repeated_approvals_are_idempotent(prig: PlanRig) -> None:
    number = prig.issue()
    prig.published(number)
    event = prig.approve_label(number)
    snapshot = prig.remote(number)
    assert prig.action(number, pd.LABEL_APPROVE, OPERATOR, snapshot=snapshot) == 0   # a re-run / duplicate event
    assert len(prig.bot_comments(number)) == 1
    del event
    first = prig.poll()
    assert len(first.approved) == 1
    for _ in range(3):
        again = prig.poll()
        assert not again.approved and not again.plans and not again.rejected
    writes = prig.tracker.writes
    prig.poll()
    assert prig.tracker.writes == writes                                            # nothing new: no write
    assert len(prig.plan_comments(number)) == 1 and len(prig.store.list_records("intake")) == 1
    assert len([c for c in prig.own_comments(number) if "Approved for the Worker" in c]) == 1
    prig.approve_label(number)                                                       # approving again
    report = prig.poll()
    assert report.duplicates and pd.LABEL_APPROVE not in prig.labels(number)
    prig.request_plan(number)                                                        # re-planning an approved Issue
    report = prig.poll()
    assert report.duplicates and len(prig.planner.requests) == 1
    prig.poll()
    notices = [c for c in prig.own_comments(number) if "ignored" in c]
    assert len(notices) == 2 and len(prig.store.list_features()) == 1


def test_a_label_without_a_receipt_waits_then_is_refused(prig: PlanRig) -> None:
    number = prig.issue()
    prig.published(number)
    prig.approve_label(number, action=False)        # the workflow is not active (e.g. not on main yet)
    report = prig.poll()
    assert any("waiting for the approval receipt" in w for w in report.waiting) and not report.rejected
    prig.now += prig.config.github.planning.receipt_wait_seconds + 3600
    report = prig.poll()
    assert any("no approval receipt" in r["reason"] for r in report.rejected)
    assert pd.LABEL_APPROVE not in prig.labels(number) and load_intake(prig.store, feature_id_for(number)) is None


# -- 9. recovery after interruption --------------------------------------------------------------------------------

@pytest.mark.parametrize("fault", ["crash-after-plan-recorded", "crash-after-plan-comment"])
def test_an_interrupted_planning_pass_publishes_exactly_one_plan(prig: PlanRig, fault: str) -> None:
    number = prig.issue()
    prig.request_plan(number)
    with pytest.raises(SimulatedCrash):
        prig.poll(faults=(f"{fault}:{number}",))
    prig.coord = prig.make()                           # a restarted service
    report = prig.poll()
    assert report.ok
    record = prig.plan_record(number)
    assert record.status == "published" and [v["version"] for v in record.versions] == [1]
    assert len(prig.plan_comments(number)) == 1 and len(prig.planner.requests) == 1
    assert prig.labels(number) == {pd.LABEL_READY}


def test_an_interrupted_approval_is_completed_once(prig: PlanRig) -> None:
    number = prig.issue()
    prig.published(number)
    prig.approve_label(number)
    with pytest.raises(SimulatedCrash):
        prig.poll(faults=(f"crash-after-plan-approval:{number}",))
    prig.coord = prig.make()
    report = prig.poll()
    feature_id = feature_id_for(number)
    assert report.planned == [feature_id] and prig.labels(number) == {pd.LABEL_APPROVED}
    assert len(prig.store.list_records("intake")) == 1 and not report.rejected


def test_a_planner_run_killed_mid_way_is_retried_within_its_attempt_budget(repo: Path, state_dir: Path) -> None:
    def crash(_request: PlannerRequest) -> PlannerResult:
        raise SimulatedCrash("the Worker died while Claude Code planned")
    rig = PlanRig(repo, state_dir, planner=FakePlanner(crash, SINGLE))
    number = rig.issue()
    rig.request_plan(number)
    with pytest.raises(SimulatedCrash):
        rig.poll()
    assert rig.plan_record(number).attempts == 1      # counted before the run (write-ahead)
    rig.coord = rig.make()
    assert rig.poll().plans and rig.plan_record(number).status == "published"
    assert len(rig.planner.requests) == 2 and rig.plan_record(number).attempts == 0


def test_a_crash_loop_cannot_exceed_the_attempt_budget(repo: Path, state_dir: Path) -> None:
    def crash(_request: PlannerRequest) -> PlannerResult:
        raise SimulatedCrash("killed")
    rig = PlanRig(repo, state_dir, config=planning_config(max_attempts=2), planner=FakePlanner(crash))
    number = rig.issue()
    rig.request_plan(number)
    for _ in range(2):
        with pytest.raises(SimulatedCrash):
            rig.poll()
        rig.coord = rig.make()
    rig.poll()
    assert len(rig.planner.requests) == 2 and rig.plan_record(number).status == "attention"


def test_an_unavailable_planner_defers_without_using_an_attempt(prig: PlanRig) -> None:
    prig.planner.results = [PlannerResult("unavailable", error="Claude Code is using API-key authentication")]
    number = prig.issue()
    prig.request_plan(number)
    report = prig.poll()
    assert not report.ok and "API-key" in report.deferred[0]
    assert prig.plan_record(number).attempts == 0 and prig.plan_record(number).status == "planning"
    prig.planner.results = [SINGLE]
    assert prig.poll().plans


# -- 10. backward compatibility ----------------------------------------------------------------------------------------

def test_legacy_worker_approve_comments_still_work_and_keep_schema_one(prig: PlanRig) -> None:
    number = prig.issue()
    prig.approve(number, "cli")
    report = prig.poll()
    feature_id = feature_id_for(number)
    assert report.activated == [feature_id]
    record = load_intake(prig.store, feature_id)
    assert not record.planned and not record.task_ids and record.all_task_ids == [f"{feature_id}.cli"]
    raw = json.loads((prig.state / "intake" / f"{feature_id}.json").read_text())
    assert raw["schema_version"] == 1 and not {"task_ids", "anchor", "plan", "integration_gates"} & set(raw)
    assert prig.store.load_feature(feature_id).task_ids == [f"{feature_id}.cli"]
    assert not prig.planner.requests and not prig.plan_comments(number)
    prig.request_plan(number)                       # a plan request for a legacy-approved Issue is ignored
    assert prig.poll().duplicates and not prig.planner.requests


def test_a_schema_one_record_written_by_the_previous_version_still_loads() -> None:
    legacy = {"schema_version": 1, "id": "issue-3", "issue_number": 3, "issue_id": 900003, "node_id": "I_mock900003",
              "url": "mock://issues/3", "issue_author": "someone", "title": TITLE, "body": "b", "digest": "0" * 64,
              "role": "cli", "gates": ["cli-lint"], "task_id": "issue-3.cli", "approved_by": OPERATOR,
              "approval_comment_id": 1001, "approval_comment_url": "", "approved_at": "2026-01-01T00:00:01Z",
              "recorded_at": "2026-01-01T00:00:01Z", "status": "active", "spec_changed": None,
              "activation_error": "", "completed": None, "revision": 3}
    record = IntakeRecord.from_dict(legacy)
    assert record.anchor_id == "issue-3.cli" and record.all_task_ids == ["issue-3.cli"]
    assert record.to_dict() == legacy                 # rewritten byte-compatible: a rollback still reads it
    with pytest.raises(Exception, match="unknown fields"):
        IntakeRecord.from_dict({**legacy, "plan": None})


def test_planning_is_off_by_default_and_the_labels_are_then_ignored(repo: Path, state_dir: Path) -> None:
    rig = PlanRig(repo, state_dir, config=intake_config())
    number = rig.issue()
    rig.request_plan(number)
    report = rig.poll()
    assert report.ok and not rig.planner.requests and rig.plan_record(number) is None
    assert default_config().github.planning == GitHubPlanningConfig() and not GitHubPlanningConfig().enabled
    data = yaml.safe_load(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
    assert config_from_mapping(data).github.planning == GitHubPlanningConfig()


@pytest.mark.parametrize("change, message", [
    ({"receipt_author": "spyderbg"}, "bot login"),
    ({"provider": "codex"}, "must be a Claude Code provider"),
    ({"max_tasks": 9}, "max_tasks"),
    ({"receipt_workflow": "scripts/x.yml"}, ".github/workflows"),
    ({"max_attempts": 0}, "max_attempts"),
])
def test_the_planning_configuration_is_validated(change: dict, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        planning_config(**change)
    config = default_config()
    with pytest.raises(ConfigError, match="needs github.intake.enabled"):
        dataclasses.replace(config, github=dataclasses.replace(config.github, planning=dataclasses.replace(
            config.github.planning, enabled=True)))
    data = yaml.safe_load(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
    data["github"]["planning"]["api_key"] = "x"
    with pytest.raises(ConfigError, match="unknown github.planning keys"):
        config_from_mapping(data)


# -- 11. forged or unauthorized evidence ------------------------------------------------------------------------------

def _repo_change(payload: dict) -> None:
    payload["repository"]["id"] = 1


@pytest.mark.parametrize("change, reason", [
    (lambda p: p["approval"].update(sha256="f" * 64), "approval digest does not match"),
    (lambda p: p["plan"].update(version=2), "another plan version"),
    (_repo_change, "another repository"),
    (lambda p: p["issue"].update(id=1), "another Issue"),
    (lambda p: p["label_event"].update(actor="intruder"), "label event differs"),
    (lambda p: p["workflow_run"].update(id=99999), "does not exist"),
])
def test_forged_receipts_are_refused(prig: PlanRig, change, reason: str) -> None:
    number = prig.issue()
    prig.published(number)
    prig.approve_label(number)
    prig.tracker.unlabel_as(number, pd.LABEL_APPROVE, OPERATOR)  # a fresh approval, answered only by the forgery
    event = prig.tracker.label_as(number, pd.LABEL_APPROVE, OPERATOR)

    def forged(payload: dict) -> None:
        payload["label_event"].update(id=event["id"], created_at=event["created_at"])
        change(payload)
    prig.forge(number, forged)
    report = prig.poll()
    assert any(reason in r["reason"] for r in report.rejected), report.rejected
    assert load_intake(prig.store, feature_id_for(number)) is None and not prig.store.list_features()


@pytest.mark.parametrize("run, reason", [
    ({"path": ".github/workflows/other.yml"}, "not '.github/workflows/worker-issue-labels.yml'"),
    ({"event": "push"}, "not an Issue event"),
    ({"head_branch": "feature/evil"}, "default branch"),
    ({"actor": "someone-else"}, "not the approver"),
])
def test_a_receipt_from_another_workflow_run_is_refused(prig: PlanRig, run: dict, reason: str) -> None:
    number = prig.issue()
    prig.published(number)
    prig.approve_label(number, run=run)
    report = prig.poll()
    assert any(reason in r["reason"] for r in report.rejected), report.rejected
    assert load_intake(prig.store, feature_id_for(number)) is None


def test_receipts_from_people_and_duplicate_receipts_are_never_accepted(prig: PlanRig) -> None:
    number = prig.issue()
    prig.published(number)
    event = prig.approve_label(number, action=False)
    prig.action(number, pd.LABEL_APPROVE, OPERATOR)
    body = prig.bot_comments(number)[0]
    rec = prig.tracker._record(number)  # the same receipt text, but posted by a person (the shared identity)
    rec["comments"] = [c for c in rec["comments"] if c["author"] != BOT]
    prig.tracker._save()
    prig.tracker.add_comment_as(number, body, OPERATOR)
    assert any("waiting for the approval receipt" in w for w in prig.poll().waiting)
    prig.tracker.add_comment_as(number, body, BOT)
    prig.tracker.add_comment_as(number, body, BOT)
    report = prig.poll()
    assert any("more than one approval receipt" in r["reason"] for r in report.rejected)
    assert load_intake(prig.store, feature_id_for(number)) is None and event["id"] in prig.plan_record(number).rejected


@pytest.mark.parametrize("who, permission", [("stranger", "write"), (OPERATOR, "read")])
def test_unauthorized_requests_and_approvals_are_refused(prig: PlanRig, who: str, permission: str) -> None:
    prig.tracker.permissions[who] = permission
    number = prig.issue()
    prig.request_plan(number, actor=who)
    assert prig.action(number, pd.LABEL_PLAN, who) == 0
    if permission == "read":                       # the Action refuses below `write` and removes the label
        assert "has repository permission `read`" in prig.bot_comments(number)[0]
        assert pd.LABEL_PLAN not in prig.labels(number)
    else:                                          # write access but no command author: the coordinator refuses
        report = prig.poll()
        assert report.ignored and "not a configured command author" in report.ignored[0]["reason"]
        assert pd.LABEL_PLAN not in prig.labels(number)
    assert not prig.planner.requests
    assert prig.plan_record(number) is None or not prig.plan_record(number).versions
    prig.tracker.permissions[OPERATOR] = "admin"
    prig.published(number)                         # the operator plans it
    prig.tracker.permissions[who] = permission
    prig.approve_label(number, actor=who, action=False)
    prig.action(number, pd.LABEL_APPROVE, who)
    if permission != "read":
        report = prig.poll()
        assert any("may not approve" in r["reason"] for r in report.rejected)
    assert load_intake(prig.store, feature_id_for(number)) is None


def test_an_approval_label_older_than_the_plan_is_refused(prig: PlanRig) -> None:
    number = prig.issue()
    event = prig.tracker.label_as(number, pd.LABEL_APPROVE, OPERATOR)   # approved before any plan existed
    prig.published(number)                                                # publication removes it ...
    assert pd.LABEL_APPROVE not in prig.labels(number)
    rec = prig.tracker._record(number)                                    # ... a forger puts it back silently
    rec["labels"].append(pd.LABEL_APPROVE)
    rec["events"] = [e for e in rec["events"] if not (e["event"] == "unlabeled" and e["label"] == pd.LABEL_APPROVE)]
    rec["events"] = [e for e in rec["events"] if e["id"] == event["id"] or e["label"] != pd.LABEL_APPROVE]
    prig.tracker._save()
    plan = prig.plan_record(number).latest
    remote = prig.remote(number)
    repo = {"full_name": REPO_NAME.lower(), "id": prig.tracker.repository_id}
    statement = pd.approval_statement(repository=repo, issue={"number": number, "id": remote.id,
                                                              "node_id": remote.node_id},
                                      title=remote.title, body=remote.body, plan=plan["plan"])
    prig.tracker.add_workflow_run({"id": 6001, "event": "issues", "path": WORKFLOW, "head_branch": "main",
                                   "actor": OPERATOR, "repository": REPO_NAME})
    receipt = {"format": pd.RECEIPT_FORMAT, "repository": repo,
               "issue": {"number": number, "id": remote.id, "node_id": remote.node_id},
               "label_event": {"id": event["id"], "actor": OPERATOR, "created_at": event["created_at"]},
               "actor_permission": "admin",
               "plan": {"comment_id": plan["comment_id"], "version": 1, "sha256": plan["sha256"]},
               "spec_sha256": plan["spec_sha256"],
               "approval": {"format": pd.APPROVAL_FORMAT, "sha256": pd.approval_sha256(statement)},
               "workflow_run": {"id": 6001, "attempt": 1}}
    prig.tracker.add_comment_as(number, pd.encode_marker(pd.RECEIPT_MARKER, receipt), BOT)
    report = prig.poll()
    assert any("before plan v1 was published" in r["reason"] for r in report.rejected), report.rejected
    assert load_intake(prig.store, feature_id_for(number)) is None


# -- the Action, the workflow file, the planner command line ----------------------------------------------------

def test_the_action_refuses_an_approval_without_a_plan_and_answers_each_event_once(prig: PlanRig) -> None:
    number = prig.issue()
    prig.approve_label(number, action=False)
    assert prig.action(number, pd.LABEL_APPROVE, OPERATOR) == 0
    assert prig.action(number, pd.LABEL_APPROVE, OPERATOR) == 0
    bodies = prig.bot_comments(number)
    assert len(bodies) == 1 and "no published plan" in bodies[0]
    refusal = pd.decode_marker(pd.REJECTION_MARKER, bodies[0])
    assert refusal["label_event_id"] and pd.LABEL_APPROVE not in prig.labels(number)
    assert prig.action(number, "worker:approved", OPERATOR) == 0 and len(prig.bot_comments(number)) == 1


def test_the_workflow_is_minimal_pinned_and_never_interpolates_issue_text() -> None:
    text = WORKFLOW_FILE.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    assert data[True] == {"issues": {"types": ["labeled"]}}  # `on:` parses as True in YAML 1.1
    assert data["permissions"] == {}
    job = data["jobs"]["label"]
    assert job["permissions"] == {"contents": "read", "issues": "write"}
    assert "worker:plan" in job["if"] and "worker:approve" in job["if"] and "worker:approved" not in job["if"]
    steps = job["steps"]
    assert steps[0]["uses"] == "actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683"
    assert steps[0]["with"]["persist-credentials"] is False
    assert steps[1]["run"] == "python3 -I .github/scripts/worker_issue_labels.py"
    assert "github.event" not in steps[1]["run"] and "secrets." not in text
    assert "claude" not in text.lower().replace("claude code", "") and "codex" not in text.lower()


def test_the_action_needs_only_the_standard_library_and_the_digest_module() -> None:
    source = (REPO_ROOT / "feature_coordinator/plan_digest.py").read_text(encoding="utf-8")
    imports = {line.split()[1].split(".")[0] for line in source.splitlines() if line.startswith(("import ", "from "))}
    assert imports <= {"__future__", "base64", "binascii", "hashlib", "json", "re", "typing"}
    script = (REPO_ROOT / ".github/scripts/worker_issue_labels.py").read_text(encoding="utf-8")
    assert "from feature_coordinator import plan_digest" in script and "subprocess" not in script


def test_the_planner_runs_claude_code_without_tools_outside_the_checkout(tmp_path: Path) -> None:
    config = planning_config()
    seen: dict[str, Any] = {}

    def fake_run(argv, *, cwd, env, stdout_path, **kwargs):
        seen.update(argv=list(argv), cwd=cwd, env=dict(env), kwargs=kwargs)
        stdout_path.write_text("\n".join(json.dumps(e) for e in (
            {"type": "system", "subtype": "init", "model": "claude-opus-5-5", "apiKeySource": "none"},
            {"type": "result", "subtype": "success", "is_error": False, "result": "done",
             "structured_output": SINGLE})), encoding="utf-8")
        return Supervision("exited", 0, 1.0, 4242)
    probe = RunnerProbe("claude", ProviderKind.CLAUDE_CODE, "claude", "/usr/bin/claude", "2.1.318")
    planner = ClaudeCodePlanner(config, probe=lambda _p: probe,
                                auth_check=lambda *_a, **_k: SubscriptionCheck(True, "claude.ai subscription (pro)"),
                                run=fake_run, environ={"PATH": "/usr/bin", "HOME": str(tmp_path),
                                                       "ANTHROPIC_API_KEY": "sk-ant-xxx", "ANTHROPIC_BASE_URL": "x"})
    result = planner.plan(PlannerRequest(feature_id="issue-1", issue_number=1, prompt="plan it",
                                         schema={"type": "object"}, run_dir=tmp_path / "runs", timeout_seconds=60))
    assert result.status == "ok" and result.proposal == SINGLE
    argv = seen["argv"]
    assert argv[argv.index("--tools") + 1] == "" and argv[argv.index("--permission-mode") + 1] == "dontAsk"
    for flag in ("--restricted", "--strict-mcp-config", "--no-session-persistence", "--disable-slash-commands",
                 "--json-schema"):
        assert flag in argv
    assert argv[argv.index("--model") + 1] == "opus"
    assert "ANTHROPIC_API_KEY" not in seen["env"] and "ANTHROPIC_BASE_URL" not in seen["env"]
    assert seen["cwd"].name == "empty" and not any(seen["cwd"].iterdir()) and REPO_ROOT not in seen["cwd"].parents
    # an API-key login is refused BEFORE any request; an API key seen afterwards fails the run
    refused = ClaudeCodePlanner(config, probe=lambda _p: probe, run=fake_run,
                                auth_check=lambda *_a, **_k: SubscriptionCheck(False, "API key", "API key in use"))
    seen.clear()
    assert refused.plan(PlannerRequest("issue-1", 1, "p", {}, tmp_path / "r2", 60)).status == "unavailable"
    assert not seen

    def api_key_run(argv, *, stdout_path, **kwargs):
        del argv, kwargs
        stdout_path.write_text(json.dumps({"type": "system", "subtype": "init", "model": "claude-opus-5-5",
                                           "apiKeySource": "ANTHROPIC_API_KEY"}), encoding="utf-8")
        return Supervision("exited", 0, 1.0, 1)
    keyed = ClaudeCodePlanner(config, probe=lambda _p: probe, run=api_key_run,
                              auth_check=lambda *_a, **_k: SubscriptionCheck(True, "claude.ai subscription"))
    outcome = keyed.plan(PlannerRequest("issue-1", 1, "p", {}, tmp_path / "r3", 60))
    assert outcome.status == "failed" and "API key" in outcome.error
    assert "bypassPermissions" not in planner_argv("claude", model="", budget_usd=1, schema={})


def test_the_planning_commands(prig: PlanRig, repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    number = prig.issue()
    prig.published(number)
    common = ["--repo", str(repo), "--state-dir", str(prig.state)]
    assert cli_main([*common, "github", "planning", "status"]) == 0
    out = capsys.readouterr().out
    assert f"#{number}" in out and "published" in out and "cli-export (cli)" in out
    assert cli_main([*common, "github", "planning", "show", str(number), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["versions"][0]["version"] == 1 and data["status"] == "published"
    assert cli_main([*common, "--tracker", "local", "github", "planning", "labels"]) == 0
    out = capsys.readouterr().out
    assert "worker:plan: would be created" in out and "dry run" in out
    with pytest.raises(TrackerError):
        prig.tracker.add_worker_labels(number, ["feature:verified"])  # the worker-label path writes worker:* only
    with pytest.raises(KeyError):
        prig.coord.run_task(f"{feature_id_for(number)}.cli-export")   # no task exists before approval
    assert parse_op(prig.plan_comments(number)[0].body)[2] == "plan-v1"


def test_an_approval_of_a_never_planned_issue_is_refused_and_leaves_an_idle_record(prig: PlanRig) -> None:
    number = prig.issue()
    prig.approve_label(number, action=False)
    prig.now += prig.config.github.planning.receipt_wait_seconds + 3600
    report = prig.poll()
    assert any("no approval receipt" in r["reason"] for r in report.rejected)
    record = prig.plan_record(number)
    assert record.status == "idle" and not record.versions and not prig.store.list_features()


def test_an_edit_after_a_plan_approval_holds_every_task_of_the_feature(repo: Path, state_dir: Path) -> None:
    rig = multi_rig(repo, state_dir)
    number = rig.issue(title="Category export across the stack", body="Backend API, UI, CLI and docs.")
    rig.published(number)
    rig.approve_label(number)
    rig.poll()
    feature_id = feature_id_for(number)
    rig.tracker.tamper(number, body="Something else entirely")
    report = rig.poll()
    assert report.changed and report.changed[0]["task"] == f"{feature_id}.api"
    states = {t.id: t.state for t in rig.store.load_tasks(rig.store.load_feature(feature_id)).values()}
    assert set(states.values()) == {TaskState.NEEDS_HUMAN}, states
    notices = [c for c in rig.own_comments(number) if "Approval withdrawn" in c]
    assert len(notices) == 1 and all(f"`{t}`" in notices[0] for t in states)
    with pytest.raises(ClaimError):
        rig.coord.run_task(f"{feature_id}.api")       # nothing runs the changed text
    assert values(rig.coord, feature_id)["Status"] == "Blocked"
