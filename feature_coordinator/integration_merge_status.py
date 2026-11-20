"""W4.7 (M5): what `integration status` and `integration approve --stage merge` say about the merge stage — derived
from the
record and the current configuration, READ-ONLY (the only subprocess is `ssh-keygen -Y verify` for the signed approval).

A job is described as MERGE-READY only when nothing in this module's view objects: M3's validation is current for the
gate
plan the current configuration derives, M4's review is eligible (PASS or PASS_WITH_NOTES under the current independence
policy), a signed merge approval is effective, verifies against the allowed-signers file now, is unexpired and binds
exactly this candidate and these evidence entries, and no GitHub write of the job is in flight. `ready` is a view of the
record: `integration merge` re-verifies everything against GitHub and origin at the time of the merge (design §5.8).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .config import ProjectConfig
from .integration import IntegrationJob, JobState, effective_approval
from .integration_approval import SshSignatureVerifier, approval_view, effective_approval_problems
from .integration_merge_record import known_pr_number, merge_record_template
from .integration_merge_schema import attempt_state, merge_pr_entry, open_merge_attempt
from .integration_review import review_eligibility
from .integration_validate import validated_for


def merge_approval_blockers(job: IntegrationJob, config: ProjectConfig) -> list[str]:
    """Why a merge approval cannot be recorded for *job* now (empty: it can): the evidence it would bind is not
    current."""
    problems = []
    if job.candidate is None or not validated_for(job, config):
        problems.append("M3's validation is not current for the gate plan the current configuration derives")
    decision, why = review_eligibility(job, config)
    if decision is None:
        problems.append(f"no eligible review applies: {why or 'the job has no current PASS or PASS_WITH_NOTES review'}")
    return problems


def merge_status(job: IntegrationJob, config: ProjectConfig, verifier: SshSignatureVerifier,
                 now: datetime) -> dict[str, Any]:  # pylint: disable=too-many-locals
    """The merge stage of *job* as a JSON-serializable view (see the module docstring)."""
    candidate = job.candidate
    decision, review_why = review_eligibility(job, config) if candidate is not None else (None, "")
    validation_ok = candidate is not None and validated_for(job, config)
    entry = effective_approval(job, "merge")
    approval: dict[str, Any] | None = None
    if entry is not None:
        _entry, problems = effective_approval_problems(job, config=config, verifier=verifier, now=now, stage="merge")
        approval = {**approval_view(entry), "valid": not problems, "problems": problems}
    opened = open_merge_attempt(job)
    bound = merge_pr_entry(job)
    known = known_pr_number(job)
    creation = bool(entry and entry["merge"]["authorizes"]["create_pr"])
    result = job.merge_result
    blockers: list[str] = []
    actions: list[str] = []
    jid = job.job_id
    if job.state is JobState.AWAITING_MERGE_APPROVAL:
        if not validation_ok:
            blockers.append("M3's validation is not current for the current gate plan (validate cannot run here: "
                            "restore the configuration, or abort the job and supersede it)")
        if decision is None:
            blockers.append(f"the review is not eligible: {review_why or 'no current review applies'}")
        if approval is None:
            blockers.append("no signed merge approval")
            actions.append(f"sign a version 2 merge record and run `integration approve --job {jid} --stage merge "
                           "--record FILE --apply`")
        elif not approval["valid"]:
            blockers += [f"the merge approval does not authorize the merge now: {p}" for p in approval["problems"]]
            actions.append("sign a new merge approval record (its issued_at later than the recorded one)")
        if job.attention is not None:
            blockers.append(f"the last run recorded attention: {job.attention['reason']}")
            actions.append(f"resolve it, then run `integration merge --job {jid}` (the run clears it once it passes)")
        if opened is not None and attempt_state(job, opened) == "dispatched":
            blockers.append(f"a GitHub {opened['action']} request ({opened['id']}) was dispatched and its outcome is "
                            "unknown: it is never repeated until GitHub shows its result")
            actions.append(f"run `integration merge --job {jid} --apply` (it reads GitHub first and records a result "
                           "that has appeared); if GitHub shows none, look there yourself — merge the PR or open it "
                           "by hand, or abort the job and start a new one")
        elif opened is not None:
            actions.append(f"the {opened['action']} intent {opened['id']} was never dispatched (nothing was sent): "
                           f"`integration merge --job {jid} --apply` closes it and decides afresh")
        if not blockers:
            actions.append(f"run `integration merge --job {jid}` (dry run), then `--apply`")
    elif job.state is JobState.MERGED:
        blockers.append("post-merge verification is pending (a check is running or GitHub/origin could not be read)")
        actions.append(f"run `integration merge --job {jid} --apply` again")
    elif job.state is JobState.FAILED and result is not None:
        blockers.append("the merge is on the base branch but failed its verification: manual operator remediation is "
                        "required (never an automatic revert, reset or force-push)")
    return {"state": job.state.value, "ready": job.state is JobState.AWAITING_MERGE_APPROVAL and not blockers,
            "approval": approval, "candidate": {"head_sha": candidate["head_sha"], "tree_sha": candidate["tree_sha"]}
            if candidate else None,
            "validation": {"eligible": validation_ok}, "review": {"eligible": decision is not None,
                                                                   "id": decision["id"] if decision else None,
                                                                   "outcome": decision["outcome"] if decision else None,
                                                                   "why": review_why},
            "pr": {"bound": bound["number"] if bound else None, "known": known,
                   "creation_authorized": creation and known is None and bound is None},
            "open_attempt": {"id": opened["id"], "action": opened["action"], "state": attempt_state(job, opened)}
            if opened else None,
            "merge_result": result, "blockers": blockers, "actions": actions,
            "record_template": merge_record_template(job) if job.state is JobState.AWAITING_MERGE_APPROVAL
            and (approval is None or not approval["valid"]) and decision is not None else None}
