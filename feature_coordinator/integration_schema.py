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
Every entry's `sha256` covers its payload (everything but id, time and digest); what a result claims is checked after
the digest, as M2 does.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping

from .errors import CoordinatorError

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
                                         "validation": "val"}


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
        else:
            _check_validation_entry(entry, where, earlier)
        _expect(_is_time(entry["at"]), f"{where}: at must be a timestamp")
        _expect(_is_text_list(entry.get("reasons", [])) and len(entry.get("reasons", [])) <= 20,
                f"{where}: reasons must be at most 20 texts")
        _expect(entry["sha256"] == evidence_digest(entry), f"{where}: its SHA-256 does not match its payload")
        _check_entry_meaning(entry, where, earlier)   # after the digest: a consistent payload must also be coherent
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
        _expect(not any(entry["kind"] in ("attempt", "gate", "validation") for entry in job.evidence),
                "attempt, gate and validation evidence need a built candidate")
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
    _expect(all(entry["candidate"] == built[0]["id"] for entry in job.evidence
                if entry["kind"] in ("attempt", "gate", "validation")),
            "attempt, gate and validation evidence must name the job's built candidate")


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
