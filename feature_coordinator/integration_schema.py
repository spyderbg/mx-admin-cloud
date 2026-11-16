"""W4.7 (M1-M3): the integration job record's schema — pure checks of its values, classified paths and evidence.

Split out of integration.py (which re-exports every public name here, so `from .integration import …` keeps
working) when M3 added the candidate and the gate and validation evidence. No Git, no network, no state store: every
function checks values it is given (a job is read by attribute: approvals, evidence, candidate, source, base, issue).

Evidence (append-only; every entry refers only to EARLIER entries, ids are `<prefix>-<position>`):
  * inspection (M2) — one inspection decision under an approval, bound to the pinned SHAs;
  * candidate (M3)  — one candidate build under a passed inspection: built, conflict, needs-authorization or refused;
                      at most one `built` entry, and no candidate entry after it (its identity is immutable);
  * attempt (M3)    — a validation attempt, recorded BEFORE its gates run (write-ahead): the gate plan it validates
                      (digest and gates) and the gates it will run. Every gate entry follows an attempt; a validation
                      entry names the newest attempt (whose plan it decides). An attempt no validation names was
                      interrupted;
  * gate (M3)       — one gate's outcome (PASS, FAIL, SKIPPED, BLOCKED, with the runner's status, exit code, duration,
                      log digests and toolchain), bound to the built candidate's head and tree SHAs;
  * validation (M3) — the outcome of the complete gate plan for the built candidate; its result must follow from its
                      gate results (any FAIL → failed, else any BLOCKED → blocked, else passed) AND from every FAIL
                      recorded for the candidate before it: a recorded failure is never dropped (Codex review of M3,
                      F1), so no passed or blocked validation follows a FAIL of the same candidate.
  * review-attempt (M4) — one invocation of the independent AI reviewer, recorded BEFORE its process starts
                      (write-ahead): the candidate's head/tree SHAs, the pinned SHAs, the approval, the PASSED
                      validation decision it relies on (and that decision's plan), the review round, the effective
                      reviewer, its independence, the digests of the exact reviewer input and the process tag, run
                      directory and deadline. A new attempt follows only an attempt that ended (a run names it);
                      a new ROUND only an ERROR decision, with the operator's retry; no attempt follows a PASS /
                      PASS_WITH_NOTES / REQUEST_CHANGES decision;
  * review-run (M4)  — how one attempt ended: the reviewer's status, whether it counts against the round's budget
                      (`unavailable`, `cancelled` and `not-started` are given back), the validated answer and its
                      outcome (only for `ok`; the outcome must be review_result.decide()'s for that answer and the
                      attempt's coordinator notes), the digests of the raw answer and of the result file;
  * review (M4)      — the review DECISION for the candidate: PASS / PASS_WITH_NOTES / REQUEST_CHANGES carry exactly the
                      answer of an `ok` run of the decided attempt; ERROR follows a run without a valid answer. It
                      repeats the attempt's bindings (candidate, SHAs, approval, validation, plan, round, reviewer,
                      independence, input digests), so one entry tells whether it applies to the current candidate.
Every entry's `sha256` covers its payload (everything but id, time and digest); what a result claims is checked after
the digest, as M2 does.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping

from .errors import CoordinatorError
from .review_result import BLOCKING, SEVERITIES, VERDICTS, Review, decide

SHA_RE = re.compile(r"[0-9a-f]{40}")


MAX_REASON = 500


class IntegrationError(CoordinatorError):
    """A deliberate refusal of an integration-job operation (malformed input, unknown job, conflict)."""


def clean_text(value: Any, limit: int = MAX_REASON) -> str:
    """*value* as one printable line of at most *limit* characters (internal reasons; never raises)."""
    text = " ".join(re.sub(r"[\x00-\x1f\x7f]", " ", str(value)).split())
    return text if len(text) <= limit else text[:limit - 3] + "..."


def _shown(value: Any) -> str:
    text = repr(value)
    return text if len(text) <= 60 else text[:57] + "..."


_TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z")   # model.utc_now()


MAX_TEXT = 1000


MAX_PATH_TEXT = 1000


# -- M2: the classified changed paths and the inspection evidence ---------------------------------------------------
PATH_CLASSES = ("owned", "approval-required", "unowned", "protected")


PATH_CHANGES = ("added", "deleted", "modified", "mode", "type")


PATH_FLAGS = ("symlink", "gitlink", "executable", "git-control", "renamed", "history-only", "empty-tree")


PATH_AUTHORIZATION = ("not-needed", "authorized", "required", "refused")


_PATH_KEYS = frozenset({"path", "class", "role", "changes", "flags", "authorization"})


INSPECTION_RESULTS = ("passed", "needs-authorization", "refused", "stale")


_INSPECTION_KEYS = frozenset({"kind", "id", "at", "approval", "source_sha", "base_sha", "result", "pr_match",
                              "provenance", "paths", "reasons", "sha256"})


_PR_MATCH_KEYS = frozenset({"result", "number", "candidates"})


PR_MATCH_RESULTS = ("none", "reuse", "refused", "stale", "unknown")


_INSPECTION_PROVENANCE_KEYS = frozenset({"source", "verified", "unverified", "unverifiable", "accepted"})


PROVENANCE_SOURCES = ("github", "unavailable", "unknown")


_INSPECTION_PATHS_KEYS = frozenset({"count", "sha256", "required", "authorized", "refused"})


_HEX64_RE = re.compile(r"[0-9a-f]{64}")


MAX_EVIDENCE = 500


# -- M3: the integration candidate, its gate evidence and the validation summary (integration_validate.py) ---------
# Evidence ids are `<prefix>-<position in the evidence list>` for every kind (M2's `ins-NNNN` rule, unchanged).
EVIDENCE_PREFIXES: Mapping[str, str] = {"inspection": "ins", "candidate": "can", "attempt": "att", "gate": "gat",
                                         "validation": "val", "review-attempt": "rva", "review-run": "rvr",
                                         "review": "rev"}


_EVIDENCE_KINDS = {prefix: kind for kind, prefix in EVIDENCE_PREFIXES.items()}


_EVIDENCE_ID_RE = re.compile(r"([a-z]{3})-(\d{4})")


CANDIDATE_RESULTS = ("built", "conflict", "needs-authorization", "refused")


_CANDIDATE_EVIDENCE_KEYS = frozenset({"kind", "id", "at", "approval", "inspection", "source_sha", "base_sha", "issue",
                                      "result", "head_sha", "tree_sha", "conflicts", "paths", "roles", "git", "reasons",
                                      "sha256"})


# The candidate as recorded on the job (design §8.2 `candidate`), set once by INSPECTING → CANDIDATE_BUILT, immutable.
_CANDIDATE_FIELD_KEYS = frozenset({"evidence", "head_sha", "tree_sha", "base_sha", "source_sha", "repository",
                                   "worktree", "git", "built_at", "paths"})


GATE_RESULTS = ("PASS", "FAIL", "SKIPPED", "BLOCKED")


# The gate runner's status (model.GATE_STATUSES, plus `skipped` and `missing` decided before running) → the result.
# A gate that could not run or did not finish (blocked, error, timeout, missing) is BLOCKED: it never passes.
GATE_STATUS_RESULT: Mapping[str, str] = {"passed": "PASS", "failed": "FAIL", "skipped": "SKIPPED",
                                         "timeout": "BLOCKED", "blocked": "BLOCKED", "error": "BLOCKED",
                                         "missing": "BLOCKED"}


_GATE_EVIDENCE_KEYS = frozenset({"kind", "id", "at", "candidate", "head_sha", "tree_sha", "gate", "required_by",
                                 "fingerprint", "command", "result", "status", "exit_code", "duration_seconds",
                                 "started_at", "summary", "log", "stdout", "stderr", "toolchain", "sha256"})


_LOG_DIGEST_KEYS = frozenset({"sha256", "bytes"})


VALIDATION_RESULTS = ("passed", "failed", "blocked")


_VALIDATION_EVIDENCE_KEYS = frozenset({"kind", "id", "at", "candidate", "head_sha", "tree_sha", "attempt", "result",
                                       "plan", "gates", "reasons", "sha256"})


_ATTEMPT_EVIDENCE_KEYS = frozenset({"kind", "id", "at", "candidate", "head_sha", "tree_sha", "approval", "plan", "run",
                                    "sha256"})


MAX_CONFLICTS_LISTED = 200


MAX_GATES = 100


# -- M4: the independent AI review of the candidate (integration_review.py) ------------------------------------------
REVIEW_OUTCOMES = ("PASS", "PASS_WITH_NOTES", "REQUEST_CHANGES", "ERROR")   # review_result.OUTCOMES
POSITIVE_OUTCOMES = ("PASS", "PASS_WITH_NOTES")                              # → AWAITING_MERGE_APPROVAL
# How one reviewer invocation ended: the reviewer's own statuses (reviewer.RESULT_STATUSES) plus `invalid` (an answer
# that is not a valid review), `interrupted` (it started — its output file exists — and ended without a result) and
# `not-started` (no model request was made: the process never launched the CLI).
REVIEW_RUN_STATUSES = ("ok", "invalid", "failed", "timeout", "refused", "cancelled", "unavailable", "interrupted",
                       "not-started")
UNCOUNTED_RUN_STATUSES = ("cancelled", "unavailable", "not-started")   # given back: they do not use the round's budget
INDEPENDENCE_LEVELS = ("provider", "model", "none", "unknown")          # review_context.independence + `unknown`
_REVIEW_ATTEMPT_KEYS = frozenset({"kind", "id", "at", "candidate", "head_sha", "tree_sha", "source_sha", "base_sha",
                                  "approval", "validation", "plan_sha256", "round", "reviewer", "independence",
                                  "context", "timeout_seconds", "run_dir", "tag", "deadline", "retry", "sha256"})
_REVIEW_RUN_KEYS = frozenset({"kind", "id", "at", "attempt", "candidate", "head_sha", "tree_sha", "status", "counted",
                              "outcome", "verdict", "summary", "findings", "limitations", "output_sha256",
                              "result_sha256", "error", "details", "started_at", "ended_at", "duration_seconds",
                              "reasons", "sha256"})
_REVIEW_KEYS = frozenset({"kind", "id", "at", "candidate", "head_sha", "tree_sha", "source_sha", "base_sha", "approval",
                          "validation", "plan_sha256", "round", "attempt", "run", "outcome", "verdict", "summary",
                          "findings", "limitations", "coordinator_notes", "blocking", "reviewer", "independence",
                          "input_sha256", "diff_sha256", "spec_sha256", "output_sha256", "result_sha256", "reasons",
                          "sha256"})
_REVIEWER_KEYS = frozenset({"provider", "kind", "model", "config_sha256"})
_INDEPENDENCE_KEYS = frozenset({"mode", "level", "author", "session", "limitation"})
_REVIEW_CONTEXT_KEYS = frozenset({"input_sha256", "diff_sha256", "spec_sha256", "files", "redactions", "notes"})
_FINDING_KEYS = frozenset({"severity", "file", "line_start", "line_end", "title", "explanation", "recommendation"})
_TAG_RE = re.compile(r"review-[0-9a-f]{16}")
MAX_REVIEW_FINDINGS = 100        # github.review.max_findings is at most 100
MAX_REVIEW_NOTES = 20


def check_path_text(value: Any, label: str = "path") -> str:
    """A repository-relative POSIX path as an operator names it in an authorization record (no glob, no `..`)."""
    valid = isinstance(value, str) and 0 < len(value) <= MAX_PATH_TEXT and not value.startswith("/")
    if not valid or re.search(r"[\x00-\x1f\x7f\\]", value) or any(p in ("", ".", "..") for p in value.split("/")):
        raise IntegrationError(f"{label} must be a repository-relative path (no '..', no leading '/', no control "
                               f"characters), got {_shown(value)}")
    return value


def _check_paths(paths: list[dict[str, Any]]) -> None:
    seen: set[str] = set()
    for index, entry in enumerate(paths):
        where = f"paths[{index}]"
        _expect(set(entry) == _PATH_KEYS, f"{where} must have exactly {sorted(_PATH_KEYS)}")
        check_path_text(entry["path"], f"{where}: path")
        _expect(entry["path"] not in seen, f"{where}: path must be a unique repository path")
        seen.add(entry["path"])
        _expect(entry["class"] in PATH_CLASSES, f"{where}: class must be one of {list(PATH_CLASSES)}")
        _expect(entry["role"] is None or _is_text(entry["role"], 40), f"{where}: role must be null or a role name")
        _expect((entry["role"] is not None) == (entry["class"] == "owned") or entry["class"] == "approval-required",
                f"{where}: an owned path names its role, a protected or unowned path none")
        for key, allowed in (("changes", PATH_CHANGES), ("flags", PATH_FLAGS)):
            value = entry[key]
            _expect(isinstance(value, list) and all(item in allowed for item in value)
                    and value == sorted(set(value)), f"{where}: {key} must be a sorted subset of {list(allowed)}")
        _expect(bool(entry["changes"]), f"{where}: changes must not be empty")
        _expect(entry["authorization"] in PATH_AUTHORIZATION,
                f"{where}: authorization must be one of {list(PATH_AUTHORIZATION)}")
        needs = entry["class"] in ("approval-required", "unowned") or bool({"symlink", "git-control"}
                                                                              & set(entry["flags"]))
        must_refuse = entry["class"] == "protected" or bool({"gitlink", "empty-tree"} & set(entry["flags"]))
        status = entry["authorization"]
        _expect(status == "refused" if must_refuse else (status != "not-needed" or not needs),
                f"{where}: authorization {status!r} contradicts class {entry['class']!r} and flags {entry['flags']}")
        _expect(status not in ("authorized", "required") or needs,
                f"{where}: authorization {status!r} for a path that needs none")


def inspection_digest(entry: Mapping[str, Any]) -> str:
    """SHA-256 of an evidence payload (everything but its id, time and digest): two inspections with the same digest
    found exactly the same thing under the same approval for the same pinned SHAs. M3 binds candidate, gate and
    validation entries the same way (evidence_digest)."""
    payload = {k: v for k, v in entry.items() if k not in ("id", "at", "sha256")}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


evidence_digest = inspection_digest


def _check_evidence(job: "IntegrationJob") -> None:
    """Every evidence entry, by kind. Entries may only refer to EARLIER entries (append-only audit trail)."""
    _expect(len(job.evidence) <= MAX_EVIDENCE, f"at most {MAX_EVIDENCE} evidence entries")
    approval_ids = {entry["id"] for entry in job.approvals}
    earlier: dict[str, dict[str, Any]] = {}
    built: list[str] = []
    for index, entry in enumerate(job.evidence):
        where = f"evidence {index + 1}"
        kind = entry.get("kind")
        _expect(kind in EVIDENCE_PREFIXES, f"{where}: kind must be one of {list(EVIDENCE_PREFIXES)}")
        match = _EVIDENCE_ID_RE.fullmatch(str(entry.get("id")))
        _expect(match is not None and int(match.group(2)) == index + 1,
                f"{where} must have the id {EVIDENCE_PREFIXES[kind]}-{index + 1:04d}")
        assert match is not None
        _expect(match.group(1) == EVIDENCE_PREFIXES[kind],
                f"{where}: kind must be {_EVIDENCE_KINDS.get(match.group(1), '?')!r} for the id {entry['id']} (an "
                f"entry of kind {kind!r} has the id {EVIDENCE_PREFIXES[kind]}-{index + 1:04d})")
        if kind == "inspection":
            _check_inspection_entry(job, entry, where, approval_ids)
        elif kind == "candidate":
            _expect(not built, f"{where}: no candidate entry follows the built candidate {built[:1]} (its identity "
                               "is immutable)")
            _check_candidate_entry(job, entry, where, approval_ids, earlier)
            if entry["result"] == "built":
                built.append(entry["id"])
        elif kind == "attempt":
            _check_attempt_entry(entry, where, approval_ids, earlier)
        elif kind == "gate":
            _check_gate_entry(entry, where, earlier)
        elif kind == "validation":
            _check_validation_entry(entry, where, earlier)
        elif kind == "review-attempt":
            _check_review_attempt_entry(job, entry, where, approval_ids, earlier)
        elif kind == "review-run":
            _check_review_run_entry(entry, where, earlier)
        else:
            _check_review_entry(job, entry, where, approval_ids, earlier)
        _expect(_is_time(entry["at"]), f"{where}: at must be a timestamp")
        _expect(_is_text_list(entry.get("reasons", [])) and len(entry.get("reasons", [])) <= 20,
                f"{where}: reasons must be at most 20 texts")
        _expect(entry["sha256"] == evidence_digest(entry), f"{where}: its SHA-256 does not match its payload")
        _check_entry_meaning(entry, where, earlier)   # after the digest: a consistent payload must also be coherent
        if kind in ("review-attempt", "review-run", "review"):
            _check_review_meaning(entry, where, earlier)
        earlier[entry["id"]] = entry
    if job.paths:
        with_paths = [entry for entry in job.evidence if entry["kind"] == "inspection" and entry["paths"]["count"]]
        _expect(bool(with_paths) and with_paths[-1]["paths"]["sha256"] == paths_digest(job.paths)
                and with_paths[-1]["paths"]["count"] == len(job.paths),
                "paths are not the classified paths bound into the newest inspection evidence")


def _check_inspection_entry(job: "IntegrationJob", entry: dict[str, Any], where: str, approval_ids: set[str]) -> None:
    _expect(set(entry) == _INSPECTION_KEYS, f"{where} must have exactly {sorted(_INSPECTION_KEYS)}")
    _expect(entry["approval"] in approval_ids, f"{where} names approval {_shown(entry['approval'])}, which this "
                                               "job does not carry")
    _expect(entry["source_sha"] == job.source["sha"] and entry["base_sha"] == job.base["expected_sha"],
            f"{where} is bound to other SHAs than the job's pinned source and base")
    _expect(entry["result"] in INSPECTION_RESULTS, f"{where}: result must be one of {list(INSPECTION_RESULTS)}")
    pr_match = entry["pr_match"]
    _expect(isinstance(pr_match, dict) and set(pr_match) == _PR_MATCH_KEYS
            and pr_match["result"] in PR_MATCH_RESULTS
            and (pr_match["number"] is None or _is_int(pr_match["number"], 1))
            and isinstance(pr_match["candidates"], list) and all(_is_int(n, 1) for n in pr_match["candidates"]),
            f"{where}: pr_match must have exactly {sorted(_PR_MATCH_KEYS)}")
    prov = entry["provenance"]
    _expect(isinstance(prov, dict) and set(prov) == _INSPECTION_PROVENANCE_KEYS
            and prov["source"] in PROVENANCE_SOURCES and isinstance(prov["accepted"], bool)
            and all(_is_int(prov[k], 0) for k in ("verified", "unverified", "unverifiable")),
            f"{where}: provenance must have exactly {sorted(_INSPECTION_PROVENANCE_KEYS)}")
    _check_paths_summary(entry["paths"], where)


def _check_entry_meaning(entry: dict[str, Any], where: str, earlier: Mapping[str, dict[str, Any]]) -> None:
    """What a result claims must follow from the rest of its entry and, for a validation, from the gate evidence
    before it (checked after the digest, like M2)."""
    summary = entry.get("paths")
    if entry["kind"] == "inspection" and entry["result"] == "passed":
        _expect(summary["required"] == 0 and summary["refused"] == 0
                and entry["pr_match"]["result"] in ("none", "reuse"),
                f"{where}: a passed inspection cannot leave a path unauthorized or refused or a PR unresolved")
    elif entry["kind"] == "candidate" and entry["result"] == "built":
        _expect(summary["required"] == 0 and summary["refused"] == 0,
                f"{where}: a built candidate cannot carry an unauthorized or refused path")
    elif entry["kind"] == "candidate" and entry["result"] == "needs-authorization":
        _expect(summary["required"] > 0 and summary["refused"] == 0,
                f"{where}: needs-authorization must list paths that need authorization and none refused")
    elif entry["kind"] == "validation":
        results = {item["result"] for item in entry["gates"]}
        failed_before = sorted(e["id"] for e in earlier.values() if e["kind"] == "gate"
                               and e["candidate"] == entry["candidate"] and e["result"] == "FAIL")
        expected = ("failed" if "FAIL" in results or failed_before
                    else "blocked" if "BLOCKED" in results or not results else "passed")
        _expect(entry["result"] == expected, f"{where}: result {entry['result']!r} contradicts its gate results "
                                             f"and the failures recorded for its candidate {failed_before[:5]} "
                                             f"(expected {expected!r})")


def _check_paths_summary(summary: Any, where: str) -> None:
    _expect(isinstance(summary, dict) and set(summary) == _INSPECTION_PATHS_KEYS
            and all(_is_int(summary[k], 0) for k in ("count", "required", "authorized", "refused"))
            and isinstance(summary["sha256"], str)
            and (summary["sha256"] == "" or _HEX64_RE.fullmatch(summary["sha256"]) is not None),
            f"{where}: paths must have exactly {sorted(_INSPECTION_PATHS_KEYS)}")


def _check_candidate_entry(job: "IntegrationJob", entry: dict[str, Any], where: str, approval_ids: set[str],
                           earlier: Mapping[str, dict[str, Any]]) -> None:
    """M3: a candidate built (or found unusable) from the job's pinned SHAs under a passed inspection."""
    _expect(set(entry) == _CANDIDATE_EVIDENCE_KEYS, f"{where} must have exactly {sorted(_CANDIDATE_EVIDENCE_KEYS)}")
    _expect(entry["approval"] in approval_ids, f"{where} names approval {_shown(entry['approval'])}, which this "
                                               "job does not carry")
    inspection = earlier.get(str(entry["inspection"]))
    _expect(inspection is not None and inspection["kind"] == "inspection" and inspection["result"] == "passed"
            and inspection["approval"] == entry["approval"],
            f"{where} must name an earlier passed inspection under the same approval")
    _expect(entry["source_sha"] == job.source["sha"] and entry["base_sha"] == job.base["expected_sha"],
            f"{where} is bound to other SHAs than the job's pinned source and base")
    _expect(entry["issue"] == {"number": job.issue, "state": "open"},
            f"{where}: issue must record the job's Issue #{job.issue} as verified open")
    result = entry["result"]
    _expect(result in CANDIDATE_RESULTS, f"{where}: result must be one of {list(CANDIDATE_RESULTS)}")
    _expect(_is_sha(entry["tree_sha"]) and (entry["head_sha"] is None if result == "conflict"
                                            else _is_sha(entry["head_sha"])),
            f"{where}: tree_sha must be a SHA, head_sha a SHA (null only for a conflict)")
    conflicts = entry["conflicts"]
    _expect(isinstance(conflicts, list) and len(conflicts) <= MAX_CONFLICTS_LISTED
            and all(_is_text(path, MAX_PATH_TEXT) for path in conflicts) and bool(conflicts) == (result == "conflict"),
            f"{where}: conflicts must list the conflicted paths exactly for a conflict")
    _check_paths_summary(entry["paths"], where)
    _expect(isinstance(entry["roles"], list) and all(_is_text(role, 40) for role in entry["roles"])
            and entry["roles"] == sorted(set(entry["roles"])), f"{where}: roles must be a sorted list of role names")
    _expect(_is_text(entry["git"], 200), f"{where}: git must be the Git version text")


