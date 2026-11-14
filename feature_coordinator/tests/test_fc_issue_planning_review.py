"""W4.5 review fixes: the approval Action under GitHub event lag, the workflow's concurrency and event filter,
and the binding of an approval receipt to its workflow run (github-intake.md §12.5, §12.6).

The GitHub Action's real script runs against the persisted mock tracker (test_fc_issue_planning.PlanRig); a
lagging REST adapter hides the newest Issue events for a number of looks, as GitHub's events API can. No
model, no real GitHub, no network."""

# pylint: disable=missing-function-docstring,redefined-outer-name,protected-access

from __future__ import annotations

import json
import re
from typing import Any

import pytest
import yaml

from feature_coordinator import plan_digest as pd
from feature_coordinator.github_api import parse_comment, parse_workflow_run
from feature_coordinator.intake import feature_id_for, load_intake

from test_fc_intake import OPERATOR
from test_fc_issue_planning import ACTION, BOT, WORKFLOW_FILE, MockApi, PlanRig, prig  # pylint: disable=unused-import

APPROVE = pd.LABEL_APPROVE


class LaggingApi(MockApi):
    """The Action's REST client over the mock, but the events list hides events newer than *visible_up_to*
    for the first *looks* reads (GitHub's events API lagging the webhook). Records every wait."""

    def __init__(self, tracker: Any, *, visible_up_to: int, looks: int) -> None:
        super().__init__(tracker)
        self.visible_up_to, self.looks, self.reads = visible_up_to, looks, 0
        self.waits: list[float] = []
        self.sleep = self.waits.append

    def request(self, method: str, path: str, body: Any = None, *, ok: tuple[int, ...] = ()) -> tuple[int, Any]:
        status, data = super().request(method, path, body, ok=ok)
        if method == "GET" and path.split("?")[0].endswith("/events"):
            self.reads += 1
            if self.reads <= self.looks:
                data = [e for e in data if e["id"] <= self.visible_up_to]
        return status, data

    def writes(self) -> list[str]:
        return [c for c in self.calls if not c.startswith("GET")]


def receipts(rig: PlanRig, number: int) -> list[dict[str, Any]]:
    return [p for p in (pd.decode_marker(pd.RECEIPT_MARKER, b) for b in rig.bot_comments(number)) if p]


def reapprove(rig: PlanRig, number: int) -> dict[str, Any]:
    """A person removes `worker:approve` and adds it again: a NEW label event (no Action run yet)."""
    rig.tracker.unlabel_as(number, APPROVE, OPERATOR)
    return rig.tracker.label_as(number, APPROVE, OPERATOR)


# -- finding 1: the events API lags the webhook ----------------------------------------------------------------

def test_a_new_approval_is_never_mistaken_for_an_earlier_answered_one(prig: PlanRig) -> None:
    number = prig.issue()
    prig.published(number)
    first = prig.approve_label(number)               # answered: a receipt for the first label event
    second = reapprove(prig, number)
    # the confirmed failure: while only the FIRST (answered) event is visible, the run must not stop there
    lagging = LaggingApi(prig.tracker, visible_up_to=first["id"], looks=99)
    assert prig.action(number, APPROVE, OPERATOR, api=lagging) == 1
    assert lagging.writes() == [] and [r["label_event"]["id"] for r in receipts(prig, number)] == [first["id"]]
    assert lagging.waits == list(ACTION.EVENT_RETRY_SECONDS)            # bounded: 80 s, then an explicit failure
    assert lagging.reads == len(ACTION.EVENT_RETRY_SECONDS) + 1
    assert "did not become visible" in prig.out[-1] and f"event {first['id']} was already answered" in prig.out[-1]
    run_id = max(prig.tracker.runs)
    # the job is re-run (same run, attempt 2) and the new event appears on the third look: ONE receipt for it
    lagging = LaggingApi(prig.tracker, visible_up_to=first["id"], looks=2)
    assert prig.action(number, APPROVE, OPERATOR, api=lagging, run_id=run_id, attempt=2) == 0
    assert lagging.waits == list(ACTION.EVENT_RETRY_SECONDS[:2])
    found = receipts(prig, number)
    assert [r["label_event"]["id"] for r in found] == [first["id"], second["id"]]
    assert found[-1]["workflow_run"] == {"id": run_id, "attempt": 2}
    # a further re-run finds its own receipt and posts nothing; another run for the event fails explicitly
    assert prig.action(number, APPROVE, OPERATOR, run_id=run_id, attempt=3) == 0
    assert "already answered by this run" in prig.out[-1]
    assert prig.action(number, APPROVE, OPERATOR) == 1 and len(receipts(prig, number)) == 2
    # the coordinator accepts the newest event's receipt
    report = prig.poll()
    record = load_intake(prig.store, feature_id_for(number))
    assert report.ok and len(report.approved) == 1 and record is not None
    assert record.plan["evidence"]["label_event_id"] == second["id"]


