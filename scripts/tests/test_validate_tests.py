"""Tests for scripts/validate_tests.py — summary parsing and the rule that a suite must really run."""

# pylint: disable=missing-function-docstring

from __future__ import annotations

import pytest

import validate_tests as vt


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("=========== 1010 passed, 1 warning in 31.28s ===========", (1010, 0, 0, 0, "31.28s")),
        ("1010 passed, 1 warning in 31.28s", (1010, 0, 0, 0, "31.28s")),  # -q drops the frame
        ("== 2 failed, 7 passed, 3 skipped in 75.12s (0:01:15) ==", (7, 2, 0, 3, "75.12s")),
        ("!!! Interrupted: 4 errors during collection !!!\n4 errors in 0.17s", (0, 0, 4, 0, "0.17s")),
        ("=== 1 failed, 5 passed, 1 error in 2.00s ===", (5, 1, 1, 0, "2.00s")),
    ],
)
def test_pytest_summary_counts_every_outcome(output: str, expected: tuple) -> None:
    result = vt._parse_pytest_summary("collected …\n" + output + "\n", 0)
    assert (result["passed"], result["failed"], result["errors"], result["skipped"], result["time"]) == expected
    assert result["total"] == expected[0] + expected[1] + expected[2]


def test_pytest_summary_with_no_tests_is_a_failure() -> None:
    result = vt._parse_pytest_summary("no tests ran in 0.01s\n", 5)
    assert result["total"] == 0
    assert vt._failure_reason(result) == "exited with code 5"


def test_collection_errors_fail_the_suite() -> None:
    result = vt._parse_pytest_summary("4 errors in 0.17s\n", 2)
    assert vt._failure_reason(result) == "0 failed, 4 errors"


SPEC_OUTPUT = """\
✔ a test (0.5ms)
✖ another test (1.2ms)
ℹ tests 53
ℹ suites 0
ℹ pass 50
ℹ fail 2
ℹ cancelled 1
ℹ skipped 0
ℹ todo 0
ℹ duration_ms 517.407766
"""

TAP_OUTPUT = """\
TAP version 13
ok 1 - a test
# tests 3
# pass 3
# fail 0
# cancelled 0
# skipped 1
# todo 1
# duration_ms 1250
"""


def test_node_summary_from_the_spec_reporter() -> None:
    result = vt._parse_node_test_summary(SPEC_OUTPUT, 1)
    assert (result["passed"], result["failed"], result["skipped"], result["total"], result["time"]) == (
        50, 3, 0, 53, "0.52s",
    )


def test_node_summary_from_the_tap_reporter() -> None:
    result = vt._parse_node_test_summary(TAP_OUTPUT, 0)
    assert (result["passed"], result["failed"], result["skipped"], result["total"], result["time"]) == (
        3, 0, 2, 3, "1.25s",
    )
    assert vt._failure_reason(result) is None


def test_node_summaries_from_several_runs_are_summed() -> None:
    result = vt._parse_node_test_summary(TAP_OUTPUT + TAP_OUTPUT, 0)
    assert (result["passed"], result["time"]) == (6, "2.50s")


def test_unparsable_output_fails_through_the_exit_code_or_the_empty_total() -> None:
    assert vt._failure_reason(vt._parse_node_test_summary("Error: Cannot find module\n", 1)) == "exited with code 1"
    assert vt._failure_reason(vt._parse_node_test_summary("", 0)) == "ran no tests"


def test_a_suite_that_did_not_run_is_a_failure() -> None:
    assert vt._failure_reason(None) == "did not run"


def test_frontend_without_a_test_script_did_not_run(tmp_path) -> None:
    (tmp_path / "package.json").write_text('{"scripts": {"build": "vite build"}}', encoding="utf-8")
    assert vt._run_frontend_tests(tmp_path) is None
