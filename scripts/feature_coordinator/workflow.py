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

Only one feature may be active per checkout; others wait in `planned`.
"""

from __future__ import annotations

import subprocess
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
from .gitops import CommitRequest, GitCoordinator, file_digest
from .issues import BLOCKED_LABEL, PARENT_LABEL, REVIEW_LABEL, ROLE_LABELS, IssueTracker, PullRequest
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
    validate_dag,
)
from .paths import PathPolicy, ScopedWorkspace
from .runners import RunRequest, RunResult, Runner
from .store import StateStore

GateRunner = Callable[[str, Sequence[str], Path], int]
ACTIVE_FEATURE_STATES = (FeatureState.ACTIVE, FeatureState.INTEGRATION, FeatureState.REVIEW)
DEFAULT_INTEGRATION_GATES = ("contract-check", "docs-check")
CONTRACT_GATES = ("contract-check", "backend-tests")


def subprocess_gate_runner(name: str, argv: Sequence[str], repo_root: Path) -> int:
    """Run a configured gate. `backend-*`, `cli-*` and `frontend-*` gates run in that directory."""
    cwd = repo_root / name.split("-", 1)[0] if name.startswith(("backend-", "cli-", "frontend-")) else repo_root
    return subprocess.run(list(argv), cwd=cwd, check=False).returncode


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
                   acceptance=tuple(data.get("acceptance", ())), base_branch=data.get("base_branch"))


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

    def __init__(self, config: ProjectConfig, repo_root: Path, store: StateStore, *, tracker: IssueTracker,
                 runners: Mapping[str, Runner], gate_runner: GateRunner = subprocess_gate_runner,
                 author: tuple[str, str] | None = None) -> None:
        self.config = config
        self.root = Path(repo_root)
        self.store = store
        self.tracker = tracker
        self.runners = dict(runners)
        self.gate_runner = gate_runner
        self.author = author
        self.policy = PathPolicy(self.root, config)
        self.claims = ClaimManager(store)
        self.git = GitCoordinator(self.root, store.locks, self.policy, tmp_dir=store.root / "tmp")

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
            unknown_gates = [g for g in t.required_gates if g not in self.config.gates]
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
                          task_ids=list(tasks))
        with self.store.transaction():
            try:
                self.store.load_feature(feature_id)
            except KeyError:
                pass
            else:
                raise CoordinatorError(f"feature {feature_id!r} already exists")
            parent = self.tracker.ensure_issue(f"feature:{feature_id}", spec.title,
                                               self._feature_body(feature, tasks), [PARENT_LABEL])
            feature.issue_number = parent.number
            for task in tasks.values():
                issue = self.tracker.ensure_issue(f"task:{task.id}", f"[{task.role}] {task.title}",
                                                  self._task_body(task), [ROLE_LABELS[task.role]])
                task.issue_number = issue.number
            for task in tasks.values():
                for dep in task.depends_on:
                    self.tracker.link_dependency(task.issue_number, tasks[dep].issue_number)
                self.store.save_task(task)
            self.store.save_feature(feature)
            self.store.append_event("feature-planned", feature=feature_id, tasks=list(tasks))
        return feature

    @staticmethod
    def _feature_body(feature: Feature, tasks: Mapping[str, Task]) -> str:
        lines = [f"Feature `{feature.id}` on branch `{feature.branch}` (base `{feature.base_branch}`).", "",
                 "Acceptance criteria:", *[f"- {a}" for a in feature.acceptance or ("(to be defined)",)], "",
                 "Component tasks:", *[f"- `{t.id}` ({t.role}): {t.title}" for t in tasks.values()], "",
                 "One shared branch; exactly one final PR after all gates pass."]
        return "\n".join(lines)

    @staticmethod
    def _task_body(task: Task) -> str:
        lines = [f"Task `{task.id}` for role **{task.role}**.", "",
                 "Acceptance criteria:", *[f"- {a}" for a in task.acceptance or ("(to be defined)",)],
                 f"Required gates: {', '.join(task.required_gates) or 'none'}",
                 f"Depends on: {', '.join(task.depends_on) or 'nothing'}"]
        return "\n".join(lines)

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
            transition_feature(feature, FeatureState.ACTIVE, f"branch {feature.branch} created")
            tasks = self.store.load_tasks(feature)
            for task in tasks.values():
                if task.state is TaskState.PROPOSED:
                    transition_task(task, TaskState.READY, "feature activated")
            refresh_readiness(tasks)
            self._sync_task_labels(tasks)
            for task in tasks.values():
                self.store.save_task(task)
            self.store.save_feature(feature)
            self.store.append_event("feature-activated", feature=feature_id, branch=feature.branch,
                                    base_sha=feature.base_sha)
        return feature

    def _sync_task_labels(self, tasks: Mapping[str, Task]) -> None:
        for task in tasks.values():
            if task.issue_number is None:
                continue
            if task.state is TaskState.BLOCKED:
                self.tracker.set_labels(task.issue_number, add=[BLOCKED_LABEL])
            else:
                self.tracker.set_labels(task.issue_number, remove=[BLOCKED_LABEL])

    # -- running tasks -----------------------------------------------------------

    def _head(self, feature: Feature) -> str:
        return self.git.rev_parse(f"refs/heads/{feature.branch}")

    def _run_gates(self, gates: Sequence[str], head: str) -> Evidence:
        results = tuple(GateResult(g, self.gate_runner(g, self.config.gates[g], self.root), self.config.gates[g])
                        for g in gates)
        return Evidence(head_sha=head, gates=results)

    def gate_evidence(self, feature: Feature, gates: Sequence[str], attempts: int = 3) -> Evidence:
        """Run *gates* and bind them to the branch HEAD; retry if another role committed meanwhile."""
        for _attempt in range(attempts):
            head = self._head(feature)
            evidence = self._run_gates(gates, head)
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
        head = self._head(feature)
        claim = self.claims.claim(task_id, role=task.role, branch=feature.branch, head_sha=head)
        try:
            workspace = ScopedWorkspace(self.policy, task.role, grants=task.grants)
            result = self.runners[task.role].run(RunRequest(task_id, task.role, prompt or task.title, workspace))
            run = TaskRun(task_id, TaskState.IN_PROGRESS, result)
            if result.status == "unavailable":
                return self._defer(run, "runner-unavailable", task.role, result.error)
            if not result.ok:
                return self._finish(task_id, TaskState.FAILED, f"runner {result.status}: {result.error}", run)
            try:
                run.commit = self._commit(feature, task, head, result.changed_paths)
            except UnstableFileError as exc:
                return self._defer(run, "commit-deferred", task.role, str(exc))
            except HeadMismatchError as exc:
                return self._finish(task_id, TaskState.NEEDS_HUMAN, f"commit conflicts with the branch: {exc}", run)
            except (ScopeViolationError, GitOperationError) as exc:
                return self._finish(task_id, TaskState.FAILED, f"commit refused: {exc}", run)
            return self._verify(feature, task, run)
        finally:
            # Only a task still in-progress is affected: it goes back to ready for a later claim.
            self.claims.release(claim, yield_task=True)

    def _commit(self, feature: Feature, task: Task, head: str, changed: Sequence[str]) -> str | None:
        if not changed:
            return None
        files = {p: file_digest(self.root / p) for p in changed}
        try:
            return self.git.commit(CommitRequest(feature.id, task.id, task.role, feature.branch, head, files,
                                                 task.title, task.grants), author=self.author).sha
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
            if state is TaskState.VERIFIED:
                if run.evidence is None or run.evidence.head_sha != self._head(feature):
                    self.store.save_task(task)
                    return None
                transition_task(task, state, reason, evidence=run.evidence, expected_head=run.evidence.head_sha)
            else:
                transition_task(task, state, reason)
            self.store.save_task(task)
            tasks = self.store.load_tasks(feature)
            tasks[task_id] = task
            changed = refresh_readiness(tasks)
            for changed_id in changed:
                self.store.save_task(tasks[changed_id])
            run.unblocked = [t for t in changed if tasks[t].state is TaskState.READY]
            self._sync_task_labels(tasks)
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
            transition_task(task, TaskState.VERIFIED, "evidence recorded", evidence=evidence,
                            expected_head=self._head(feature))
            self.store.save_task(task)
            tasks = self.store.load_tasks(feature)
            tasks[task_id] = task
            unblocked = refresh_readiness(tasks)
            for changed in unblocked:
                self.store.save_task(tasks[changed])
            self._sync_task_labels(tasks)
        return unblocked

    def reopen_task(self, task_id: str, reason: str) -> list[str]:
        """Re-open a verified task (e.g. its contract must change); verified dependents re-verify."""
        with self.store.transaction():
            task = self.store.load_task(task_id)
            feature = self.store.load_feature(task.feature_id)
            transition_task(task, TaskState.READY, reason)
            tasks = self.store.load_tasks(feature)
            tasks[task_id] = task
            blocked = invalidate_dependents(task_id, tasks)
            for changed in (task_id, *blocked):
                self.store.save_task(tasks[changed])
            self._sync_task_labels(tasks)
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
                backend = Task(id=contract_id, feature_id=feature.id, role="backend", title=title,
                               acceptance=(contract,), required_gates=CONTRACT_GATES, provides_contract=True,
                               state=TaskState.READY)
                issue = self.tracker.ensure_issue(f"task:{contract_id}", f"[backend] {title}",
                                                  self._task_body(backend) + f"\n\nRequested contract:\n{contract}",
                                                  [ROLE_LABELS["backend"]])
                backend.issue_number = issue.number
                feature.task_ids.append(contract_id)
                tasks[contract_id] = backend
                self.store.save_task(backend)
                self.store.save_feature(feature)
            if contract_id not in consumer.depends_on:
                consumer.depends_on = (*consumer.depends_on, contract_id)
                tasks[consumer_id] = consumer
                validate_dag(tasks)  # rejects a cycle before anything is saved for the consumer
                if consumer.issue_number is not None and backend.issue_number is not None:
                    self.tracker.link_dependency(consumer.issue_number, backend.issue_number)
            if backend.state is not TaskState.VERIFIED:
                # Only this consumer step waits; an in-progress worker keeps its claim until it yields.
                transition_task(consumer, TaskState.BLOCKED, f"waiting for {contract_id}")
            self.store.save_task(consumer)
            self._sync_task_labels({consumer_id: consumer})
            self.store.append_event("contract-requested", consumer=consumer_id, contract_task=contract_id)
        return backend

    # -- integration and the single PR -------------------------------------------

    def integrate(self, feature_id: str, *, gates: Sequence[str] = DEFAULT_INTEGRATION_GATES) -> Feature:
        """All tasks verified, clean checkout, integration gates at the final HEAD → PR candidate.

        Runs as a quiescent barrier: project.lock EXCLUSIVE, so no scoped writer is active and
        no new claim can start while the combined gates run."""
        with self.store.locks.project(shared=False, busy_error=WritersActiveError).acquire():
            return self._integrate(feature_id, gates)

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
        lines = [f"Feature `{feature.id}`: {feature.title}", "", f"Parent issue: #{feature.issue_number}", "",
                 "## Component tasks", *[f"- `{t.id}` ({t.role}) — issue #{t.issue_number}, commits "
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
        with self.store.transaction():
            feature = self.store.load_feature(feature_id)
            if feature.state is not FeatureState.REVIEW or feature.pull_request is None:
                raise PullRequestPolicyError(f"feature {feature_id!r} has no reviewed PR candidate")
            if feature.pull_request.url:
                raise PullRequestPolicyError(f"feature {feature_id!r} already has its PR: {feature.pull_request.url}")
            if self._head(feature) != feature.pull_request.head_sha:
                raise PullRequestPolicyError("the branch moved after integration; integrate again")
            candidate = feature.pull_request
            pr = self.tracker.create_pull_request(candidate.head, candidate.base, candidate.title, candidate.body)
            candidate.url = f"pr#{pr.number}"
            candidate.ci_state = "pending"
            if feature.issue_number is not None:
                self.tracker.set_labels(feature.issue_number, add=[REVIEW_LABEL])
            self.store.save_feature(feature)
            self.store.append_event("pull-request-opened", feature=feature_id, pr=pr.number)
        return pr

    def complete(self, feature_id: str, *, merged_sha: str) -> Feature:
        """Mark the feature done — only with merge evidence for its single PR."""
        with self.store.transaction():
            feature = self.store.load_feature(feature_id)
            if feature.pull_request is None or not feature.pull_request.url:
                raise EvidenceError("done requires the feature PR to exist and be merged")
            transition_feature(feature, FeatureState.DONE, "PR merged after human review", merged_sha=merged_sha)
            for task in self.store.load_tasks(feature).values():
                if task.issue_number is not None:
                    self.tracker.close(task.issue_number)
            if feature.issue_number is not None:
                self.tracker.close(feature.issue_number)
            self.store.save_feature(feature)
            self.store.append_event("feature-done", feature=feature_id, merged=merged_sha)
        return feature
