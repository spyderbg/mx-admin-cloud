"""Sync engine: Findings -> GitLab issues, with dedup (implementation_plan.md §11).

`sync` defaults to a dry run — creating issues in a shared tracker is outward-facing and
awkward to undo, so by default this only prints the create/update/reopen plan; nothing
is written without `apply=True`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from gitlab.exceptions import GitlabGetError
from gitlab.v4.objects import Project, ProjectIssue

from . import cache as cache_module
from . import client, fingerprint, state
from .models import Finding

Action = str  # "create" | "update" | "reopen" | "skip-not-owned"


@dataclass
class SyncItem:
    finding: Finding
    fingerprint: str
    action: Action
    iid: int | None = None
    detail: str = ""


@dataclass
class SyncReport:
    items: list[SyncItem] = field(default_factory=list)

    def count(self, action: Action) -> int:
        return sum(1 for item in self.items if item.action == action)

    def summary(self) -> str:
        return (
            f"{self.count('create')} created, {self.count('update')} updated, "
            f"{self.count('reopen')} reopened, {self.count('skip-not-owned')} skipped (not owned)"
        )


def _find_existing(project: Project, fp: str, local_cache: dict[str, int]) -> ProjectIssue | None:
    cached_iid = local_cache.get(fp)
    if cached_iid is not None:
        try:
            candidate = project.issues.get(cached_iid)
        except GitlabGetError:
            candidate = None
        if candidate is not None:
            meta = fingerprint.parse_meta_block(candidate.description or "")
            if meta and meta.get("fp") == fp:
                return candidate
        # Cache entry gone or stale — fall through to a real search, never to "create".

    matches = project.issues.list(search=f"mx-fp:{fp}", in_=["description"], state="all", all=True)
    for candidate in matches:
        meta = fingerprint.parse_meta_block(candidate.description or "")
        if meta and meta.get("fp") == fp:
            return candidate
    return None


def _apply_create(project: Project, finding: Finding, fp: str, now: str) -> ProjectIssue:
    meta_block = fingerprint.build_meta_block(
        fingerprint=fp, source=finding.source, key=finding.fingerprint_key, first_seen=now, occurrences=1,
    )
    description = f"{finding.body}\n\n{meta_block}"
    labels = [client.OWNERSHIP_LABEL, "status::open", f"source::{finding.source}", f"severity::{finding.severity}"]
    return project.issues.create({"title": finding.title, "description": description, "labels": labels})


def _apply_recurrence(project: Project, issue: ProjectIssue, finding: Finding, fp: str, now: str) -> tuple[ProjectIssue, bool]:
    """Bump occurrences, comment, and reopen-as-regression if the issue was done/closed.

    Returns (refreshed_issue, was_regression).
    """
    meta = fingerprint.parse_meta_block(issue.description or "") or {}
    occurrences = int(meta.get("occurrences", 1)) + 1
    first_seen = meta.get("first_seen", now)
    new_block = fingerprint.build_meta_block(fingerprint=fp, source=finding.source, key=finding.fingerprint_key, first_seen=first_seen, occurrences=occurrences)
    issue.description = fingerprint.replace_meta_block(issue.description or "", new_block)
    issue.save()

    was_done = client.status_label_of(issue) == "status::done" or issue.state == "closed"
    if was_done:
        issue.notes.create({"body": f"**Regression** — recurred at {now} (occurrence {occurrences}). Reopening."})
        state.transition(project, issue, "status::open")
    else:
        issue.notes.create({"body": f"Recurred at {now} (occurrence {occurrences})."})

    refreshed = project.issues.get(issue.iid)
    return refreshed, was_done


def run_sync(project: Project, findings: list[Finding], *, apply: bool) -> SyncReport:
    report = SyncReport()
    local_cache = cache_module.load()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    for finding in findings:
        fp = fingerprint.compute(finding.source, finding.fingerprint_key)
        existing = _find_existing(project, fp, local_cache)

        if existing is None:
            if apply:
                issue = _apply_create(project, finding, fp, now)
                local_cache[fp] = issue.iid
                report.items.append(SyncItem(finding, fp, "create", issue.iid, issue.web_url))
            else:
                report.items.append(SyncItem(finding, fp, "create", None, "(dry run — would create)"))
            continue

        if not client.is_owned(existing):
            report.items.append(SyncItem(finding, fp, "skip-not-owned", existing.iid, existing.web_url))
            continue

        if apply:
            refreshed, was_regression = _apply_recurrence(project, existing, finding, fp, now)
            local_cache[fp] = refreshed.iid
            action = "reopen" if was_regression else "update"
            report.items.append(SyncItem(finding, fp, action, refreshed.iid, refreshed.web_url))
        else:
            was_done = client.status_label_of(existing) == "status::done" or existing.state == "closed"
            action = "reopen" if was_done else "update"
            report.items.append(SyncItem(finding, fp, action, existing.iid, "(dry run — would " + ("reopen as a regression" if was_done else "bump occurrences") + ")"))

    if apply:
        cache_module.save(local_cache)

    return report
