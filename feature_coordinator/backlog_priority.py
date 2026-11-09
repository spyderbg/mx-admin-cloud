"""Read-only ordering of approved waiting Features from GitHub Projects Priority.

The board remains a projection for Status, claims and approvals; ONLY its Priority
single-select value influences which *planned* Feature is activated next. No board
value may activate an unapproved Issue, interrupt an active Feature or change a task.
"""

from __future__ import annotations

import re
from typing import Any, Sequence, TypeVar

from .errors import ProjectBoardError

T = TypeVar("T")

_PRIORITY: dict[str, int] = {
    "p0": 0, "critical": 0, "urgent": 0,
    "p1": 1, "high": 1,
    "p2": 2, "normal": 2, "medium": 2,
    "p3": 3, "low": 3,
}
_DEFAULT_RANK = 2
_PREFIX = re.compile(r"^(p[0-3])(?:$|[\s:—–-])", re.IGNORECASE)


def priority_rank(value: str) -> int:
    """Rank a configured GitHub option, accepting P0-P3 and descriptive aliases.

    Empty values behave as Normal only in the scheduler; GitHub's field is not edited.
    Unknown nonempty values block activation rather than silently changing the order.
    """
    name = value.strip().lower()
    if not name:
        return _DEFAULT_RANK
    prefix = _PREFIX.match(name)
    key = prefix.group(1).lower() if prefix else name
    if key not in _PRIORITY:
        raise ProjectBoardError(f"unsupported GitHub Project Priority option {value!r}; "
                                "use P0/Critical, P1/High, P2/Normal or P3/Low")
    return _PRIORITY[key]


def ordered_backlog(records: Sequence[T], project_sync: Any) -> list[T]:
    """Sort *eligible, approved, planned* intake records by Priority, then Issue number.

    The caller has already filtered out changed approvals and checked that no Feature
    is active. This function never writes to GitHub or the local state. Only the
    configured Project and the exact approved Issue identities count.
    """
    if not records or project_sync is None or not project_sync.cfg.enabled:
        return list(records)

    project = project_sync.resolve()  # verifies configured project owner, number and node ID
    priority = project.field("Priority")
    if priority is None:
        return list(records)           # backward-compatible board with no Priority field
    if priority.data_type != "SINGLE_SELECT":
        raise ProjectBoardError("GitHub Project Priority must be a SINGLE_SELECT field")

    expected = {r.issue_number: r for r in records}
    choices: dict[int, int] = {}
    for item in project_sync.board.list_items(project.id):  # one paginated read; no mutations
        record = expected.get(item.issue_number)
        if record is None or item.archived or item.content_type != "Issue" \
                or item.repository != project_sync.config.github.repository:
            continue
        if item.issue_db_id != record.issue_id or (record.node_id and item.issue_node_id != record.node_id):
            raise ProjectBoardError(f"Priority item for Issue #{record.issue_number} has different identity")
        if record.issue_number in choices:
            raise ProjectBoardError(f"duplicate Priority project items for Issue #{record.issue_number}")

        value = item.values.get(priority.name, "")
        if value and priority.option(value) is None:
            raise ProjectBoardError(f"unrecognized Priority option {value!r} on Issue #{record.issue_number}")
        choices[record.issue_number] = priority_rank(value)

    return sorted(records, key=lambda rec: (choices.get(rec.issue_number, _DEFAULT_RANK), rec.issue_number))
