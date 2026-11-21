"""W4.7 (M6): what happens next to an integration job, and who does it.

ONE decision, read from the job record and the current configuration, shared by the Worker's scheduling step
(integration_worker.py), `integration schedule` and `integration recover` (integration_recovery.py) — so the Worker
never starts something the operator's view would not show, and the view never promises what the Worker will not do.

`next_step(job, config)` answers with a Step:

    who          worker    a stage the Worker may start (only when `github.integration.worker` lists it; else the same
                           step is the operator's, with the command to run)
                 operator  a person must act (a signature, a decision, a repair); `urgent` says whether the job is
                           stopped for it (NEEDS_ATTENTION) or simply waits (WAITING)
                 nobody    the job is terminal
    stage        inspect | validate | review | recover — never anything else (`STAGES`): the merge, an approval, an
                 intake and an abort are not stages. `stage_argv` is the ONLY place a stage becomes a command line
    interrupted  the record shows an operation that began and did not finish (a crash, a kill, a lost answer); `reason`
                 says which, and what the next run does about it

The schedule record (`integration-schedule/<job>.json`, one per job the Worker has started a stage for;
integration_slot.Schedule) is the Worker's OWN bookkeeping — launches, the running stage process, delays and holds. It
is not evidence and decides nothing about a job: the job record (integration.py) is untouched by it and every check
stays in the stage commands. It does decide who holds the checkout's execution slot (integration_slot.py).

Bounded automation (`finish`): a stage run that leaves the job record exactly as it was is a STALL. Stalls are repeated
after `retry_seconds`, doubled each time up to `max_retry_seconds`; `max_stalls` in a row, or `max_launches` runs of
one stage, put the job on HOLD for the operator. A run that changed the record but left the SAME stage as the next
step (a verification still pending, a reviewer given back) is repeated after `retry_seconds` without counting a
stall. A hold made by stalls lapses by itself when the job record changes (someone acted); `integration schedule
--resume` clears any hold and the counters.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from .config import ProjectConfig
from .errors import CoordinatorError
from .integration import IntegrationJob, JobState, current_inspection_passed, effective_approval
from .integration_config import WORKER_STAGES, IntegrationWorkerConfig
from .integration_merge_schema import attempt_dispatch, open_merge_attempt
from .integration_schema import clean_text, open_attempt, open_review_attempt, pending_answer
from .integration_slot import HISTORY_LIMIT, MAX_LOG_BYTES, RECORD_KIND, STAGE_TAG_ENV, STAGES, Schedule, \
    load_schedule, log_name, new_tag

assert STAGES == WORKER_STAGES            # inspect, validate, review, recover — and nothing else, ever
ACTOR = "worker-service"                  # recorded in the job history for a scheduled run
__all__ = ["ACTOR", "RECORD_KIND", "STAGES", "STAGE_TAG_ENV", "Schedule", "Step", "assigned", "basis_of", "finish",
           "launch_hold", "load_schedule", "log_name", "new_tag", "next_step", "not_started", "read_outcome",
           "refresh", "resume", "stage_argv", "unacknowledged_end"]
WORKER, OPERATOR, NOBODY = "worker", "operator", "nobody"
PHASES = ("intake", "approval", "inspection", "candidate", "validation", "review", "merge-approval", "pr", "merge",
          "verification", "repair", "terminal")   # what a Step's `phase` may be


def stage_argv(stage: str, job_id: str) -> list[str]:
    """The command-line tail of a scheduled *stage* for *job_id* — the only place a stage becomes a command. A stage
    outside `STAGES` (the merge above all) is refused here, whatever a caller or a record says."""
    if stage not in STAGES:
        raise CoordinatorError(f"{stage!r} is not a stage the Worker schedules ({', '.join(STAGES)}); the merge and "
                               "every approval are an operator's commands")
    return ["integration", stage, "--job", job_id, "--apply", "--actor", ACTOR, "--json"]


@dataclass(frozen=True)
class Step:  # pylint: disable=too-many-instance-attributes
    """What happens next to one job (module docstring)."""

    job: str
    state: str
    who: str
    phase: str
    reason: str
    stage: str = ""
    action: str = ""
    urgent: bool = False
    interrupted: bool = False

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        return asdict(self)


def _command(stage: str, job_id: str, extra: str = "") -> str:
    return f"python -m feature_coordinator integration {stage} --job {job_id}{extra} --apply"


def _attention(job: IntegrationJob) -> str:
    return clean_text((job.attention or {}).get("reason") or "the job needs an operator", 400)


def next_step(job: IntegrationJob, config: ProjectConfig) -> Step:  # pylint: disable=too-many-return-statements,too-many-branches
    """The next step of *job* from its record and the current configuration (module docstring). Read-only; raises
    nothing for a loadable job: a fact that cannot be derived makes the step the operator's."""
    job_id, state = job.job_id, job.state

    def worker(stage: str, phase: str, reason: str, interrupted: bool = False) -> Step:
        return Step(job_id, state.value, WORKER, phase, reason, stage=stage, action=_command(stage, job_id),
                    interrupted=interrupted)

    def operator(phase: str, reason: str, action: str, *, urgent: bool, interrupted: bool = False) -> Step:
        return Step(job_id, state.value, OPERATOR, phase, reason, action=action, urgent=urgent,
                    interrupted=interrupted)

    if job.terminal:
        return Step(job_id, state.value, NOBODY, "terminal", f"{state.value} is final: the job is never continued")
    if state is JobState.INTAKE:
        why = f"its intake is deferred ({clean_text((job.deferred or {}).get('reason'), 300)})" if job.deferred \
            else "its intake was recorded but its checks did not finish"
        return operator("intake", why, "repeat the same `integration intake … --apply` (it resumes this job)",
                        urgent=False, interrupted=True)
    if state is JobState.AWAITING_APPROVAL:
        if effective_approval(job) is None:
            return operator("approval", "it waits for the operator's signed inspect approval",
                            f"`integration approve --job {job_id} --stage inspect --record FILE --apply`",
                            urgent=False)
        return worker("inspect", "inspection", "its inspect approval is recorded and it has not been inspected")
    if state is JobState.INSPECTING:
        if job.attention:
            return operator("inspection", _attention(job),
                            f"resolve it (e.g. sign a new inspect approval), then `{_command('inspect', job_id)}`",
                            urgent=True)
        if current_inspection_passed(job):
            return worker("validate", "candidate", "its inspection passed: the candidate is built (or rebuilt — an "
                                                   "interrupted build leaves nothing that is trusted) and validated")
        return worker("inspect", "inspection", "it has no current passed inspection under its effective approval")
    if state is JobState.CANDIDATE_BUILT:
        return worker("validate", "validation", "its candidate is recorded and no validation started: the run stopped "
                                                "between CANDIDATE_BUILT and VALIDATING", True)
    if state is JobState.VALIDATING:
        return _validating(job, config, worker, operator)
    if state is JobState.IN_REVIEW:
        if job.attention:
            return operator("review", _attention(job),
                            f"`{_command('review', job_id, ' --retry --reason TEXT')}` starts the next review round, "
                            f"or `integration abort --job {job_id} --reason TEXT --apply`", urgent=True)
        opened = open_review_attempt(job)
        if opened is not None:
            return worker("review", "review", f"review attempt {opened['id']} has no recorded run: its reviewer "
                                              "process is waited for and collected, or recorded as interrupted — "
                                              "never a second reviewer for that attempt", True)
        if pending_answer(job) is not None:
            return worker("review", "review", "a valid reviewer answer is recorded and awaits its decision: it is "
                                              "decided after the freshness checks, without another reviewer run", True)
        return worker("review", "review", "it is in review and no attempt is open")
    if state is JobState.AWAITING_MERGE_APPROVAL:
        return _awaiting_merge(job, worker, operator)
    if state is JobState.MERGED:
        return worker("recover", "verification", "GitHub merged it and its post-merge verification has not passed yet: "
                                                 "the verification is repeated (nothing is sent to GitHub)", True)
    return operator("repair", _attention(job) if job.attention else "the candidate needs a repair",
                    f"repair the source in a new commit, `integration abort --job {job_id} --reason TEXT --apply`, "
                    "then a NEW `integration intake` for the new SHA (its own approval and evidence; nothing "
                    "transfers)",
                    urgent=True)


