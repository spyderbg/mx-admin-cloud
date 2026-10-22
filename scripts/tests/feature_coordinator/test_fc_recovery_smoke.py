"""W3.2 offline rehearsal: the checkpoint recovery smoke scenario with the fake agent.

The same harness as the real-agent run (fc_recovery_smoke.py): a real temporary Git repository,
the real CLI path (`feature.py work --apply` → `launch` → native-cli backend), real checkpoint
storage, a Worker process killed at the boundary before step 3, a NEW Worker process resuming.
No model, no GitHub."""

# pylint: disable=missing-function-docstring

from __future__ import annotations

from pathlib import Path

import pytest

from fc_helpers import install_fake_agent
from fc_recovery_smoke import Harness


@pytest.mark.usefixtures("needs_bwrap")  # the task's gate runs in the existing gate sandbox
def test_completed_steps_are_not_run_again_after_a_worker_restart(tmp_path: Path, fake_home: Path) -> None:
    del fake_home
    agent = install_fake_agent(tmp_path / "fake-agent")
    report = Harness(tmp_path / "w3-recovery", agent, fake=True, echo=lambda _line: None, step_timeout=120).run()
    failed = [c for c in report["checks"] if not c["ok"]]
    assert report["passed"] and not failed, failed
    sessions = [(e["step"], e["decision"]) for e in report["agent_audit"]]
    assert sessions == [("model", "started"), ("service", "started"), ("validation", "started")]
    before, after = report["at_boundary"], report["checkpoints_after"]
    assert [c["commit"] for c in before["checkpoints"]] == [c[4] for c in after[:2]]  # A and B unchanged
    assert report["preview"]["expected_ai_executions"] == 1 and report["preview"]["remaining"] == ["validation"]