def _check_built_reference(entry: dict[str, Any], where: str, earlier: Mapping[str, dict[str, Any]]) -> None:
    candidate = earlier.get(str(entry["candidate"]))
    _expect(candidate is not None and candidate["kind"] == "candidate" and candidate["result"] == "built"
            and candidate["head_sha"] == entry["head_sha"] and candidate["tree_sha"] == entry["tree_sha"],
            f"{where} must name the built candidate and carry its head and tree SHAs")


def _newest_attempt(entry: dict[str, Any], earlier: Mapping[str, dict[str, Any]]) -> dict[str, Any] | None:
    """The newest attempt entry for *entry*'s candidate before it (earlier keeps the record order)."""
    return next((e for e in reversed(earlier.values()) if e["kind"] == "attempt"
                 and e["candidate"] == entry["candidate"]), None)


def _check_plan(plan: Any, where: str) -> None:
    _expect(isinstance(plan, dict) and set(plan) == {"sha256", "gates"} and isinstance(plan["sha256"], str)
            and _HEX64_RE.fullmatch(plan["sha256"]) is not None and isinstance(plan["gates"], list)
            and len(plan["gates"]) <= MAX_GATES
            and all(isinstance(g, dict) and set(g) == {"gate", "required_by"} for g in plan["gates"]),
            f"{where}: plan must be {{sha256, gates: [{{gate, required_by}}]}}")


