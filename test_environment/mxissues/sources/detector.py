"""Maps the origin document's §1 detector JSON schema to Findings.

No LLM call here — this is purely the hand-off shape an eventual detector would produce
(implementation_plan.md: "sources/detector.py (B10) defines the hand-off so an LLM
detector can plug in unchanged"), from docs/test_environment/initial_spec.md §1:

    {"issues": [{"title", "type", "severity", "confidence", "files",
                 "evidence", "acceptance_criteria"}]}

Fingerprint key is normalised title + sorted affected files — "LLM ids aren't stable;
this is the weakest key and the reason the doc's critic/confidence gate matters"
(implementation_plan.md §11). No such gate exists yet (§7 is explicitly not built), so
this module also drops anything below a confidence floor rather than trusting the
caller to have filtered already.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..models import Finding

MIN_CONFIDENCE = 0.7

_WHITESPACE_RE = re.compile(r"\s+")
_SEVERITY_ALIASES = {"critical": "high", "major": "high", "minor": "low"}


def _normalize_title(title: str) -> str:
    return _WHITESPACE_RE.sub(" ", title.strip().lower())


def parse(json_path: Path, *, min_confidence: float = MIN_CONFIDENCE) -> list[Finding]:
    data = json.loads(json_path.read_text(encoding="utf-8"))
    findings: list[Finding] = []

    for issue in data.get("issues", []):
        title = str(issue.get("title", "")).strip()
        if not title:
            continue

        confidence = issue.get("confidence")
        if isinstance(confidence, (int, float)) and confidence < min_confidence:
            continue  # below the floor — no critic (origin doc §7) exists yet to have filtered this

        files = sorted(str(f) for f in issue.get("files", []) or [])
        key = f"{_normalize_title(title)}|{','.join(files)}"

        severity = str(issue.get("severity", "medium")).lower()
        severity = _SEVERITY_ALIASES.get(severity, severity)
        if severity not in ("high", "medium", "low"):
            severity = "medium"

        evidence: list[Any] = issue.get("evidence", []) or []
        acceptance: list[Any] = issue.get("acceptance_criteria", []) or []

        body_lines = [f"**Type:** {issue.get('type', 'bug')}"]
        if confidence is not None:
            body_lines.append(f"**Detector confidence:** {confidence}")
        if files:
            body_lines.append("\n**Affected files:**")
            body_lines.extend(f"- `{f}`" for f in files)
        if evidence:
            body_lines.append("\n**Evidence:**")
            body_lines.extend(f"- {e}" for e in evidence)
        if acceptance:
            body_lines.append("\n**Acceptance criteria:**")
            body_lines.extend(f"- [ ] {a}" for a in acceptance)

        findings.append(Finding(
            source="detector",
            title=title,
            body="\n".join(body_lines),
            fingerprint_key=key,
            severity=severity,
            affected_files=tuple(files),
        ))

    return findings
