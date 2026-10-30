"""Phase 4 acceptance scenario: the real dependency lifecycle on real GitHub Issues (v5 §11 Phase 4).

`run_lifecycle()` drives the actual coordinator — FeatureCoordinator, IssueSync, the serialized
GitCoordinator and the bubblewrap-sandboxed gate runner — through steps A–R of the Phase 4
assignment, in a DISPOSABLE Git repository and state directory under *workdir* (never the
live checkout). Only the agents are scripted (mock runners writing fixed files): the commits,
gates, evidence, claims, locks and every GitHub request are real.

With the real tracker it creates exactly four Issues (one parent, three components), all
carrying `feature:test` and the ownership marker. Writes to them that simulate a human
(labels, closing, `/feature` command comments) are made only after their ownership is
verified. Every other Issue is snapshotted before and compared after (step Q). No pull
request is created: the GitHub adapter refuses, and the test proves none exists.

When every check passes (and *cleanup* is set) each test Issue gets a final verification
comment and is closed; otherwise the Issues stay open, the failure is commented on the parent
Issue, and the report lists their numbers and URLs.
"""

from __future__ import annotations

import dataclasses
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .config import ProjectConfig, default_config
from .demo import DEMO_AUTHOR, init_demo_repo
from .errors import ClaimError, CoordinatorError, EvidenceError, PullRequestPolicyError, RoleBusyError
from .gates import SandboxedGateRunner
from .github_api import sanitize
from .issue_sync import IssueSync, SimulatedCrash, enqueue_comment
from .issues import BLOCKED_LABEL, TEST_LABEL, VERIFIED_LABEL, TrackerClient, extract_block
from .model import Evidence, FeatureState, GateResult, TaskState
from .runners import FileEdit, MockClaudeCodeRunner, MockCodexRunner
from .statedir import ensure_state_dir
from .store import StateStore
from .workflow import FeatureCoordinator, FeatureSpec, TaskSpec

REPO_ROOT = Path(__file__).resolve().parents[2]
ENDPOINT = "/api/v1/items/export"
CHECK_CONTRACT = f'''"""Contract gate of the Phase 4 live test (disposable repository)."""
import json
import sys

with open("backend/openapi.json", encoding="utf-8") as handle:
    spec = json.load(handle)
operation = spec.get("paths", {{}}).get("{ENDPOINT}", {{}}).get("get")
if not operation or "200" not in operation.get("responses", {{}}) or not operation.get("security"):
    print("contract missing: GET {ENDPOINT} (200 response and security required)")
    sys.exit(1)
print("contract available: GET {ENDPOINT}")
'''
SEED_FILES = {
    "backend/openapi.json": '{"openapi": "3.1.0", "paths": {}}\n',
    "backend/src/app.py": "ROUTES = []\n",
    "cli/src/kiddo_cli/commands.py": "COMMANDS = []\n",
    "frontend/src/view.ts": "export const views: string[] = []\n",
    "scripts/check_contract.py": CHECK_CONTRACT,
    "docs_agents/coordination/README.md": "# Coordination\n",
}
TEST_GATES = {
    "contract-check": ("python", "scripts/check_contract.py"),
    "backend-tests": ("python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"),
}
INTEGRATION_GATES = ("contract-check", "backend-tests")


def _openapi(description: str = "") -> str:
    operation: dict[str, Any] = {"security": [{"bearer": []}], "responses": {"200": {"description": "CSV"}}}
    if description:
        operation["description"] = description
    return json.dumps({"openapi": "3.1.0", "paths": {ENDPOINT: {"get": operation}}}, indent=2) + "\n"


BACKEND_TEST = f'''import json
import pathlib


def test_items_export_contract():
    spec = json.loads((pathlib.Path(__file__).parents[1] / "openapi.json").read_text(encoding="utf-8"))
    operation = spec["paths"]["{ENDPOINT}"]["get"]
    assert operation["security"] and "200" in operation["responses"]
'''


def backend_edits(version: int) -> list[FileEdit]:
    """What the (scripted) Backend agent writes: v1, then a changed contract v2."""
    description = "" if version == 1 else "CSV export of category items (v2: adds this description)"
    return [FileEdit("backend/openapi.json", _openapi(description)),
            FileEdit("backend/src/app.py", f"ROUTES = ['GET {ENDPOINT}']  # contract v{version}\n"),
            FileEdit("backend/tests/test_items_export.py", BACKEND_TEST)]