def _check_attempt_entry(entry: dict[str, Any], where: str, approval_ids: set[str],
                         earlier: Mapping[str, dict[str, Any]]) -> None:
    """M3: one validation attempt of the built candidate, recorded before its gates run (write-ahead)."""
    _expect(set(entry) == _ATTEMPT_EVIDENCE_KEYS, f"{where} must have exactly {sorted(_ATTEMPT_EVIDENCE_KEYS)}")
    _check_built_reference(entry, where, earlier)
    _expect(entry["approval"] in approval_ids, f"{where} names approval {_shown(entry['approval'])}, which this "
                                               "job does not carry")
    _check_plan(entry["plan"], where)
    planned = [g["gate"] for g in entry["plan"]["gates"]]
    _expect(isinstance(entry["run"], list) and all(isinstance(name, str) and name in planned for name in entry["run"])
            and len(set(entry["run"])) == len(entry["run"]), f"{where}: run must list distinct planned gates")


def _check_gate_entry(entry: dict[str, Any], where: str, earlier: Mapping[str, dict[str, Any]]) -> None:
    """M3: one gate's outcome, bound to the built candidate's immutable head and tree SHAs, run by an attempt."""
    _expect(set(entry) == _GATE_EVIDENCE_KEYS, f"{where} must have exactly {sorted(_GATE_EVIDENCE_KEYS)}")
    _check_built_reference(entry, where, earlier)
    _expect(_newest_attempt(entry, earlier) is not None, f"{where}: a gate runs only within a recorded validation "
                                                         "attempt (none precedes it)")
    _expect(_is_text(entry["gate"], 100) and bool(entry["gate"]), f"{where}: gate must be a gate name")
    _expect(isinstance(entry["required_by"], list) and bool(entry["required_by"])
            and all(_is_text(item, 40) for item in entry["required_by"])
            and entry["required_by"] == sorted(set(entry["required_by"])),
            f"{where}: required_by must be a sorted, non-empty list of roles")
    _expect(isinstance(entry["fingerprint"], str) and _HEX64_RE.fullmatch(entry["fingerprint"]) is not None,
            f"{where}: fingerprint must be a SHA-256")
    _expect(isinstance(entry["command"], list) and len(entry["command"]) <= 100
            and all(_is_text(arg) for arg in entry["command"]), f"{where}: command must be a list of texts")
    _expect(entry["result"] in GATE_RESULTS and GATE_STATUS_RESULT.get(entry["status"]) == entry["result"],
            f"{where}: result must be one of {list(GATE_RESULTS)} and agree with the status {_shown(entry['status'])}")
    _expect(entry["exit_code"] is None or type(entry["exit_code"]) is int,  # pylint: disable=unidiomatic-typecheck
            f"{where}: exit_code must be null or an integer")
    _expect(entry["result"] != "PASS" or entry["exit_code"] == 0, f"{where}: a PASS needs exit code 0")
    duration = entry["duration_seconds"]
    _expect(isinstance(duration, (int, float)) and not isinstance(duration, bool) and duration >= 0,
            f"{where}: duration_seconds must be a non-negative number")
    _expect(_is_time(entry["started_at"]), f"{where}: started_at must be a timestamp")
    _expect(_is_text(entry["summary"]) and _is_text(entry["log"]), f"{where}: summary and log must be texts")
    for stream in ("stdout", "stderr"):
        value = entry[stream]
        _expect(isinstance(value, dict) and set(value) == _LOG_DIGEST_KEYS and _is_int(value["bytes"], 0)
                and isinstance(value["sha256"], str)
                and (value["sha256"] == "" or _HEX64_RE.fullmatch(value["sha256"]) is not None),
                f"{where}: {stream} must be {{sha256, bytes}} of its log")
    toolchain = entry["toolchain"]
    _expect(isinstance(toolchain, dict) and len(toolchain) <= 20
            and all(_is_text(k, 100) and _is_text(v) for k, v in toolchain.items()),
            f"{where}: toolchain must map names to texts")


