"""W4.5: GitHub-native planning and approval of Issues (labels), on top of the W4.3.1 intake.

    worker:plan             a person asks for a plan            → the Desktop Worker plans (Claude Code,
                                                                  subscription), validates, publishes
    worker:ready-for-review set by the coordinator: plan vN is published and waits for review
    worker:approve          a person approves the published plan → the GitHub Action posts a receipt;
                                                                  the coordinator re-verifies it all
    worker:approved         set by the coordinator: the approval was validated; the feature is planned

One bounded pass, called by the intake poll (intake_poll.py) under its lock: `review()` (steps 1 and 2)
before approved features are planned and activated by the existing intake lifecycle, `plan_requests()`
(step 3, the slow one) after:

  1. approvals: an Issue labeled `worker:approve` — the approval receipt of the newest such label
     event is validated INDEPENDENTLY (below); valid → an intake record of schema 2 (write-ahead) whose
     feature the poll then plans with the plan's tasks and activates as usual. Invalid, stale, forged
     or missing (after `receipt_wait_seconds`) → refused with one notice, the label removed;
  2. published plans whose Issue title or body changed since planning become stale (one notice);
  3. plan requests: an Issue labeled `worker:plan` whose newest label event is not answered yet, by an
     authorized actor (`command_authors`, `command_min_permission`) → at most ONE planner run per
     pass (write-ahead: the request and each attempt are recorded first; at most `max_attempts`
     invocations per request, also across crashes), deterministic validation (planning.py), the plan
     recorded as the next version, then published as one comment (operation marker: found again,
     never duplicated, after a crash). A refused or unassignable plan → operator attention.

Approval evidence (the receipt) is never trusted as such. The coordinator accepts an approval only if
ALL of these hold, re-read from GitHub and its own state at validation time:

  * the receipt comment's author is `receipt_author` (`github-actions[bot]`; no person can post as that
    login, but every workflow of the repository can) and it is the only receipt for its label event; it
    is a well-formed `RECEIPT_FORMAT` object and was never edited (`updated_at` = `created_at`);
  * repository (name and ID) and Issue (number, database ID, node ID) match;
  * GitHub's own Issue events show that label event: `labeled` `worker:approve`, same ID, actor and
    time, and it is the NEWEST approval label event; the label is still present;
  * the actor is a command author with at least the minimum permission NOW;
  * the receipt names the latest plan version, its digest and its published comment; that comment
    still carries exactly the recorded plan; the plan was published before the label was added, is
    still `published` (not stale, not being revised) and still valid under the current configuration;
  * the Issue's title and body are unchanged since planning (spec digest);
  * the approval digest recomputed locally (plan_digest.approval_statement) equals the receipt's;
  * the GitHub Actions run named by the receipt exists, ran `receipt_workflow` for an `issues`
    event on the default branch, was triggered by the same actor, and was CREATED between the label
    event and the receipt comment (RUN_CLOCK_TOLERANCE); it backs no other receipt on the Issue and no
    approval accepted before (any Issue, local records): one run, one receipt.

The receipt author is NOT added to `command_authors`: it is accepted only as this one structured
receipt, and only as corroboration of facts the coordinator checks itself. The run checks bind a
receipt to a run of this workflow made for this approval; they cannot prove that run posted it, because
any workflow of the repository (on any branch) posts as the same bot. A forged receipt needs write access
(to push a workflow), and can at most corroborate facts the coordinator verifies anyway.

KNOWN LIMITATION (documented, accepted, not solved): the Worker and the operator share one GitHub
identity. GitHub records a label added by the Worker's token exactly like one added by the operator, so
actor and permission checks cannot tell them apart; anything holding that token could approve. The
coordinator itself never adds `worker:approve`. Agent sessions do not get the token while
`runtime.native_hardening` is on (it hides `.config/gh`); with it off (the production default today), an
agent with shell access under the real HOME may reach the `gh` login, exactly as for `/worker approve`.
A separate Worker identity (GitHub App or machine user) is needed to close this.
"""

from __future__ import annotations

import calendar
import time
from typing import Any, Callable, Iterable

from .errors import CoordinatorError, GitHubNotFoundError, GitHubUncertainWriteError
from .github_api import sanitize
from .intake import IntakeRecord, feature_id_for, load_intake, not_eligible, spec_problem
from .issues import RemoteComment, RemoteIssue, normalize_body, op_marker, parse_op
from .model import utc_now
from .plan_digest import (
    APPROVAL_FORMAT,
    LABEL_APPROVE,
    LABEL_APPROVED,
    LABEL_PLAN,
    LABEL_READY,
    PLAN_MARKER,
    RECEIPT_MARKER,
    REJECTION_MARKER,
    WORKER_LABEL_SPECS,
    WORKER_LABELS,
    approval_sha256,
    approval_statement,
    decode_marker,
    latest_label_event,
    plan_sha256,
    receipt_problems,
    spec_sha256,
)
from .planner import Planner, PlannerRequest, conventions, planner_prompt
from .planning import PlanRecord, canonical_plan, load_plan, plan_problems, proposal_schema, render_plan