def test_config(base: ProjectConfig | None = None) -> ProjectConfig:
    """The project configuration with gates that check the disposable repository for real."""
    base = base or default_config()
    return dataclasses.replace(base, gates={**base.gates, **TEST_GATES}, unsafe_gates={})


def lifecycle_spec(feature_id: str) -> FeatureSpec:
    """The test feature: Backend provides an API that CLI and Frontend consume."""
    return FeatureSpec(
        slug=feature_id, test=True, title="Items export (Phase 4 dependency lifecycle test)",
        acceptance=(f"Backend provides GET {ENDPOINT}, proven by contract-check and backend-tests on the shared branch",
                    "CLI and Frontend consume it only after the Backend task is verified",
                    "Exactly one PR candidate for the feature; no real pull request is created"),
        tasks=(TaskSpec("contract-items-export", "backend", "Items export API contract", provides_contract=True,
                        required_gates=("contract-check", "backend-tests"),
                        acceptance=(f"GET {ENDPOINT} with a 200 response and security in backend/openapi.json",)),
               TaskSpec("cli", "cli", "CLI command for items export", required_gates=("contract-check",),
                        acceptance=("`kiddo-cli items export` calls the endpoint over HTTP",)),
               TaskSpec("web", "frontend", "Frontend export button", required_gates=("contract-check",),
                        acceptance=("The category view offers an export button",))))


def new_feature_id() -> str:
    """A unique test feature ID, so a second run never touches the first run's Issues."""
    return "fc-p4-" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")


class Checks:
    """Named pass/fail checks with details (the report's evidence)."""

    def __init__(self, log: Callable[[str], None]) -> None:
        self.items: list[dict[str, Any]] = []
        self.log = log

    def __call__(self, name: str, passed: bool, detail: Any = "") -> bool:
        self.items.append({"check": name, "passed": bool(passed), "detail": sanitize(detail, 600)})
        self.log(f"[{'PASS' if passed else 'FAIL'}] {name}" + (f" — {sanitize(detail, 200)}" if detail else ""))
        return bool(passed)

    @property
    def passed(self) -> bool:
        """True when every check passed."""
        return bool(self.items) and all(item["passed"] for item in self.items)


def snapshot(client: TrackerClient) -> dict[int, dict[str, Any]]:
    """Every Issue and PR of the repository: number → fields that change when it is modified."""
    return {i.number: {"updated_at": i.updated_at, "state": i.state, "labels": sorted(i.labels), "title": i.title,
                       "comments": i.comments, "body_len": len(i.body)}
            for i in client.list_issues(state="all")}


def _labels(client: TrackerClient, number: int) -> set[str]:
    return set(client.get_issue(number).labels)


def _comments(client: TrackerClient, number: int) -> list[str]:
    return [c.body for c in client.list_comments(number)]