def _check_validation_entry(entry: dict[str, Any], where: str, earlier: Mapping[str, dict[str, Any]]) -> None:
    """M3: the outcome of the complete gate set for the built candidate."""
    _expect(set(entry) == _VALIDATION_EVIDENCE_KEYS, f"{where} must have exactly {sorted(_VALIDATION_EVIDENCE_KEYS)}")
    _check_built_reference(entry, where, earlier)
    _expect(entry["result"] in VALIDATION_RESULTS, f"{where}: result must be one of {list(VALIDATION_RESULTS)}")
    plan, gates = entry["plan"], entry["gates"]
    _check_plan(plan, where)
    attempt = _newest_attempt(entry, earlier)
    _expect(attempt is not None and entry["attempt"] == attempt["id"]
            and attempt["plan"]["sha256"] == plan["sha256"],
            f"{where}: attempt must name the newest validation attempt of its candidate, for the same plan")
    _expect(isinstance(gates, list) and [g.get("gate") for g in gates if isinstance(g, dict)]
            == [g["gate"] for g in plan["gates"]],
            f"{where}: gates must give one result per planned gate, in plan order")
    for item in gates:
        _expect(set(item) == {"gate", "evidence", "result"} and item["result"] in GATE_RESULTS,
                f"{where}: each gate result must be {{gate, evidence, result}}")
        gate_entry = earlier.get(str(item["evidence"]))
        _expect(gate_entry is not None and gate_entry["kind"] == "gate" and gate_entry["gate"] == item["gate"]
                and gate_entry["result"] == item["result"] and gate_entry["head_sha"] == entry["head_sha"]
                and gate_entry["tree_sha"] == entry["tree_sha"],
                f"{where}: gate {_shown(item['gate'])} must refer to its earlier gate evidence for this candidate")