def test_an_event_that_never_appears_fails_the_run_without_posting(prig: PlanRig) -> None:
    number = prig.issue()
    prig.published(number)
    prig.tracker.label_as(number, APPROVE, OPERATOR)
    lagging = LaggingApi(prig.tracker, visible_up_to=0, looks=99)          # no label event visible at all
    assert prig.action(number, APPROVE, OPERATOR, api=lagging) == 1
    assert lagging.writes() == [] and receipts(prig, number) == [] and APPROVE in prig.labels(number)
    assert "no `worker:approve` label event is visible" in prig.out[-1]


def test_a_newer_event_by_another_person_does_not_end_the_wait(prig: PlanRig) -> None:
    number = prig.issue()
    prig.tracker.permissions["someone-else"] = "write"
    prig.tracker.label_as(number, pd.LABEL_PLAN, "someone-else")           # the newest plan request is theirs
    lagging = LaggingApi(prig.tracker, visible_up_to=10**9, looks=0)
    assert prig.action(number, pd.LABEL_PLAN, OPERATOR, api=lagging) == 1
    assert lagging.writes() == [] and "is by `someone-else`" in prig.out[-1]


def test_refusals_name_their_run_so_a_rerun_recognizes_them(prig: PlanRig) -> None:
    number = prig.issue()
    prig.tracker.permissions["triager"] = "triage"
    prig.tracker.label_as(number, pd.LABEL_PLAN, "triager")
    assert prig.action(number, pd.LABEL_PLAN, "triager") == 0
    (body,) = prig.bot_comments(number)
    refusal = pd.decode_marker(pd.REJECTION_MARKER, body)
    assert refusal["workflow_run_id"] == max(prig.tracker.runs) and "`triage`" in refusal["reason"]
    assert prig.action(number, pd.LABEL_PLAN, "triager", run_id=refusal["workflow_run_id"], attempt=2) == 0
    assert len(prig.bot_comments(number)) == 1


# -- finding 2: the workflow's concurrency and event filter --------------------------------------------------

_TOKEN = re.compile(r"\s*(?:(\(|\)|&&|\|\||==|!=)|'([^']*)'|(null)\b|([A-Za-z_][\w.-]*))")


