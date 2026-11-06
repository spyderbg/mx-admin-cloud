"""A FAKE reviewer process for the W4.6.1 R2 tests: a real OS process that runs the real `run_exec`
(review_process.py — the request, the tag check, the cancellation, `result.json`) with a scripted reviewer instead
of Claude Code. No model, no network, no CLI.

    python fc_fake_reviewer_process.py RUN_DIR      (environment: FAKE_REVIEW_MODE, FAKE_STATE_DIR, the tag)

Modes: pass | notes | changes | invalid | refused | timeout | unavailable — answer at once; slow:<seconds> — pass
after a while; hang — run until cancelled (SIGTERM or the control file), then report how the CLI ended ("failed",
as a killed CLI would); hang-hard — ignore SIGTERM (only SIGKILL ends it); crash — exit without a result; orphan —
start a tagged child that keeps running (an orphaned CLI), then exit without a result.
"""

# pylint: disable=missing-function-docstring

from __future__ import annotations

import dataclasses
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

from feature_coordinator.config import default_config
from feature_coordinator.review_process import run_exec
from feature_coordinator.reviewer import ReviewRequest, ReviewResult
from feature_coordinator.supervisor import install_termination_handlers, stop_requested


def answer(request: ReviewRequest, verdict: str, findings: list[dict]) -> dict:
    head = re.search(r"Reviewed head: ([0-9a-f]{40})", request.prompt)
    return {"verdict": verdict, "summary": "The export command is added as specified.",
            "head_sha": head.group(1) if head else "", "findings": findings, "limitations": []}


def finding(severity: str) -> dict:
    return {"severity": severity, "file": "cli/src/commands.py", "line_start": 1, "line_end": 1,
            "title": "Name the command list", "explanation": "The list is a bare constant.",
            "recommendation": "Document it."}


class ScriptedReviewer:  # pylint: disable=too-few-public-methods
    """Stands in for ClaudeCodeReviewer inside the real reviewer process."""

    def __init__(self, mode: str) -> None:
        self.mode = mode

    def review(self, request: ReviewRequest) -> ReviewResult:  # pylint: disable=too-many-return-statements
        mode = self.mode
        details = {"auth": "claude.ai subscription (pro)", "model": "opus"}
        (request.run_dir / "fake-pid").write_text(str(os.getpid()), encoding="utf-8")
        if mode.startswith("slow:"):
            deadline = time.monotonic() + float(mode.split(":", 1)[1])
            while time.monotonic() < deadline:
                if stop_requested() or (request.cancelled and request.cancelled()):
                    return ReviewResult("cancelled", error="cancelled", details=details)
                time.sleep(0.05)
            mode = "pass"
        if mode in ("hang", "hang-hard"):
            if mode == "hang-hard":
                signal.signal(signal.SIGTERM, signal.SIG_IGN)
            while not (mode == "hang" and (stop_requested() or (request.cancelled and request.cancelled()))):
                time.sleep(0.05)
            return ReviewResult("failed", error="Claude Code exited with -15", details=details)
        if mode == "crash":
            os._exit(3)  # pylint: disable=protected-access
        if mode == "orphan":
            subprocess.Popen(["sleep", "300"], start_new_session=True,  # pylint: disable=consider-using-with
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            os._exit(3)  # pylint: disable=protected-access
        if mode in ("timeout", "unavailable", "refused"):
            return ReviewResult(mode, error=f"the fake reviewer says {mode}", details=details)
        if mode == "invalid":
            return ReviewResult("ok", output={"verdict": "pass"}, details=details)
        verdict, findings = {"pass": ("pass", []), "notes": ("pass_with_notes", [finding("minor")]),
                             "changes": ("request_changes", [finding("major")])}[mode]
        return ReviewResult("ok", output=answer(request, verdict, findings), details=details)


def main() -> int:
    install_termination_handlers()
    config = default_config()
    github = config.github
    config = dataclasses.replace(config, github=dataclasses.replace(
        github, intake=dataclasses.replace(github.intake, enabled=True),
        publish=dataclasses.replace(github.publish, enabled=True),
        review=dataclasses.replace(github.review, enabled=True)))
    run_dir = Path(sys.argv[1])
    (run_dir / "fake-env.json").write_text(json.dumps(sorted(os.environ)), encoding="utf-8")
    return run_exec(config, Path(os.environ["FAKE_STATE_DIR"]), run_dir,
                    reviewer=ScriptedReviewer(os.environ.get("FAKE_REVIEW_MODE", "pass")))


if __name__ == "__main__":
    sys.exit(main())
