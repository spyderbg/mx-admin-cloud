"""W4.7 (M5): the operator's signed MERGE approval — the format, and what it binds (design §3 `authorization`, §9).

The merge approval extends the M2 mechanism instead of replacing it: it is an authorization record signed with the
operator's SSH key in the SAME namespace (`integration.APPROVAL_NAMESPACE`), verified against the SAME allowed-signers
file and `github.integration.operators`, appended to the SAME append-only `approvals` list (ids `apr-NNNN`) and ordered
by `issued_at` per stage exactly like an inspect approval. Version 1 records (stage `inspect`) are unchanged and keep
validating; version 2 is the merge stage and nothing else:

    {"kind": "w47-integration-approval", "version": 2, "job": "int-20261116-0001", "stage": "merge",
     "operator": "spyderbg", "issued_at": "2026-11-16T09:00:00Z", "expires_at": "2026-11-16T21:00:00Z",
     "repository": "spyderbg/mx-admin-cloud",
     "source": {"repo": "spyderbg/mx-admin-cloud", "ref": "integration/x", "sha": "<40 hex>"},
     "base": {"ref": "dev", "sha": "<40 hex>"}, "issue": 65, "pr": null,
     "candidate": {"head_sha": "<40 hex>", "tree_sha": "<40 hex>"},
     "validation": {"evidence": "val-0012", "sha256": "<64 hex>", "plan_sha256": "<64 hex>"},
     "review": {"evidence": "rev-0016", "sha256": "<64 hex>", "outcome": "PASS"},
     "authorizes": {"operation": "merge", "method": "merge", "create_pr": true}, "note": "optional"}

What the signature binds (every field is signed text; a changed byte is another record):
  * the job, the repository, the source branch and its immutable SHA, the target branch and its immutable base SHA,
    the Issue — as in an inspect approval;
  * the candidate: the coordinator's deterministic merge commit (`head_sha`) and its tree, the very candidate M3
    validated and M4 reviewed;
  * the evidence: the id and the SHA-256 of the M3 validation decision (and the digest of the gate plan it decided) and
    of the M4 review decision (and its outcome, PASS or PASS_WITH_NOTES) — an approval for other evidence, or for
    evidence that has since been replaced, authorizes nothing;
  * the PR identity: `pr` is the PR known when the operator signs (the job's PR, else the PR the inspection decided to
    reuse, else null) and must equal it. `authorizes.create_pr` is the operator's explicit authorization for the
    coordinator to OPEN a PR — and it is only possible when `pr` is null. Binding rule for a PR created later: the job's
    immutable inputs are never edited; the coordinator records the PR it opened (or reused) as `merge-pr` evidence
    bound to this approval, accepts only a PR from the pinned source branch at the pinned SHA into the pinned base, and
    merges that PR and no other. An approval signed for PR N never authorizes another PR;
  * the operation: `authorizes.operation` is `merge` and `method` is `merge` — GitHub's merge-commit method, never a
    squash, a rebase, a fast-forward or a push.

Nothing is inferred from GitHub (comments, labels, reviews, CLI actor names). The signature, the operator, the key's
presence in the allowed-signers file and the time window are checked again whenever the approval is USED
(integration_approval.effective_approval_problems, stage `merge`), including inside the coordinator transaction that
records the merge intent.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from .integration_schema import (
    APPROVAL_KIND,
    MAX_REASON,
    IntegrationError,
    _expect,
    _shown,
    check_job_id,
    check_number,
    check_ref,
    check_repo,
    check_sha,
    parse_record_time,
)

MERGE_RECORD_VERSION = 2
MERGE_STAGE = "merge"
INSPECT_STAGE = "inspect"
MERGE_OUTCOMES = ("PASS", "PASS_WITH_NOTES")
_REQUIRED = ("kind", "version", "job", "stage", "operator", "issued_at", "expires_at", "repository", "source", "base",
             "issue", "pr", "candidate", "validation", "review", "authorizes")
RECORD_KEYS = frozenset({*_REQUIRED, "note"})
_EVIDENCE_ID_RE = {"validation": re.compile(r"val-\d{4}"), "review": re.compile(r"rev-\d{4}")}
_HEX64_RE = re.compile(r"[0-9a-f]{64}")
_LOGIN_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")
BINDING_KEYS = ("repository", "candidate", "validation", "review", "authorizes")


def parse_merge_record(data: Mapping[str, Any]) -> dict[str, Any]:  # pylint: disable=too-many-branches
    """The version 2 (merge stage) authorization record, strictly: exactly the record keys (`note` optional), every
    field
    of its type. *data* is already one JSON object without duplicate keys. Binding to a job: merge_binding_problems."""
    missing = [key for key in _REQUIRED if key not in data]
    unknown = sorted(set(data) - RECORD_KEYS)
    if missing or unknown:
        raise IntegrationError(f"the merge authorization record has missing field(s) {missing} and unknown field(s) "
                               f"{unknown}")
    _expect(data["kind"] == APPROVAL_KIND and type(data["version"]) is int  # pylint: disable=unidiomatic-typecheck
            and data["version"] == MERGE_RECORD_VERSION,
            f"a merge authorization record must be kind {APPROVAL_KIND!r}, version {MERGE_RECORD_VERSION}")
    _expect(data["stage"] == MERGE_STAGE, f"a version {MERGE_RECORD_VERSION} record authorizes the merge stage only, "
                                          f"got {_shown(data['stage'])} (inspect records are version 1)")
    check_job_id(data["job"])
    _expect(isinstance(data["operator"], str) and _LOGIN_RE.fullmatch(data["operator"]) is not None,
            f"operator must be a GitHub login, got {_shown(data['operator'])}")
    issued = parse_record_time(data["issued_at"], "issued_at")
    expires = parse_record_time(data["expires_at"], "expires_at")
    _expect(expires > issued, "expires_at must be later than issued_at")
    check_repo(data["repository"], "repository")
    source, base = data["source"], data["base"]
    _expect(isinstance(source, dict) and set(source) == {"repo", "ref", "sha"},
            "source must have exactly repo, ref and sha")
    _expect(isinstance(base, dict) and set(base) == {"ref", "sha"}, "base must have exactly ref and sha")
    check_repo(source["repo"], "source.repo")
    check_ref(source["ref"], "source.ref")
    check_sha(source["sha"], "source.sha")
    check_ref(base["ref"], "base.ref")
    check_sha(base["sha"], "base.sha")
    check_number(data["issue"], "issue")
    if data["pr"] is not None:
        check_number(data["pr"], "pr")
    candidate = data["candidate"]
    _expect(isinstance(candidate, dict) and set(candidate) == {"head_sha", "tree_sha"},
            "candidate must have exactly head_sha and tree_sha")
    check_sha(candidate["head_sha"], "candidate.head_sha")
    check_sha(candidate["tree_sha"], "candidate.tree_sha")
    for name, extra in (("validation", "plan_sha256"), ("review", "outcome")):
        item = data[name]
        _expect(isinstance(item, dict) and set(item) == {"evidence", "sha256", extra},
                f"{name} must have exactly evidence, sha256 and {extra}")
        _expect(isinstance(item["evidence"], str) and _EVIDENCE_ID_RE[name].fullmatch(item["evidence"]) is not None,
                f"{name}.evidence must be the id of a {name} evidence entry")
        _expect(isinstance(item["sha256"], str) and _HEX64_RE.fullmatch(item["sha256"]) is not None,
                f"{name}.sha256 must be the SHA-256 of that evidence entry")
    _expect(_HEX64_RE.fullmatch(str(data["validation"]["plan_sha256"])) is not None,
            "validation.plan_sha256 must be a SHA-256")
    _expect(data["review"]["outcome"] in MERGE_OUTCOMES, f"review.outcome must be one of {list(MERGE_OUTCOMES)}")
    auth = data["authorizes"]
    _expect(isinstance(auth, dict) and set(auth) == {"operation", "method", "create_pr"}
            and auth["operation"] == "merge" and auth["method"] == "merge" and isinstance(auth["create_pr"], bool),
            "authorizes must be {operation: merge, method: merge, create_pr: true|false}: the only operation a merge "
            "approval can authorize is GitHub's merge-commit merge of the PR")
    _expect(not auth["create_pr"] or data["pr"] is None,
            "authorizes.create_pr needs pr null: nothing is created for a PR that is already known")
    note = data.get("note", "")
    _expect(isinstance(note, str) and len(note) <= MAX_REASON and not re.search(r"[\x00-\x1f\x7f]", note),
            f"note must be one line of at most {MAX_REASON} printable characters")
    return dict(data)


def binding_of(record: Mapping[str, Any]) -> dict[str, Any]:
    """What an approval entry stores next to the common fields (its `merge` key): the merge-specific signed values."""
    return {key: _plain(record[key]) for key in BINDING_KEYS}


def _plain(value: Any) -> Any:
    return {k: _plain(v) for k, v in value.items()} if isinstance(value, Mapping) else value


def known_pr_number(job: Any) -> int | None:
    """The PR identity known for *job* (what a merge approval's `pr` must equal): the PR the job was given at intake,
    else the PR the newest inspection decided to reuse, else None."""
    if job.pr and job.pr.get("number"):
        return job.pr["number"]
    inspections = [entry for entry in job.evidence if entry.get("kind") == "inspection"]
    match = inspections[-1]["pr_match"] if inspections else {}
    return match["number"] if match.get("result") == "reuse" and match.get("number") else None


def merge_record_template(job: Any) -> dict[str, Any] | None:
    """The merge record an operator would sign for *job* as it is now, with the operator and the times left as
    placeholders (None: the job has no candidate, validation or review to bind). Shown by `integration status`; it is a
    convenience only — the record that counts is the signed text, which merge_binding_problems checks."""
    kinds = {kind: [e for e in job.evidence if e.get("kind") == kind] for kind in ("validation", "review")}
    if job.candidate is None or not kinds["validation"] or not kinds["review"]:
        return None
    validation, review, pr = kinds["validation"][-1], kinds["review"][-1], known_pr_number(job)
    return {"kind": APPROVAL_KIND, "version": MERGE_RECORD_VERSION, "job": job.job_id, "stage": MERGE_STAGE,
            "operator": "<your GitHub login in github.integration.operators>",
            "issued_at": "<UTC now, e.g. 2026-11-16T09:00:00Z>", "expires_at": "<UTC, later, at most 7 days>",
            "repository": job.source["repo"],
            "source": {"repo": job.source["repo"], "ref": job.source["ref"], "sha": job.source["sha"]},
            "base": {"ref": job.base["ref"], "sha": job.base["expected_sha"]}, "issue": job.issue, "pr": pr,
            "candidate": {"head_sha": job.candidate["head_sha"], "tree_sha": job.candidate["tree_sha"]},
            "validation": {"evidence": validation["id"], "sha256": validation["sha256"],
                           "plan_sha256": validation["plan"]["sha256"]},
            "review": {"evidence": review["id"], "sha256": review["sha256"], "outcome": review["outcome"]},
            "authorizes": {"operation": "merge", "method": "merge", "create_pr": pr is None}}


def _entry(job: Any, kind: str, evidence_id: str) -> dict[str, Any] | None:
    return next((e for e in job.evidence if e.get("kind") == kind and e.get("id") == evidence_id), None)


def merge_binding_problems(record: Mapping[str, Any], job: Any) -> list[str]:  # pylint: disable=too-many-branches
    """Why a parsed merge record does not bind exactly THIS job's pinned values, candidate and evidence (empty: it
    does). Applied when the record is recorded, whenever it is used and on every load of the job."""
    problems = []
    if record["job"] != job.job_id:
        problems.append(f"it authorizes job {record['job']}, not {job.job_id}")
    source, base = record["source"], record["base"]
    if source["repo"].lower() != job.source["repo"].lower() or source["ref"] != job.source["ref"] \
            or record["repository"].lower() != job.source["repo"].lower():
        problems.append(f"its repository/source is {record['repository']}/{source['repo']}:{source['ref']}, not "
                        f"{job.source['repo']}:{job.source['ref']}")
    if source["sha"] != job.source["sha"]:
        problems.append(f"its source SHA {source['sha']} is not the job's pinned source SHA {job.source['sha']}")
    if base["ref"] != job.base["ref"] or base["sha"] != job.base["expected_sha"]:
        problems.append(f"its base {base['ref']}@{base['sha']} is not the job's pinned base "
                        f"{job.base['ref']}@{job.base['expected_sha']}")
    if record["issue"] != job.issue:
        problems.append(f"it names Issue #{record['issue']}, not #{job.issue}")
    known = known_pr_number(job)
    if record["pr"] != known:
        problems.append(f"it names PR {record['pr']}, but the PR known for the job is {known}: an approval signed for "
                        "another PR identity authorizes nothing")
    candidate = job.candidate
    if candidate is None:
        return problems + ["the job has no candidate"]
    if (record["candidate"]["head_sha"], record["candidate"]["tree_sha"]) != (candidate["head_sha"],
                                                                              candidate["tree_sha"]):
        problems.append(f"its candidate {record['candidate']['head_sha']} (tree {record['candidate']['tree_sha']}) is "
                        f"not the job's candidate {candidate['head_sha']} (tree {candidate['tree_sha']})")
    for name in ("validation", "review"):
        wanted = record[name]
        found = _entry(job, name, wanted["evidence"])
        if found is None or found["sha256"] != wanted["sha256"] or found["candidate"] != candidate["evidence"]:
            problems.append(f"it names {name} evidence {wanted['evidence']} (sha256 {wanted['sha256'][:16]}…) that "
                            "this job does not carry for its candidate")
        elif name == "validation" and (found["result"] != "passed" or found["plan"]["sha256"] != wanted["plan_sha256"]):
            problems.append(f"validation {wanted['evidence']} is not a passed decision for the signed gate plan")
        elif name == "review" and (found["outcome"] != wanted["outcome"] or found["outcome"] not in MERGE_OUTCOMES):
            problems.append(f"review {wanted['evidence']} is {found['outcome']}, not the signed {wanted['outcome']}")
    newest = {kind: [e for e in job.evidence if e.get("kind") == kind] for kind in ("validation", "review")}
    for name in ("validation", "review"):
        if newest[name] and newest[name][-1]["id"] != record[name]["evidence"]:
            problems.append(f"the newest {name} evidence is {newest[name][-1]['id']}, not the signed "
                            f"{record[name]['evidence']}: the approval is for superseded evidence")
    return problems