def _validating(job: IntegrationJob, config: ProjectConfig, worker: Any, operator: Any) -> Step:
    from .integration_validate import validated_for  # pylint: disable=import-outside-toplevel  # (heavy imports)
    job_id = job.job_id
    if job.attention:
        return operator("validation", _attention(job), "fix the cause (a blocked gate is an environment problem), "
                                                       f"then `{_command('validate', job_id)}`", urgent=True)
    opened = open_attempt(job)
    if opened is not None:
        return worker("validate", "validation", f"validation attempt {opened['id']} has no decision: its gates were "
                                                "interrupted. Recorded PASS/SKIPPED results are reused, a recorded "
                                                "FAIL stays, the interrupted gate runs again against the same SHAs",
                      True)
    try:
        current = validated_for(job, config)
    except (CoordinatorError, OSError, ValueError, KeyError) as exc:
        return operator("validation", "the gate plan of the current configuration cannot be derived: "
                                      f"{clean_text(exc, 300)}", f"`{_command('validate', job_id)}` after fixing it",
                        urgent=True)
    if current:
        return worker("review", "review", "its validation passed for the current gate plan: the independent review of "
                                          "exactly that candidate is next")
    return worker("validate", "validation", "it has no validation that is current for the gate plan the current "
                                            "configuration derives")