def _check_candidate(job: "IntegrationJob") -> None:
    """M3: the job's candidate field agrees with its built candidate evidence (and binds its classified paths)."""
    built = [entry for entry in job.evidence if entry["kind"] == "candidate" and entry["result"] == "built"]
    candidate = job.candidate
    if candidate is None:
        _expect(not built, "a built candidate evidence entry needs the job's candidate")
        _expect(not any(entry["kind"] in _CANDIDATE_BOUND for entry in job.evidence),
                "attempt, gate, validation and review evidence need a built candidate")
        return
    _expect(set(candidate) == _CANDIDATE_FIELD_KEYS, f"candidate must have exactly {sorted(_CANDIDATE_FIELD_KEYS)}")
    _expect(len(built) == 1 and candidate["evidence"] == built[0]["id"]
            and candidate["head_sha"] == built[0]["head_sha"] and candidate["tree_sha"] == built[0]["tree_sha"],
            "candidate must name its built candidate evidence and carry the same head and tree SHAs")
    _expect(candidate["base_sha"] == job.base["expected_sha"] and candidate["source_sha"] == job.source["sha"],
            "candidate is bound to other SHAs than the job's pinned source and base")
    _expect(all(_is_text(candidate[k]) for k in ("repository", "worktree", "git")) and _is_time(candidate["built_at"]),
            "candidate repository, worktree and git must be texts, built_at a timestamp")
    paths = candidate["paths"]
    _expect(isinstance(paths, list) and all(isinstance(item, dict) for item in paths),
            "candidate.paths must be a list of objects")
    _check_paths(paths)
    summary = built[0]["paths"]
    _expect(summary["count"] == len(paths) and summary["sha256"] == (paths_digest(paths) if paths else ""),
            "candidate.paths are not the classified paths bound into its candidate evidence")
    _expect(all(entry["candidate"] == built[0]["id"] for entry in job.evidence if entry["kind"] in _CANDIDATE_BOUND),
            "attempt, gate, validation and review evidence must name the job's built candidate")


