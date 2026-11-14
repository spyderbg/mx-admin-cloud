"""W4.6.1 (R1): the reviewer's answer — its JSON schema, deterministic validation, the outcome, and the PR comment.

Nothing the reviewer says is trusted as such. `validate_output()` accepts an answer only when it has exactly the
expected shape: a verdict, a bounded summary, the reviewed head SHA (it must be the bound head), and findings that
each name a CHANGED file of the bound diff with line numbers inside that file (null for a binary or withheld file)
and a severity. `decide()` then derives the outcome from the validated findings; the verdict only has to agree:

    PASS             valid, verdict `pass`, no finding, no limitation
    PASS_WITH_NOTES  valid, no blocking finding, verdict `pass` or `pass_with_notes`, at least one note
                     (a minor/info finding or a limitation, including the coordinator's own)
    REQUEST_CHANGES  valid, at least one BLOCKING finding (`blocker` or `major`), verdict `request_changes`
    ERROR            everything else (review.py): invalid output on every attempt, a verdict that contradicts
                     the findings, an unavailable or refused reviewer, a timeout, a binding that does not hold

A blocking finding never yields PASS or PASS_WITH_NOTES. Every outcome is advisory: none merges, approves or
marks a pull request ready.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from .github_api import sanitize
from .issues import escape_markdown_comment

SEVERITIES = ("blocker", "major", "minor", "info")
BLOCKING = ("blocker", "major")
VERDICTS = ("pass", "pass_with_notes", "request_changes")
OUTCOMES = ("PASS", "PASS_WITH_NOTES", "REQUEST_CHANGES", "ERROR")
LIMITS = {"summary": 2000, "title": 160, "explanation": 2000, "recommendation": 1500, "limitation": 500}
MAX_LIMITATIONS = 10
MAX_COMMENT = 60000  # characters of one PR comment (GitHub accepts 65536)
_ANSWER_KEYS = {"verdict", "summary", "head_sha", "findings", "limitations"}
_FINDING_KEYS = {"severity", "file", "line_start", "line_end", "title", "explanation", "recommendation"}
_MENTION_RE = re.compile(r"@(?=[A-Za-z0-9])")


@dataclass(frozen=True)
class FileFacts:
    """What the coordinator knows about one changed file of the bound diff."""

    status: str              # A (added) | M | D | R (renamed) | C | T
    lines: int | None        # line count of the reviewed version (base for a deletion); None: binary / withheld


@dataclass
class Review:
    """A validated reviewer answer."""

    verdict: str
    summary: str
    findings: list[dict[str, Any]] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)


def review_schema(max_findings: int) -> dict[str, Any]:
    """The JSON schema given to the reviewer (`--json-schema`); validate_output() stays the authority."""
    text = {"type": "string"}
    line = {"type": ["integer", "null"], "minimum": 1}
    finding = {"type": "object", "additionalProperties": False, "required": sorted(_FINDING_KEYS),
               "properties": {"severity": {"type": "string", "enum": list(SEVERITIES)}, "file": text,
                              "line_start": line, "line_end": line, "title": text, "explanation": text,
                              "recommendation": text}}
    return {"type": "object", "additionalProperties": False, "required": sorted(_ANSWER_KEYS),
            "properties": {"verdict": {"type": "string", "enum": list(VERDICTS)}, "summary": text,
                           "head_sha": {"type": "string", "pattern": "^[0-9a-f]{40}$"},
                           "findings": {"type": "array", "maxItems": max_findings, "items": finding},
                           "limitations": {"type": "array", "maxItems": MAX_LIMITATIONS, "items": text}}}


def _text(value: Any, name: str, where: str, problems: list[str], *, empty: bool = False) -> str:
    if not isinstance(value, str) or (not empty and not value.strip()):
        problems.append(f"{where}.{name} must be a non-empty string")
        return ""
    if len(value) > LIMITS[name]:
        problems.append(f"{where}.{name} has {len(value)} characters; at most {LIMITS[name]}")
    return value.strip()


def _finding(raw: Any, where: str, files: Mapping[str, FileFacts], problems: list[str]) -> dict[str, Any] | None:
    if not isinstance(raw, dict) or set(raw) != _FINDING_KEYS:
        keys = sorted(raw) if isinstance(raw, dict) else type(raw).__name__
        problems.append(f"{where} must have exactly the keys {sorted(_FINDING_KEYS)}, got {keys}")
        return None
    before = len(problems)
    severity = raw["severity"]
    if severity not in SEVERITIES:
        problems.append(f"{where}.severity must be one of {list(SEVERITIES)}")
    path = raw["file"]
    facts = files.get(path) if isinstance(path, str) else None
    if facts is None:
        problems.append(f"{where}.file {sanitize(path, 120)!r} is not a file changed by this pull request")
    start, end = raw["line_start"], raw["line_end"]
    if start is None or end is None:
        if start is not None or end is not None:
            problems.append(f"{where}: line_start and line_end must both be set or both be null")
    elif not all(isinstance(n, int) and not isinstance(n, bool) for n in (start, end)) or not 1 <= start <= end:
        problems.append(f"{where}: line_start and line_end must be integers with 1 <= line_start <= line_end")
    elif facts is not None and (facts.lines is None or end > facts.lines):
        size = "has no reviewable lines" if facts.lines is None else f"has {facts.lines} lines"
        problems.append(f"{where}: lines {start}-{end} are outside {path} ({size})")
    finding = {"severity": severity, "file": path, "line_start": start, "line_end": end,
               "title": _text(raw["title"], "title", where, problems),
               "explanation": _text(raw["explanation"], "explanation", where, problems),
               "recommendation": _text(raw["recommendation"], "recommendation", where, problems, empty=True)}
    return finding if len(problems) == before else None


def validate_output(output: Any, *, head_sha: str, files: Mapping[str, FileFacts],
                    max_findings: int) -> tuple[Review | None, list[str]]:
    """(the validated review, []) or (None, every problem). Deterministic; nothing is repaired or guessed."""
    if not isinstance(output, dict):
        return None, ["the reviewer's answer is not a JSON object"]
    if set(output) != _ANSWER_KEYS:
        return None, [f"the answer must have exactly the keys {sorted(_ANSWER_KEYS)}, got {sorted(output)[:12]}"]
    problems: list[str] = []
    verdict = output["verdict"]
    if verdict not in VERDICTS:
        problems.append(f"verdict must be one of {list(VERDICTS)}, got {sanitize(verdict, 40)!r}")
    if output["head_sha"] != head_sha:
        problems.append(f"head_sha {sanitize(output['head_sha'], 60)!r} is not the reviewed head {head_sha}")
    summary = _text(output["summary"], "summary", "answer", problems)
    raw_findings, raw_limits = output["findings"], output["limitations"]
    if not isinstance(raw_findings, list) or len(raw_findings) > max_findings:
        problems.append(f"findings must be a list of at most {max_findings}")
        raw_findings = []
    if not isinstance(raw_limits, list) or len(raw_limits) > MAX_LIMITATIONS:
        problems.append(f"limitations must be a list of at most {MAX_LIMITATIONS} strings")
        raw_limits = []
    findings = [_finding(raw, f"findings[{i}]", files, problems) for i, raw in enumerate(raw_findings)]
    limitations = [_text(raw, "limitation", f"limitations[{i}]", problems) for i, raw in enumerate(raw_limits)]
    if problems:
        return None, problems
    return Review(verdict, summary, [f for f in findings if f is not None], limitations), []


def decide(review: Review, extra_limitations: list[str]) -> tuple[str, str]:
    """(outcome, reason) for a VALIDATED review. *extra_limitations*: the coordinator's own notes (e.g. text
    withheld from the reviewer); they rule out PASS. A verdict that contradicts the findings is ERROR."""
    blocking = [f for f in review.findings if f["severity"] in BLOCKING]
    notes = len(review.findings) + len(review.limitations) + len(extra_limitations)
    if blocking:
        if review.verdict != "request_changes":
            return "ERROR", (f"the verdict {review.verdict!r} contradicts {len(blocking)} blocking finding(s)")
        return "REQUEST_CHANGES", f"{len(blocking)} blocking finding(s)"
    if review.verdict == "request_changes":
        return "ERROR", "the verdict 'request_changes' names no blocking finding"
    if notes:
        return "PASS_WITH_NOTES", f"{notes} note(s), no blocking finding"
    if review.verdict != "pass":
        return "ERROR", f"the verdict {review.verdict!r} names no finding and no limitation"
    return "PASS", "no finding"


# ---------------------------------------------------------------------------
# The PR comment (posted only by review.py, with an operation marker)
# ---------------------------------------------------------------------------

def safe(text: Any, limit: int) -> str:
    """Model or Issue text made safe for a coordinator comment: tokens redacted, no control characters, one line,
    no comment markers, no @-mentions, table cells intact, bounded."""
    value = escape_markdown_comment(sanitize(text, limit))
    return _MENTION_RE.sub("@\u200b", value).replace("|", "\\|")


def _location(finding: Mapping[str, Any]) -> str:
    where = f"`{safe(finding['file'], 200)}`"
    if finding.get("line_start"):
        where += f":{finding['line_start']}" + (f"-{finding['line_end']}" if finding["line_end"] != finding[
            "line_start"] else "")
    return where


def render_comment(entry: Mapping[str, Any]) -> str:
    """The review comment for one completed entry (without its marker)."""
    binding, outcome = entry["binding"], entry["outcome"]
    reviewer = binding["reviewer"]
    lines = [f"**Independent AI review: {outcome}** — advisory; it does not approve or merge anything, and the "
             "Worker never merges.", "",
             "| Field | Value |", "| --- | --- |",
             f"| Pull request | #{binding['pr']} `{binding['head_branch']}` → `{binding['base_branch']}` |",
             f"| Reviewed head | `{binding['head_sha']}` |",
             f"| Diff base | `{binding['merge_base']}` (merge base; `{binding['base_branch']}` was at "
             f"`{str(binding['base_tip'])[:12]}`) |",
             f"| Approved specification | sha256 `{binding['spec_sha256']}` |"]
    if binding.get("plan_sha256"):
        lines.append(f"| Plan | sha256 `{binding['plan_sha256']}`, approval sha256 `{binding['approval_sha256']}` |")
    lines += [f"| Reviewer | {safe(reviewer['provider'], 40)} / {safe(reviewer['model'], 40)} (no tools), "
              f"independence: {entry.get('independence') or 'not assessed'} |",
              f"| Review | `{entry['key']}`, binding sha256 `{entry['binding_sha256'][:16]}…` |", ""]
    if outcome == "ERROR":
        lines += [f"The review could not be completed: {safe(entry.get('reason'), 600)}", "",
                  "No result is available for this head; an operator may retry with "
                  f"`python -m feature_coordinator github review retry {binding['feature']} --apply`."]
        return "\n".join(lines)
    lines += [f"**Summary.** {safe(entry.get('summary'), LIMITS['summary'])}", ""]
    findings = entry.get("findings") or []
    if findings:
        lines += ["| Severity | Location | Finding | Recommendation |", "| --- | --- | --- | --- |"]
        lines += [f"| {f['severity']} | {_location(f)} | **{safe(f['title'], LIMITS['title'])}** — "
                  f"{safe(f['explanation'], LIMITS['explanation'])} | {safe(f['recommendation'], 1500) or '—'} |"
                  for f in findings]
        lines.append("")
    notes = [*(entry.get("limitations") or []), *(entry.get("coordinator_notes") or [])]
    if notes:
        lines += ["**Limitations.**", *(f"- {safe(n, LIMITS['limitation'])}" for n in notes), ""]
    lines.append("Findings are the reviewer's opinion on this exact head; the task and integration gates remain "
                 "authoritative, and a person decides whether to merge.")
    text = "\n".join(lines)
    return text if len(text) <= MAX_COMMENT else text[:MAX_COMMENT - 80] + "\n\n… (truncated; see `github review show`)"


def render_invalidation(entry: Mapping[str, Any]) -> str:
    """The notice posted when a published review no longer applies."""
    reason = (entry.get("invalidated") or {}).get("reason", "")
    return (f"**Independent AI review `{entry['key']}` no longer applies** ({entry.get('outcome') or 'no outcome'} "
            f"for head `{entry['binding']['head_sha'][:12]}`): {safe(reason, 600)}")