MAX_COMMENT = 65000
ACTOR = "github-planning"
RUN_CLOCK_TOLERANCE = 5  # seconds: GitHub's whole-second timestamps come from different services


def _epoch(stamp: str) -> float:
    """Seconds since the epoch of a GitHub timestamp (UTC, `Z`)."""
    return float(calendar.timegm(time.strptime(stamp[:19], "%Y-%m-%dT%H:%M:%S")))


class IssuePlanning:  # pylint: disable=too-many-instance-attributes
    """One bounded planning and approval pass (see the module docstring)."""

    def __init__(self, poller: Any, *, planner: Planner | None = None,  # pylint: disable=too-many-arguments
                 heartbeat: Callable[[], None] | None = None, cancelled: Callable[[], bool] | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.poller = poller
        self.coord, self.store, self.config = poller.coord, poller.store, poller.config
        self.cfg = self.config.github.planning
        self.client = poller.client
        self.authority = poller.authority  # a fresh IssueSync per poll: permissions are re-read every poll
        self.planner = planner
        self.heartbeat, self.cancelled, self.clock = heartbeat, cancelled, clock
        self._comments: dict[int, list[RemoteComment]] = {}
        self._events: dict[int, list[dict[str, Any]]] = {}
        self._repository: dict[str, Any] | None = None

    # -- GitHub reads (cached within the pass) -----------------------------------------------

    def comments(self, number: int, *, fresh: bool = False) -> list[RemoteComment]:
        """The Issue's comments, oldest first."""
        if fresh or number not in self._comments:
            self._comments[number] = sorted(self.client.list_comments(number), key=lambda c: c.id)
        return self._comments[number]

    def events(self, number: int) -> list[dict[str, Any]]:
        """The Issue's events as plain dicts (plan_digest.latest_label_event)."""
        if number not in self._events:
            self._events[number] = [{"id": e.id, "event": e.event, "actor": e.actor, "created_at": e.created_at,
                                     "label": e.label} for e in self.client.list_issue_events(number)]
        return self._events[number]

    def repository(self) -> dict[str, Any]:
        """{full_name (lowercase), id, default_branch}: the configured repository, as GitHub reports it."""
        if self._repository is None:
            info = self.client.repository_info()
            name = self.config.github.repository
            if getattr(self.client, "live", False) and str(info.get("full_name", "")).lower() != name.lower():
                raise CoordinatorError(f"the tracker reports repository {info.get('full_name')!r}, not {name!r}")
            if not isinstance(info.get("id"), int) or info["id"] <= 0:
                raise CoordinatorError("the tracker did not report the repository's ID")
            self._repository = {"full_name": name.lower(), "id": int(info["id"]),
                                "default_branch": str(info.get("default_branch") or "")}
        return self._repository

    def _authorize(self, login: str) -> str | None:
        """The `/feature` command rules for the actor of a label event (IssueSync.authorize)."""
        return self.authority.authorize(RemoteComment(id=0, author=login, body="", created_at=""))

    def _own_op(self, number: int, key: str, op: str) -> RemoteComment | None:
        viewer = self.authority.viewer()
        for comment in self.comments(number, fresh=True):
            parsed = parse_op(comment.body)
            if parsed == (self.config.github.coordinator_id, key, op) and comment.author == viewer:
                return comment
        return None

    # -- the pass --------------------------------------------------------------------------

    def labeled(self) -> list[RemoteIssue]:
        """Open, eligible Issues carrying any `worker:*` label (re-read at each call)."""
        return [i for i in self.client.list_issues(state="open")
                if set(i.labels) & set(WORKER_LABELS) and not_eligible(i) is None]

    def review(self, report: Any, apply: bool) -> None:
        """Steps 1 and 2: approvals and stale plans (quick; before approved features are planned)."""
        for issue in self.labeled():
            if LABEL_APPROVE in issue.labels:
                self._approval(issue, report, apply)
            elif LABEL_READY in issue.labels:
                self._check_stale(issue, report, apply)

    def plan_requests(self, report: Any, apply: bool) -> None:
        """Step 3: plan requests, at most one planner run (last in a poll: it may take minutes)."""
        planned = False
        for issue in self.labeled():
            if LABEL_PLAN in issue.labels:
                planned = self._request(issue, report, apply, may_plan=not planned) or planned

    # -- small helpers ------------------------------------------------------------------------

    def _record(self, issue: RemoteIssue) -> PlanRecord:
        """The Issue's plan record, created (inside a transaction) when missing."""
        feature_id = feature_id_for(issue.number)
        record = load_plan(self.store, feature_id)
        if record is None:
            record = PlanRecord(id=feature_id, issue_number=issue.number, issue_id=issue.id, node_id=issue.node_id,
                                url=issue.url)
            self.store.save_record("plan", record)
            record = load_plan(self.store, feature_id)
            assert record is not None
        return record

    def _update(self, issue: RemoteIssue, mutate: Callable[[PlanRecord], None],
                event: tuple[str, dict[str, Any]] | None = None) -> PlanRecord:
        with self.store.transaction():
            record = self._record(issue)
            mutate(record)
            record.updated_at = utc_now()
            self.store.save_record("plan", record)
            if event is not None:
                self.store.append_event(event[0], **event[1])
        fresh = load_plan(self.store, record.id)
        assert fresh is not None
        return fresh

    def _labels(self, issue: RemoteIssue, *, add: Iterable[str] = (), remove: Iterable[str] = ()) -> None:
        """Converge the `worker:*` labels (only those that differ: every label write is an Issue event)."""
        present = set(issue.labels)
        wanted = [label for label in add if label not in present]
        if wanted:
            self.client.add_worker_labels(issue.number, wanted)
        for label in remove:
            if label in present:
                self.client.remove_worker_label(issue.number, label)

    def _notice(self, issue: RemoteIssue, op: str, text: str) -> None:
        """One coordinator comment per operation name, ever (recorded, and found again after a crash)."""
        record = load_plan(self.store, feature_id_for(issue.number))
        if record is not None and op in record.notices:
            return
        key = f"{feature_id_for(issue.number)}/plan"
        if self._own_op(issue.number, key, op) is None:
            self.client.create_comment(issue.number, f"{op_marker(self.config.github.coordinator_id, key, op)}\n{text}")
        self._update(issue, lambda r: r.notices.append(op) if op not in r.notices else None)

    # -- 3. plan requests -------------------------------------------------------------------------

    def _request(self, issue: RemoteIssue, report: Any, apply: bool, *, may_plan: bool) -> bool:  # pylint: disable=too-many-return-statements
        """Answer the newest `worker:plan` label event (True: a planner run happened)."""
        feature_id = feature_id_for(issue.number)
        event = latest_label_event(self.events(issue.number), LABEL_PLAN)
        if event is None:
            report.waiting.append(f"#{issue.number}: `{LABEL_PLAN}` has no label event yet (GitHub lag?)")
            return False
        record = load_plan(self.store, feature_id)
        entry = {"issue": issue.number, "comment": event["id"], "author": event["actor"]}
        if record is not None and event["id"] in record.handled_requests:
            if apply:  # answered; only the label cleanup was interrupted
                self._labels(issue, add=[LABEL_READY] if record.status == "published" else [], remove=[LABEL_PLAN])
            return False
        refusal = self._authorize(event["actor"])
        if refusal is None and load_intake(self.store, feature_id) is not None:
            reason = "the Issue is already approved; its recorded specification stays (open a new Issue to change it)"
            report.duplicates.append({**entry, "reason": reason})
            if apply:
                self._answered(issue, event, f"plan-request-e{event['id']}", f"**Plan request ignored**: {reason}.")
            return False
        if refusal is not None:
            report.ignored.append({**entry, "reason": f"plan request: {refusal}"})
            if apply:
                self._answered(issue, event, f"plan-request-e{event['id']}",
                               f"**Plan request not accepted**: `{LABEL_PLAN}` was added by `{event['actor']}`, who "
                               f"may not request plans ({sanitize(refusal, 200)}). Nothing was planned.")
            return False
        problem = spec_problem(issue, self.config.github.intake.max_spec_characters)
        if problem:
            report.rejected.append({**entry, "reason": f"plan request: {problem}"})
            if apply:
                self._attention(issue, event, f"the Issue cannot be planned: {problem}")
            return False
        if not may_plan:
            report.waiting.append(f"#{issue.number}: plan request queued (one planner run per poll)")
            return False
        if not apply:
            report.writes.append(f"plan #{issue.number} (label event {event['id']} by {event['actor']}) and publish "
                                 "the plan on the Issue")
            return False
        latest = record.latest if record else None
        if latest is not None and latest["request"]["id"] == event["id"] and not latest.get("comment_id"):
            self._publish(issue, report)  # planned before an interruption: publish it now
            return False
        self._plan(issue, event, report)
        return True

    def _answered(self, issue: RemoteIssue, event: dict[str, Any], op: str, text: str) -> None:
        self._update(issue, lambda r: r.handled_requests.append(event["id"]),
                     event=("plan-request-refused", {"issue": issue.number, "label_event": event["id"],
                                                     "actor": event["actor"]}))
        self._notice(issue, op, text)
        self._labels(issue, remove=[LABEL_PLAN])

    def _attention(self, issue: RemoteIssue, event: dict[str, Any], reason: str) -> None:
        """The request needs an operator: recorded, one notice, the request label removed."""
        reason = sanitize(reason, 900)

        def mark(r: PlanRecord) -> None:
            r.status, r.attention = "attention", {"reason": reason, "at": utc_now(), "request": event["id"]}
            r.handled_requests.append(event["id"])
            r.attempts, r.feedback = 0, []
        self._update(issue, mark, event=("plan-attention", {"issue": issue.number, "label_event": event["id"],
                                                            "reason": reason}))
        self._notice(issue, f"plan-attention-e{event['id']}", (
            f"**Plan needs an operator**: {reason}\n\nNo plan was published and nothing runs. Adjust the Issue (or the "
            f"role configuration), then remove and re-add `{LABEL_PLAN}` to plan again."))
        self._labels(issue, remove=[LABEL_PLAN, LABEL_READY, LABEL_APPROVE])

    def _plan(self, issue: RemoteIssue, event: dict[str, Any], report: Any) -> None:  # pylint: disable=too-many-locals
        """Run the planner (bounded attempts, write-ahead), validate, record the next version, publish."""
        feature_id = feature_id_for(issue.number)

        def start(r: PlanRecord) -> None:
            if r.request is None or r.request.get("id") != event["id"]:
                r.request, r.attempts, r.feedback = dict(event), 0, []
            r.status = "planning"  # an earlier version can no longer be approved from here on
        record = self._update(issue, start, event=("plan-requested", {"issue": issue.number, "label_event": event["id"],
                                                                      "actor": event["actor"]}))
        if record.attempts >= self.cfg.max_attempts:
            self._attention(issue, event, f"the planner did not produce a valid plan in {record.attempts} attempts: "
                                          + "; ".join(record.feedback or ["interrupted"]))
            return
        planner = self.planner or getattr(self.coord, "planner", None) or self._default_planner()
        repository = self.repository()
        spec = spec_sha256(issue.title, issue.body)
        version = (record.latest["version"] if record.latest else 0) + 1
        texts = conventions(self.coord.root, self.config)
        attention, problems, plan = "", list(record.feedback), None
        while record.attempts < self.cfg.max_attempts:
            record = self._update(issue, lambda r: setattr(r, "attempts", r.attempts + 1))  # counted BEFORE the run
            prompt = planner_prompt(config=self.config, number=issue.number, title=issue.title.strip(),
                                    body=normalize_body(issue.body), convention_texts=texts, feedback=problems)
            result = planner.plan(PlannerRequest(feature_id=feature_id, issue_number=issue.number, prompt=prompt,
                                                 schema=proposal_schema(self.config),
                                                 run_dir=self.store.root / "runs" / "planning",
                                                 timeout_seconds=self.cfg.timeout_seconds, heartbeat=self.heartbeat,
                                                 cancelled=self.cancelled))
            if result.status in ("unavailable", "cancelled"):  # not the plan's fault: the attempt is given back
                self._update(issue, lambda r: setattr(r, "attempts", max(r.attempts - 1, 0)),
                             event=("plan-deferred", {"issue": issue.number, "status": result.status,
                                                      "error": sanitize(result.error, 300)}))
                report.deferred.append(f"#{issue.number}: planning {result.status}: {sanitize(result.error, 300)}")
                return
            if result.status != "ok":
                problems = [f"the planner run {result.status}: {sanitize(result.error, 300)}"]
            else:
                outcome = canonical_plan(result.proposal, config=self.config, repository=repository["full_name"],
                                         issue={"number": issue.number, "id": issue.id, "node_id": issue.node_id},
                                         spec_sha=spec, version=version)
                attention, problems, plan = outcome.attention, list(outcome.problems), outcome.plan
                if plan is not None:
                    size = len(render_plan(plan, sha=plan_sha256(plan), request=event, op="x" * 120))
                    if size > MAX_COMMENT:
                        problems, plan = [f"the rendered plan has {size} characters; at most {MAX_COMMENT} fit in "
                                          "one comment: shorten the instructions"], None
                if attention or plan is not None:
                    break
            record = self._update(issue, lambda r, p=problems: setattr(r, "feedback",
                                                                       [sanitize(x, 400) for x in p][:20]))
        if plan is None:
            self._attention(issue, event, f"the planner asked for an operator: {attention}" if attention else
                            "the planner's proposal was refused: " + "; ".join(problems[:8]))
            report.rejected.append({"issue": issue.number, "comment": event["id"], "author": event["actor"],
                                    "reason": "plan needs an operator"})
            return
        sha = plan_sha256(plan)

        def recorded(r: PlanRecord) -> None:
            r.versions.append({"version": version, "plan": plan, "sha256": sha, "spec_sha256": spec,
                               "request": dict(event), "created_at": utc_now(), "comment_id": 0, "comment_url": "",
                               "published_at": ""})
            r.attention, r.feedback = None, []
        self._update(issue, recorded, event=("plan-proposed", {"issue": issue.number, "version": version, "sha256": sha,
                                                               "tasks": [t["id"] for t in plan["tasks"]]}))
        self.poller._fault(f"crash-after-plan-recorded:{issue.number}")  # pylint: disable=protected-access
        self._publish(issue, report)

    def _default_planner(self) -> Planner:
        from .planner import ClaudeCodePlanner  # pylint: disable=import-outside-toplevel
        return ClaudeCodePlanner(self.config)

    def _publish(self, issue: RemoteIssue, report: Any) -> None:
        """Post the latest version as ONE comment (found again after a crash), then the labels."""
        feature_id = feature_id_for(issue.number)
        record = load_plan(self.store, feature_id)
        assert record is not None and record.latest is not None
        latest = record.latest
        key, op = f"{feature_id}/plan", f"plan-v{latest['version']}"
        comment = self._own_op(issue.number, key, op)
        if comment is None:
            body = render_plan(latest["plan"], sha=latest["sha256"], request=latest["request"],
                               op=op_marker(self.config.github.coordinator_id, key, op))
            try:
                comment = self.client.create_comment(issue.number, body)
            except GitHubUncertainWriteError as exc:
                report.deferred.append(f"#{issue.number}: plan v{latest['version']} may have been posted: {exc}")
                return
            self.poller._fault(f"crash-after-plan-comment:{issue.number}")  # pylint: disable=protected-access

        def published(r: PlanRecord) -> None:
            r.versions[-1].update(comment_id=comment.id, comment_url=comment.url, published_at=comment.created_at)
            r.status = "published"
            if latest["request"]["id"] not in r.handled_requests:
                r.handled_requests.append(latest["request"]["id"])
            r.attempts, r.feedback, r.attention = 0, [], None
        self._update(issue, published, event=("plan-published", {"issue": issue.number, "version": latest["version"],
                                                                 "sha256": latest["sha256"], "comment": comment.id}))
        self._labels(issue, add=[LABEL_READY], remove=[LABEL_PLAN, LABEL_APPROVE, LABEL_APPROVED])
        report.plans.append({"issue": issue.number, "version": latest["version"], "sha256": latest["sha256"],
                             "tasks": [f"{t['id']} ({t['role']})" for t in latest["plan"]["tasks"]]})

    # -- 2. stale plans -------------------------------------------------------------------------

    def _check_stale(self, issue: RemoteIssue, report: Any, apply: bool) -> None:
        record = load_plan(self.store, feature_id_for(issue.number))
        if record is None or record.status != "published" or record.latest is None:
            return
        if spec_sha256(issue.title, issue.body) == record.latest["spec_sha256"]:
            return
        version = record.latest["version"]
        report.waiting.append(f"#{issue.number}: plan v{version} is stale (the Issue changed after planning)")
        if not apply:
            return
        self._update(issue, lambda r: setattr(r, "status", "stale"),
                     event=("plan-stale", {"issue": issue.number, "version": version}))
        self._notice(issue, f"plan-stale-v{version}", (
            f"**Plan v{version} is stale**: the Issue's title or body changed after it was planned, so it can no "
            f"longer be approved. Remove and re-add `{LABEL_PLAN}` for a plan of the current text."))
        self._labels(issue, remove=[LABEL_READY])

    # -- 1. approvals ---------------------------------------------------------------------------

    def _approval(self, issue: RemoteIssue, report: Any, apply: bool) -> None:  # pylint: disable=too-many-return-statements,too-many-branches
        feature_id = feature_id_for(issue.number)
        event = latest_label_event(self.events(issue.number), LABEL_APPROVE)
        if event is None:
            report.waiting.append(f"#{issue.number}: `{LABEL_APPROVE}` has no label event yet (GitHub lag?)")
            return
        entry = {"issue": issue.number, "comment": event["id"], "author": event["actor"]}
        intake = load_intake(self.store, feature_id)
        if intake is not None:
            evidence = (intake.plan or {}).get("evidence") or {}
            if evidence.get("label_event_id") == event["id"]:
                if apply:  # accepted before an interruption: finish the labels
                    self._labels(issue, add=[LABEL_APPROVED], remove=[LABEL_APPROVE, LABEL_READY])
                return
            reason = "the Issue is already approved; its recorded specification and plan stay"
            report.duplicates.append({**entry, "reason": reason})
            if apply:
                self._notice(issue, f"approval-duplicate-e{event['id']}", f"**Approval ignored**: {reason}.")
                self._labels(issue, remove=[LABEL_APPROVE])
            return
        record = load_plan(self.store, feature_id)
        if record is not None and event["id"] in record.rejected:
            if apply:
                self._labels(issue, remove=[LABEL_APPROVE])  # refused before; only the cleanup was interrupted
            return
        found = self._receipts(issue, event["id"])
        if found["rejected"]:
            self._reject(issue, event, [f"the approval workflow refused it: {found['rejected']}"], report, apply,
                         notify=False)
            return
        if not found["receipts"]:
            waited = self.clock() - _epoch(event["created_at"])
            if waited < self.cfg.receipt_wait_seconds:
                report.waiting.append(f"#{issue.number}: waiting for the approval receipt of label event {event['id']}")
                return
            self._reject(issue, event, [f"no approval receipt from the GitHub Action within "
                                        f"{self.cfg.receipt_wait_seconds}s (is `{self.cfg.receipt_workflow}` active on "
                                        "the default branch?)"], report, apply)
            return
        if len(found["receipts"]) > 1:
            self._reject(issue, event, ["more than one approval receipt for one label event"], report, apply)
            return
        comment, payload = found["receipts"][0]
        problems = self.receipt_problems(issue, record, event, comment, payload)
        if problems:
            self._reject(issue, event, problems, report, apply, comment=comment.id)
            return
        if not apply:
            report.writes.append(f"accept the approval of plan v{payload['plan']['version']} on #{issue.number} "
                                 f"(label event {event['id']} by {event['actor']})")
            return
        self._accept(issue, record, event, comment, payload, report)

    def _receipts(self, issue: RemoteIssue, event_id: int) -> dict[str, Any]:
        """Receipts (and the Action's own refusal) for one label event, from `receipt_author` only."""
        receipts, rejected = [], ""
        for comment in self.comments(issue.number):
            if comment.author != self.cfg.receipt_author:
                continue
            payload = decode_marker(RECEIPT_MARKER, comment.body)
            if isinstance(payload, dict) and (payload.get("label_event") or {}).get("id") == event_id:
                receipts.append((comment, payload))
            refusal = decode_marker(REJECTION_MARKER, comment.body)
            if isinstance(refusal, dict) and refusal.get("label_event_id") == event_id:
                rejected = sanitize(refusal.get("reason", "refused"), 300)
        return {"receipts": receipts, "rejected": rejected}

    def receipt_problems(self, issue: RemoteIssue, record: PlanRecord | None,  # pylint: disable=too-many-arguments,too-many-locals,too-many-branches
                         event: dict[str, Any], comment: RemoteComment, payload: Any) -> list[str]:
        """Why this receipt does not prove an approval of the current plan (empty list: it does)."""
        problems = receipt_problems(payload)
        if problems:
            return [f"malformed receipt: {'; '.join(problems[:5])}"]
        if comment.author != self.cfg.receipt_author:
            return [f"the receipt was posted by {comment.author!r}, not {self.cfg.receipt_author!r}"]
        if comment.updated_at != comment.created_at:
            problems.append("the receipt comment was edited after it was posted (anyone with write access can edit "
                            "a bot's comment)")
        repo = self.repository()
        if payload["repository"] != {"full_name": repo["full_name"], "id": repo["id"]}:
            problems.append("the receipt names another repository")
        if payload["issue"] != {"number": issue.number, "id": issue.id, "node_id": issue.node_id}:
            problems.append("the receipt names another Issue (number, database ID or node ID)")
        if payload["label_event"] != {"id": event["id"], "actor": event["actor"], "created_at": event["created_at"]}:
            problems.append("the receipt's label event differs from GitHub's newest `worker:approve` event")
        if comment.created_at < event["created_at"]:
            problems.append("the receipt is older than the label event")
        replan = latest_label_event(self.events(issue.number), LABEL_PLAN)
        if replan is not None and (replan["created_at"], replan["id"]) > (event["created_at"], event["id"]):
            problems.append("a revised plan was requested after this approval")
        refusal = self._authorize(event["actor"])
        if refusal:
            problems.append(f"the approver may not approve: {refusal}")
        latest = record.latest if record else None
        if record is None or latest is None or not latest.get("comment_id"):
            return [*problems, "no plan was published for this Issue"]
        if record.status != "published":
            problems.append(f"plan v{latest['version']} is {record.status}, not published")
        if payload["plan"] != {"comment_id": latest["comment_id"], "version": latest["version"],
                               "sha256": latest["sha256"]}:
            problems.append(f"the receipt approves another plan version or digest (latest is v{latest['version']})")
        if event["created_at"] < latest["published_at"]:
            problems.append(f"the label was added before plan v{latest['version']} was published")
        shown = next((c for c in self.comments(issue.number) if c.id == latest["comment_id"]), None)
        if shown is None or shown.author != self.authority.viewer() \
                or decode_marker(PLAN_MARKER, shown.body) != latest["plan"]:
            problems.append("the published plan comment is missing or no longer shows the recorded plan")
        problems += [f"the plan is no longer valid: {p}" for p in plan_problems(latest["plan"], self.config)[:3]]
        current = spec_sha256(issue.title, issue.body)
        if current != latest["spec_sha256"] or payload["spec_sha256"] != current:
            problems.append(f"the Issue's title or body changed after plan v{latest['version']} was made; request a "
                            "revised plan")
        statement = approval_statement(repository=repo, issue={"number": issue.number, "id": issue.id,
                                                               "node_id": issue.node_id},
                                       title=issue.title, body=issue.body, plan=latest["plan"])
        if payload["approval"] != {"format": APPROVAL_FORMAT, "sha256": approval_sha256(statement)}:
            problems.append("the approval digest does not match this Issue text and plan version")
        problems += self._run_problems(payload, event, repo, comment)
        problems += self._run_reused(issue, comment, payload["workflow_run"]["id"])
        return problems

    def _run_problems(self, payload: Any, event: dict[str, Any], repo: dict[str, Any],  # pylint: disable=too-many-arguments
                      comment: RemoteComment) -> list[str]:
        run_id = payload["workflow_run"]["id"]
        try:
            run = self.client.get_workflow_run(run_id)
        except GitHubNotFoundError:
            return [f"the receipt's workflow run {run_id} does not exist"]
        problems = []
        created = str(run.get("created_at") or "")
        if not created:
            problems.append(f"workflow run {run_id} reports no creation time")
        elif _epoch(created) + RUN_CLOCK_TOLERANCE < _epoch(event["created_at"]):
            problems.append(f"workflow run {run_id} was created at {created}, before the label event "
                            f"({event['created_at']}): it was not made for this approval")
        elif _epoch(created) > _epoch(comment.created_at) + RUN_CLOCK_TOLERANCE:
            problems.append(f"workflow run {run_id} was created at {created}, after the receipt was posted "
                            f"({comment.created_at})")
        path, wanted = str(run.get("path", "")), self.cfg.receipt_workflow
        if path != wanted and not path.startswith(wanted + "@"):
            problems.append(f"workflow run {run_id} ran {path!r}, not {wanted!r}")
        if run.get("event") != "issues":
            problems.append(f"workflow run {run_id} was triggered by {run.get('event')!r}, not an Issue event")
        if not repo["default_branch"] or run.get("head_branch") != repo["default_branch"]:
            problems.append(f"workflow run {run_id} did not run the default branch's workflow")
        if run.get("actor") != event["actor"]:
            problems.append(f"workflow run {run_id} was triggered by {run.get('actor')!r}, not the approver")
        if run.get("repository") and str(run["repository"]).lower() != repo["full_name"]:
            problems.append(f"workflow run {run_id} belongs to another repository")
        return problems

    def _run_reused(self, issue: RemoteIssue, comment: RemoteComment, run_id: int) -> list[str]:
        """One workflow run backs at most one receipt: another receipt naming it on this Issue, or an approval
        accepted with it before (any Issue; the local intake records), refuses this one."""
        for other in self.comments(issue.number):
            if other.id != comment.id and other.author == self.cfg.receipt_author:
                data = decode_marker(RECEIPT_MARKER, other.body)
                if isinstance(data, dict) and (data.get("workflow_run") or {}).get("id") == run_id:
                    return [f"workflow run {run_id} also backs receipt comment {other.id}: one run, one receipt"]
        for raw in self.store.list_records("intake"):
            evidence = (raw.get("plan") or {}).get("evidence") or {}
            if evidence.get("workflow_run_id") == run_id and evidence.get("receipt_comment_id") != comment.id:
                return [f"workflow run {run_id} already authorized the approval of #{raw.get('issue_number')}"]
        return []

    def _reject(self, issue: RemoteIssue, event: dict[str, Any], problems: list[str], report: Any,  # pylint: disable=too-many-arguments
                apply: bool, *, comment: int = 0, notify: bool = True) -> None:
        reason = sanitize("; ".join(problems), 900)
        report.rejected.append({"issue": issue.number, "comment": comment or event["id"], "author": event["actor"],
                                "reason": f"approval: {reason}"})
        if not apply:
            report.writes.append(f"refuse the approval on #{issue.number} and remove `{LABEL_APPROVE}`")
            return
        self._update(issue, lambda r: r.rejected.append(event["id"]) if event["id"] not in r.rejected else None,
                     event=("plan-approval-rejected", {"issue": issue.number, "label_event": event["id"],
                                                       "actor": event["actor"], "receipt": comment, "reason": reason}))
        if notify:
            self._notice(issue, f"approval-rejected-e{event['id']}", (
                f"**Approval not accepted** (`{LABEL_APPROVE}` added by `{event['actor']}`, label event "
                f"{event['id']}): "
                f"{reason}.\n\nNothing was started. Make sure the newest published plan matches the current Issue "
                f"(remove and re-add `{LABEL_PLAN}` for a revised plan), then add `{LABEL_APPROVE}` again."))
        self._labels(issue, remove=[LABEL_APPROVE])

    def _accept(self, issue: RemoteIssue, record: PlanRecord | None, event: dict[str, Any],  # pylint: disable=too-many-arguments
                comment: RemoteComment, payload: Any, report: Any) -> None:
        """Record the validated approval as an intake record of schema 2 (write-ahead), then the labels."""
        assert record is not None and record.latest is not None
        latest, feature_id = record.latest, feature_id_for(issue.number)
        plan = latest["plan"]
        clash = self._clash(feature_id)
        if clash:
            self._reject(issue, event, [clash], report, True, comment=comment.id)
            return
        evidence = {"receipt_comment_id": comment.id, "receipt_url": comment.url, "label_event_id": event["id"],
                    "actor": event["actor"], "labeled_at": event["created_at"],
                    "workflow_run_id": payload["workflow_run"]["id"],
                    "workflow_run_attempt": payload["workflow_run"]["attempt"], "validated_at": utc_now()}
        task_ids = [f"{feature_id}.{t['id']}" for t in plan["tasks"]]
        intake = IntakeRecord(
            id=feature_id, issue_number=issue.number, issue_id=issue.id, node_id=issue.node_id, url=issue.url,
            issue_author=issue.author, title=issue.title.strip(), body=normalize_body(issue.body),
            digest=latest["spec_sha256"], role="", gates=[], task_id=task_ids[0], approved_by=event["actor"],
            approval_comment_id=comment.id, approval_comment_url=comment.url, approved_at=event["created_at"],
            task_ids=task_ids, anchor=feature_id, integration_gates=list(plan["integration_gates"]),
            plan={"format": APPROVAL_FORMAT, "version": latest["version"], "sha256": latest["sha256"],
                  "approval_sha256": payload["approval"]["sha256"], "comment_id": latest["comment_id"],
                  "canonical": plan, "evidence": evidence})
        with self.store.transaction():
            if load_intake(self.store, feature_id) is not None:
                return  # recorded by a concurrent caller
            self.store.save_record("intake", intake)
            fresh = self._record(issue)
            fresh.status, fresh.approval = "approved", {"version": latest["version"],
                                                        "approval_sha256": payload["approval"]["sha256"], **evidence}
            self.store.save_record("plan", fresh)
            self.store.append_event("intake-approved", issue=issue.number, feature=feature_id, task=task_ids[0],
                                    tasks=task_ids, role="plan", digest=intake.digest, approver=event["actor"],
                                    comment=comment.id, plan_version=latest["version"],
                                    approval_sha256=payload["approval"]["sha256"])
        self.poller._fault(f"crash-after-plan-approval:{issue.number}")  # pylint: disable=protected-access
        report.approved.append({"issue": issue.number, "comment": comment.id, "author": event["actor"],
                                "role": f"plan v{latest['version']} ({', '.join(t['role'] for t in plan['tasks'])})",
                                "feature": feature_id, "digest": payload["approval"]["sha256"]})
        self._labels(issue, add=[LABEL_APPROVED], remove=[LABEL_APPROVE, LABEL_READY])

    def _clash(self, feature_id: str) -> str:
        try:
            self.store.load_feature(feature_id)
            return f"a feature {feature_id!r} already exists in the local state"
        except KeyError:
            pass
        branch = self.config.branch_for(feature_id)
        if self.coord.git.branch_exists(branch):
            return f"branch {branch!r} already exists"
        return ""


def ensure_worker_labels(client: Any, *, apply: bool) -> list[str]:
    """Create the four `worker:*` labels that are missing (never edits one). Returns what was (or would be) done."""
    done = []
    existing = {label["name"] for label in client.list_labels()} if hasattr(client, "list_labels") else set()
    for name, (color, description) in WORKER_LABEL_SPECS.items():
        if name in existing:
            done.append(f"{name}: exists")
        elif not apply:
            done.append(f"{name}: would be created")
        else:
            done.append(f"{name}: {client.ensure_worker_label(name, color, description)}")
    return done