def _awaiting_merge(job: IntegrationJob, worker: Any, operator: Any) -> Step:
    job_id = job.job_id
    opened = open_merge_attempt(job)
    if opened is not None:
        what = "PR-creation" if opened["action"] == "create-pr" else "merge"
        phase = "pr" if opened["action"] == "create-pr" else "merge"
        if attempt_dispatch(job, opened) is None:
            return worker("recover", phase, f"the {what} intent {opened['id']} was recorded and never dispatched: no "
                                            "request left for it. It is closed as a recorded fact; a new request needs "
                                            "the operator's `integration merge --apply`", True)
        unknown = (f"the {what} request {opened['id']} was DISPATCHED and its outcome is not recorded: it may have "
                   "taken effect, so it is never sent again")
        if opened["action"] == "create-pr":
            return operator(phase, unknown, f"`{_command('merge', job_id)}` looks the PR up in every state and binds "
                                            "it — it never creates a second one; or open the PR by hand, or abort",
                            urgent=True, interrupted=True)
        if job.attention:
            return operator(phase, f"{unknown}; GitHub did not show a result within the observation window",
                            f"check PR #{opened['pr']} on GitHub: once it shows the result, "
                            f"`{_command('recover', job_id)}` reconciles it; if you are sure nothing was accepted, "
                            "abort the job and start a new one", urgent=True, interrupted=True)
        return worker("recover", phase, f"{unknown}; GitHub's state is read and reconciled", True)
    if job.attention:
        return operator("merge-approval", _attention(job), f"resolve it, then `{_command('merge', job_id)}`",
                        urgent=True)
    signed = effective_approval(job, "merge") is not None
    return operator("merge-approval",
                    "validated and reviewed: the merge is the operator's decision" if not signed else
                    "a signed merge approval is recorded: the merge is the operator's command",
                    (f"`{_command('merge', job_id)}`" if signed else
                     f"sign the merge approval (`integration status --job {job_id}`), `integration approve --job "
                     f"{job_id} --stage merge --record FILE --apply`, then `{_command('merge', job_id)}`")
                    + " — the Worker never merges", urgent=False)


