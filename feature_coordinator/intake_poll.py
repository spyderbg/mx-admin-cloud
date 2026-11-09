"""W4.3.1: the GitHub intake poll — approved Issues become features with one task (see intake.py).

`IntakePoller.poll()` is one bounded poll: discover `/worker approve` comments on eligible Issues
whose `updated_at` changed (at most `max_issues_per_poll`), record each valid approval (write-ahead),
plan its feature (workflow.plan: the existing records, state machine, Issue and board sync), post
one acknowledgement, activate the highest-priority approved planned feature on a free checkout, and compare every
approved, unfinished Issue with its approved text (a difference holds the task: fail closed).
`verify_task()` is the Worker service's re-check right before an agent may start and while it
runs: a withdrawn approval holds the task, or stops its run through the existing cancellation.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

from .backlog_priority import ordered_backlog
from .errors import (
    ClaimError,
    CoordinatorError,
    GitHubError,
    IllegalTransitionError,
    LockBusyError,
    ProjectBoardError,
)
from .escalation import HOLDABLE
from .github_api import sanitize
from .intake import (
    IntakeRecord,
    feature_id_for,
    issue_problem,
    load_intake,
    not_eligible,
    parse_worker_command,
    spec_digest,
    spec_problem,
)
from .issue_sync import IssueSync, SimulatedCrash, enqueue_comment
from .issues import RemoteComment, RemoteIssue, normalize_body, parse_op
from .model import FeatureState, utc_now
from .planning import load_plan, task_instructions
from .workflow import ACTIVE_FEATURE_STATES, CANCEL_FLAG, FeatureSpec, TaskSpec

CURSOR_FLAG = "intake-poll"
ACTOR = "github-intake"


# ---------------------------------------------------------------------------
# Polling
# ---------------------------------------------------------------------------

@dataclass
class IntakeReport:  # pylint: disable=too-many-instance-attributes
    """What one poll did (apply) or would do (dry run)."""

    applied: bool
    examined: list[int] = field(default_factory=list)            # Issues whose new comments were read
    approved: list[dict[str, Any]] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(default_factory=list)  # from authorized authors
    ignored: list[dict[str, Any]] = field(default_factory=list)   # unauthorized authors
    duplicates: list[dict[str, Any]] = field(default_factory=list)
    planned: list[str] = field(default_factory=list)
    activated: list[str] = field(default_factory=list)
    waiting: list[str] = field(default_factory=list)
    changed: list[dict[str, Any]] = field(default_factory=list)
    restored: list[str] = field(default_factory=list)
    writes: list[str] = field(default_factory=list)               # dry run: what would be written
    deferred: list[str] = field(default_factory=list)
    plans: list[dict[str, Any]] = field(default_factory=list)     # W4.5: plan versions published on Issues

    @property
    def ok(self) -> bool:
        """True when nothing was deferred."""
        return not self.deferred

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        return {**asdict(self), "ok": self.ok}

    def lines(self) -> list[str]:
        """Human-readable lines (also the service's log lines)."""
        verb = "" if self.applied else "would "
        out = [f"approved #{a['issue']} by {a['author']} for role {a['role']}: {verb}record feature {a['feature']}"
               for a in self.approved]
        out += [f"rejected /worker command on #{r['issue']} by {r['author']} (comment {r['comment']}): {r['reason']}"
                for r in self.rejected]
        out += [f"ignored /worker command on #{r['issue']} by {r['author']}: {r['reason']}" for r in self.ignored]
        out += [f"duplicate approval on #{d['issue']} (comment {d['comment']}) ignored: {d['reason']}"
                for d in self.duplicates]
        out += [f"published plan v{p['version']} on #{p['issue']}: {', '.join(p['tasks'])} (sha256 {p['sha256'][:16]})"
                for p in self.plans]
        out += [f"planned {f}" for f in self.planned] + [f"activated {f}" for f in self.activated]
        out += [f"waiting: {w}" for w in self.waiting]
        out += [f"CHANGED after approval: #{c['issue']} ({c['task']}): {c['reason']}" for c in self.changed]
        out += [f"approved text restored on {f} (the hold stays until an operator releases it)" for f in self.restored]
        out += [f"dry run: {w}" for w in self.writes]
        out += [f"deferred: {sanitize(d, 300)}" for d in self.deferred]
        return out


class IntakePoller:
    """One bounded poll of the GitHub intake for one coordinator (see the module docstring)."""

    def __init__(self, coord: Any, *, faults: Iterable[str] = (), planner: Any = None,  # pylint: disable=too-many-arguments
                 heartbeat: Any = None, cancelled: Any = None, clock: Any = None) -> None:
        self.coord = coord
        self.store = coord.store
        self.config = coord.config
        self.cfg = coord.config.github.intake
        self.client = coord.sync.client
        # A fresh synchronizer per poll: the author permissions it caches are re-read every poll.
        self.authority = IssueSync(coord.store, self.client, coord.config)
        self.faults = set(faults)
        # W4.5 (intake_plan.py): the planner and the Worker service's hooks while it runs (heartbeat, stop/pause)
        self.planning_hooks = {"planner": planner, "heartbeat": heartbeat, "cancelled": cancelled,
                               **({"clock": clock} if clock is not None else {})}

    def _fault(self, name: str) -> None:
        if name in self.faults:
            self.faults.discard(name)
            raise SimulatedCrash(name)

    # -- the poll ------------------------------------------------------------------------

    def poll(self, *, apply: bool) -> IntakeReport:
        """Discover approvals, then advance every approved Issue (plan, activate, verify the text).
        A dry run reads GitHub and the local state and writes nothing."""
        report = IntakeReport(apply)
        if apply and not self.cfg.enabled:
            raise CoordinatorError("github.intake.enabled is false: the GitHub intake is off until an operator "
                                   "enables it in the project configuration")
        lock = self.store.locks.intake() if apply else None
        if lock is not None:
            try:
                lock.acquire()
            except LockBusyError:
                report.deferred.append("another intake poll is running")
                return report
        try:
            self._discover(report, apply)
            planning = self._planning()
            if planning is not None:
                planning.review(report, apply)        # W4.5: label approvals become intake records
            self._advance(report, apply)
            if planning is not None:
                planning.plan_requests(report, apply)  # W4.5: at most one planner run, last
        except GitHubError as exc:
            report.deferred.append(sanitize(str(exc)))
        finally:
            if lock is not None:
                lock.release()
        return report

    def _planning(self) -> Any:
        """W4.5: the label-driven planning pass, when `github.planning.enabled`."""
        if not self.config.github.planning.enabled:
            return None
        from .intake_plan import IssuePlanning  # pylint: disable=import-outside-toplevel
        return IssuePlanning(self, **self.planning_hooks)

    def _discover(self, report: IntakeReport, apply: bool) -> None:
        before = dict((self.store.get_flag(CURSOR_FLAG) or {}).get("issues") or {})
        cursor = dict(before)
        open_numbers: set[int] = set()
        for issue in self.client.list_issues(state="open"):
            if not_eligible(issue) is not None:
                continue
            open_numbers.add(issue.number)
            mark = cursor.get(str(issue.number)) or {}
            if mark.get("updated_at") == issue.updated_at:
                continue  # unchanged since it was last read
            if issue.comments == 0:
                cursor[str(issue.number)] = {"updated_at": issue.updated_at, "comment": int(mark.get("comment", 0))}
                continue
            if len(report.examined) >= self.cfg.max_issues_per_poll:
                continue  # read in a later poll (its mark is unchanged)
            report.examined.append(issue.number)
            cursor[str(issue.number)] = self._examine(issue, int(mark.get("comment", 0)), report, apply)
        kept = {k: v for k, v in cursor.items() if int(k) in open_numbers}
        if apply and kept != before:
            with self.store.transaction():
                self.store.set_flag(CURSOR_FLAG, {"issues": kept, "updated_at": utc_now()})

    def _examine(self, issue: RemoteIssue, last: int, report: IntakeReport, apply: bool) -> dict[str, Any]:
        """Read the Issue's new comments; act on `/worker` commands (oldest first)."""
        approved = load_intake(self.store, feature_id_for(issue.number)) is not None
        for comment in sorted(self.client.list_comments(issue.number), key=lambda c: c.id):
            if comment.id <= last:
                continue
            last = comment.id
            if self._own(comment):
                continue
            command = parse_worker_command(comment.body)
            if command is None:
                continue
            entry = {"issue": issue.number, "comment": comment.id, "author": comment.author}
            if approved:
                self._note(report.duplicates, "intake-duplicate-ignored",
                           {**entry, "reason": "the Issue is already approved; its recorded specification stays"},
                           apply)
                continue
            refusal = self.authority.authorize(comment)
            if refusal is not None:
                self._note(report.ignored, "intake-approval-ignored", {**entry, "reason": refusal}, apply)
                continue
            problem = self._approval_problem(issue, command)
            if problem is not None:
                self._note(report.rejected, "intake-approval-rejected", {**entry, "reason": problem}, apply)
                continue
            approved = self._approve(issue, comment, command[1], report, apply)
        return {"updated_at": issue.updated_at, "comment": last}

    def _own(self, comment: RemoteComment) -> bool:
        parsed = parse_op(comment.body)
        return parsed is not None and parsed[0] == self.config.github.coordinator_id \
            and comment.author == self.authority.viewer()

    def _note(self, bucket: list[dict[str, Any]], kind: str, entry: dict[str, Any], apply: bool) -> None:
        bucket.append(entry)
        if apply:
            with self.store.transaction():
                self.store.append_event(kind, **{**entry, "reason": sanitize(entry["reason"], 300)})

    def _approval_problem(self, issue: RemoteIssue,  # pylint: disable=too-many-return-statements
                          command: tuple[str, str, str]) -> str | None:
        verb, role, digest = command
        if verb != "approve":
            return (f"unrecognized command {role!r}: use `/worker approve <role> <spec-sha256>` "
                    f"(`python -m feature_coordinator github intake show {issue.number}` prints it)")
        problem = spec_problem(issue, self.cfg.max_spec_characters)
        if problem is not None:
            return problem
        if digest != spec_digest(issue.title, issue.body):
            return ("the digest does not match the Issue's current title and body (changed after review?); review it "
                    f"again (`python -m feature_coordinator github intake show {issue.number}`) and approve the new "
                    "digest")
        if role not in self.config.roles:
            return f"unknown role {role!r}"
        if not self.config.role(role).enabled:
            return f"role {role!r} is disabled in the configuration"
        if not self.cfg.role_gates.get(role):
            return f"no intake gates are configured for role {role!r} (github.intake.role_gates)"
        feature_id = feature_id_for(issue.number)
        try:
            self.store.load_feature(feature_id)
            return f"a feature {feature_id!r} already exists in the local state"
        except KeyError:
            pass
        branch = self.config.branch_for(feature_id)
        if self.coord.git.branch_exists(branch):
            return f"branch {branch!r} already exists"
        return None

    def _approve(self, issue: RemoteIssue, comment: RemoteComment, role: str, report: IntakeReport,
                 apply: bool) -> bool:
        feature_id = feature_id_for(issue.number)
        entry = {"issue": issue.number, "comment": comment.id, "author": comment.author, "role": role,
                 "feature": feature_id, "digest": spec_digest(issue.title, issue.body)}
        report.approved.append(entry)
        if not apply:
            return True
        record = IntakeRecord(
            id=feature_id, issue_number=issue.number, issue_id=issue.id, node_id=issue.node_id, url=issue.url,
            issue_author=issue.author, title=issue.title.strip(), body=normalize_body(issue.body),
            digest=entry["digest"], role=role, gates=list(self.cfg.role_gates[role]), task_id=f"{feature_id}.{role}",
            approved_by=comment.author, approval_comment_id=comment.id, approval_comment_url=comment.url,
            approved_at=comment.created_at)
        with self.store.transaction():
            if load_intake(self.store, feature_id) is not None:  # another poll recorded it first
                report.approved.pop()
                return True
            self.store.save_record("intake", record)
            self.store.append_event("intake-approved", issue=issue.number, feature=feature_id, task=record.task_id,
                                    role=role, digest=record.digest, approver=comment.author, comment=comment.id)
        self._fault(f"crash-after-approval:{issue.number}")
        return True

    # -- advancing approved Issues -------------------------------------------------------------

    def records(self) -> list[IntakeRecord]:
        """Every intake record, oldest Issue first."""
        return sorted((IntakeRecord.from_dict(raw) for raw in self.store.list_records("intake")),
                      key=lambda r: r.issue_number)

    def _advance(self, report: IntakeReport, apply: bool) -> None:
        records = self.records()
        for record in records:
            self._ensure_planned(record, report, apply)
        for record in records:
            try:
                feature = self.store.load_feature(record.id)
            except KeyError:
                continue  # planning was deferred
            if feature.state is not FeatureState.DONE:
                self._check_spec(load_intake(self.store, record.id) or record, report, apply)
        self._activate_next(report, apply)

    def _ensure_planned(self, record: IntakeRecord, report: IntakeReport, apply: bool) -> None:
        """Create the feature and its single task (once), then acknowledge the approval (once)."""
        try:
            self.store.load_feature(record.id)
            exists = True
        except KeyError:
            exists = False
        if exists and record.status != "approved":
            return
        if not apply:
            report.writes.append(f"acknowledge the approval on #{record.issue_number}" if exists else
                                 f"plan feature {record.id} with tasks {', '.join(record.all_task_ids)} for "
                                 f"#{record.issue_number}")
            return
        if not exists:
            spec = self._feature_spec(record)
            try:
                self.coord.plan(spec)
            except CoordinatorError as exc:
                try:
                    self.store.load_feature(record.id)  # planned by a concurrent caller: continue
                except KeyError:
                    report.deferred.append(f"{record.id}: planning failed: {sanitize(str(exc), 300)}")
                    return
            self._fault(f"crash-after-plan:{record.id}")
        with self.store.transaction():
            current = load_intake(self.store, record.id)
            if current is None or current.status != "approved":
                return
            current.status = "planned"
            self.store.save_record("intake", current)
            enqueue_comment(self.store, record.anchor_id, self._acknowledgement(current))
            self.store.append_event("intake-planned", issue=record.issue_number, feature=record.id,
                                    task=record.task_id, tasks=record.all_task_ids)
        report.planned.append(record.id)
        self.coord.sync_issues(record.id)  # posts the acknowledgement; refreshes the board

    def _feature_spec(self, record: IntakeRecord) -> FeatureSpec:
        """The feature of an approval: ONE task of the approved role (`/worker approve`), or (W4.5) the
        approved plan's tasks with their roles, dependencies and gates."""
        if not record.planned:
            return FeatureSpec(slug=record.id, title=sanitize(record.title, 200), tasks=(TaskSpec(
                key=record.role, role=record.role, title=sanitize(record.title, 200),
                acceptance=(self._instructions(record),), required_gates=tuple(record.gates)),))
        assert record.plan is not None
        plan = record.plan["canonical"]
        tasks = tuple(TaskSpec(
            key=t["id"], role=t["role"], title=sanitize(t["title"], 200), depends_on=tuple(t["depends_on"]),
            required_gates=tuple(t["gates"]),
            acceptance=(task_instructions(plan, t, approval_sha=record.plan["approval_sha256"],
                                          approved_by=record.approved_by, title=record.title, body=record.body),))
            for t in plan["tasks"])
        return FeatureSpec(slug=record.id, title=sanitize(plan["feature"]["title"], 200), tasks=tasks,
                           acceptance=tuple(plan["feature"]["acceptance_criteria"]),
                           base_branch=plan["feature"]["base_branch"])

    def _instructions(self, record: IntakeRecord) -> str:
        return (f"Implement GitHub Issue #{record.issue_number} as approved by {record.approved_by} (specification "
                f"sha256 {record.digest[:16]}). Work only within this role's paths. The approved specification "
                f"follows; it is the complete task:\n\n# {record.title}\n\n{record.body}")

    def _acknowledgement(self, record: IntakeRecord) -> str:
        project = self.config.github.project
        board = f"Project #{project.number}" if project.enabled else "the coordinator state"
        if record.planned:
            assert record.plan is not None
            tasks = ", ".join(f"`{t}`" for t in record.all_task_ids)
            return (f"**Approved for the Worker** by `{record.approved_by}` (label `worker:approve`, receipt comment "
                    f"{record.approval_comment_id}, validated by the coordinator). This Issue's title and body (spec "
                    f"sha256 `{record.digest[:16]}…`) and execution plan v{record.plan['version']} (approval sha256 "
                    f"`{record.plan['approval_sha256'][:16]}…`) are now feature `{record.id}`, branch "
                    f"`{self.config.branch_for(record.id)}`, with tasks {tasks}.\n\n"
                    "The approved text and plan are recorded locally and do not change: later edits to this Issue are "
                    "never used, and an edit stops every task of the feature for an operator. Progress is shown on "
                    f"{board}; moving the card there changes nothing.")
        return (f"**Approved for the Worker** by `{record.approved_by}` (comment {record.approval_comment_id}). "
                f"This Issue's title and body (spec sha256 `{record.digest[:16]}…`) are now the specification of task "
                f"`{record.task_id}` (role `{record.role}`, gates {', '.join(f'`{g}`' for g in record.gates)}) in "
                f"feature `{record.id}`, branch `{self.config.branch_for(record.id)}`.\n\n"
                "The approved text is recorded locally and does not change: later edits to this Issue are never "
                "used, and an edit stops the task for an operator. Progress is shown on "
                f"{board}; moving the card there changes nothing.")

    def _activate_next(self, report: IntakeReport, apply: bool) -> None:
        """Activate the highest-priority approved planned feature on a free checkout."""
        planned = []
        for record in self.records():
            try:
                feature = self.store.load_feature(record.id)
            except KeyError:
                continue
            if feature.state is not FeatureState.PLANNED and record.status == "planned" and apply:
                self._update(record.id, lambda r: setattr(r, "status", "active"))  # activated, not recorded yet
            elif feature.state is FeatureState.PLANNED and record.status == "planned":
                planned.append(record)
        report.waiting += [f"{r.id} is not activated: its Issue changed after approval" for r in planned
                           if r.spec_changed]
        planned = [r for r in planned if not r.spec_changed]
        if not planned:
            return
        active = [f.id for f in self.store.list_features() if f.state in ACTIVE_FEATURE_STATES]
        if active:
            report.waiting += [f"{r.id} stays in Backlog: feature {active[0]} is active (one feature per checkout)"
                               for r in planned]
            return
        try:
            planned = ordered_backlog(planned, self.coord.board)
        except (GitHubError, ProjectBoardError) as exc:
            report.deferred.append(f"Backlog Priority read failed; no feature activated: {sanitize(str(exc), 300)}")
            return
        record = planned[0]
        if not apply:
            report.writes.append(f"activate {record.id} (branch {self.config.branch_for(record.id)})")
            return
        try:
            self.coord.activate(record.id)
        except CoordinatorError as exc:
            error = sanitize(str(exc), 300)
            report.deferred.append(f"{record.id}: activation deferred: {error}")
            self._update(record.id, lambda r: setattr(r, "activation_error", error),
                         event=("intake-activation-deferred", {"feature": record.id, "error": error})
                         if error != record.activation_error else None)
            return

        def activated(r: IntakeRecord) -> None:
            r.status, r.activation_error = "active", ""
        self._update(record.id, activated, event=("intake-activated", {"feature": record.id,
                                                                        "issue": record.issue_number}))
        report.activated.append(record.id)

    def _update(self, feature_id: str, mutate: Any, event: tuple[str, dict[str, Any]] | None = None) -> None:
        with self.store.transaction():
            record = load_intake(self.store, feature_id)
            if record is None:
                return
            mutate(record)
            self.store.save_record("intake", record)
            if event is not None:
                self.store.append_event(event[0], **event[1])

    # -- the approved text stays the approved text ------------------------------------------------

    def _check_spec(self, record: IntakeRecord, report: IntakeReport, apply: bool) -> str:
        current, reason = issue_problem(self.client, record)
        if not reason:
            if record.spec_changed:
                report.restored.append(record.id)
                if apply:
                    self._update(record.id, lambda r: setattr(r, "spec_changed", None),
                                 event=("intake-spec-restored", {"feature": record.id, "issue": record.issue_number}))
            return ""
        report.changed.append({"issue": record.issue_number, "task": record.task_id, "reason": reason})
        if apply:
            self._fail_closed(record, current, reason)
        else:
            report.writes.append(f"hold {record.task_id}: {reason}")
        return reason

    def _fail_closed(self, record: IntakeRecord, current: str, reason: str) -> None:
        """Record the withdrawn approval (once per distinct state), notify once, hold the task when it can
        be held — a running one is first stopped through the existing graceful cancellation, at its
        next safe point (completed checkpoints stay); its result is not accepted (workflow.py)."""
        if not record.spec_changed or record.spec_changed.get("digest") != current \
                or record.spec_changed.get("reason") != sanitize(reason, 300):
            with self.store.transaction():
                fresh = load_intake(self.store, record.id)
                if fresh is not None:
                    first = not fresh.spec_changed
                    fresh.spec_changed = {"digest": current, "reason": sanitize(reason, 300), "detected_at": utc_now()}
                    self.store.save_record("intake", fresh)
                    self.store.append_event("intake-spec-changed", feature=record.id, issue=record.issue_number,
                                            task=record.task_id, reason=sanitize(reason, 300))
                    if first and current and record.planned:  # W4.5: every task of the plan is held
                        tasks = ", ".join(f"`{t}`" for t in record.all_task_ids)
                        enqueue_comment(self.store, record.anchor_id, (
                            f"**Approval withdrawn**: {sanitize(reason, 300)}. Tasks {tasks} are stopped and held "
                            "for an operator; they only ever run the approved text and plan, and this Issue is never "
                            "reopened or edited by the coordinator. To continue, restore the approved title and body "
                            "(reopen the Issue if it was closed), then release each held task "
                            "(`python -m feature_coordinator release <task> --apply`); to change the work, open a "
                            "new Issue."))
                    elif first and current:  # changed or closed; an Issue that is no longer ours gets no comment
                        enqueue_comment(self.store, record.anchor_id, (
                            f"**Approval withdrawn**: {sanitize(reason, 300)}. Task `{record.task_id}` is stopped and "
                            "held for an operator; it only ever runs the approved text, and this Issue is never "
                            "reopened or edited by the coordinator. To continue, restore the approved title and body "
                            "(reopen the Issue if it was closed), then release the task "
                            f"(`python -m feature_coordinator release {record.task_id} --apply`); to change the "
                            "task, open a new Issue."))
        held = False
        for task_id in record.all_task_ids:  # W4.5: every task of a planned feature
            self._stop_running(record, reason, task_id)
            if self.store.load_task(task_id).state in HOLDABLE:
                try:
                    self.coord.hold_task(task_id, reason=f"approved Issue #{record.issue_number} changed: "
                                                         f"{sanitize(reason, 200)}", actor=ACTOR)
                    held = True
                except (ClaimError, IllegalTransitionError):
                    pass  # it was launched meanwhile: the hold follows once it can be applied
        if not held:
            self.coord.sync_issues(record.id)  # the notice (hold_task synchronizes by itself)

    def _stop_running(self, record: IntakeRecord, reason: str, task_id: str | None = None) -> None:
        """A running intake task: ask its run to stop (`request_cancel`, the operator's
        `python -m feature_coordinator stop`), once per claim."""
        task_id = task_id or record.task_id
        claim = self.store.load_claim(task_id)
        flag = self.store.get_flag(CANCEL_FLAG.format(task=task_id))
        if claim is None or (flag and flag.get("token") == claim.get("token")):
            return
        try:
            self.coord.request_cancel(task_id, reason=sanitize(f"approval withdrawn: {reason}", 300))
        except ClaimError:
            pass  # released meanwhile

    def verify_task(self, task: Any) -> str | None:
        """For an intake task: re-read its Issue (before a launch, and while it runs). Returns why it
        must not start or continue (it is then held, or its run is asked to stop), or None. Not an
        intake task: None, no request. GitHub errors propagate: never proceed unchecked."""
        record = load_intake(self.store, task.feature_id)
        if record is None or task.id not in record.all_task_ids:
            return None
        current, reason = issue_problem(self.client, record)
        if not reason:
            return None
        self._fail_closed(load_intake(self.store, record.id) or record, current, reason)
        return reason

    # -- read-only views ---------------------------------------------------------------------------

    def describe(self, number: int) -> dict[str, Any]:
        """Everything an operator needs to review Issue *number* before approving it (read-only)."""
        issue = self.client.get_issue(number)
        feature_id = feature_id_for(number)
        record = load_intake(self.store, feature_id)
        digest = spec_digest(issue.title, issue.body)
        roles = {role: list(gates) for role, gates in self.cfg.role_gates.items()
                 if role in self.config.roles and self.config.role(role).enabled and gates}
        data: dict[str, Any] = {
            "number": issue.number, "title": sanitize(issue.title, 200), "state": issue.state, "author": issue.author,
            "url": issue.url, "labels": [sanitize(label, 60) for label in issue.labels], "digest": digest,
            "characters": len(normalize_body(issue.body)), "eligible": not_eligible(issue) is None,
            "not_eligible": not_eligible(issue) or spec_problem(issue, self.cfg.max_spec_characters),
            "roles": roles, "approve": {role: f"/worker approve {role} {digest}" for role in roles},
            "intake_enabled": self.cfg.enabled, "feature": feature_id, "approved": None}
        if record is not None:
            data["approved"] = {"by": record.approved_by, "at": record.approved_at, "role": record.role,
                                "digest": record.digest, "status": record.status, "task": record.task_id,
                                "tasks": record.all_task_ids, "matches_issue": record.digest == digest,
                                "spec_changed": record.spec_changed,
                                "plan_version": record.plan["version"] if record.plan else None}
        plan = load_plan(self.store, feature_id)
        data["plan"] = None if plan is None else {
            "status": plan.status, "version": plan.latest["version"] if plan.latest else None,
            "sha256": plan.latest["sha256"] if plan.latest else None,
            "comment": plan.latest.get("comment_url") if plan.latest else None,
            "tasks": [f"{t['id']} ({t['role']})" for t in plan.latest["plan"]["tasks"]] if plan.latest else [],
            "attention": plan.attention, "planning_enabled": self.config.github.planning.enabled}
        return data


def planning_hooks(store: Any, heartbeat: Any) -> dict[str, Any]:
    """W4.5: the Worker service's hooks for a planner run inside its intake poll (minutes): the service record
    keeps its *heartbeat*, and a stop signal, `worker stop` or `worker pause` ends the run (retried later)."""
    from .autonomy import read_control  # pylint: disable=import-outside-toplevel
    from .supervisor import stop_requested  # pylint: disable=import-outside-toplevel
    last = {"at": float("-inf"), "stop": False}

    def cancelled() -> bool:
        now = time.monotonic()
        if now - float(last["at"]) >= 1.0:  # the control file is read at most once a second
            control = read_control(store)
            last.update(at=now, stop=bool(control.get("stop") or control.get("pause")))
        return stop_requested() or bool(last["stop"])
    return {"heartbeat": heartbeat, "cancelled": cancelled}


def intake_status(store: Any) -> list[dict[str, Any]]:
    """Every intake record with its feature and task state (read-only, local)."""
    rows = []
    for raw in store.list_records("intake"):
        record = IntakeRecord.from_dict(raw)
        row: dict[str, Any] = {"issue": record.issue_number, "feature": record.id, "task": record.task_id,
                               "tasks": record.all_task_ids,
                               "role": record.role or (f"plan v{record.plan['version']}" if record.plan else ""),
                               "status": record.status, "approved_by": record.approved_by,
                               "approved_at": record.approved_at, "digest": record.digest,
                               "spec_changed": record.spec_changed, "activation_error": record.activation_error,
                               "feature_state": None, "task_state": None}
        try:
            row["feature_state"] = store.load_feature(record.id).state.value
            row["task_state"] = store.load_task(record.task_id).state.value
        except KeyError:
            pass
        rows.append(row)
    return sorted(rows, key=lambda r: r["issue"])
