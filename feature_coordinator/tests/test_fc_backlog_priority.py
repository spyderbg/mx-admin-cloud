"""Backlog Priority is an ordering hint, never authority to start or change a Feature."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from feature_coordinator.backlog_priority import ordered_backlog, priority_rank, ranked_backlog
from feature_coordinator.errors import ProjectBoardError


def _record(number: int):
    return SimpleNamespace(issue_number=number, issue_id=1000 + number, node_id=f"I_{number}")


def _item(number: int, priority: str = "", *, repo: str = "spyderbg/mx-admin-cloud",
          archived: bool = False):
    return SimpleNamespace(issue_number=number, issue_db_id=1000 + number, issue_node_id=f"I_{number}",
                           content_type="Issue", repository=repo, archived=archived,
                           values={"Priority": priority} if priority else {})


def _sync(items, *, enabled: bool = True, field_type: str = "SINGLE_SELECT", has_field: bool = True):
    field = SimpleNamespace(name="Priority", data_type=field_type,
                            option=lambda name: name if name in (
                                "Critical", "High", "Normal", "Low",
                                "P0 - Critical", "P1 - High", "P2 - Normal", "P3 - Low",
                            ) else None)
    project = SimpleNamespace(id="PVT_allowed", field=lambda name: field if has_field else None)
    board = SimpleNamespace(list_items=lambda project_id: list(items))
    return SimpleNamespace(cfg=SimpleNamespace(enabled=enabled), resolve=lambda: project,
                           board=board,
                           config=SimpleNamespace(github=SimpleNamespace(repository="spyderbg/mx-admin-cloud")))


@pytest.mark.parametrize(("value", "rank"), [
    ("Critical", 0), ("Urgent", 0), ("P0 - Critical", 0),
    ("High", 1), ("P1 - High", 1),
    ("Normal", 2), ("Medium", 2), ("P2 - Normal", 2), ("", 2), ("  ", 2),
    ("Low", 3), ("P3 - Low", 3),
])
def test_priority_names(value, rank):
    assert priority_rank(value) == rank


def test_unknown_priority_fails_closed():
    with pytest.raises(ProjectBoardError, match="unsupported"):
        priority_rank("Blocker")


def test_highest_priority_wins_and_ties_keep_older_issue():
    records = [_record(81), _record(82), _record(83), _record(84), _record(85)]
    items = [_item(81, "Normal"), _item(82, "Critical"),
             _item(83, "High"), _item(84, "P1 - High"), _item(85, "Low")]
    assert [r.issue_number for r in ordered_backlog(records, _sync(items))] == [82, 83, 84, 81, 85]


def test_missing_value_is_normal_without_board_mutation():
    records = [_record(81), _record(82), _record(83)]
    # Not on the board / no Priority field value: Normal for sorting only.
    project = _sync([_item(81, "Low"), _item(83, "High")])
    assert [r.issue_number for r in ordered_backlog(records, project)] == [83, 82, 81]


def test_unrelated_or_archived_items_are_ignored():
    records = [_record(81), _record(82)]
    project = _sync([_item(81, "Critical", repo="foreign/repo"), _item(82, "High"),
                     _item(81, "Critical", archived=True)])
    assert [r.issue_number for r in ordered_backlog(records, project)] == [82, 81]


def test_identity_mismatch_or_duplicate_item_holds_activation():
    records = [_record(81), _record(82)]
    bad = _item(82, "Critical")
    bad.issue_db_id = 900000
    with pytest.raises(ProjectBoardError, match="different identity"):
        ordered_backlog(records, _sync([bad]))
    with pytest.raises(ProjectBoardError, match="duplicate"):
        ordered_backlog(records, _sync([_item(81), _item(81)]))


def test_unknown_option_or_wrong_field_type_holds_activation():
    records = [_record(81), _record(82)]
    with pytest.raises(ProjectBoardError, match="unrecognized"):
        ordered_backlog(records, _sync([_item(82, "Unexpected")]))
    with pytest.raises(ProjectBoardError, match="SINGLE_SELECT"):
        ordered_backlog(records, _sync([_item(82, "High")], field_type="TEXT"))


def test_without_project_or_field_oldest_issue_wins():
    records = [_record(81), _record(82)]
    assert ordered_backlog(records, None) == records
    assert ordered_backlog(records, _sync([], enabled=False)) == records
    assert ordered_backlog(records, _sync([], has_field=False)) == records


def test_queue_preview_reports_ranks_without_writing():
    records = [_record(81), _record(82), _record(83)]
    board = _sync([_item(81, "Normal"), _item(82, "Critical"), _item(83, "Low")])
    ranked = ranked_backlog(records, board)
    assert [(rank, rec.issue_number) for rank, rec in ranked] == [(0, 82), (2, 81), (3, 83)]
    assert ordered_backlog(records, board) == [rec for _rank, rec in ranked]
