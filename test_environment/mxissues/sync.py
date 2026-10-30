"""Sync engine: Findings -> tracker issues, with dedup (implementation_plan.md §11).

`sync` defaults to a dry run — creating issues in a shared tracker is outward-facing and
awkward to undo, so by default this only prints the create/update/reopen plan; nothing
is written without `apply=True`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import cache as cache_module
from . import fingerprint
from .models import Finding
from .tracker import OWNERSHIP_LABEL, Issue, IssueNotFoundError, IssueTracker

Action = str  # "create" | "update" | "reopen" | "skip-not-owned" | "skip-limit"


@dataclass
class SyncItem:
    finding: Finding
    fingerprint: str
    action: Action
    number: int | None = None
    detail: str = ""


@dataclass
class SyncReport:
    items: list[SyncItem] = field(default_factory=list)

    def count(self, action: Action) -> int:
        return sum(1 for item in self.items if item.action == action)

    def summary(self) -> str:
        summary = (
            f"{self.count('create')} created, {self.count('update')} updated, "
            f"{self.count('reopen')} reopened, {self.count('skip-not-owned')} skipped (not owned)"
        )
        capped = self.count("skip-limit")
        if capped:
            summary += f", {capped} skipped (max_new_issues limit reached)"
        return summary


def _find_existing(tracker: IssueTracker, fp: str, local_cache: dict[str, int]) -> Issue | None:
    cached_number = local_cache.get(fp)
    if cached_number is not None:
        try:
            candidate = tracker.get(cached_number)
        except IssueNotFoundError:
            candidate = None
        if candidate is not None:
            meta = fingerprint.parse_meta_block(candidate.description)
            if meta and meta.get("fp") == fp:
                return candidate
        # Cache entry gone or stale — fall through to a real search, never to "create".
    return tracker.find_by_fingerprint(fp)


def _apply_create(tracker: IssueTracker, finding: Finding, fp: str, now: str) -> Issue:
    meta_block = fingerprint.build_meta_block(
        fingerprint=fp, source=finding.source, key=finding.fingerprint_key, first_seen=now, occurrences=1,
    )
    description = f"{finding.body}\n\n{meta_block}"
    labels = [OWNERSHIP_LABEL, f"source::{finding.source}", f"severity::{finding.severity}"]
    return tracker.create(title=finding.title, description=description, labels=labels, status="open")


def _is_done(issue: Issue) -> bool:
    return issue.status == "done" or issue.state == "closed"


def _apply_recurrence(tracker: IssueTracker, issue: Issue, finding: Finding, fp: str, now: str) -> tuple[Issue, bool]:
    """Bump occurrences, comment, and reopen-as-regression if the issue was done/closed.

    Returns (refreshed_issue, was_regression).
    """
    meta = fingerprint.parse_meta_block(issue.description) or {}
    occurrences = int(meta.get("occurrences", 1)) + 1
    first_seen = meta.get("first_seen", now)
    new_block = fingerprint.build_meta_block(fingerprint=fp, source=finding.source, key=finding.fingerprint_key, first_seen=first_seen, occurrences=occurrences)
    tracker.update_description(issue.number, fingerprint.replace_meta_block(issue.description, new_block))

    was_done = _is_done(issue)
    if was_done:
        tracker.add_comment(issue.number, f"**Regression** — recurred at {now} (occurrence {occurrences}). Reopening.")
        tracker.set_status(issue.number, "open")
    else:
        tracker.add_comment(issue.number, f"Recurred at {now} (occurrence {occurrences}).")

    return tracker.get(issue.number), was_done


def run_sync(tracker: IssueTracker, findings: list[Finding], *, apply: bool, max_new_issues: int | None = None) -> SyncReport:
    """Sync *findings* to issues on *tracker*.

    `max_new_issues` caps how many NEW issues this call will create, regardless of how
    many findings have no existing match — a safety rail against one broken build
    flooding a shared tracker (testenv.toml [limits] max_new_issues; see its comment
    there for the full reasoning). It never caps updates/reopens on issues that already
    exist: only issue *creation* is limited. None means unlimited, honoring the counted
    limit in both --apply and dry-run mode so the printed plan matches what --apply
    would actually do.
    """
    report = SyncReport()
    local_cache = cache_module.load(tracker.name)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    created_count = 0

    for finding in findings:
        fp = fingerprint.compute(finding.source, finding.fingerprint_key)
        existing = _find_existing(tracker, fp, local_cache)

        if existing is None:
            if max_new_issues is not None and created_count >= max_new_issues:
                report.items.append(SyncItem(
                    finding, fp, "skip-limit", None,
                    f"(max_new_issues={max_new_issues} reached — not filed this run; still deduplicated next time)",
                ))
                continue
            created_count += 1
            if apply:
                issue = _apply_create(tracker, finding, fp, now)
                local_cache[fp] = issue.number
                report.items.append(SyncItem(finding, fp, "create", issue.number, issue.web_url))
            else:
                report.items.append(SyncItem(finding, fp, "create", None, "(dry run — would create)"))
            continue

        if not existing.owned:
            report.items.append(SyncItem(finding, fp, "skip-not-owned", existing.number, existing.web_url))
            continue

        if apply:
            refreshed, was_regression = _apply_recurrence(tracker, existing, finding, fp, now)
            local_cache[fp] = refreshed.number
            action = "reopen" if was_regression else "update"
            report.items.append(SyncItem(finding, fp, action, refreshed.number, refreshed.web_url))
        else:
            was_done = _is_done(existing)
            action = "reopen" if was_done else "update"
            report.items.append(SyncItem(finding, fp, action, existing.number, "(dry run — would " + ("reopen as a regression" if was_done else "bump occurrences") + ")"))

    if apply:
        cache_module.save(tracker.name, local_cache)

    return report