def assigned(step: Step, cfg: IntegrationWorkerConfig) -> Step:
    """*step* as the current configuration assigns it: a Worker stage that scheduling does not cover (the Worker's
    scheduling is off, or the stage is not listed) is the operator's, with the same command."""
    if step.who != WORKER or (cfg.enabled and step.stage in cfg.stages):
        return step
    why = "the Worker's integration scheduling is off" if not cfg.enabled else \
        f"`{step.stage}` is not in github.integration.worker.stages"
    return Step(step.job, step.state, OPERATOR, step.phase, f"{step.reason} ({why})", stage=step.stage,
                action=f"`{step.action}`", urgent=False, interrupted=step.interrupted)


# -- the Worker's bookkeeping (the record itself: integration_slot.Schedule) -------------------------------------

def basis_of(job: IntegrationJob) -> dict[str, Any]:
    """What a stage run is measured against: the job's state and record revision when it started."""
    return {"state": job.state.value, "revision": job.revision}


def read_outcome(log: Path) -> tuple[str, str]:
    """(outcome, first reason) a stage command printed with --json into its *log* — for the operator's message only
    (it decides nothing: progress is read from the job record). ('', '') when the log holds no such answer."""
    try:
        with open(log, "rb") as handle:
            text = handle.read(MAX_LOG_BYTES).decode("utf-8", errors="replace")
    except OSError:
        return "", ""
    start = text.find("{")
    while start != -1:
        try:
            data, _end = json.JSONDecoder().raw_decode(text[start:])
        except ValueError:
            start = text.find("{", start + 1)
            continue
        if isinstance(data, Mapping):
            error = data.get("error") if isinstance(data.get("error"), Mapping) else {}
            reasons = data.get("reasons") if isinstance(data.get("reasons"), list) else []
            outcome = data.get("outcome") or ("error" if error else "")
            reason = (reasons[0] if reasons else "") or error.get("message", "")
            return clean_text(outcome, 80), clean_text(reason, 400)
        break
    tail = [line for line in text.splitlines() if line.startswith("error:")]
    return ("error", clean_text(tail[-1], 400)) if tail else ("", "")


def _stall(rec: Schedule, job: IntegrationJob, cfg: IntegrationWorkerConfig, now: float, stage: str, shown: str,
           at: str) -> None:
    """One more run (or failed start) that changed nothing: the growing delay, then the hold."""
    rec.stalls += 1
    rec.next_attempt_at = now + min(cfg.retry_seconds * 2 ** min(rec.stalls - 1, 30), cfg.max_retry_seconds)
    if rec.stalls >= cfg.max_stalls:
        rec.hold = {"kind": "stalls", "stage": stage, "since": at, "basis": basis_of(job),
                    "reason": clean_text(f"`integration {stage}` left the job unchanged {rec.stalls} times in a row; "
                                         f"last: {shown}", 500)}


def finish(rec: Schedule, job: IntegrationJob, cfg: IntegrationWorkerConfig, now: float, *,  # pylint: disable=too-many-arguments
           cause: str, outcome: str = "", detail: str = "", exit_code: int | None = None,
           at: str = "", repeats: bool = True) -> dict[str, Any]:
    """Close *rec*'s running stage (module docstring, bounded automation) and return the entry recorded as `last`.
    *cause*: exited | deadline | orphaned | service-stop. A stop for the service's own shutdown is nobody's stall.
    *repeats*: the job's next step is this same stage again — after progress only that is delayed (`retry_seconds`:
    a stage that keeps moving the record without finishing is paced); another stage may start at once."""
    running = rec.running or {}
    stage, basis = str(running.get("stage") or ""), dict(running.get("basis") or {})
    progressed = basis_of(job) != basis
    entry = {"stage": stage, "launch": running.get("launch"), "started_at": running.get("started_at"), "ended_at": at,
             "cause": cause, "exit_code": exit_code, "outcome": clean_text(outcome, 80),
             "detail": clean_text(detail, 400), "progressed": progressed, "state_before": basis.get("state"),
             "state_after": job.state.value}
    rec.running = None
    if cause == "service-stop":
        rec.next_attempt_at = None
    elif progressed:
        rec.stalls = 0
        rec.next_attempt_at = now + cfg.retry_seconds if repeats else None
    else:
        shown = f"`{outcome}`" + (f": {detail}" if detail else "") if outcome else \
            (detail or f"it ended without an answer ({cause}, exit {exit_code})")
        _stall(rec, job, cfg, now, stage, shown, at)
    rec.last = entry
    rec.history = [*rec.history, entry][-HISTORY_LIMIT:]
    return entry


