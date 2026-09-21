"""Label state machine: atomic status transitions with a read-back assertion.

Scoped labels (`status::foo`) are mutually exclusive only on GitLab Premium+ — this
instance is Enterprise Edition but that's a codebase distinction, not proof of a
Premium *license*, so exclusivity is enforced client-side regardless (harmless if the
license does enforce it server-side too). Never add_labels/remove_labels — they're
separate API calls that can race or leave two status:: labels set. Always compute the
full label set and send it as one replacement, then re-fetch by iid and assert exactly
the target status label survived. See implementation_plan.md §10.
"""

from __future__ import annotations

from dataclasses import dataclass

from gitlab.v4.objects import Project, ProjectIssue

from .client import STATUS_LABELS


class TransitionError(Exception):
    """Raised when a status transition's read-back assertion fails."""


@dataclass(frozen=True)
class TransitionResult:
    iid: int
    from_status: str | None
    to_status: str
    closed: bool
    reopened: bool


def transition(project: Project, issue: ProjectIssue, target_status: str) -> TransitionResult:
    """Set *issue*'s status:: label to exactly *target_status*, closing/reopening as needed.

    Takes an already-fetched issue (the caller decides how it was found — by iid
    directly, or via a search during sync) so this stays a pure state-machine step with
    no opinion on ownership; the ownership rule is enforced by callers, not here.
    """
    if target_status not in STATUS_LABELS:
        raise ValueError(f"Unknown status label: {target_status!r} (expected one of {STATUS_LABELS})")

    from_status = next((label for label in (issue.labels or []) if label.startswith("status::")), None)
    was_closed = issue.state == "closed"

    labels = [label for label in (issue.labels or []) if not label.startswith("status::")] + [target_status]
    issue.labels = labels

    closed = False
    reopened = False
    if target_status == "status::done":
        if not was_closed:
            issue.state_event = "close"
            closed = True
    elif was_closed:
        # A non-done target on an already-closed issue — e.g. the GitHub->GitLab mirror
        # auto-closed it via a "Closes #N" commit, but a later regression reopened it
        # here as status::open; or a human is reopening by hand. Either way the label
        # must not say "open"/"in-progress" while GitLab itself still shows closed.
        issue.state_event = "reopen"
        reopened = True

    issue.save()

    # The read-back assertion is the point — it is the verification Free-tier scoped
    # labels don't give you, and it costs one cheap GET.
    refreshed = project.issues.get(issue.iid)
    current_status_labels = [label for label in (refreshed.labels or []) if label.startswith("status::")]
    if current_status_labels != [target_status]:
        raise TransitionError(
            f"Issue #{issue.iid}: expected exactly [{target_status!r}] after transition, "
            f"got {current_status_labels!r}. GitLab-side state may be inconsistent — check the UI.",
        )
    if target_status == "status::done" and refreshed.state != "closed":
        raise TransitionError(f"Issue #{issue.iid}: status::done set but GitLab state is {refreshed.state!r}, not closed.")
    if target_status != "status::done" and refreshed.state == "closed":
        raise TransitionError(f"Issue #{issue.iid}: status is {target_status!r} but GitLab state is still closed.")

    return TransitionResult(
        iid=issue.iid, from_status=from_status, to_status=target_status, closed=closed, reopened=reopened,
    )


def set_status_by_iid(project: Project, iid: int, target_status: str) -> TransitionResult:
    """Convenience wrapper: fetch *iid*, then transition() it."""
    issue = project.issues.get(iid)
    return transition(project, issue, target_status)
