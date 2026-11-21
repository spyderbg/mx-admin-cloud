"""W4.7 (M6): recovery of interrupted integration jobs — what was interrupted, what the next run does about it, and
what is deliberately NOT done.

    integration recover [--job JOB] [--actor NAME] [--apply] [--json]
        without --apply: the DIAGNOSIS of one job or of every open job, read-only — which operation the record shows
        as begun and not finished, the next step and who takes it (integration_schedule.next_step), whether a command
        runs for the job right now, and for NEEDS_REPAIR the roles a repair routes to.
        with --apply (one job): SETTLES what a merge run left behind, by reading GitHub and never writing to it
        (IntegrationMerger.settle): an intent that was never dispatched is closed; a dispatched merge request is
        reconciled from GitHub's state; a MERGED job's post-merge verification is repeated. For every other
        interruption there is nothing to settle: the stage command itself resumes it, and recover says which.
    integration schedule [--job JOB] [--resume --reason TEXT [--actor NAME] --apply] [--json]
        the Worker's view of the open jobs (integration_worker.schedule_view): next step, running stage, delay, hold.
        --resume clears a job's hold, delay and counters (an operator's recorded decision).

The recovery rules, per interrupted operation (each is the M3–M5 behaviour; M6 adds the one place that names them, the
Worker that applies them, and the tests that kill a real process at each point):

    candidate preparation   nothing of an interrupted build is trusted: the candidate is rebuilt from the pinned SHAs
                            and must reproduce the same commit and tree, or the job is STALE
    validation gates        the open attempt is resumed against the same SHAs: recorded PASS/SKIPPED results are kept,
                            a recorded FAIL is final, the interrupted gate runs again
    independent AI review   the open attempt's reviewer process is waited for and collected, or recorded as interrupted
                            — never a second reviewer for one attempt; a recorded answer is decided without a new run
    PR discovery/creation   an intent without a dispatch was never sent: closed, decided afresh by the operator's merge.
                            A DISPATCHED creation is never repeated: only the complete lookup (every state) binds a PR
    merge dispatch          an intent without a dispatch: closed. A DISPATCHED merge is never repeated: GitHub's state
                            is read; merged by this coordinator's account → recorded; merged otherwise → STALE;
                            not merged → unresolved, for the operator
    post-merge verification repeated in full on every run; COMPLETED only with the complete evidence; a failure is
                            FAILED with manual remediation — nothing is reverted

Never, anywhere in recovery: a GitHub write; a repeated request whose outcome is unknown; a change of the pinned
source, base or candidate SHAs; a new job (a job that cannot continue is aborted by the operator, and a NEW intake —
own SHA, own approval, own evidence — follows); evidence moved between candidates.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from .config import ProjectConfig
from .errors import CoordinatorError
from .integration import IntegrationJob, JobState
from .integration_merge_schema import attempt_dispatch, attempt_state, merge_attempts, open_merge_attempt
from .integration_schedule import assigned, next_step
from .integration_schema import (
    clean_text,
    latest_evidence,
    open_attempt,
    open_review_attempt,
    pending_answer,
    recorded_failures,
    review_attempts,
    review_decision,
)

SETTLED_OUTCOMES = ("completed", "settled", "nothing-to-settle", "would-settle", "would-verify", "would-reconcile")
MAX_ROUTED_PATHS = 20
_OPERATOR = "operator"


def repair_routing(job: IntegrationJob, config: ProjectConfig) -> dict[str, Any] | None:
    """Design §6.4, steps 1–3, as a REPORT for a NEEDS_REPAIR job (nothing is opened, scheduled or patched): what
    failed, which paths it concerns, and the role each path routes to under the CURRENT configuration — a path no
    role owns (or one that needs approval) goes to the operator. The repair itself is ordinary authored work on its
    own branch through the existing feature workflow (an approved Issue, the owning roles' write scopes and gates);
    its result is a new commit, and therefore a new job with its own approval and evidence (design §5.5)."""
    if job.state is not JobState.NEEDS_REPAIR or job.candidate is None:
        return None
    from .integration_validate import current_paths  # pylint: disable=import-outside-toplevel  # (heavy imports)
    paths = current_paths(job, config)
    owner = {entry["path"]: (entry["role"] if entry["class"] == "owned" and entry["role"] else _OPERATOR)
             for entry in paths}
    attempts = review_attempts(job)
    decision = review_decision(job, attempts[-1]) if attempts else None
    failures = recorded_failures(job)
    routed: dict[str, list[str]] = {}
    if decision is not None and decision["outcome"] == "REQUEST_CHANGES":
        cause = "review"
        what = [f"review {decision['id']}: {f['severity']} — {f['file']}: {clean_text(f['title'], 160)}"
                for f in decision["findings"]]
        concerned = sorted({f["file"] for f in decision["findings"] if f["file"]}) or sorted(owner)
    else:
        cause = "validation"
        what = [f"gate {name} FAILED ({entry['id']}; required by {', '.join(entry['required_by'])})"
                for name, entry in sorted(failures.items())]
        required = {role for entry in failures.values() for role in entry["required_by"]}
        concerned = sorted(path for path, role in owner.items()
                           if role in required or (role == _OPERATOR and "unowned" in required)) or sorted(owner)
    for path in concerned:
        routed.setdefault(owner.get(path, _OPERATOR), []).append(path)
    return {"cause": cause, "findings": what[:MAX_ROUTED_PATHS],
            "roles": {role: {"count": len(items), "paths": items[:MAX_ROUTED_PATHS]}
                      for role, items in sorted(routed.items()) if role != _OPERATOR},
            "operator": {"count": len(routed.get(_OPERATOR, [])),
                         "paths": routed.get(_OPERATOR, [])[:MAX_ROUTED_PATHS]},
            "next": ["the source is never patched by an integration job, and no evidence moves to another commit",
                     "repair on a branch through the existing feature workflow (an approved Issue; the roles above "
                     "write only their own paths; operator paths need the operator) — Issue #67 (revising a published "
                     "Draft PR) is a separate, unimplemented feature",
                     f"`integration abort --job {job.job_id} --reason \"repaired in <branch>\" --apply`, then a NEW "
                     "`integration intake` for the repaired SHA: its own approval, validation and review"]}


def interruption(job: IntegrationJob, now: datetime | None = None) -> dict[str, Any] | None:  # pylint: disable=too-many-return-statements
    """The operation the job record shows as begun and not finished, as facts ({operation, evidence, github_write,
    detail}) — None when the record shows none. `github_write`: none | not-sent | unknown — whether a request to
    GitHub may have left for it (only `unknown` can have changed GitHub)."""
    state = job.state
    if job.terminal:
        return None
    if state is JobState.INTAKE:
        return {"operation": "intake", "evidence": None, "github_write": "none",
                "detail": "the request was recorded before its checks; they did not finish"}
    if state is JobState.CANDIDATE_BUILT:
        return {"operation": "validation", "evidence": (job.candidate or {}).get("evidence"), "github_write": "none",
                "detail": "the candidate was recorded and no validation attempt followed"}
    if state is JobState.VALIDATING and open_attempt(job) is not None:
        attempt = open_attempt(job)
        assert attempt is not None
        return {"operation": "validation", "evidence": attempt["id"], "github_write": "none",
                "detail": "a validation attempt has no decision: a gate was running or its result was not recorded"}
    if state is JobState.IN_REVIEW:
        opened = open_review_attempt(job)
        if opened is not None:
            return {"operation": "review", "evidence": opened["id"], "github_write": "none",
                    "detail": "a review attempt has no recorded run: its reviewer process may still run, or ended "
                              "unrecorded"}
        pending = pending_answer(job)
        if pending is not None:
            return {"operation": "review", "evidence": pending["id"], "github_write": "none",
                    "detail": "a reviewer answer is recorded and no decision followed"}
        return None
    if state is JobState.AWAITING_MERGE_APPROVAL:
        opened = open_merge_attempt(job)
        if opened is None:
            return None
        dispatch = attempt_dispatch(job, opened)
        operation = "pr-creation" if opened["action"] == "create-pr" else "merge"
        if dispatch is None:
            return {"operation": operation, "evidence": opened["id"], "github_write": "not-sent",
                    "detail": "the intent was recorded and the write boundary never dispatched a request for it"}
        age = int((now - _time(dispatch["at"])).total_seconds()) if now is not None else None
        return {"operation": operation, "evidence": opened["id"], "github_write": "unknown", "dispatch": dispatch["id"],
                "dispatched_at": dispatch["at"], "age_seconds": age,
                "detail": "a request was dispatched and no outcome is recorded: it may have taken effect and is never "
                          "sent again"}
    if state is JobState.MERGED:
        result = job.merge_result or {}
        verification = (result.get("verification") or {}).get("state") or "not-run"
        return {"operation": "post-merge-verification", "evidence": result.get("attempt"), "github_write": "none",
                "detail": f"merge commit {result.get('commit_sha')} is recorded; its verification is {verification}"}
    return None


def _time(text: str) -> datetime:
    from .integration import parse_record_time  # pylint: disable=import-outside-toplevel
    return parse_record_time(text, "at")


def diagnose(job: IntegrationJob, config: ProjectConfig, *, now: datetime | None = None,
             schedule: Mapping[str, Any] | None = None, running: bool | None = None) -> dict[str, Any]:
    """One job's recovery diagnosis (module docstring), read-only: its next step as the current configuration assigns
    it, the interrupted operation, the Worker's bookkeeping (*schedule*: its row of schedule_view) and — for
    NEEDS_REPAIR — the repair routing. *running*: whether a command holds the job's lock right now (None: not
    probed)."""
    step = assigned(next_step(job, config), config.github.integration.worker)
    sent = [a for a in merge_attempts(job) if a["action"] == "merge"]
    try:
        routing = repair_routing(job, config)
    except (CoordinatorError, OSError, ValueError, KeyError) as exc:
        routing = {"cause": "unknown", "findings": [f"the routing cannot be derived: {clean_text(exc, 300)}"],
                   "roles": {}, "operator": {"count": 0, "paths": []}, "next": []}
    validation, review = latest_evidence(job, "validation"), latest_evidence(job, "review")
    return {"job": job.job_id, "state": job.state.value, "terminal": job.terminal,
            "pinned": {"source": f"{job.source['ref']}@{job.source['sha']}",
                       "base": f"{job.base['ref']}@{job.base['expected_sha']}",
                       "candidate": (job.candidate or {}).get("head_sha"),
                       "tree": (job.candidate or {}).get("tree_sha")},
            "interrupted": interruption(job, now), "next": step.to_dict(), "command_running": running,
            "attention": (job.attention or {}).get("reason"),
            "evidence": {"entries": len(job.evidence), "validation": validation["id"] if validation else None,
                         "review": review["id"] if review else None,
                         "merge_requests": [{"attempt": a["id"], "state": attempt_state(job, a)} for a in sent]},
            "worker": dict(schedule) if schedule is not None else None, "repair": routing}


def diagnosis_lines(found: Mapping[str, Any]) -> list[str]:
    """The diagnosis as text."""
    step = found["next"]
    lines = [f"{found['job']}  {found['state']}"]
    hit = found["interrupted"]
    if hit:
        lines.append(f"  interrupted: {hit['operation']}" + (f" ({hit['evidence']})" if hit.get("evidence") else "")
                     + f" — {hit['detail']}; GitHub write: {hit['github_write']}")
    elif not found["terminal"]:
        lines.append("  interrupted: nothing (the record shows no unfinished operation)")
    if found["command_running"]:
        lines.append("  a command holds this job's lock right now: it is running, not interrupted")
    if found["attention"]:
        lines.append(f"  attention: {found['attention']}")
    lines.append(f"  next ({step['who']}{', URGENT' if step['urgent'] else ''}): {step['reason']}")
    if step["action"]:
        lines.append(f"    {step['action']}")
    worker = found["worker"]
    if worker and worker.get("category") in ("running", "unsettled", "held", "delayed"):
        detail = {"running": lambda: f"`{worker['running']['stage']}` runs since {worker['running']['started_at']}",
                  "unsettled": lambda: f"`{worker['running']['stage']}` ended; the next Worker step settles it",
                  "held": lambda: f"HELD — {worker['hold']['reason']}",
                  "delayed": lambda: f"`{worker['stage']}` is repeated at {worker['next_attempt_at']}"}
        lines.append(f"  worker: {detail[worker['category']]()}")
    repair = found["repair"]
    if repair:
        lines.append(f"  repair routing ({repair['cause']}):")
        lines += [f"    - {text}" for text in repair["findings"]]
        lines += [f"    role {role}: {info['count']} path(s) — {', '.join(info['paths'][:5])}"
                  for role, info in repair["roles"].items()]
        if repair["operator"]["count"]:
            lines.append(f"    operator: {repair['operator']['count']} path(s) no role may repair — "
                         f"{', '.join(repair['operator']['paths'][:5])}")
        lines += [f"    {index}. {text}" for index, text in enumerate(repair["next"], 1)]
    return lines