def not_started(rec: Schedule, job: IntegrationJob, cfg: IntegrationWorkerConfig, now: float, stage: str,
                error: str, at: str) -> None:
    """The stage process could not be started (nothing ran): the launch is given back to the launch budget, but the
    failed start is a stall — a start that keeps failing is delayed longer each time and then held, like a stage
    that keeps changing nothing."""
    rec.launches[stage] = max(rec.launches.get(stage, 1) - 1, 0)
    rec.running = None
    _stall(rec, job, cfg, now, stage, f"the stage process could not be started: {clean_text(error, 300)}", at)


def refresh(rec: Schedule, job: IntegrationJob) -> bool:
    """A hold made by stalls lapses when the job record is no longer the one it was made for (someone acted): the
    counters start again. True when *rec* changed. A launch-budget hold stays until `--resume`."""
    if rec.hold is None or rec.hold["kind"] != "stalls" or rec.hold["basis"] == basis_of(job):
        return False
    rec.hold, rec.stalls, rec.next_attempt_at = None, 0, None
    return True


def launch_hold(rec: Schedule, job: IntegrationJob, stage: str, cfg: IntegrationWorkerConfig, at: str) -> bool:
    """True (and *rec* held) when *stage* has used its launch budget for this job."""
    if rec.launches.get(stage, 0) < cfg.max_launches:
        return False
    rec.hold = {"kind": "launches", "stage": stage, "since": at, "basis": basis_of(job),
                "reason": f"`integration {stage}` was started {rec.launches[stage]} times for this job "
                          f"(github.integration.worker.max_launches {cfg.max_launches})"}
    return True


def unacknowledged_end(rec: Schedule, job: IntegrationJob) -> bool:
    """True when a Worker-started stage ended *job* in a terminal state an operator has to look at — anything but
    COMPLETED and ABORTED (FAILED after a wrong merge, STALE after someone else's merge or a moved ref, REFUSED) —
    and nobody acknowledged it yet. Such a job is not continued by anyone, but it must not vanish from attention."""
    if not job.terminal or job.state in (JobState.COMPLETED, JobState.ABORTED) or rec.last is None:
        return False
    if rec.last.get("state_after") != job.state.value:
        return False   # it became terminal otherwise (an operator's own command told them)
    return not (rec.acknowledged and rec.acknowledged.get("state") == job.state.value)


def resume(rec: Schedule, job: IntegrationJob, *, by: str, reason: str, at: str) -> bool:
    """The operator's `integration schedule --resume`: the hold, the delay and the counters are cleared, and a
    terminal outcome a Worker stage produced is acknowledged. True when *rec* changed. The caller makes sure no
    stage process lives and no launch is in flight."""
    changed = bool(rec.hold or rec.stalls or rec.next_attempt_at is not None or rec.launches or rec.running)
    rec.hold, rec.stalls, rec.next_attempt_at, rec.launches, rec.running = None, 0, None, {}, None
    if unacknowledged_end(rec, job):
        rec.acknowledged = {"by": by, "at": at, "reason": clean_text(reason, 500), "state": job.state.value}
        changed = True
    return changed
