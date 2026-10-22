"""Feature orchestration: one feature, one branch, parallel role tasks, one final PR (v5 §7).

    plan()       feature + component tasks (DAG validated) and their feature:* issues
    activate()   create the single feature/<slug> branch (requires quiescence); block or
                 ready tasks by their dependencies
    run_task()   claim task + role slot → runner edits through a ScopedWorkspace →
                 serialized manifest commit → required gates on the combined HEAD → verified
                 (failed / needs-human, or back to ready when the provider is unavailable, a
                 commit is deferred or the branch keeps moving) → release
    request_contract()  a consumer finds a missing API: create or reuse ONE Backend contract
                 task/issue, block only the dependent consumer task; it resumes when the
                 contract task is verified on the shared branch
    integrate()  every task verified + clean checkout + integration gates at the final HEAD →
                 render the single PR candidate (review)
    open_pull_request()  operator-approved only; creates the feature's one PR
    complete()   done only with merge evidence
    probe_dependency()  run a provider task's gates at the branch HEAD and report whether its
                 contract is available (evidence only: it never unblocks anything)
    hold_task() / release_task()  human escalation (needs-human), see escalation.py

Only one feature may be active per checkout; others wait in `planned`.

Issue tracking (Phase 4). The coordinator never calls the tracker inside a state
transaction. Each operation changes the local state first (authoritative), then — outside
every coordinator lock — asks the IssueSync to converge the Issues (issue_sync.py). A tracker
failure is recorded as a `tracker-sync-deferred` event and leaves the local state intact;
`feature.py github sync` (or the next operation) completes the synchronization.

Project board (Phase 4 extension). With a ProjectSync (`board`), every such sync also
converges the feature's GitHub Projects items (project_sync.py), and `plan` authorizes the new
feature for the board. While a task runs, its worker holds its role slot, so it never talks
to GitHub itself: an optional `publisher` (operations.DetachedPublisher) starts a separate
process, which holds no coordinator lock, to show "In Progress" when the agent starts and
"Review" when its commit is being verified. The board is never read as input.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .claims import ClaimManager
from .config import ProjectConfig
from .errors import (
    ClaimError,
    CoordinatorError,
    EvidenceError,
    GitOperationError,
    HeadMismatchError,
    NothingToCommitError,
    PullRequestPolicyError,
    ScopeViolationError,
    UnstableFileError,
    WritersActiveError,
)
from .escalation import hold_task as _hold_task
from .escalation import release_task as _release_task
from .github_api import sanitize
from .gitops import CommitRequest, GitCoordinator, commit_subject, file_digest
from .issue_sync import IssueSync, SyncReport, enqueue_comment
from .project_sync import BoardReport, ProjectSync
from .issues import PullRequest, TrackerClient
from .model import (
    Evidence,
    Feature,
    FeatureState,
    GateResult,
    PullRequestCandidate,
    Task,
    TaskState,
    check_id,
    invalidate_dependents,
    refresh_readiness,
    transition_feature,
    transition_task,
    unmet_dependencies,
    utc_now,
    validate_dag,
)
from .paths import PathPolicy, ScopedWorkspace
from .gates import GateContext, SandboxedGateRunner
from .launchers import run_context
from .runners import RunRequest, RunResult, Runner
from .store import StateStore
from .workspace import identity as workspace_identity
from .workspace import require_workspace

# A gate runner gets the gate name and a GateContext and returns a GateResult (or an exit code).
GateRunner = Callable[[str, GateContext], "GateResult | int"]
ACTIVE_FEATURE_STATES = (FeatureState.ACTIVE, FeatureState.INTEGRATION, FeatureState.REVIEW)
DEFAULT_INTEGRATION_GATES = ("contract-check", "docs-check")
CONTRACT_GATES = ("contract-check", "backend-tests")
CANCEL_FLAG = "cancel-{task}"


@dataclass(frozen=True)
class TaskSpec:  # pylint: disable=too-many-instance-attributes
    """One planned component task."""

    key: str
    role: str
    title: str
    acceptance: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()  # keys of other tasks in the same feature
    required_gates: tuple[str, ...] = ()
    provides_contract: bool = False
    grants: tuple[str, ...] = ()


@dataclass(frozen=True)
class FeatureSpec:
    """An operator-approved feature objective with its component tasks."""

    slug: str
    title: str
    tasks: tuple[TaskSpec, ...]
    acceptance: tuple[str, ...] = ()
    base_branch: str | None = None
    test: bool = False  # a coordinator test feature (its Issues carry `feature:test`)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "FeatureSpec":
        """Build a spec from a YAML/JSON mapping (see feature-workflow.md)."""
        tasks = tuple(
            TaskSpec(key=str(t["key"]), role=str(t["role"]), title=str(t["title"]),
                     acceptance=tuple(t.get("acceptance", ())), depends_on=tuple(t.get("depends_on", ())),
                     required_gates=tuple(t.get("required_gates", ())),
                     provides_contract=bool(t.get("provides_contract", False)), grants=tuple(t.get("grants", ())))
            for t in data.get("tasks", ())
        )
        return cls(slug=str(data["slug"]), title=str(data["title"]), tasks=tasks,
                   acceptance=tuple(data.get("acceptance", ())), base_branch=data.get("base_branch"),
                   test=bool(data.get("test", False)))


@dataclass
class TaskRun:
    """The outcome of run_task()."""

    task_id: str
    state: TaskState
    runner: RunResult
    commit: str | None = None
    evidence: Evidence | None = None
    unblocked: list[str] = field(default_factory=list)


class FeatureCoordinator:  # pylint: disable=too-many-instance-attributes
    """Coordinates one shared checkout."""

    def __init__(self, config: ProjectConfig, repo_root: Path, store: StateStore, *, tracker: TrackerClient,  # pylint: disable=too-many-arguments
                 runners: Mapping[str, Runner], gate_runner: GateRunner | None = None,
                 author: tuple[str, str] | None = None, sync: IssueSync | None = None,
                 auto_sync: bool = True, board: ProjectSync | None = None,
                 publisher: Callable[[str], None] | None = None) -> None:
        self.config = config
        self.root = Path(repo_root)
        self.store = store
        self.tracker = tracker
        self.runners = dict(runners)
        # Gates never run unsandboxed: the default is the offline, read-only gate sandbox.
        self.gate_runner = gate_runner or SandboxedGateRunner(config, store.root / "logs")
        self.author = author
        self.policy = PathPolicy(self.root, config)
        self.claims = ClaimManager(store)
        self.git = GitCoordinator(self.root, store.locks, self.policy, tmp_dir=store.root / "tmp")
        self.sync = sync or IssueSync(store, tracker, config)
        self.auto_sync = auto_sync
        self.last_sync: SyncReport | None = None
        self.board = board
        if board is not None and board.revision_check is None:
            board.revision_check = lambda feature, sha: self.git.contains(feature.branch, sha)
        self.publisher = publisher
        self.last_board_sync: BoardReport | None = None

    # -- issue tracking (always outside coordinator locks) ------------------------

    def sync_issues(self, feature_id: str) -> SyncReport | None:
        """Converge the feature's Issues now (if auto-sync is on). Failures are recorded, not raised:
        the local state stays authoritative and a later sync completes the work."""
        if not self.auto_sync:
            return None
        try:
            report = self.sync.sync(feature_id, apply=True)
        except CoordinatorError as exc:
            self._event("tracker-sync-deferred", feature=feature_id, error=sanitize(str(exc)))
            return None
        if report.deferred or report.protected:
            self._event("tracker-sync-deferred", feature=feature_id, error="; ".join(report.deferred),
                        protected=report.protected)
        self.last_sync = report
        self.sync_board(feature_id)
        return report

    def sync_board(self, feature_id: str) -> BoardReport | None:
        """Converge the feature's project board items (if a board is configured). Like the Issue
        sync: outside every coordinator lock, failures recorded and completed later."""
        if self.board is None or not self.auto_sync:
            return None
        try:
            report = self.board.sync(feature_id, apply=True)
        except CoordinatorError as exc:
            self._event("tracker-sync-deferred", feature=feature_id, board=True, error=sanitize(str(exc)))
            return None
        if report.deferred or report.protected:
            self._event("tracker-sync-deferred", feature=feature_id, board=True, error="; ".join(report.deferred),
                        protected=report.protected)
        self.last_board_sync = report
        return report

    def _publish(self, feature_id: str) -> None:
        """Show a running task's state on GitHub from ANOTHER process (the worker holds its role
        slot, so it never makes GitHub requests itself). Never raises; never waits."""
        if self.publisher is None or not self.auto_sync:
            return
        try:
            self.publisher(feature_id)
        except (OSError, CoordinatorError) as exc:
            self._event("tracker-sync-deferred", feature=feature_id, publisher=True, error=sanitize(str(exc)))

    # -- planning --------------------------------------------------------------

    def plan(self, spec: FeatureSpec) -> Feature:
        """Record a new feature and its tasks (state planned / proposed) and their issues."""
        feature_id = check_id(spec.slug, "feature slug")
        branch = self.config.branch_for(spec.slug)
        keys = [t.key for t in spec.tasks]
        if not spec.tasks or len(set(keys)) != len(keys):
            raise CoordinatorError("a feature needs at least one task and unique task keys")
        tasks: dict[str, Task] = {}
        for t in spec.tasks:
            if t.role not in self.config.roles:
                raise CoordinatorError(f"task {t.key!r}: unknown role {t.role!r}")
            unknown_gates = [g for g in t.required_gates if g not in self.config.gate_names]
            if unknown_gates:
                raise CoordinatorError(f"task {t.key!r}: unknown gates {unknown_gates}")
            tasks[f"{feature_id}.{t.key}"] = Task(
                id=f"{feature_id}.{t.key}", feature_id=feature_id, role=t.role, title=t.title,
                acceptance=t.acceptance, depends_on=tuple(f"{feature_id}.{d}" for d in t.depends_on),
                required_gates=t.required_gates, provides_contract=t.provides_contract,
                grants=self.policy.validate_grants(t.grants))
        validate_dag(tasks)
        if self.git.branch_exists(branch):
            raise GitOperationError(f"branch {branch!r} already exists; a feature owns exactly one new branch")
        feature = Feature(id=feature_id, slug=spec.slug, title=spec.title, branch=branch,
                          base_branch=spec.base_branch or self.config.base_branch, acceptance=spec.acceptance,
                          task_ids=list(tasks), test=spec.test)
        with self.store.transaction():
            try:
                self.store.load_feature(feature_id)
            except KeyError:
                pass
            else:
                raise CoordinatorError(f"feature {feature_id!r} already exists")
            for task in tasks.values():
                self.store.save_task(task)
            self.store.save_feature(feature)
            self.store.append_event("feature-planned", feature=feature_id, tasks=list(tasks))
        if self.board is not None:
            self.board.authorize(feature_id, actor="plan")  # new features appear on the board automatically
        # The parent Issue and one Issue per task are created by the sync, outside the lock.
        self.sync_issues(feature_id)
        return self.store.load_feature(feature_id)

    # -- activation --------------------------------------------------------------

    def activate(self, feature_id: str, *, base_ref: str | None = None) -> Feature:
        """Create the feature branch (needs every writer quiescent) and compute readiness."""
        with self.store.transaction():
            others = [f.id for f in self.store.list_features()
                      if f.state in ACTIVE_FEATURE_STATES and f.id != feature_id]
            if others:
                raise CoordinatorError(f"feature {others[0]!r} is active; one active feature per checkout (queued)")
            feature = self.store.load_feature(feature_id)
            if feature.state is not FeatureState.PLANNED:
                raise CoordinatorError(f"feature {feature_id!r} is {feature.state.value}, not planned")
            feature.base_sha = self.git.create_feature_branch(feature.branch, base_ref or feature.base_branch)
            feature.workspace = workspace_identity(self.root)  # W2: verified before every run
            transition_feature(feature, FeatureState.ACTIVE, f"branch {feature.branch} created")
            tasks = self.store.load_tasks(feature)
            for task in tasks.values():
                if task.state is TaskState.PROPOSED:
                    transition_task(task, TaskState.READY, "feature activated")
            refresh_readiness(tasks)
            for task in tasks.values():
                self.store.save_task(task)
            self.store.save_feature(feature)
            self.store.append_event("feature-activated", feature=feature_id, branch=feature.branch,
                                    base_sha=feature.base_sha)
        self.sync_issues(feature_id)
        return self.store.load_feature(feature_id)

    # -- running tasks -----------------------------------------------------------

    def _head(self, feature: Feature) -> str:
        return self.git.rev_parse(f"refs/heads/{feature.branch}")

    def _run_gates(self, feature: Feature, gates: Sequence[str], head: str) -> Evidence:
        context = GateContext(self.root, head_sha=head, base_sha=feature.base_sha, feature_id=feature.id)
        results = []
        for gate in gates:
            outcome = self.gate_runner(gate, context)
            results.append(outcome if isinstance(outcome, GateResult)
                           else GateResult(gate, int(outcome), self.config.gates.get(gate, ())))
        return Evidence(head_sha=head, gates=tuple(results))

    def gate_evidence(self, feature: Feature, gates: Sequence[str], attempts: int = 3) -> Evidence:
        """Run *gates* and bind them to the branch HEAD; retry if another role committed meanwhile."""
        for _attempt in range(attempts):
            head = self._head(feature)
            evidence = self._run_gates(feature, gates, head)
            if self._head(feature) == head:
                return evidence
        raise EvidenceError(f"the branch kept moving while gates ran ({attempts} attempts)")

    def run_task(self, task_id: str, *, prompt: str = "") -> TaskRun:
        """Claim, run, commit, verify, release — for one task. See the module docstring.

        Every exit leaves the task in a defined state: verified, failed or needs-human via
        _finish(), or back to ready (released with yield) when the provider was unavailable,
        a commit had to be deferred, the branch kept moving, or an unexpected error occurred.
        """
        task = self.store.load_task(task_id)
        feature = self.store.load_feature(task.feature_id)
        if feature.state is not FeatureState.ACTIVE:
            raise ClaimError(f"feature {feature.id!r} is {feature.state.value}, not active")
        if self.git.current_branch() != feature.branch:
            raise GitOperationError(f"the shared checkout is not on {feature.branch!r}")
        require_workspace(self.root, feature)  # W2: identity, base revision, no unfinished Git operation
        head = self._head(feature)
        mark = len(self.store.events())
        claim = self.claims.claim(task_id, role=task.role, branch=feature.branch, head_sha=head)
        self._publish(feature.id)  # "In Progress" on the board while the agent runs (another process)
        try:
            workspace = ScopedWorkspace(self.policy, task.role, grants=task.grants)
            context = run_context(
                self.store.root / "runs", task_id, timeout_seconds=self.config.runtime.task_timeout_seconds,
                heartbeat=lambda checkpoint: self.claims.heartbeat(claim, dict(checkpoint)),
                cancelled=lambda: self._cancel_requested(task_id, claim.token),
                concurrent_roles=lambda: self._concurrent_roles(mark, task.role),
                git_quiet=lambda: self.store.locks.git().acquire(timeout=self.git.lock_timeout))
            request = RunRequest(task_id, task.role, prompt or self._task_prompt(task), workspace, context=context)
            run = TaskRun(task_id, TaskState.IN_PROGRESS, self.runners[task.role].run(request))

            def verifying(commit: str | None) -> None:
                # Implementation complete, awaiting verification: the board shows "Review".
                self.claims.heartbeat(claim, {"phase": "verifying", "commit": commit or ""})
                self._publish(feature.id)
            result = self._after_run(feature, task, head, run, verifying)
        finally:
            # Only a task still in-progress is affected: it goes back to ready for a later claim.
            self.claims.release(claim, yield_task=True)
            with self.store.transaction():
                flag = self.store.get_flag(CANCEL_FLAG.format(task=task_id))
                if flag and flag.get("token") == claim.token:
                    self.store.set_flag(CANCEL_FLAG.format(task=task_id), None)
        # After the role slot is released: the Issues never delay another writer.
        self.sync_issues(feature.id)
        return result

    def _after_run(self, feature: Feature, task: Task, head: str, run: TaskRun,  # pylint: disable=too-many-return-statements,too-many-arguments
                   verifying: Callable[[str | None], None] | None = None) -> TaskRun:
        """Turn the runner outcome into a task state: defer, fail, commit and verify."""
        result = run.runner
        if result.status == "unavailable":
            return self._defer(run, "runner-unavailable", task.role, result.error)
        if result.status == "cancelled":
            # The role's uncommitted work stays in its roots; a later run continues from it.
            return self._defer(run, "run-cancelled", task.role, result.error)
        if result.status == "timeout":
            return self._finish(task.id, TaskState.FAILED, f"runner timed out: {result.error}", run)
        if not result.ok:
            return self._finish(task.id, TaskState.FAILED, f"runner {result.status}: {result.error}", run)
        try:
            run.commit = self._commit(feature, task, head, result.changed_paths)
        except UnstableFileError as exc:
            return self._defer(run, "commit-deferred", task.role, str(exc))
        except HeadMismatchError as exc:
            return self._finish(task.id, TaskState.NEEDS_HUMAN, f"commit conflicts with the branch: {exc}", run)
        except (ScopeViolationError, GitOperationError) as exc:
            return self._finish(task.id, TaskState.FAILED, f"commit refused: {exc}", run)
        if verifying is not None:
            verifying(run.commit)
        return self._verify(feature, task, run)

    @staticmethod
    def _task_prompt(task: Task) -> str:
        lines = [task.title]
        if task.acceptance:
            lines += ["", "Acceptance criteria:", *(f"- {a}" for a in task.acceptance)]
        if task.required_gates:
            lines += ["", f"The coordinator will verify with these checks: {', '.join(task.required_gates)}."]
        return "\n".join(lines)

    def _concurrent_roles(self, mark: int, own: str) -> set[str]:
        """Roles other than *own* that held a claim since event *mark* (W2: native attribution)."""
        roles = {e.get("role") for e in self.store.events()[mark:] if e.get("kind") == "claim"}
        roles |= {c.get("role") for c in self.store.list_claims().values()}
        return {r for r in roles if r and r != own}

    def _cancel_requested(self, task_id: str, token: str) -> bool:
        flag = self.store.get_flag(CANCEL_FLAG.format(task=task_id))
        return bool(flag) and flag.get("token") == token

    def request_cancel(self, task_id: str, reason: str = "operator stop") -> dict[str, Any]:
        """Ask the worker running *task_id* to stop. It terminates its sandbox and yields the task."""
        with self.store.transaction():
            record = self.store.load_claim(task_id)
            if record is None:
                raise ClaimError(f"task {task_id!r} is not running (no claim)")
            flag = {"token": record["token"], "reason": reason, "requested_at": utc_now()}
            self.store.set_flag(CANCEL_FLAG.format(task=task_id), flag)
            self.store.append_event("cancel-requested", task=task_id, pid=record["identity"]["pid"], reason=reason)
        return record

    def _commit(self, feature: Feature, task: Task, head: str, changed: Sequence[str]) -> str | None:
        if not changed:
            return None
        files = {p: file_digest(self.root / p) for p in changed}
        try:
            return self.git.commit(CommitRequest(feature.id, task.id, task.role, feature.branch, head, files,
                                                 commit_subject(task.role, task.id, task.title), task.grants),
                                   author=self.author).sha
        except NothingToCommitError:
            return None

    def _verify(self, feature: Feature, task: Task, run: TaskRun, attempts: int = 3) -> TaskRun:
        """Gates on the combined HEAD, then verified — re-run if another role committed in between."""
        for _attempt in range(attempts):
            try:
                run.evidence = self.gate_evidence(feature, task.required_gates)
            except EvidenceError as exc:
                return self._defer(run, "verification-deferred", task.role, str(exc))
            failing = [g.gate for g in run.evidence.gates if not g.passed]
            if failing:
                return self._finish(task.id, TaskState.FAILED, f"gates failed: {failing}", run)
            finished = self._finish(task.id, TaskState.VERIFIED, "acceptance gates passed on the shared branch", run)
            if finished is not None:
                return finished
        return self._defer(run, "verification-deferred", task.role, "the branch kept moving after the gates ran")

    def _defer(self, run: TaskRun, kind: str, role: str, error: str) -> TaskRun:
        """The task goes back to ready when its claim is released; nothing is lost."""
        run.state = TaskState.READY
        self._event(kind, task=run.task_id, role=role, error=error, commit=run.commit)
        return run

    def _finish(self, task_id: str, state: TaskState, reason: str, run: TaskRun) -> TaskRun | None:
        """Record the outcome. Returns None only for `verified` when the branch HEAD no longer
        matches the evidence (the caller re-runs the gates)."""
        with self.store.transaction():
            task = self.store.load_task(task_id)
            feature = self.store.load_feature(task.feature_id)
            if run.commit and run.commit not in task.commits:
                task.commits.append(run.commit)
            if task.state is not TaskState.IN_PROGRESS:
                # e.g. a contract request blocked this step meanwhile: keep that state, keep the commit.
                self.store.save_task(task)
                self.store.append_event("task-result-superseded", task=task_id, state=task.state.value,
                                        result=state.value, reason=reason, commit=run.commit)
                run.state = task.state
                return run
            tasks = self.store.load_tasks(feature)
            if state is TaskState.VERIFIED:
                if run.evidence is None or run.evidence.head_sha != self._head(feature):
                    self.store.save_task(task)
                    return None
                unmet = unmet_dependencies(task, tasks)
                if unmet:
                    # A dependency was re-opened during the run: never verify against an unverified contract.
                    state, reason = TaskState.BLOCKED, f"waiting for {', '.join(unmet)}"
                    transition_task(task, state, reason)
                else:
                    transition_task(task, state, reason, evidence=run.evidence, expected_head=run.evidence.head_sha)
            else:
                transition_task(task, state, reason)
            self.store.save_task(task)
            tasks[task_id] = task
            changed = refresh_readiness(tasks)
            for changed_id in changed:
                self.store.save_task(tasks[changed_id])
            run.unblocked = [t for t in changed if tasks[t].state is TaskState.READY]
            self.store.append_event("task-finished", task=task_id, state=state.value, reason=reason, commit=run.commit)
        run.state = state
        return run

    def _event(self, kind: str, **fields: Any) -> None:
        with self.store.transaction():
            self.store.append_event(kind, **fields)

    def verify_task(self, task_id: str, evidence: Evidence) -> list[str]:
        """Record externally produced evidence for an in-progress task; returns unblocked task IDs."""
        with self.store.transaction():
            task = self.store.load_task(task_id)
            feature = self.store.load_feature(task.feature_id)
            tasks = self.store.load_tasks(feature)
            unmet = unmet_dependencies(task, tasks)
            if unmet:
                raise EvidenceError(f"task {task_id!r} cannot be verified while {unmet} are not verified")
            transition_task(task, TaskState.VERIFIED, "evidence recorded", evidence=evidence,
                            expected_head=self._head(feature))
            self.store.save_task(task)
            tasks[task_id] = task
            unblocked = refresh_readiness(tasks)
            for changed in unblocked:
                self.store.save_task(tasks[changed])
        self.sync_issues(feature.id)
        return unblocked

    def reopen_task(self, task_id: str, reason: str) -> list[str]:
        """Re-open a verified task (e.g. its contract must change). Every dependent that relied
        on it is blocked again: verified ones must re-verify, ready ones wait for it again."""
        with self.store.transaction():
            task = self.store.load_task(task_id)
            feature = self.store.load_feature(task.feature_id)
            transition_task(task, TaskState.READY, reason)
            tasks = self.store.load_tasks(feature)
            tasks[task_id] = task
            blocked = invalidate_dependents(task_id, tasks)
            changed = refresh_readiness(tasks)
            blocked += [t for t in changed if tasks[t].state is TaskState.BLOCKED and t not in blocked]
            for changed_id in sorted({task_id, *blocked, *changed}):
                self.store.save_task(tasks[changed_id])
            self.store.append_event("task-reopened", task=task_id, reason=reason, blocked=blocked)
        self.sync_issues(feature.id)
        return blocked

    # -- cross-role dependencies ------------------------------------------------

    def request_contract(self, consumer_id: str, *, key: str, title: str, contract: str) -> Task:
        """Create or reuse ONE Backend contract task for *key* and block only *consumer_id* on it."""
        with self.store.transaction():
            consumer = self.store.load_task(consumer_id)
            feature = self.store.load_feature(consumer.feature_id)
            if feature.state is not FeatureState.ACTIVE:
                raise CoordinatorError(f"feature {feature.id!r} is not active")
            if consumer.state is TaskState.VERIFIED:
                raise CoordinatorError(f"task {consumer_id!r} is verified; re-open it before adding a dependency")
            contract_id = f"{feature.id}.contract-{check_id(key, 'contract key')}"
            tasks = self.store.load_tasks(feature)
            backend = tasks.get(contract_id)
            if backend is None:
                # One Backend task (and, via the sync, one Issue) per contract key, however often it is requested.
                backend = Task(id=contract_id, feature_id=feature.id, role="backend", title=title,
                               acceptance=(contract,), required_gates=CONTRACT_GATES, provides_contract=True,
                               state=TaskState.READY)
                feature.task_ids.append(contract_id)
                tasks[contract_id] = backend
                self.store.save_task(backend)
                self.store.save_feature(feature)
            if contract_id not in consumer.depends_on:
                consumer.depends_on = (*consumer.depends_on, contract_id)
                tasks[consumer_id] = consumer
                validate_dag(tasks)  # rejects a cycle before anything is saved for the consumer
            if backend.state is not TaskState.VERIFIED:
                # Only this consumer step waits; an in-progress worker keeps its claim until it yields.
                transition_task(consumer, TaskState.BLOCKED, f"waiting for {contract_id}")
            self.store.save_task(consumer)
            self.store.append_event("contract-requested", consumer=consumer_id, contract_task=contract_id)
        self.sync_issues(feature.id)
        return self.store.load_task(contract_id)

    def probe_dependency(self, provider_id: str) -> tuple[bool, Evidence]:
        """Run the provider task's required gates at the feature-branch HEAD and report whether
        its contract is available. Evidence only: a passing probe does NOT verify the provider
        or unblock consumers (that needs the provider's own verified run); a failing probe is
        recorded and announced on the provider's and the consumers' Issues."""
        task = self.store.load_task(provider_id)
        feature = self.store.load_feature(task.feature_id)
        evidence = self.gate_evidence(feature, task.required_gates)
        available = all(g.passed for g in evidence.gates) and bool(evidence.gates)
        with self.store.transaction():
            tasks = self.store.load_tasks(feature)
            consumers = [t.id for t in tasks.values() if provider_id in t.depends_on]
            gates = ", ".join(f"`{g.gate}` {g.status} (exit {g.exit_code})" for g in evidence.gates) or "no gates"
            if available:
                verdict = ("the contract checks pass at this HEAD, but the provider task is "
                           f"**{tasks[provider_id].state.value}**: consumers stay blocked until it is verified")
            else:
                verdict = "the dependency is **unavailable**: consumers stay blocked"
            body = (f"Dependency probe for {{{{issue:{provider_id}}}}} `{provider_id}` at feature-branch HEAD "
                    f"`{evidence.head_sha[:12]}`: {gates}. Result: {verdict}. Waiting: "
                    + (", ".join(f"{{{{issue:{c}}}}} `{c}`" for c in consumers) or "nothing") + ".")
            for record_id in (provider_id, *consumers):
                enqueue_comment(self.store, record_id, body)
            self.store.append_event("dependency-probe", task=provider_id, head=evidence.head_sha, available=available,
                                    gates=[g.to_dict() for g in evidence.gates], consumers=consumers)
        self.sync_issues(feature.id)
        return available, evidence

    def hold_task(self, task_id: str, *, reason: str, actor: str = "operator") -> Task:
        """Hold a task for a human decision (needs-human); only an explicit release resumes it."""
        task = _hold_task(self.store, task_id, reason=reason, actor=actor)
        self.sync_issues(task.feature_id)
        return task

    def release_task(self, task_id: str, *, actor: str = "operator") -> Task:
        """Release a human hold; the task is ready, or blocked again if its dependencies are unmet."""
        task, _changed = _release_task(self.store, task_id, actor=actor)
        self.sync_issues(task.feature_id)
        return task

    # -- integration and the single PR -------------------------------------------

    def integrate(self, feature_id: str, *, gates: Sequence[str] = DEFAULT_INTEGRATION_GATES) -> Feature:
        """All tasks verified, clean checkout, integration gates at the final HEAD → PR candidate.

        Runs as a quiescent barrier: project.lock EXCLUSIVE, so no scoped writer is active and
        no new claim can start while the combined gates run."""
        try:
            with self.store.locks.project(shared=False, busy_error=WritersActiveError).acquire():
                return self._integrate(feature_id, gates)
        finally:
            self.sync_issues(feature_id)

    def _integrate(self, feature_id: str, gates: Sequence[str]) -> Feature:
        feature = self.store.load_feature(feature_id)
        tasks = self.store.load_tasks(feature)
        pending = sorted(t.id for t in tasks.values() if t.state is not TaskState.VERIFIED)
        if feature.state is not FeatureState.ACTIVE or pending:
            raise PullRequestPolicyError(f"integration needs an active feature with every task verified; "
                                         f"not verified: {pending}")
        dirty = self.git.tracked_changes()
        untracked = self.git.untracked_files()
        if dirty or untracked:
            raise PullRequestPolicyError(f"the shared checkout has uncommitted or foreign changes: {dirty + untracked}")
        evidence = self.gate_evidence(feature, gates)
        with self.store.transaction():
            feature = self.store.load_feature(feature_id)
            transition_feature(feature, FeatureState.INTEGRATION, "all component tasks verified")
            failing = [g.gate for g in evidence.gates if not g.passed]
            if failing:
                transition_feature(feature, FeatureState.ACTIVE, f"integration gates failed: {failing}")
                self.store.save_feature(feature)
                raise EvidenceError(f"integration gates failed: {failing}")
            feature.integration_evidence = evidence
            feature.pull_request = self._render_pr(feature, tasks, evidence)
            transition_feature(feature, FeatureState.REVIEW, "integration gates passed; single PR candidate ready")
            self.store.save_feature(feature)
            self.store.append_event("feature-integrated", feature=feature_id, head=evidence.head_sha)
        return feature

    def _render_pr(self, feature: Feature, tasks: Mapping[str, Task], evidence: Evidence) -> PullRequestCandidate:
        changed = self.git.changed_paths(feature.base_sha, evidence.head_sha)
        def issue(number: int | None) -> str:
            return f"#{number}" if number else "(Issue not synchronized yet)"
        lines = [f"Feature `{feature.id}`: {feature.title}", "", f"Parent issue: {issue(feature.issue_number)}", "",
                 "## Component tasks", *[f"- `{t.id}` ({t.role}) — issue {issue(t.issue_number)}, commits "
                                        f"{', '.join(c[:12] for c in t.commits) or 'none'}" for t in tasks.values()],
                 "", "## Acceptance criteria", *[f"- {a}" for a in feature.acceptance or ("(see parent issue)",)],
                 "", "## Changed paths", *[f"- `{p}`" for p in changed], "",
                 f"## Integration gates at `{evidence.head_sha[:12]}`",
                 *[f"- {g.gate}: {'pass' if g.passed else 'FAIL'}" for g in evidence.gates], "",
                 "## CI", "Remote CI: pending / not configured (branch protection is unavailable on the current plan).",
                 "", "Single aggregate PR for this feature. Do not merge without human review."]
        return PullRequestCandidate(head=feature.branch, base=feature.base_branch, title=feature.title,
                                    body="\n".join(lines), head_sha=evidence.head_sha)

    def open_pull_request(self, feature_id: str, *, approved: bool) -> PullRequest:
        """Create the feature's single PR. Requires operator approval and the review state."""
        if not approved:
            raise PullRequestPolicyError("creating the PR requires explicit operator approval")
        feature = self._pr_candidate(feature_id)
        candidate = feature.pull_request
        assert candidate is not None
        existing = [p for p in self.tracker.list_pull_requests(candidate.head) if p.state == "open"]
        if existing:
            raise PullRequestPolicyError(f"{candidate.head!r} already has an open PR (#{existing[0].number})")
        # Outside the state lock; the tracker itself refuses a second PR for the same head.
        pr = self.tracker.create_pull_request(candidate.head, candidate.base, candidate.title, candidate.body)
        with self.store.transaction():
            feature = self.store.load_feature(feature_id)
            if feature.pull_request is None or feature.pull_request.url:
                raise PullRequestPolicyError(f"feature {feature_id!r} changed while its PR was being created")
            feature.pull_request.url = f"pr#{pr.number}"
            feature.pull_request.ci_state = "pending"
            self.store.save_feature(feature)
            self.store.append_event("pull-request-opened", feature=feature_id, pr=pr.number)
        self.sync_issues(feature_id)
        return pr

    def _pr_candidate(self, feature_id: str) -> Feature:
        """The feature, if its single PR may be opened now (review state, unchanged HEAD, no PR yet)."""
        feature = self.store.load_feature(feature_id)
        if feature.state is not FeatureState.REVIEW or feature.pull_request is None:
            raise PullRequestPolicyError(f"feature {feature_id!r} has no reviewed PR candidate")
        if feature.pull_request.url:
            raise PullRequestPolicyError(f"feature {feature_id!r} already has its PR: {feature.pull_request.url}")
        if self._head(feature) != feature.pull_request.head_sha:
            raise PullRequestPolicyError("the branch moved after integration; integrate again")
        return feature

    def complete(self, feature_id: str, *, merged_sha: str) -> Feature:
        """Mark the feature done — only with merge evidence for its single PR."""
        with self.store.transaction():
            feature = self.store.load_feature(feature_id)
            if feature.pull_request is None or not feature.pull_request.url:
                raise EvidenceError("done requires the feature PR to exist and be merged")
            transition_feature(feature, FeatureState.DONE, "PR merged after human review", merged_sha=merged_sha)
            self.store.save_feature(feature)
            self.store.append_event("feature-done", feature=feature_id, merged=merged_sha)
        self.sync_issues(feature_id)  # a done feature's Issues are closed by the sync
        return self.store.load_feature(feature_id)