_CANDIDATE_BOUND = ("attempt", "gate", "validation", "review-attempt", "review-run", "review")


# -- M4: review evidence ------------------------------------------------------------------------------------------

def _review_attempts(earlier: Mapping[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in earlier.values() if e["kind"] == "review-attempt"]


def _check_reviewer(value: Any, where: str) -> None:
    _expect(isinstance(value, dict) and set(value) == _REVIEWER_KEYS
            and all(_is_text(value[k], 100) and value[k] for k in ("provider", "kind", "model"))
            and _is_digest(value["config_sha256"]),
            f"{where}: reviewer must be {{provider, kind, model, config_sha256}} (texts and the configuration digest)")


def _validation_current_at(validation_id: Any, earlier: Mapping[str, dict[str, Any]]) -> bool:
    """True when *validation_id* names the NEWEST validation decision at this position of the evidence and no
    validation attempt or gate followed it — integration.validated() evaluated on the evidence before this entry
    (Codex review of M4, F4): a newer M3 attempt, open or decided, or a later gate revokes it."""
    entries = list(earlier.values())
    position = next((i for i, e in enumerate(entries) if e["id"] == validation_id), -1)
    return position >= 0 and entries[position]["kind"] == "validation" \
        and not any(e["kind"] in ("validation", "attempt", "gate") for e in entries[position + 1:])


def _check_independence(value: Any, where: str) -> None:
    _expect(isinstance(value, dict) and set(value) == _INDEPENDENCE_KEYS and value["mode"] in ("prefer", "require")
            and value["level"] in INDEPENDENCE_LEVELS and all(_is_text(value[k]) for k in ("author", "session",
                                                                                          "limitation")),
            f"{where}: independence must be {sorted(_INDEPENDENCE_KEYS)} (mode prefer|require, level one of "
            f"{list(INDEPENDENCE_LEVELS)})")
    _expect(value["mode"] != "require" or value["level"] in ("provider", "model"),
            f"{where}: independence `require` was not met (level {value['level']}): no reviewer may run")
    _expect(value["level"] in ("provider", "model") or bool(value["limitation"]),
            f"{where}: an incomplete independence ({value['level']}) must state its limitation")


def _is_digest(value: Any, *, empty: bool = False) -> bool:
    return isinstance(value, str) and ((empty and value == "") or _HEX64_RE.fullmatch(value) is not None)


def _check_findings(findings: Any, limitations: Any, where: str) -> None:
    _expect(isinstance(findings, list) and len(findings) <= MAX_REVIEW_FINDINGS, f"{where}: findings must be a list")
    for finding in findings:
        _expect(isinstance(finding, dict) and set(finding) == _FINDING_KEYS and finding["severity"] in SEVERITIES
                and _is_text(finding["file"], MAX_PATH_TEXT) and all(_is_text(finding[k], 2000) for k in
                                                                      ("title", "explanation", "recommendation"))
                and all(finding[k] is None or _is_int(finding[k], 1) for k in ("line_start", "line_end")),
                f"{where}: each finding must be {sorted(_FINDING_KEYS)} with a severity of {list(SEVERITIES)}")
    _expect(isinstance(limitations, list) and len(limitations) <= MAX_REVIEW_NOTES
            and all(_is_text(item) for item in limitations), f"{where}: limitations must be a list of texts")


def _check_review_attempt_entry(job: "IntegrationJob", entry: dict[str, Any], where: str, approval_ids: set[str],
                                earlier: Mapping[str, dict[str, Any]]) -> None:
    """M4: one reviewer invocation of the built candidate, recorded before the reviewer process starts."""
    _expect(set(entry) == _REVIEW_ATTEMPT_KEYS, f"{where} must have exactly {sorted(_REVIEW_ATTEMPT_KEYS)}")
    _check_built_reference(entry, where, earlier)
    _expect(entry["approval"] in approval_ids, f"{where} names approval {_shown(entry['approval'])}, which this "
                                               "job does not carry")
    _expect(entry["source_sha"] == job.source["sha"] and entry["base_sha"] == job.base["expected_sha"],
            f"{where} is bound to other SHAs than the job's pinned source and base")
    validation = earlier.get(str(entry["validation"]))
    _expect(validation is not None and validation["kind"] == "validation" and validation["result"] == "passed"
            and validation["candidate"] == entry["candidate"] and validation["head_sha"] == entry["head_sha"]
            and validation["tree_sha"] == entry["tree_sha"] and validation["plan"]["sha256"] == entry["plan_sha256"],
            f"{where} must name an earlier PASSED validation of its candidate and carry that decision's plan digest")
    _expect(_validation_current_at(entry["validation"], earlier),
            f"{where}: validation {_shown(entry['validation'])} is not current here (a newer validation attempt, gate "
            "or validation decision follows it): no review starts from a revoked validation")
    _expect(_is_int(entry["round"], 1), f"{where}: round must be a positive integer")
    _check_reviewer(entry["reviewer"], where)
    _check_independence(entry["independence"], where)
    context = entry["context"]
    _expect(isinstance(context, dict) and set(context) == _REVIEW_CONTEXT_KEYS
            and all(_is_digest(context[k]) for k in ("input_sha256", "diff_sha256", "spec_sha256"))
            and _is_int(context["files"], 1) and _is_int(context["redactions"], 0)
            and isinstance(context["notes"], list) and len(context["notes"]) <= MAX_REVIEW_NOTES
            and all(_is_text(note) for note in context["notes"]),
            f"{where}: context must be {sorted(_REVIEW_CONTEXT_KEYS)} (digests of the exact reviewer input)")
    _expect(_is_int(entry["timeout_seconds"], 1) and _is_text(entry["run_dir"]) and bool(entry["run_dir"])
            and isinstance(entry["tag"], str) and _TAG_RE.fullmatch(entry["tag"]) is not None
            and _is_time(entry["deadline"]), f"{where}: timeout_seconds, run_dir, tag and deadline are required")
    retry = entry["retry"]
    _expect(retry is None or (isinstance(retry, dict) and set(retry) == {"by", "reason"}
                              and all(_is_text(retry[k], MAX_REASON) and retry[k] for k in ("by", "reason"))),
            f"{where}: retry must be null or {{by, reason}}")


def _check_review_run_entry(entry: dict[str, Any], where: str, earlier: Mapping[str, dict[str, Any]]) -> None:
    """M4: how one reviewer invocation ended."""
    _expect(set(entry) == _REVIEW_RUN_KEYS, f"{where} must have exactly {sorted(_REVIEW_RUN_KEYS)}")
    _check_built_reference(entry, where, earlier)
    _expect(entry["status"] in REVIEW_RUN_STATUSES, f"{where}: status must be one of {list(REVIEW_RUN_STATUSES)}")
    _expect(entry["counted"] is (entry["status"] not in UNCOUNTED_RUN_STATUSES),
            f"{where}: counted must be false exactly for {list(UNCOUNTED_RUN_STATUSES)}")
    ok = entry["status"] == "ok"
    _expect(entry["outcome"] in (POSITIVE_OUTCOMES + ("REQUEST_CHANGES",) if ok else (None,)),
            f"{where}: an `ok` run carries its outcome (PASS, PASS_WITH_NOTES, REQUEST_CHANGES), any other none")
    _expect(entry["verdict"] in (VERDICTS if ok else (None,)) and _is_text(entry["summary"], 2000)
            and (ok or (entry["summary"] == "" and entry["findings"] == [] and entry["limitations"] == [])),
            f"{where}: only an `ok` run carries a verdict, a summary, findings and limitations")
    _check_findings(entry["findings"], entry["limitations"], where)
    _expect(_is_digest(entry["output_sha256"], empty=not ok) and _is_digest(entry["result_sha256"], empty=True),
            f"{where}: output_sha256 and result_sha256 must be SHA-256 digests (an `ok` run's answer is digested)")
    details = entry["details"]
    _expect(_is_text(entry["error"]) and isinstance(details, dict) and len(details) <= 20
            and all(_is_text(k, 100) and (v is None or isinstance(v, (str, int, float, bool))) for k, v in
                    details.items()), f"{where}: error must be a text, details a flat object")
    duration = entry["duration_seconds"]
    _expect(_is_time(entry["started_at"]) and _is_time(entry["ended_at"]) and isinstance(duration, (int, float))
            and not isinstance(duration, bool) and duration >= 0,
            f"{where}: started_at and ended_at must be timestamps, duration_seconds non-negative")


def _check_review_entry(job: "IntegrationJob", entry: dict[str, Any], where: str, approval_ids: set[str],
                        earlier: Mapping[str, dict[str, Any]]) -> None:
    """M4: the review decision for the candidate."""
    _expect(set(entry) == _REVIEW_KEYS, f"{where} must have exactly {sorted(_REVIEW_KEYS)}")
    _check_built_reference(entry, where, earlier)
    _expect(entry["approval"] in approval_ids and entry["source_sha"] == job.source["sha"]
            and entry["base_sha"] == job.base["expected_sha"],
            f"{where} must name an approval of this job and the job's pinned source and base SHAs")
    _expect(entry["outcome"] in REVIEW_OUTCOMES, f"{where}: outcome must be one of {list(REVIEW_OUTCOMES)}")
    _check_findings(entry["findings"], entry["limitations"], where)
    _check_reviewer(entry["reviewer"], where)
    _check_independence(entry["independence"], where)
    _expect(isinstance(entry["coordinator_notes"], list) and all(_is_text(n) for n in entry["coordinator_notes"])
            and _is_int(entry["blocking"], 0) and _is_text(entry["summary"], 2000),
            f"{where}: coordinator_notes must be texts, blocking a count, summary a text")
    _expect(all(_is_digest(entry[k]) for k in ("input_sha256", "diff_sha256", "spec_sha256"))
            and _is_digest(entry["output_sha256"], empty=True) and _is_digest(entry["result_sha256"], empty=True),
            f"{where}: the input, output and result digests must be SHA-256 digests")


def _check_review_meaning(entry: dict[str, Any], where: str, earlier: Mapping[str, dict[str, Any]]) -> None:
    """M4 (after the digest): the order of attempts, runs and decisions, and what a run or a decision claims."""
    attempts = _review_attempts(earlier)
    decisions = [e for e in earlier.values() if e["kind"] == "review"]
    if entry["kind"] == "review-attempt":
        previous = attempts[-1] if attempts else None
        _expect(previous is None or _run_of(previous, earlier) is not None,
                f"{where}: a review attempt starts only after the previous one ended ({previous and previous['id']} "
                "has no run)")
        last = decisions[-1] if decisions else None
        _expect(last is None or last["outcome"] == "ERROR",
                f"{where}: no review attempt follows the {last and last['outcome']} decision {last and last['id']}")
        new_round = previous is None or entry["round"] != previous["round"]
        expected = 1 if previous is None else previous["round"] + (1 if new_round else 0)
        ended = last is not None and last["round"] == (previous or {}).get("round")
        _expect(entry["round"] == expected and (entry["retry"] is not None) == (previous is not None and new_round)
                and (not new_round or previous is None or ended),
                f"{where}: round {entry['round']} must continue the round of the previous attempt, or follow its ERROR "
                "decision as the next round with the operator's retry (the first round has none)")
        _expect(last is None or last["round"] != entry["round"],
                f"{where}: round {entry['round']} already ended with the ERROR decision {last and last['id']}")
        return
    attempt = earlier.get(str(entry["attempt"]))
    _expect(attempt is not None and attempt["kind"] == "review-attempt" and attempt["candidate"] == entry["candidate"]
            and attempt is (attempts[-1] if attempts else None),
            f"{where} must name the newest review attempt of its candidate")
    assert attempt is not None
    run = _run_of(attempt, earlier)
    if entry["kind"] == "review-run":
        _expect(run is None, f"{where}: attempt {attempt['id']} already ended ({run and run['id']})")
        if entry["status"] == "ok":
            outcome = decide(Review(entry["verdict"], entry["summary"], entry["findings"], entry["limitations"]),
                             list(attempt["context"]["notes"]))[0]
            _expect(outcome == entry["outcome"], f"{where}: outcome {entry['outcome']!r} is not what its answer and "
                                                 f"the attempt's notes decide ({outcome!r})")
        return
    _expect(run is not None and entry["run"] == run["id"], f"{where} must name the run of attempt {attempt['id']}")
    assert run is not None
    _expect(not any(d["attempt"] == attempt["id"] for d in decisions), f"{where}: attempt {attempt['id']} was decided")
    bound = {"validation": attempt["validation"], "plan_sha256": attempt["plan_sha256"], "round": attempt["round"],
             "approval": attempt["approval"], "reviewer": attempt["reviewer"],
             "independence": attempt["independence"], "input_sha256": attempt["context"]["input_sha256"],
             "diff_sha256": attempt["context"]["diff_sha256"], "spec_sha256": attempt["context"]["spec_sha256"],
             "coordinator_notes": attempt["context"]["notes"], "result_sha256": run["result_sha256"]}
    differing = sorted(k for k, v in bound.items() if entry[k] != v)
    _expect(not differing, f"{where}: {differing} differ from its attempt and run")
    _expect(entry["outcome"] == "ERROR" or _validation_current_at(entry["validation"], earlier),
            f"{where}: a {entry['outcome']} decision needs its validation {_shown(entry['validation'])} still current "
            "(no validation attempt, gate or decision after it)")
    answer = {k: run[k] for k in ("verdict", "summary", "findings", "limitations", "output_sha256")}
    if entry["outcome"] == "ERROR":
        _expect(run["status"] != "ok" and all(entry[k] == answer[k] for k in answer),
                f"{where}: an ERROR decision follows a run without a valid answer and carries none")
    else:
        _expect(run["status"] == "ok" and run["outcome"] == entry["outcome"] and all(entry[k] == answer[k]
                                                                                      for k in answer),
                f"{where}: a {entry['outcome']} decision carries exactly the answer of its `ok` run")
    blocking = sum(1 for f in entry["findings"] if f["severity"] in BLOCKING)
    _expect(entry["blocking"] == blocking and (entry["outcome"] == "REQUEST_CHANGES") == (blocking > 0),
            f"{where}: blocking must count the blocker/major findings, and only REQUEST_CHANGES has any")


def _run_of(attempt: Mapping[str, Any], earlier: Mapping[str, dict[str, Any]]) -> dict[str, Any] | None:
    return next((e for e in earlier.values() if e["kind"] == "review-run" and e["attempt"] == attempt["id"]), None)


def review_attempts(job: "IntegrationJob") -> list[dict[str, Any]]:
    """Every review attempt of the job's candidate, oldest first (empty without a candidate)."""
    if job.candidate is None:
        return []
    return [e for e in job.evidence if e["kind"] == "review-attempt" and e["candidate"] == job.candidate["evidence"]]


def review_run(job: "IntegrationJob", attempt: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """The run that ended *attempt* (None: none — the attempt is open: running, or interrupted)."""
    if attempt is None:
        return None
    return next((e for e in job.evidence if e["kind"] == "review-run" and e["attempt"] == attempt["id"]), None)


def review_decision(job: "IntegrationJob", attempt: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """The review decision of *attempt* (None: undecided)."""
    if attempt is None:
        return None
    return next((e for e in job.evidence if e["kind"] == "review" and e["attempt"] == attempt["id"]), None)


def open_review_attempt(job: "IntegrationJob") -> dict[str, Any] | None:
    """The newest review attempt when no run names it — its reviewer process runs, or the attempt was interrupted
    (crash) — else None."""
    attempts = review_attempts(job)
    return attempts[-1] if attempts and review_run(job, attempts[-1]) is None else None


def pending_answer(job: "IntegrationJob") -> dict[str, Any] | None:
    """The newest attempt's `ok` run when no decision names it yet: a valid answer whose freshness checks have not
    passed (they could not complete, or a crash came first) — it is decided without another reviewer run."""
    attempts = review_attempts(job)
    run = review_run(job, attempts[-1]) if attempts else None
    if run is None or run["status"] != "ok" or review_decision(job, attempts[-1]) is not None:
        return None
    return run


def _is_sha(value: Any) -> bool:
    return isinstance(value, str) and SHA_RE.fullmatch(value) is not None


def paths_digest(paths: list[dict[str, Any]]) -> str:
    """SHA-256 of a classified path list (bound into the inspection evidence)."""
    return hashlib.sha256(json.dumps(paths, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise IntegrationError(message)


def _is_int(value: Any, minimum: int) -> bool:
    return type(value) is int and value >= minimum  # pylint: disable=unidiomatic-typecheck  # bool is not a count


def _is_text(value: Any, limit: int = MAX_TEXT) -> bool:
    return isinstance(value, str) and len(value) <= limit


def _is_text_list(value: Any) -> bool:
    return isinstance(value, list) and all(_is_text(item) for item in value)


def _is_time(value: Any) -> bool:
    return isinstance(value, str) and _TIMESTAMP_RE.fullmatch(value) is not None


def _copy(value: Any) -> Any:
    return json.loads(json.dumps(value)) if value is not None else None
