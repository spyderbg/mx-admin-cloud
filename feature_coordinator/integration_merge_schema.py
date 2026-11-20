"""W4.7 (M5): the record's merge evidence and `merge_result` — pure checks of values (no Git, no network, no state).

Four evidence kinds extend the append-only evidence of integration_schema.py (ids `mpr-`, `mat-`, `mdi-`, `mou-` +
position, digest-bound like every entry, every entry referring only to EARLIER entries and to a MERGE-stage approval of
the job):

  * merge-attempt  — the write-ahead INTENT of one GitHub write, recorded BEFORE the request is sent and only after the
                     signed merge approval, the evidence, the pinned SHAs and the job's state were re-verified inside
                     the coordinator transaction. `action` is `create-pr` (open the one PR the approval authorizes:
                     `authorizes.create_pr`, `pr` null) or `merge` (GitHub's merge-commit merge of the bound PR, pinned
                     to the approved source SHA). It repeats what it acts on: candidate, source/base SHAs, the approval
                     and its record digest, the validation and review evidence the approval names, the exact request.
                     An intent is NOT proof that a request was sent;
  * merge-dispatch — recorded by the WRITE BOUNDARY (integration_authority.py) in the same coordinator transaction in
                     which it re-verified the whole authorization, immediately BEFORE the request leaves: "this attempt
                     is being dispatched now". At most one per attempt (the one-shot of the write), and none after the
                     attempt ended. The request was sent only if this entry exists, but its existence does not prove
                     the request reached GitHub (a crash may sit between the entry and the send): an attempt WITHOUT
                     it was provably never sent; an attempt WITH it and no outcome has an UNKNOWN outcome. It carries
                     `policy_sha256`, the fingerprint of the configuration snapshot the write was authorized under;
  * merge-pr       — the PR the merge goes through, recorded once and never changed: its number, where it came from
                     (`recorded` = the job's PR, `inspected` = the PR the inspection decided to reuse, `discovered`
                     = found by the complete lookup, `created` = opened by this job's DISPATCHED `create-pr` attempt),
                     its head (repository, branch, the approved source SHA) and base. The job's immutable inputs are
                     never edited to store it. A draft PR is never bound;
  * merge-outcome  — how an attempt ended, with the `basis` that establishes it (a timeout is never a basis):
                     `merged` (the PR is merged by this attempt's request: basis `response` — GitHub's own answer — or
                     `actor` — the request was dispatched and GitHub shows the merge by the authenticated account; the
                     entry needs the dispatch), `not-merged` / `not-created` (the request had no effect: basis
                     `not-dispatched` — no dispatch entry exists, so nothing was sent —, `rejected` — GitHub answered
                     with an error —, `not-sent` — the transport never sent it — or, for a PR creation, `superseded` —
                     another PR exists and is bound). An attempt with no outcome (and, for `create-pr`, no merge-pr
                     naming it) is OPEN; a dispatched OPEN attempt is UNRESOLVED until GitHub shows its result: no
                     second request is ever sent for it, and `abort` is refused while a young one may still be in
                     flight.

`merge_result` (the job field, design §8.2) holds the merge GitHub actually performed. Its identity — commit, PR,
attempt, merge time — never changes once recorded; the parents and the tree are filled in once by the post-merge
verification and then never change; `verified` is true only for a COMPLETED job whose merge commit has the parents
`base_sha`, `source_sha` and the approved candidate tree (design §5.10).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Mapping

from .integration_schema import (
    EVIDENCE_CHECKERS,
    SHA_RE,
    _HEX64_RE,
    _check_built_reference,
    _expect,
    _is_int,
    _is_text,
    _is_time,
    parse_record_time,
)

MERGE_ACTIONS = ("create-pr", "merge")
PR_ORIGINS = ("recorded", "inspected", "discovered", "created")
OUTCOME_STATUSES = ("not-created", "merged", "not-merged")
MERGED_BASES = ("response", "actor")
UNMERGED_BASES = ("not-dispatched", "rejected", "not-sent", "superseded")
VERIFICATION_STATES = ("pending", "verified", "failed")
MAX_MERGE_ATTEMPTS = 20
SETTLE_SECONDS = 120.0   # the minimum observation interval of a dispatched request with an unknown outcome
_ATTEMPT_KEYS = frozenset({"kind", "id", "at", "candidate", "head_sha", "tree_sha", "source_sha", "base_sha",
                           "approval", "approval_sha256", "validation", "review", "action", "pr", "request", "actor",
                           "sha256"})
_PR_KEYS = frozenset({"kind", "id", "at", "candidate", "head_sha", "tree_sha", "approval", "approval_sha256", "number",
                      "origin", "attempt", "head", "base", "draft", "url", "pr_id", "sha256"})
_OUTCOME_KEYS = frozenset({"kind", "id", "at", "candidate", "head_sha", "tree_sha", "attempt", "status", "basis", "pr",
                           "merge_commit_sha", "detail", "sha256"})
_DISPATCH_KEYS = frozenset({"kind", "id", "at", "candidate", "head_sha", "tree_sha", "attempt", "action", "approval",
                            "approval_sha256", "policy_sha256", "sha256"})
_RESULT_KEYS = frozenset({"commit_sha", "parents", "tree_sha", "verified", "pr", "merged_at", "merged_by", "attempt",
                          "verification"})
_VERIFICATION_KEYS = frozenset({"state", "at", "checks"})
_CHECK_KEYS = frozenset({"name", "status", "detail"})
CHECK_STATUSES = ("pass", "fail", "pending", "unavailable", "not-configured")
_IMMUTABLE_RESULT = ("commit_sha", "pr", "attempt", "merged_at", "merged_by")
# The core of post-merge verification (integration_postmerge.py): each must appear exactly once and pass in a COMPLETED
# job. The check POLICY entry may be `not-configured` (no CI on this plan, SEC-7) — and only that one.
CORE_CHECKS = ("pr-merged", "dev-contains", "commit", "parents", "tree", "agreement")
POLICY_CHECK = "checks"
POLICY_PREFIX = "check "


# -- accessors (pure; the job is read by attribute) --------------------------------------------------------------------

def merge_attempts(job: Any) -> list[dict[str, Any]]:
    """Every merge attempt of the job, oldest first."""
    return [e for e in job.evidence if e.get("kind") == "merge-attempt"]


def attempt_closed(job: Any, attempt: Mapping[str, Any]) -> bool:
    """True when an outcome — or, for a PR creation, the merge-pr it created — names *attempt*."""
    return any(e.get("kind") in ("merge-outcome", "merge-pr") and e.get("attempt") == attempt["id"]
               for e in job.evidence)


def open_merge_attempt(job: Any) -> dict[str, Any] | None:
    """The newest merge attempt when nothing names it yet: its request may be in flight, or its answer was lost."""
    attempts = merge_attempts(job)
    return attempts[-1] if attempts and not attempt_closed(job, attempts[-1]) else None


def merge_pr_entry(job: Any) -> dict[str, Any] | None:
    """The merge-pr evidence (the PR the merge goes through), or None."""
    found = [e for e in job.evidence if e.get("kind") == "merge-pr"]
    return found[-1] if found else None


def attempt_dispatch(job: Any, attempt: Mapping[str, Any]) -> dict[str, Any] | None:
    """The merge-dispatch entry of *attempt* (the write boundary let the request leave), or None."""
    return next((e for e in job.evidence if e.get("kind") == "merge-dispatch" and e.get("attempt") == attempt["id"]),
                None)


def attempt_state(job: Any, attempt: Mapping[str, Any]) -> str:
    """`closed` (an outcome or the created PR names it), `dispatched` (the request may have been sent; its outcome is
    unknown) or `not-dispatched` (open, and no dispatch entry exists: nothing was ever sent for it)."""
    if attempt_closed(job, attempt):
        return "closed"
    return "dispatched" if attempt_dispatch(job, attempt) is not None else "not-dispatched"


def creation_history_problems(jobs: Iterable[Any], job: Any) -> list[str]:
    """Codex F3: why *job* must not send a PR creation now because of OTHER jobs' history for the same source branch
    (empty: nothing in the way). Called only when the complete lookup shows no PR for the branch in any state — so
    every finding here means a PR may exist that GitHub does not show (a lagging or inconsistent listing):

      * a DISPATCHED `create-pr` attempt of another job that never ended — its outcome is UNKNOWN, whether that job is
        still open, ABORTED, STALE or superseded (an abort, a restart, a supersession or a timer is not evidence that
        the request failed);
      * a PR another job RECORDED for the branch (`merge-pr`, whatever its origin, a created one included).

    A dispatched creation that ended without a PR on GitHub's own word (`rejected`) or because it never left
    (`not-sent`) is resolved and blocks nothing. The identity is the existing PR-discovery one: the source repository
    (any letter case) and branch — any base, any source SHA (a PR follows its branch), and any target repository (a
    renamed or transferred repository keeps its PRs; a different one blocks conservatively and is resolved the same
    way). Nothing here clears
    an uncertainty: the job that holds it stays as it is; the blocker disappears when GitHub shows a PR for the branch
    (the lookup then decides — reuse, or refuse) — an operator may open the PR by hand."""
    branch = (job.source["repo"].lower(), job.source["ref"])
    problems = []
    for other in jobs:
        if other.job_id == job.job_id or (other.source["repo"].lower(), other.source["ref"]) != branch:
            continue
        for entry in other.evidence:
            if entry.get("kind") == "merge-pr":
                problems.append(f"{other.job_id} ({other.state.value}) recorded PR #{entry['number']} for "
                                f"{job.source['ref']} ({entry['origin']}), but GitHub shows no PR for the branch now "
                                "(a lagging listing?): no second PR is created — repeat later")
        for attempt in merge_attempts(other):
            if attempt["action"] != "create-pr" or attempt_dispatch(other, attempt) is None \
                    or attempt_closed(other, attempt):
                continue
            problems.append(f"{other.job_id} ({other.state.value}) dispatched the PR creation {attempt['id']} for "
                            f"{job.source['ref']} and its outcome is still UNKNOWN (it may have created a PR GitHub "
                            "does not show yet): no PR is created for the branch until GitHub shows one — open it by "
                            "hand if you know none exists")
    return problems


def merge_in_flight_problem(job: Any, now: datetime | None = None, settle_seconds: float = SETTLE_SECONDS) -> str:
    """Why the job must not be aborted now (empty: it may). A MERGE request that was dispatched and has no recorded
    outcome may be in flight, so aborting would leave a job that says ABORTED over a PR GitHub may merge: refused
    while the request is younger than *settle_seconds* (and always when *now* is not given). A merge attempt that was
    never dispatched is provably not in flight; an unresolved one older than the settle window is the operator's call
    (the abort says so in its reason)."""
    opened = open_merge_attempt(job)
    dispatch = attempt_dispatch(job, opened) if opened is not None else None
    if opened is None or opened["action"] != "merge" or job.terminal or dispatch is None:
        return ""
    if now is not None and (now - parse_record_time(dispatch["at"], "at")).total_seconds() >= settle_seconds:
        return ""
    return (f"{job.job_id} has a GitHub merge request in flight ({opened['id']}, recorded {opened['at']}): its outcome "
            f"is not recorded, so the PR may already be merged — run `integration merge --job {job.job_id} --apply` "
            "(it reads GitHub first and records the outcome); the job cannot be aborted while the request may still "
            "be in flight")


def check_merge_entry(job: Any, entry: dict[str, Any], where: str, earlier: Mapping[str, dict[str, Any]]) -> None:
    """One merge evidence entry (called by integration_schema._check_evidence, after its common checks)."""
    kind = entry["kind"]
    _expect("AWAITING_MERGE_APPROVAL" in {item["to"] for item in job.history},
            f"{where}: merge evidence exists only for a job that reached AWAITING_MERGE_APPROVAL")
    _check_built_reference(entry, where, earlier)
    if kind == "merge-outcome":
        _check_outcome(entry, where, earlier)
        return
    approval = next((a for a in job.approvals if a["id"] == entry["approval"]), None)
    _expect(approval is not None and approval["stage"] == "merge" and entry["approval_sha256"] ==
            approval["record_sha256"], f"{where} must name a merge approval of this job and carry its record digest")
    assert approval is not None
    if kind == "merge-attempt":
        _check_attempt(job, entry, where, earlier, approval)
    elif kind == "merge-dispatch":
        _check_dispatch(entry, where, earlier)
    else:
        _check_pr(job, entry, where, earlier, approval)


EVIDENCE_CHECKERS.update({kind: check_merge_entry
                         for kind in ("merge-pr", "merge-attempt", "merge-dispatch", "merge-outcome")})


def _check_attempt(job: Any, entry: dict[str, Any], where: str, earlier: Mapping[str, dict[str, Any]],
                   approval: Mapping[str, Any]) -> None:
    _expect(set(entry) == _ATTEMPT_KEYS, f"{where} must have exactly {sorted(_ATTEMPT_KEYS)}")
    binding = approval["merge"]
    _expect(entry["source_sha"] == job.source["sha"] and entry["base_sha"] == job.base["expected_sha"]
            and entry["validation"] == binding["validation"]["evidence"]
            and entry["review"] == binding["review"]["evidence"],
            f"{where} is bound to other SHAs or evidence than the job's pinned values and its approval's")
    action = entry["action"]
    _expect(action in MERGE_ACTIONS, f"{where}: action must be one of {list(MERGE_ACTIONS)}")
    _expect(_is_text(entry["actor"], 80) and bool(entry["actor"]), f"{where}: actor must be a name")
    previous = [e for e in earlier.values() if e["kind"] == "merge-attempt"]
    _expect(len(previous) < MAX_MERGE_ATTEMPTS, f"{where}: at most {MAX_MERGE_ATTEMPTS} merge attempts")
    _expect(not previous or any(e["kind"] in ("merge-outcome", "merge-pr") and e["attempt"] == previous[-1]["id"]
                                for e in earlier.values()),
            f"{where}: an attempt starts only after the previous one ended ({previous and previous[-1]['id']} is open)")
    _expect(not any(e["kind"] == "merge-outcome" and e["status"] == "merged" for e in earlier.values()),
            f"{where}: no attempt follows a merged outcome")
    bound = [e for e in earlier.values() if e["kind"] == "merge-pr"]
    if action == "create-pr":
        _expect(entry["pr"] is None and binding["authorizes"]["create_pr"] is True and not bound
                and entry["request"] == {"head": job.source["ref"], "base": job.base["ref"], "draft": False},
                f"{where}: a create-pr attempt needs the approval's explicit create_pr authorization, no PR bound yet "
                "and exactly the request {head: source ref, base: base ref, draft: false}")
    else:
        _expect(bound and entry["pr"] == bound[-1]["number"]
                and entry["request"] == {"merge_method": "merge", "sha": job.source["sha"]},
                f"{where}: a merge attempt names the bound PR and carries exactly the request {{merge_method: merge, "
                "sha: the approved source SHA}} (no squash, no rebase)")


def _check_dispatch(entry: dict[str, Any], where: str, earlier: Mapping[str, dict[str, Any]]) -> None:
    _expect(set(entry) == _DISPATCH_KEYS, f"{where} must have exactly {sorted(_DISPATCH_KEYS)}")
    attempts = [e for e in earlier.values() if e["kind"] == "merge-attempt"]
    attempt = earlier.get(str(entry["attempt"]))
    _expect(attempt is not None and attempt["kind"] == "merge-attempt" and attempt is attempts[-1],
            f"{where} must name the newest merge attempt")
    assert attempt is not None
    _expect(entry["action"] == attempt["action"] and entry["approval"] == attempt["approval"]
            and entry["approval_sha256"] == attempt["approval_sha256"],
            f"{where} must repeat its attempt's action and approval")
    _expect(isinstance(entry["policy_sha256"], str) and _HEX64_RE.fullmatch(entry["policy_sha256"]) is not None,
            f"{where}: policy_sha256 must be the SHA-256 of the configuration snapshot the write was authorized under")
    _expect(not any(e["kind"] in ("merge-dispatch", "merge-outcome", "merge-pr") and e["attempt"] == attempt["id"]
                    for e in earlier.values()),
            f"{where}: an attempt is dispatched once, and never after it ended")


def _check_pr(job: Any, entry: dict[str, Any], where: str, earlier: Mapping[str, dict[str, Any]],
              approval: Mapping[str, Any]) -> None:
    _expect(set(entry) == _PR_KEYS, f"{where} must have exactly {sorted(_PR_KEYS)}")
    _expect(not any(e["kind"] == "merge-pr" for e in earlier.values()),
            f"{where}: the PR a merge goes through is bound once and never changed")
    origin, signed = entry["origin"], approval["pr"]
    _expect(origin in PR_ORIGINS and _is_int(entry["number"], 1) and _is_int(entry["pr_id"], 1),
            f"{where}: origin must be one of {list(PR_ORIGINS)}, number and pr_id positive integers")
    if signed is not None:
        _expect(entry["number"] == signed and origin in ("recorded", "inspected"),
                f"{where}: the approval was signed for PR #{signed}: no other PR is bound "
                f"({origin} #{entry['number']})")
    else:
        _expect(origin in ("discovered", "created"),
                f"{where}: the approval names no PR, so the PR is discovered or created, not {origin!r}")
    attempt = earlier.get(str(entry["attempt"])) if entry["attempt"] is not None else None
    created = origin == "created"
    _expect((attempt is not None and attempt["kind"] == "merge-attempt" and attempt["action"] == "create-pr"
             and any(a["id"] == attempt["approval"] and a["stage"] == "merge" for a in job.approvals)
             and any(e["kind"] == "merge-dispatch" and e["attempt"] == attempt["id"] for e in earlier.values()))
            if created else entry["attempt"] is None,
            f"{where}: a created PR names its DISPATCHED create-pr attempt (made under a merge approval of this job — "
            "a newer approval may have replaced it since), any other PR none")
    head, base = entry["head"], entry["base"]
    _expect(isinstance(head, dict) and set(head) == {"repo", "ref", "sha"} and isinstance(base, dict)
            and set(base) == {"repo", "ref"}, f"{where}: head must be {{repo, ref, sha}}, base {{repo, ref}}")
    _expect(str(head["repo"]).lower() == job.source["repo"].lower() and head["ref"] == job.source["ref"]
            and head["sha"] == job.source["sha"] and base["ref"] == job.base["ref"]
            and str(base["repo"]).lower() == job.source["repo"].lower(),
            f"{where}: the bound PR must go from the pinned source branch at the pinned SHA into the pinned base")
    _expect(entry["draft"] is False and _is_text(entry["url"], 300), f"{where}: a draft PR is never bound; url a text")


def _check_outcome(entry: dict[str, Any], where: str, earlier: Mapping[str, dict[str, Any]]) -> None:
    _expect(set(entry) == _OUTCOME_KEYS, f"{where} must have exactly {sorted(_OUTCOME_KEYS)}")
    attempts = [e for e in earlier.values() if e["kind"] == "merge-attempt"]
    attempt = earlier.get(str(entry["attempt"]))
    _expect(attempt is not None and attempt["kind"] == "merge-attempt" and attempt is attempts[-1],
            f"{where} must name the newest merge attempt")
    assert attempt is not None
    status, basis = entry["status"], entry["basis"]
    _expect(not any(e["kind"] in ("merge-outcome", "merge-pr") and e["attempt"] == attempt["id"]
                    for e in earlier.values()),
            f"{where}: the attempt already ended (an outcome is final: a request is never 'in flight after all' — "
            "GitHub's state, not elapsed time, decides)")
    _expect(status in OUTCOME_STATUSES and _is_text(entry["detail"], 300), f"{where}: status must be one of "
                                                                          f"{list(OUTCOME_STATUSES)}")
    _expect(status == "not-created" if attempt["action"] == "create-pr" else status in ("merged", "not-merged"),
            f"{where}: {status!r} does not end a {attempt['action']} attempt")
    dispatched = any(e["kind"] == "merge-dispatch" and e["attempt"] == attempt["id"] for e in earlier.values())
    merged = status == "merged"
    _expect(basis in (MERGED_BASES if merged else UNMERGED_BASES),
            f"{where}: the basis of a {status} outcome must be one of "
            f"{list(MERGED_BASES if merged else UNMERGED_BASES)} (elapsed time is never a basis)")
    _expect(dispatched != (basis == "not-dispatched"),
            f"{where}: basis {basis!r} {'needs' if basis != 'not-dispatched' else 'is impossible with'} a dispatch "
            "entry for the attempt (a merge outcome of this job's own request needs the request to have been "
            "dispatched)")
    _expect(entry["pr"] == attempt["pr"], f"{where}: pr must be the attempt's")
    _expect(isinstance(entry["merge_commit_sha"], str)
            and (SHA_RE.fullmatch(entry["merge_commit_sha"]) is not None if merged
                 else entry["merge_commit_sha"] == ""),
            f"{where}: a merged outcome carries GitHub's actual merge commit, any other none")


# -- merge_result ------------------------------------------------------------------------------------------------------

def check_merge_result(job: Any) -> None:
    """The job's `merge_result` (null before the merge). The minimal shape {commit_sha, [parents, tree_sha,] verified}
    stays valid; a record written by M5 (it carries `verification`) is checked completely."""
    result = job.merge_result
    if result is None:
        return
    _expect(isinstance(result, dict) and set(result) <= _RESULT_KEYS,
            f"merge_result may only have {sorted(_RESULT_KEYS)}")
    _expect(result.get("commit_sha") is None or SHA_RE.fullmatch(str(result["commit_sha"])) is not None,
            "merge_result.commit_sha must be a full commit SHA")
    _expect("verified" not in result or isinstance(result["verified"], bool),
            "merge_result.verified must be true or false")
    reached = {entry["to"] for entry in job.history}
    if "verification" not in result and not ("MERGED" in reached and job.candidate is not None):
        return   # the legacy minimal shape: only a job without a candidate (a state-machine unit record) may have it
    missing = sorted(_RESULT_KEYS - set(result))
    _expect(not missing, f"merge_result lacks {missing}")
    _expect(job.candidate is not None and _is_int(result["pr"], 1) and _is_text(result["merged_by"], 100)
            and (result["merged_at"] == "" or _is_time(result["merged_at"])),
            "merge_result.pr must be a PR number, merged_by a login, merged_at a timestamp or empty")
    parents, tree = result["parents"], result["tree_sha"]
    _expect(isinstance(parents, list) and len(parents) <= 2 and all(SHA_RE.fullmatch(str(p)) for p in parents)
            and (tree == "" or SHA_RE.fullmatch(str(tree)) is not None) and (not parents) == (tree == ""),
            "merge_result.parents must be [] or the commit's parents, and tree_sha empty or a SHA, filled in together")
    verification = result["verification"]
    _expect(isinstance(verification, dict) and set(verification) == _VERIFICATION_KEYS
            and verification["state"] in VERIFICATION_STATES and _is_time(verification["at"])
            and isinstance(verification["checks"], list) and len(verification["checks"]) <= 30
            and all(isinstance(c, dict) and set(c) == _CHECK_KEYS and c["status"] in CHECK_STATUSES
                    and _is_text(c["name"], 120) and _is_text(c["detail"], 600) for c in verification["checks"]),
            "merge_result.verification must be {state, at, checks: [{name, status, detail}]}")
    outcome = next((e for e in job.evidence if e.get("kind") == "merge-outcome" and e["status"] == "merged"), None)
    _expect(outcome is not None and outcome["attempt"] == result["attempt"] and outcome["pr"] == result["pr"]
            and outcome["merge_commit_sha"] == result["commit_sha"],
            "merge_result must be the merged outcome of its attempt: same attempt, PR and merge commit")
    completed = "COMPLETED" in reached
    _expect(result["verified"] is completed and (verification["state"] == "verified") == completed,
            "merge_result.verified is true exactly for a COMPLETED job (and then its verification state is `verified`)")
    if completed:
        _expect(parents == [job.base["expected_sha"], job.source["sha"]] and tree == job.candidate["tree_sha"],
                "a COMPLETED job's merge commit must have the parents base_sha and source_sha and the approved "
                "candidate tree (design §5.10)")
        problems = completion_problems(verification["checks"])
        _expect(not problems, "a COMPLETED job needs the complete post-merge verification (design §5.10): "
                              + "; ".join(problems))


def completion_problems(checks: list[dict[str, Any]]) -> list[str]:
    """Why *checks* (the verification checks of a merge_result) are not the complete evidence a COMPLETED job needs
    (empty: they are). Every core check (CORE_CHECKS) appears exactly once and passes — `all([])` is not a
    verification: an empty or reduced list proves nothing — and the check POLICY was evaluated: exactly one `checks`
    entry that is `not-configured` (no check is required) or `pass`, or one `check NAME` entry per required check, all
    passing. Nothing else (a pending, unavailable or failed entry, or an unknown name) belongs to a completed job."""
    problems = []
    names = [str(c["name"]) for c in checks]
    for name in CORE_CHECKS:
        found = [c for c in checks if c["name"] == name]
        if len(found) != 1:
            problems.append(f"the core check {name!r} must appear exactly once ({len(found)} found)")
        elif found[0]["status"] != "pass":
            problems.append(f"the core check {name!r} must pass, not {found[0]['status']!r}")
    policy = [c for c in checks if c["name"] == POLICY_CHECK]
    required = [c for c in checks if str(c["name"]).startswith(POLICY_PREFIX)]
    unknown = [n for n in names if n not in CORE_CHECKS and n != POLICY_CHECK and not n.startswith(POLICY_PREFIX)]
    if unknown:
        problems.append(f"unknown verification checks {unknown}")
    if len(set(names)) != len(names):
        problems.append("a verification check is recorded twice")
    if bool(policy) == bool(required):
        problems.append("the check policy must be evaluated: exactly one `checks` entry (none required) or one "
                        "`check NAME` entry per required check, not both and not neither")
    elif policy and (len(policy) != 1 or policy[0]["status"] not in ("not-configured", "pass")):
        problems.append("the `checks` entry must be not-configured (no check is required) or pass")
    elif required and any(c["status"] != "pass" for c in required):
        problems.append("every required post-merge check must pass")
    return problems


def merge_result_change_problem(before: Mapping[str, Any] | None, after: Mapping[str, Any] | None) -> str:
    """Why a save may not change the recorded merge_result (empty: it may). The merge's identity never changes; the
    parents and the tree are filled in once; only `verified` and `verification` may follow the verification."""
    if before is None:
        return ""
    if after is None:
        return "the recorded merge_result cannot be removed"
    for key in _IMMUTABLE_RESULT:
        if key in before and before[key] != after.get(key):
            return f"the recorded merge_result.{key} cannot change (the merge GitHub performed is a fact)"
    for key in ("parents", "tree_sha"):
        if before.get(key) and after.get(key) != before[key]:
            return f"the recorded merge_result.{key} cannot change once the verification filled it in"
    return ""
