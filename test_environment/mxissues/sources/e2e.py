"""Maps a Playwright JSON reporter report to Findings.

Fingerprint key is spec path + test title — "stable across reruns and refactors"
(implementation_plan.md §11). Verified against this repo's own Playwright 1.63 JSON
reporter output (`playwright.config.ts`'s `['json', {outputFile: ...}]`).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..models import Finding

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text or "")


def _iter_specs(suite: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten a (possibly nested, via test.describe()) suite into its specs."""
    specs = list(suite.get("specs", []))
    for child in suite.get("suites", []) or []:
        specs.extend(_iter_specs(child))
    return specs


def parse(json_path: Path) -> list[Finding]:
    data = json.loads(json_path.read_text(encoding="utf-8"))
    findings: list[Finding] = []

    for suite in data.get("suites", []):
        for spec in _iter_specs(suite):
            if spec.get("ok", True):
                continue  # passed (or, with retries, eventually passed)

            spec_file = str(spec.get("file", ""))
            spec_title = str(spec.get("title", ""))
            key = f"{spec_file}::{spec_title}"

            error_text = "(no error captured)"
            for test in spec.get("tests", []):
                results = test.get("results", [])
                if not results:
                    continue
                last = results[-1]
                error = last.get("error") or (last.get("errors") or [{}])[0]
                message = error.get("message")
                if message:
                    error_text = _strip_ansi(message)
                    break

            body = (
                f"**Playwright spec failed:** `{spec_file}` — {spec_title}\n\n"
                "**Error:**\n```\n" + error_text.strip()[:1200] + "\n```"
            )

            findings.append(Finding(
                source="e2e",
                title=f"E2E failed: {spec_title} ({spec_file})",
                body=body,
                fingerprint_key=key,
                severity="high",
                affected_files=(spec_file,) if spec_file else (),
            ))

    return findings
