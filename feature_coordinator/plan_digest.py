"""Deterministic serialization, digests and comment markers of planned GitHub Issue approvals.

Shared by the coordinator (intake_plan.py) and the GitHub Actions approval step
(.github/scripts/worker_issue_labels.py), which loads this file from the default branch's checkout
with the STANDARD LIBRARY ONLY: no other coordinator module may be imported here.

Canonical JSON (`canonical_json`): UTF-8, keys sorted, no insignificant whitespace, no ASCII
escaping, and only dicts with string keys, lists, strings, integers, booleans and null — floats
are refused, so no number can serialize two ways. Every digest below is the SHA-256 of the
canonical JSON of one object, and every object names its own format version, so a later format
can never be confused with this one.

Objects:

  * the canonical execution plan (`PLAN_SCHEMA`), built and validated by planning.py; its digest is
    `plan_sha256(plan)`;
  * the approval statement (`APPROVAL_FORMAT`, `approval_statement()`): repository and Issue
    identity, the exact (normalized) Issue title and body with the legacy spec digest, the plan's
    version and digest, every task's role, dependencies and gates, and the integration gates. Its
    digest (`approval_sha256`) is what a `worker:approve` label approves; nobody types it;
  * the approval receipt (`RECEIPT_FORMAT`), posted by the GitHub Action as `github-actions[bot]`.
    It is EVIDENCE only: the coordinator recomputes every value itself (intake_plan.py).

Markers (`encode_marker` / `decode_marker`) carry one such object inside an HTML comment as
base64 of its canonical JSON plus that JSON's SHA-256, so Markdown can never alter or break it:

    <!-- feature-coordinator:plan v1 sha256=<hex> data=<base64> -->
    <!-- feature-coordinator:approval-receipt v1 sha256=<hex> data=<base64> -->
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from typing import Any, Iterable, Mapping

PLAN_SCHEMA = "mx-feature-plan/v1"
APPROVAL_FORMAT = "mx-worker-approval/v1"
RECEIPT_FORMAT = "mx-worker-approval-receipt/v1"

LABEL_PLAN = "worker:plan"
LABEL_READY = "worker:ready-for-review"
LABEL_APPROVE = "worker:approve"
LABEL_APPROVED = "worker:approved"
WORKER_LABELS = (LABEL_PLAN, LABEL_READY, LABEL_APPROVE, LABEL_APPROVED)
WORKER_LABEL_SPECS = {
    LABEL_PLAN: ("5319e7", "Feature Coordinator: ask the Desktop Worker for an execution plan"),
    LABEL_READY: ("fbca04", "Feature Coordinator: a plan is published and waits for review"),
    LABEL_APPROVE: ("0e8a16", "Feature Coordinator: approve the published plan (exact Issue text and plan version)"),
    LABEL_APPROVED: ("1d76db", "Feature Coordinator: the coordinator validated the approval"),
}

PLAN_MARKER = "plan"
RECEIPT_MARKER = "approval-receipt"
REJECTION_MARKER = "approval-rejected"
MAX_MARKER_JSON = 48000  # bytes of canonical JSON in one marker (a GitHub comment holds 65536 characters)
_MARKER_RE = re.compile(r"<!-- feature-coordinator:([a-z-]{1,40}) v1 sha256=([0-9a-f]{64}) "
                        r"data=([A-Za-z0-9+/=]{4,70000}) -->")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_LOGIN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})(?:\[bot\])?$")
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")
PERMISSION_RANK = {"admin": 4, "maintain": 3, "write": 2, "triage": 1, "read": 0, "none": -1}


# ---------------------------------------------------------------------------
# Canonical JSON and digests
# ---------------------------------------------------------------------------

def _check(value: Any, where: str) -> None:
    if value is None or isinstance(value, (bool, str)):
        return
    if isinstance(value, int):
        return
    if isinstance(value, float):
        raise ValueError(f"{where}: floats are not allowed in canonical JSON")
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _check(item, f"{where}[{index}]")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{where}: object keys must be strings")
            _check(item, f"{where}.{key}")
        return
    raise ValueError(f"{where}: unsupported type {type(value).__name__}")


def canonical_json(value: Any) -> bytes:
    """The one byte serialization of *value* (see the module docstring)."""
    _check(value, "$")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    """Lowercase hexadecimal SHA-256."""
    return hashlib.sha256(data).hexdigest()


def normalize_text(body: str) -> str:
    """Line endings and trailing whitespace normalized (GitHub may return CRLF). Identical to
    issues.normalize_body (a test enforces it); duplicated so this module needs nothing else."""
    return "\n".join(line.rstrip() for line in (body or "").replace("\r\n", "\n").split("\n")).strip()


def spec_sha256(title: str, body: str) -> str:
    """The legacy Issue specification digest (intake.spec_digest): trimmed title, a blank line, body."""
    return sha256_hex(f"{(title or '').strip()}\n\n{normalize_text(body)}".encode("utf-8"))


def plan_sha256(plan: Mapping[str, Any]) -> str:
    """The digest of a canonical execution plan (its version is part of it)."""
    return sha256_hex(canonical_json(plan))


def approval_statement(*, repository: Mapping[str, Any], issue: Mapping[str, Any], title: str, body: str,
                       plan: Mapping[str, Any]) -> dict[str, Any]:
    """What a `worker:approve` label approves: the exact Issue text and the exact plan version."""
    return {
        "format": APPROVAL_FORMAT,
        "repository": {"full_name": str(repository["full_name"]).lower(), "id": int(repository["id"])},
        "issue": {"number": int(issue["number"]), "id": int(issue["id"]), "node_id": str(issue["node_id"])},
        "spec": {"title": (title or "").strip(), "body": normalize_text(body), "sha256": spec_sha256(title, body)},
        "plan": {"schema": plan["schema"], "version": int(plan["version"]), "sha256": plan_sha256(plan)},
        "tasks": [{"id": t["id"], "role": t["role"], "depends_on": list(t["depends_on"]), "gates": list(t["gates"])}
                  for t in plan["tasks"]],
        "integration_gates": list(plan["integration_gates"]),
    }


def approval_sha256(statement: Mapping[str, Any]) -> str:
    """The approval digest (SHA-256 of the canonical approval statement)."""
    if statement.get("format") != APPROVAL_FORMAT:
        raise ValueError(f"unsupported approval format {statement.get('format')!r}")
    return sha256_hex(canonical_json(statement))


def display_digest(sha: str) -> str:
    """The versioned display form of an approval digest."""
    return f"{APPROVAL_FORMAT}:sha256:{sha}"


# ---------------------------------------------------------------------------
# Markers
# ---------------------------------------------------------------------------

def encode_marker(kind: str, payload: Mapping[str, Any]) -> str:
    """One marker line carrying *payload* (see the module docstring)."""
    data = canonical_json(payload)
    if len(data) > MAX_MARKER_JSON:
        raise ValueError(f"marker payload has {len(data)} bytes; at most {MAX_MARKER_JSON} are allowed")
    return (f"<!-- feature-coordinator:{kind} v1 sha256={sha256_hex(data)} "
            f"data={base64.b64encode(data).decode('ascii')} -->")


def decode_marker(kind: str, text: str) -> dict[str, Any] | None:
    """The payload of the single *kind* marker in *text*; None when there is none, more than one,
    or it is damaged (digest, base64, JSON or a non-canonical encoding)."""
    found = [m for m in _MARKER_RE.finditer(text or "") if m.group(1) == kind]
    if len(found) != 1 or (text or "").count(f"<!-- feature-coordinator:{kind} ") != 1:
        return None
    match = found[0]
    try:
        data = base64.b64decode(match.group(3), validate=True)
        payload = json.loads(data.decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None
    if not isinstance(payload, dict) or sha256_hex(data) != match.group(2):
        return None
    try:
        if canonical_json(payload) != data:
            return None
    except ValueError:
        return None
    return payload


# ---------------------------------------------------------------------------
# The approval receipt (evidence from the GitHub Action; never trusted as such)
# ---------------------------------------------------------------------------

_RECEIPT_SHAPE: dict[str, Any] = {
    "format": str,
    "repository": {"full_name": str, "id": int},
    "issue": {"number": int, "id": int, "node_id": str},
    "label_event": {"id": int, "actor": str, "created_at": str},
    "actor_permission": str,
    "plan": {"comment_id": int, "version": int, "sha256": str},
    "spec_sha256": str,
    "approval": {"format": str, "sha256": str},
    "workflow_run": {"id": int, "attempt": int},
}


def _shape_problems(value: Any, shape: Any, where: str) -> list[str]:
    if isinstance(shape, dict):
        if not isinstance(value, dict):
            return [f"{where} is not an object"]
        problems = [f"{where}.{k} is missing" for k in shape if k not in value]
        problems += [f"{where}.{k} is not allowed" for k in value if k not in shape]
        for key, sub in shape.items():
            if key in value:
                problems += _shape_problems(value[key], sub, f"{where}.{key}")
        return problems
    if shape is int and (not isinstance(value, int) or isinstance(value, bool) or value <= 0):
        return [f"{where} is not a positive integer"]
    if shape is str and (not isinstance(value, str) or not value or len(value) > 200):
        return [f"{where} is not a short non-empty string"]
    return []


def receipt_problems(payload: Any) -> list[str]:
    """Why *payload* is not a well-formed approval receipt (empty list: well-formed). Structure only:
    whether it is TRUE is decided by the coordinator against GitHub and its own state."""
    problems = _shape_problems(payload, _RECEIPT_SHAPE, "receipt")
    if problems:
        return problems
    if payload["format"] != RECEIPT_FORMAT:
        problems.append(f"unsupported receipt format {payload['format']!r}")
    if payload["approval"]["format"] != APPROVAL_FORMAT:
        problems.append(f"unsupported approval format {payload['approval']['format']!r}")
    for where, value in (("plan.sha256", payload["plan"]["sha256"]), ("spec_sha256", payload["spec_sha256"]),
                         ("approval.sha256", payload["approval"]["sha256"])):
        if not _HEX64.fullmatch(value):
            problems.append(f"receipt.{where} is not a SHA-256")
    if not _LOGIN.fullmatch(payload["label_event"]["actor"]):
        problems.append("receipt.label_event.actor is not a GitHub login")
    if not _ISO.fullmatch(payload["label_event"]["created_at"]):
        problems.append("receipt.label_event.created_at is not a GitHub timestamp")
    if payload["actor_permission"] not in PERMISSION_RANK:
        problems.append("receipt.actor_permission is not a repository permission")
    return problems


def latest_label_event(events: Iterable[Mapping[str, Any]], label: str) -> dict[str, Any] | None:
    """The newest `labeled` event adding *label* (by creation time, then ID), as
    {id, actor, created_at}; None when the label was never added. *events* are dicts with
    `id`, `event`, `label`, `actor` and `created_at` (the coordinator's and the Action's view)."""
    found = [dict(e) for e in events if e.get("event") == "labeled" and e.get("label") == label]
    if not found:
        return None
    newest = max(found, key=lambda e: (str(e.get("created_at", "")), int(e.get("id", 0))))
    return {"id": int(newest["id"]), "actor": str(newest.get("actor", "")),
            "created_at": str(newest.get("created_at", ""))}
