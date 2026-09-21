"""Maps a pylint JSON report (--output-format=json) to Findings.

Fingerprint key is file path + symbol — NOT the line number, which shifts on every
edit and would duplicate the issue on the next commit (implementation_plan.md §11).
Verified against this repo's own real `pylint --output-format=json src/` output.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..models import Finding

_SEVERITY_BY_TYPE = {
    "fatal": "high",
    "error": "high",
    "warning": "medium",
    "refactor": "medium",
    "convention": "low",
}


def parse(json_path: Path) -> list[Finding]:
    entries = json.loads(json_path.read_text(encoding="utf-8"))
    findings: list[Finding] = []

    for entry in entries:
        path = str(entry.get("path", ""))
        symbol = str(entry.get("symbol", ""))
        message = str(entry.get("message", ""))
        message_id = str(entry.get("message-id", ""))
        pylint_type = str(entry.get("type", "warning"))
        line = entry.get("line")

        key = f"{path}:{symbol}"
        title = f"pylint {pylint_type} [{symbol}]: {path}"
        body = (
            f"**pylint {pylint_type}** (`{message_id}` / `{symbol}`)\n\n"
            f"**File:** `{path}`" + (f" (currently line {line})" if line is not None else "") + "\n\n"
            f"**Message:** {message}\n\n"
            "_Line number is not part of the fingerprint — it shifts on every edit._"
        )

        findings.append(Finding(
            source="pylint",
            title=title,
            body=body,
            fingerprint_key=key,
            severity=_SEVERITY_BY_TYPE.get(pylint_type, "medium"),
            affected_files=(path,) if path else (),
        ))

    return findings