def evaluate(expression: str, context: dict[str, Any]) -> Any:
    """A GitHub Actions expression of the shapes this workflow uses: context paths, 'strings', null, ==, !=,
    &&, || and parentheses (missing context properties are null, as on GitHub)."""
    tokens, pos, text = [], 0, expression.strip()
    while pos < len(text):
        match = _TOKEN.match(text, pos)
        assert match is not None, f"unsupported expression syntax at {text[pos:]!r}"
        op, string, null, name = match.groups()
        tokens.append(("op", op) if op else ("value", string) if string is not None else ("value", None) if null
                      else ("name", name))
        pos = match.end()

    def lookup(path: str) -> Any:
        value: Any = context
        for part in path.split("."):
            value = value.get(part) if isinstance(value, dict) else None
        return value

    def atom(i: int) -> tuple[Any, int]:
        kind, value = tokens[i]
        if (kind, value) == ("op", "("):
            result, i = either(i + 1)
            assert tokens[i] == ("op", ")")
            return result, i + 1
        return (lookup(value) if kind == "name" else value), i + 1

    def compare(i: int) -> tuple[Any, int]:
        left, i = atom(i)
        if i < len(tokens) and tokens[i] in (("op", "=="), ("op", "!=")):
            right, j = atom(i + 1)
            return (left == right) == (tokens[i][1] == "=="), j
        return left, i

    def both(i: int) -> tuple[Any, int]:
        result, i = compare(i)
        while i < len(tokens) and tokens[i] == ("op", "&&"):
            right, i = compare(i + 1)
            result = bool(result) and bool(right)
        return result, i

    def either(i: int) -> tuple[Any, int]:
        result, i = both(i)
        while i < len(tokens) and tokens[i] == ("op", "||"):
            right, i = both(i + 1)
            result = bool(result) or bool(right)
        return result, i

    value, end = either(0)
    assert end == len(tokens)
    return value


def labeled(label: str, number: int = 7, *, pull_request: bool = False) -> dict[str, Any]:
    issue: dict[str, Any] = {"number": number}
    if pull_request:
        issue["pull_request"] = {"url": "https://api.github.com/repos/o/r/pulls/7"}
    return {"github": {"event": {"action": "labeled", "label": {"name": label}, "issue": issue}}}


def test_concurrency_is_per_issue_and_label_at_job_level_and_never_cancels() -> None:
    data = yaml.safe_load(WORKFLOW_FILE.read_text(encoding="utf-8"))
    assert "concurrency" not in data                       # not workflow-level: skipped events never queue
    job = data["jobs"]["label"]
    assert job["concurrency"]["cancel-in-progress"] is False

    def group(context: dict[str, Any]) -> str:
        return re.sub(r"\$\{\{(.*?)\}\}", lambda m: str(evaluate(m.group(1), context)), job["concurrency"]["group"])
    assert group(labeled(APPROVE)) == "worker-issue-labels-7-worker:approve"
    assert group(labeled(APPROVE)) == group(labeled(APPROVE))           # the same label on one Issue: serialized
    assert group(labeled(pd.LABEL_PLAN)) != group(labeled(APPROVE))     # a plan request never replaces approvals
    assert group(labeled(APPROVE, 8)) != group(labeled(APPROVE, 7))     # other Issues run independently


@pytest.mark.parametrize("label, pull_request, runs", [
    (pd.LABEL_PLAN, False, True), (APPROVE, False, True),
    (pd.LABEL_READY, False, False), (pd.LABEL_APPROVED, False, False),   # the coordinator's own labels
    ("bug", False, False), ("worker:Approve", False, False), (APPROVE, True, False),
])
def test_only_the_two_request_labels_on_issues_start_the_job(prig: PlanRig, label: str, pull_request: bool,
                                                             runs: bool) -> None:
    data = yaml.safe_load(WORKFLOW_FILE.read_text(encoding="utf-8"))
    assert data[True] == {"issues": {"types": ["labeled"]}}
    assert evaluate(data["jobs"]["label"]["if"], labeled(label, pull_request=pull_request)) is runs
    if not runs and not pull_request:  # and the script itself never acts on such an event
        number = prig.issue()
        api = MockApi(prig.tracker)
        payload = prig.state / "foreign.json"
        payload.write_text(json.dumps({"action": "labeled", "label": {"name": label},
                                          "issue": {"number": number, "state": "open"},
                                          "repository": {"full_name": "o/r"}}), encoding="utf-8")
        assert ACTION.run({"GITHUB_EVENT_PATH": str(payload)}, api, out=prig.out.append) == 0 and not api.calls


# -- finding 3: a receipt is bound to a run made for this approval --------------------------------------------