class Lifecycle:  # pylint: disable=too-many-instance-attributes
    """One run of the scenario. See run_lifecycle()."""

    def __init__(self, client: TrackerClient, workdir: Path, *, operator_comment: Callable[[int, str], Any],
                 tracker_name: str, feature_id: str | None = None, log: Callable[[str], None] = print) -> None:
        self.client = client
        self.fid = feature_id or new_feature_id()
        self.workdir = workdir / self.fid
        self.repo = self.workdir / "repo"
        self.state = self.workdir / "state"
        self.operator_comment = operator_comment
        self.tracker_name = tracker_name
        self.log = log
        self.check = Checks(log)
        self.config = test_config()
        self.backend_id = f"{self.fid}.contract-items-export"
        self.cli_id, self.web_id = f"{self.fid}.cli", f"{self.fid}.web"
        self.report: dict[str, Any] = {"feature": self.fid, "tracker": tracker_name, "workdir": str(self.workdir)}
        self.coord: FeatureCoordinator | None = None

    # -- set-up ----------------------------------------------------------------------

    def _coordinator(self, faults: tuple[str, ...] = ()) -> FeatureCoordinator:
        store = StateStore(self.state)
        claude, codex = self.config.providers["claude"], self.config.providers["codex"]
        runners = {
            "backend": MockClaudeCodeRunner(claude, {self.backend_id: backend_edits(1)}),  # type: ignore[arg-type]
            "cli": MockClaudeCodeRunner(claude, {self.cli_id: [  # type: ignore[arg-type]
                FileEdit("cli/src/kiddo_cli/commands.py", "COMMANDS = ['items export']\n"),
                FileEdit("docs_agents/cli/items-export.md", f"# items export\n\nCalls GET {ENDPOINT} over HTTP.\n")]}),
            "frontend": MockCodexRunner(codex, {self.web_id: [  # type: ignore[arg-type]
                FileEdit("frontend/src/view.ts", "export const views: string[] = ['items-export-button']\n")]}),
        }
        sync = IssueSync(store, self.client, self.config, faults=faults)
        return FeatureCoordinator(self.config, self.repo, store, tracker=self.client, runners=runners,
                                  gate_runner=SandboxedGateRunner(self.config, self.state / "logs"), author=DEMO_AUTHOR,
                                  sync=sync)

    def number(self, record_id: str) -> int:
        """The Issue number mapped to a local record."""
        assert self.coord is not None
        record = (self.coord.store.load_feature(record_id) if record_id == self.fid
                  else self.coord.store.load_task(record_id))
        assert record.issue_number is not None, f"{record_id} has no Issue"
        return record.issue_number

    def task_state(self, task_id: str) -> TaskState:
        """The local state of a task."""
        assert self.coord is not None
        return self.coord.store.load_task(task_id).state

    # -- the scenario --------------------------------------------------------------------

    def run(self, *, cleanup: bool = True) -> dict[str, Any]:
        """Run every step; never raises for a failed check (the report says what failed)."""
        try:
            self._preflight()
            self._create_issues()
            self._dependencies_block()
            self._tampering_is_ignored()
            self._verified_backend_unblocks()
            self._invalidation_reblocks()
            self._idempotent_sync()
            self._single_pr_candidate()
            self._restart_and_reconcile()
            self._unrelated_untouched()
        except Exception as exc:  # pylint: disable=broad-except
            self.check("scenario completed without an unexpected error", False, f"{type(exc).__name__}: {exc}")
        self._finish(cleanup)
        return self.report

    def _preflight(self) -> None:
        check, client = self.check, self.client
        viewer = client.viewer_login()
        check("A0 tracker authentication", bool(viewer), f"acting as {viewer}")
        self.report["viewer"] = viewer
        if hasattr(client, "repository_info"):
            info = client.repository_info()  # type: ignore[attr-defined]
            perms = info["permissions"]
            check("A0 repository access with Issue permissions",
                  info["has_issues"] and not info["archived"] and (perms.get("push") or perms.get("triage")),
                  f"{info['full_name']}: has_issues={info['has_issues']}, permissions={perms}")
        self.before = snapshot(client)  # pylint: disable=attribute-defined-outside-init
        self.labels_before = {l["name"]: l for l in client.list_labels()} \
            if hasattr(client, "list_labels") else {}  # pylint: disable=attribute-defined-outside-init
        self.pulls_before = {(p.number, p.state) for p in client.list_pull_requests()}  # pylint: disable=attribute-defined-outside-init
        self.report["unrelated_issues_before"] = len(self.before)
        ensure_state_dir(self.state)
        init_demo_repo(self.repo, SEED_FILES)
        check("A0 disposable repository (not the live checkout)", self.repo.resolve() != REPO_ROOT.resolve(),
              str(self.repo))

    def _create_issues(self) -> None:
        check = self.check
        # A + B, with a simulated crash right after GitHub created the Frontend Issue.
        self.coord = self._coordinator(faults=(f"crash-after-create:{self.web_id}",))
        crashed = False
        try:
            self.coord.plan(lifecycle_spec(self.fid))
        except SimulatedCrash:
            crashed = True
        check("A1 crash injected after the Frontend Issue was created remotely", crashed)
        link = self.coord.sync.load_link(self.web_id)
        check("A2 the interrupted creation is recorded as pending, without an Issue number",
              link is not None and link.pending_create is not None
              and self.coord.store.load_task(self.web_id).issue_number is None)
        # Restart the synchronizer (no faults): it must find and reuse the Issue, not create a second one.
        self.coord = self._coordinator()
        report = self.coord.sync.sync(self.fid)
        check("A3 recovery adopted the existing Frontend Issue (no duplicate)",
              self.web_id in report.adopted and not report.created and report.ok, report.to_dict())
        parent = self.client.get_issue(self.number(self.fid))
        numbers = {rid: self.number(rid) for rid in (self.fid, self.backend_id, self.cli_id, self.web_id)}
        self.report["issues"] = {rid: {"number": n, "url": self.client.get_issue(n).url} for rid, n in numbers.items()}
        check("A parent feature Issue created with the ownership marker and feature:test",
              TEST_LABEL in parent.labels and "feature:parent" in parent.labels
              and f"feature={self.fid}" in parent.body, f"#{parent.number}")
        roles = {self.backend_id: "feature:backend", self.cli_id: "feature:cli", self.web_id: "feature:frontend"}
        check("B Backend, CLI and Frontend Issues created",
              all(label in _labels(self.client, numbers[rid]) for rid, label in roles.items()),
              {rid: numbers[rid] for rid in roles})
        parent_block = extract_block(parent.body) or ""
        children_ok = all(f"#{numbers[rid]}" in parent_block for rid in roles)
        back_refs = all(f"parent Issue #{numbers[self.fid]}" in (extract_block(self.client.get_issue(numbers[rid]).body)
                                                                  or "") for rid in roles)
        check("C parent ↔ component relationships recorded in the Issues", children_ok and back_refs)
        owned = self.coord.sync.find_owned(self.fid)
        check("A4 exactly four coordinator-owned Issues exist for the feature",
              sorted(i.number for i, _m in owned) == sorted(numbers.values()), [i.number for i, _m in owned])
        self.coord.activate(self.fid)
        check("A5 feature activated on its single branch", self.coord.git.current_branch() == f"feature/{self.fid}")

    def _dependencies_block(self) -> None:
        check, coord = self.check, self.coord
        assert coord is not None
        first = coord.request_contract(self.cli_id, key="items-export", title="Items export API contract",
                                       contract=f"GET {ENDPOINT}")
        second = coord.request_contract(self.web_id, key="items-export", title="duplicate request",
                                        contract=f"GET {ENDPOINT}")
        feature = coord.store.load_feature(self.fid)
        check("D/E CLI and Frontend depend on ONE reused Backend task",
              first.id == second.id == self.backend_id and len(feature.task_ids) == 3
              and coord.store.load_task(self.cli_id).depends_on == (self.backend_id,)
              and coord.store.load_task(self.web_id).depends_on == (self.backend_id,))
        available, evidence = coord.probe_dependency(self.backend_id)
        gate = evidence.gates[0] if evidence.gates else None
        check("F dependency probed and marked unavailable (real sandboxed contract gate failed)",
              not available and gate is not None and gate.status == "failed" and Path(gate.log).exists(),
              f"{[(g.gate, g.status, g.exit_code) for g in evidence.gates]} at {evidence.head_sha[:12]}")
        check("G CLI and Frontend blocked; the independent Backend task stays runnable",
              self.task_state(self.cli_id) is TaskState.BLOCKED and self.task_state(self.web_id) is TaskState.BLOCKED
              and self.task_state(self.backend_id) is TaskState.READY)
        for rid in (self.cli_id, self.web_id):
            number = self.number(rid)
            comments = _comments(self.client, number)
            check(f"H GitHub shows {rid} blocked (label and comments)",
                  BLOCKED_LABEL in _labels(self.client, number)
                  and any("→ **blocked**" in c for c in comments) and any("**unavailable**" in c for c in comments),
                  f"#{number}")
        check("H Backend Issue is not labeled blocked",
              BLOCKED_LABEL not in _labels(self.client, self.number(self.backend_id)))

    def _tampering_is_ignored(self) -> None:
        """A label, a closed Issue or a comment never unblocks a dependency; a human hold works."""
        check, coord, client = self.check, self.coord, self.client
        assert coord is not None
        backend, cli, web = self.number(self.backend_id), self.number(self.cli_id), self.number(self.web_id)
        for number, record in ((backend, self.backend_id), (cli, self.cli_id), (web, self.web_id)):
            verdict = coord.sync.inspect(number)["verdict"]  # verified before simulating any human write
            if verdict != f"owned: task `{record}`":
                raise CoordinatorError(f"#{number} is not provably owned ({verdict}); nothing simulated")
        client.add_labels(backend, [VERIFIED_LABEL])
        client.remove_label(cli, BLOCKED_LABEL)
        client.update_issue(backend, state="closed", state_reason="completed")
        self.operator_comment(cli, f"/feature release {self.cli_id}")
        self.operator_comment(web, f"/feature hold {self.web_id} waiting for a UX review")
        report = coord.sync.sync(self.fid)
        results = {c["command"]: c["result"] for c in report.commands}
        check("T1 a verified label, a closed Issue and a release comment do NOT unblock the CLI",
              self.task_state(self.cli_id) is TaskState.BLOCKED and self.task_state(self.backend_id) is TaskState.READY
              and results.get(f"release {self.cli_id}", "").startswith("rejected"), results)
        check("T2 drift restored from the local state (labels, reopened Issue)",
              client.get_issue(backend).state == "open" and VERIFIED_LABEL not in _labels(client, backend)
              and BLOCKED_LABEL in _labels(client, cli) and len(report.drift) >= 2, report.drift)
        check("T3 an authorized operator command holds the Frontend task for a human",
              self.task_state(self.web_id) is TaskState.NEEDS_HUMAN
              and results.get(f"hold {self.web_id}", "").startswith("accepted"), results)

    def _verified_backend_unblocks(self) -> None:
        check, coord = self.check, self.coord
        assert coord is not None
        feature = coord.store.load_feature(self.fid)
        head = coord.git.rev_parse(f"refs/heads/{feature.branch}")
        claim = coord.claims.claim(self.backend_id, role="backend", branch=feature.branch, head_sha=head)
        try:
            try:
                coord.claims.claim(self.backend_id, role="backend", branch=feature.branch, head_sha=head)
                duplicate = "accepted"
            except ClaimError:
                duplicate = "rejected"
            try:
                coord.store.locks.role("backend", busy_error=RoleBusyError).acquire()
                second_writer = "accepted"
            except RoleBusyError:
                second_writer = "rejected"
            check("I1 atomic claim: a duplicate claim and a second Backend writer are rejected",
                  duplicate == second_writer == "rejected")
            unsupported = []
            for evidence in (Evidence(head_sha=head, gates=()),
                             Evidence(head_sha="0" * 40, gates=(GateResult("contract-check", 0),
                                                                GateResult("backend-tests", 0)))):
                try:
                    coord.verify_task(self.backend_id, evidence)
                    unsupported.append("accepted")
                except EvidenceError:
                    unsupported.append("rejected")
            check("I2 an unsupported completion claim (no gates / wrong HEAD) is rejected",
                  unsupported == ["rejected", "rejected"], unsupported)
        finally:
            coord.claims.release(claim, yield_task=True)
        run = coord.run_task(self.backend_id)
        gates = run.evidence.gates if run.evidence else ()
        check("I Backend verified by the coordinator's verification (serialized commit, real sandboxed gates)",
              run.state is TaskState.VERIFIED and bool(run.commit) and len(gates) == 2
              and all(g.passed and Path(g.log).exists() for g in gates),
              f"commit {str(run.commit)[:12]}; {[(g.gate, g.status) for g in gates]}")
        check("J CLI is ready; the human-held Frontend task stays held",
              self.task_state(self.cli_id) is TaskState.READY and self.task_state(self.web_id) is TaskState.NEEDS_HUMAN
              and run.unblocked == [self.cli_id], run.unblocked)
        self.operator_comment(self.number(self.web_id), f"/feature release {self.web_id}")
        coord.sync.sync(self.fid)
        check("J2 the operator release resumes the Frontend task (its dependency is verified)",
              self.task_state(self.web_id) is TaskState.READY)
        backend = self.number(self.backend_id)
        check("K GitHub: Backend verified with evidence; CLI and Frontend unblocked",
              VERIFIED_LABEL in _labels(self.client, backend)
              and any("Verification evidence" in c and "contract-check" in c for c in _comments(self.client, backend))
              and BLOCKED_LABEL not in _labels(self.client, self.number(self.cli_id))
              and BLOCKED_LABEL not in _labels(self.client, self.number(self.web_id)))

    def _invalidation_reblocks(self) -> None:
        check, coord = self.check, self.coord
        assert coord is not None
        blocked = coord.reopen_task(self.backend_id, "contract v2: the response gains a description")
        check("K2 an invalidated contract re-blocks its dependents",
              sorted(blocked) == sorted([self.cli_id, self.web_id])
              and BLOCKED_LABEL in _labels(self.client, self.number(self.cli_id))
              and VERIFIED_LABEL not in _labels(self.client, self.number(self.backend_id)), blocked)
        coord.runners["backend"].script[self.backend_id] = backend_edits(2)  # type: ignore[attr-defined]
        run = coord.run_task(self.backend_id)
        check("K3 the re-verified contract (new commit, gates at the new HEAD) unblocks them again",
              run.state is TaskState.VERIFIED and bool(run.commit)
              and sorted(run.unblocked) == sorted([self.cli_id, self.web_id])
              and BLOCKED_LABEL not in _labels(self.client, self.number(self.web_id)), run.unblocked)

    def _idempotent_sync(self) -> None:
        check, coord = self.check, self.coord
        assert coord is not None
        coord.sync.sync(self.fid)
        writes_before = getattr(self.client, "writes", 0)
        again = coord.sync.sync(self.fid)
        check("L repeated synchronization performs no remote write",
              again.ok and not again.writes and getattr(self.client, "writes", 0) == writes_before, again.writes)
        owned = coord.sync.find_owned(self.fid)
        keys = [m.key for _i, m in owned]
        check("M no duplicate Issues (one per key, four in total)", len(owned) == 4 and len(set(keys)) == 4,
              [i.number for i, _m in owned])

    def _single_pr_candidate(self) -> None:
        check, coord = self.check, self.coord
        assert coord is not None
        for task_id in (self.cli_id, self.web_id):
            run = coord.run_task(task_id)
            check(f"R1 {task_id} verified at the combined HEAD", run.state is TaskState.VERIFIED, run.state.value)
        feature = coord.integrate(self.fid, gates=INTEGRATION_GATES)
        try:
            coord.integrate(self.fid, gates=INTEGRATION_GATES)
            second = "accepted"
        except PullRequestPolicyError:
            second = "refused"
        refused = "not attempted (local tracker)"
        if getattr(self.client, "live", False):
            try:
                coord.open_pull_request(self.fid, approved=True)
                refused = "CREATED"
            except PullRequestPolicyError as exc:
                refused = f"refused: {exc}"
        branch = feature.branch
        remote = self.client.list_pull_requests(branch)
        candidate = feature.pull_request
        check("R exactly one PR candidate; a second is refused; no real PR exists",
              feature.state is FeatureState.REVIEW and candidate is not None and candidate.head == branch
              and candidate.base == "dev" and candidate.url is None and second == "refused" and not remote
              and refused != "CREATED", f"candidate {branch} → dev at {candidate.head_sha[:12] if candidate else '-'}; "
                                        f"second integration {second}; open_pull_request {refused}; "
                                        f"remote PRs for the branch: {len(remote)}")
        self.report["pr_candidate"] = {"head": candidate.head, "base": candidate.base,
                                       "head_sha": candidate.head_sha} if candidate else None
        commits = subprocess.run(["git", "-C", str(self.repo), "log", "--format=%h %s",
                                  f"{feature.base_sha}..{branch}"], capture_output=True, text=True,
                                 check=True).stdout.splitlines()
        self.report["commits"] = commits
        check("R2 one shared branch, serialized single-role commits",
              len(commits) == 4 and subprocess.run(["git", "-C", str(self.repo), "branch", "--format=%(refname:short)"],
                                                   capture_output=True, text=True, check=True).stdout.split()
              == ["dev", branch], commits)

    def _restart_and_reconcile(self) -> None:
        check = self.check
        assert self.coord is not None
        before = {link.id: link.issue_number for link in self.coord.sync.links(self.fid)}
        argv = [sys.executable, str(REPO_ROOT / "scripts" / "feature.py"), "--repo", str(self.repo), "--state-dir",
                str(self.state), "--tracker", self.tracker_name, "github", "reconcile", self.fid, "--apply", "--json"]
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=600, check=False)
        try:
            result = json.loads(proc.stdout)
        except ValueError:
            result = {}
        feature_report = result.get("features", {}).get(self.fid, {})
        check("N coordinator restarted in a new process and reconciled",
              proc.returncode == 0 and result.get("ok") is True, sanitize(proc.stderr or proc.stdout, 400))
        check("O reconciliation found no drift, orphan or duplicate and wrote nothing",
              not feature_report.get("writes") and not feature_report.get("drift") and not result.get("orphans")
              and not result.get("duplicates"), feature_report.get("writes"))
        self.coord = self._coordinator()
        after = {link.id: link.issue_number for link in self.coord.sync.links(self.fid)}
        remote = {rid: info["number"] for rid, info in feature_report.get("issues", {}).items()}
        check("P Issue numbers stable across the restart", before == after == remote and len(after) == 4, after)

    def _unrelated_untouched(self) -> None:
        check, client = self.check, self.client
        ours = {info["number"] for info in self.report.get("issues", {}).values()}
        after = snapshot(client)
        changed = sorted(n for n, fields in self.before.items() if n not in ours and after.get(n) != fields)
        check("Q no unrelated Issue or PR was modified", not changed and set(self.before) - ours <= set(after),
              f"{len(self.before)} pre-existing compared; changed: {changed}")
        if self.labels_before:
            labels_after = {l["name"]: l for l in client.list_labels()}  # type: ignore[attr-defined]
            altered = [n for n, l in self.labels_before.items() if labels_after.get(n) != l]
            added = sorted(set(labels_after) - set(self.labels_before))
            check("Q2 existing labels unchanged; only feature:* labels added",
                  not altered and all(n.startswith("feature:") for n in added), f"added {added}")
        pulls_after = {(p.number, p.state) for p in client.list_pull_requests()}
        check("Q3 no pull request created or changed", pulls_after == self.pulls_before, len(pulls_after))
        trees = ("test_environment/mxfix", "test_environment/mxissues")
        tracked = subprocess.run(["git", "-C", str(REPO_ROOT), "ls-files", "--", *trees],
                                 capture_output=True, text=True, check=False).stdout
        status = subprocess.run(["git", "-C", str(REPO_ROOT), "status", "--porcelain", "--", *trees],
                                capture_output=True, text=True, check=False).stdout
        # An empty status also means "no such path", so both trees must be tracked to count.
        check("Q4 mxfix/mxissues untouched in the live checkout",
              status == "" and all(f"{tree}/" in tracked for tree in trees),
              status or f"{len(tracked.split())} tracked")

    # -- end -----------------------------------------------------------------------------

    def _finish(self, cleanup: bool) -> None:
        self.report["checks"] = self.check.items
        self.report["passed"] = self.check.passed
        self.report["requests"] = getattr(self.client, "requests", None)
        self.report["writes"] = getattr(self.client, "writes", None)
        coord = self.coord
        if coord is None or "issues" not in self.report:
            return
        failed = [c["check"] for c in self.check.items if not c["passed"]]
        summary = "\n".join([
            f"Result: **{'all checks passed' if not failed else 'FAILED'}** "
            f"({sum(c['passed'] for c in self.check.items)}/{len(self.check.items)} checks).",
            f"Coordinator feature `{self.fid}`; tasks `{self.backend_id}`, `{self.cli_id}`, `{self.web_id}`.",
            "", *[f"- {'PASS' if c['passed'] else 'FAIL'}: {c['check']}" for c in self.check.items]])
        if failed or not cleanup:
            if failed:
                with coord.store.transaction():
                    enqueue_comment(coord.store, self.fid, "**Phase 4 live test failed; Issues kept open as "
                                    "evidence.**\n\n" + summary)
                try:
                    coord.sync.sync(self.fid)
                except CoordinatorError as exc:
                    self.report["failure_comment_error"] = sanitize(exc)
            self.report["cleanup"] = "skipped: " + ("checks failed" if failed else "not requested")
            return
        result = coord.sync.cleanup_test_feature(self.fid, summary, apply=True)
        closed = {rid: self.client.get_issue(info["number"]).state for rid, info in self.report["issues"].items()}
        self.report["cleanup"] = {"ok": result.ok and all(s == "closed" for s in closed.values()), "states": closed,
                                  "writes": result.writes}
        again = coord.sync.cleanup_test_feature(self.fid, summary, apply=True)
        self.report["cleanup"]["repeat_writes"] = again.writes


def run_lifecycle(client: TrackerClient, workdir: Path, *, tracker_name: str,  # pylint: disable=too-many-arguments
                  operator_comment: Callable[[int, str], Any], cleanup: bool = True, feature_id: str | None = None,
                  log: Callable[[str], None] = print) -> dict[str, Any]:
    """Run the Phase 4 scenario and return its report (see the module docstring)."""
    lifecycle = Lifecycle(client, workdir, operator_comment=operator_comment, tracker_name=tracker_name,
                          feature_id=feature_id, log=log)
    report = lifecycle.run(cleanup=cleanup)
    report_path = lifecycle.workdir / "report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    report["report_path"] = str(report_path)
    return report