@pytest.mark.parametrize("created_at, reason", [
    ("2026-01-01T00:00:00Z", "before the label event"),       # an older run of the workflow (another approval)
    ("2026-01-01T00:59:00Z", "after the receipt was posted"),
    ("", "reports no creation time"),
])
def test_a_receipt_naming_a_run_outside_the_approval_window_is_refused(prig: PlanRig, created_at: str,
                                                                       reason: str) -> None:
    number = prig.issue()
    prig.published(number)
    prig.approve_label(number, run={"created_at": created_at})
    report = prig.poll()
    assert any(reason in r["reason"] for r in report.rejected), report.rejected
    assert load_intake(prig.store, feature_id_for(number)) is None and APPROVE not in prig.labels(number)


def test_an_edited_receipt_is_refused(prig: PlanRig) -> None:
    number = prig.issue()
    prig.published(number)
    prig.approve_label(number)
    receipt = next(c for c in prig.tracker.list_comments(number) if c.author == BOT)
    prig.tracker.edit_comment_as(number, receipt.id, receipt.body)  # even an edit that changes nothing visible
    report = prig.poll()
    assert any("edited after it was posted" in r["reason"] for r in report.rejected), report.rejected
    assert load_intake(prig.store, feature_id_for(number)) is None


def test_one_run_cannot_back_two_receipts_on_an_issue(prig: PlanRig) -> None:
    number = prig.issue()
    prig.published(number)
    old = prig.tracker.label_as(number, APPROVE, OPERATOR)          # never answered
    reapprove(prig, number)
    assert prig.action(number, APPROVE, OPERATOR) == 0               # the genuine receipt of the new event
    genuine = receipts(prig, number)[-1]
    forged = {**genuine, "label_event": {"id": old["id"], "actor": old["actor"], "created_at": old["created_at"]}}
    prig.tracker.add_comment_as(number, "copy\n" + pd.encode_marker(pd.RECEIPT_MARKER, forged), BOT)
    report = prig.poll()
    assert any("also backs receipt comment" in r["reason"] for r in report.rejected), report.rejected
    assert load_intake(prig.store, feature_id_for(number)) is None


def test_one_run_cannot_authorize_approvals_of_two_issues(prig: PlanRig) -> None:
    first, second = prig.issue(), prig.issue(title="Another export", body="Another CLI command.")
    prig.published(first)
    prig.published(second)
    prig.tracker.label_as(second, APPROVE, OPERATOR)               # labeled first, answered later
    prig.approve_label(first)                                      # run X answers the first Issue ...
    run_id = max(prig.tracker.runs)
    assert prig.action(second, APPROVE, OPERATOR, run_id=run_id) == 0  # ... and X is named again for the second
    assert receipts(prig, second)[0]["workflow_run"]["id"] == run_id
    report = prig.poll()
    assert len(report.approved) == 1, report.lines()
    assert any(f"workflow run {run_id} already authorized the approval of #" in r["reason"] for r in report.rejected)


def test_the_live_adapter_reads_edit_and_creation_times() -> None:
    comment = parse_comment({"id": 5, "user": {"login": BOT}, "body": "x", "created_at": "2026-11-04T10:00:00Z",
                             "updated_at": "2026-11-04T10:05:00Z", "html_url": "https://github.com/c/5"})
    assert comment.updated_at == "2026-11-04T10:05:00Z" and comment.created_at == "2026-11-04T10:00:00Z"
    assert parse_comment({"id": 6, "user": {"login": BOT}, "body": "x", "created_at": "2026-11-04T10:00:00Z",
                          "updated_at": "yesterday"}).updated_at == ""
    run = parse_workflow_run({"id": 9, "event": "issues", "created_at": "2026-11-04T10:00:03Z",
                              "actor": {"login": OPERATOR}, "repository": {"full_name": "o/r"}})
    assert run["created_at"] == "2026-11-04T10:00:03Z"
    assert parse_workflow_run({"id": 9, "created_at": "soon"})["created_at"] == ""
