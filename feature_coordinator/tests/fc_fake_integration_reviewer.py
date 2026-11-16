"""A FAKE reviewer process for the W4.7 M4 tests: a real OS process that runs the real `integration_exec`
(integration_review_process.py → review_process.run_exec: the request, the tag check, the open-attempt check, the
cancellation, `result.json`) with a scripted reviewer instead of Claude Code. No model, no network, no CLI.

    python fc_fake_integration_reviewer.py RUN_DIR
        (environment: FAKE_REVIEW_MODE, FAKE_STATE_DIR, the tag; FAKE_REVIEW_MODEL / FAKE_REVIEW_INDEPENDENCE: this
         process's configured `github.review.model` / `independence` — another one than the attempt's is a
         configuration that changed before it started)

Modes: pass | notes | changes | invalid | contradict | refused | timeout | unavailable | failed — answer at once;
slow:<seconds> — pass after a while (cancellable); hang — run until cancelled (a stop signal or the job leaving
IN_REVIEW), then report how the CLI ended ("failed", as a killed CLI would); deaf — like hang, but it never reads the
job record (only a signal from the waiting side stops it); crash — the CLI started (its output file
exists), then exit without a result; nolaunch — exit without a result before the CLI would have started;
othermodel — a valid PASS whose run facts say another model ran (sonnet); nofacts — a valid PASS without run facts.
The run facts are ClaudeCodeReviewer's: `model` (the configured model) and `reported_model` (Claude Code's init event).
"""

# pylint: disable=missing-function-docstring

from __future__ import annotations

import dataclasses
import json
import os
import re
import sys
import time
from pathlib import Path

from feature_coordinator.config import default_config
from feature_coordinator.integration_config import GitHubIntegrationConfig
from feature_coordinator.integration_review_process import integration_exec
from feature_coordinator.reviewer import ReviewRequest, ReviewResult
from feature_coordinator.supervisor import install_termination_handlers, stop_requested


def first_file(prompt: str) -> str:
    section = prompt.split("Changed files:\n", 1)[1]
    match = re.match(r"- (.+?) \(", section)
    return match.group(1) if match else ""


def answer(request: ReviewRequest, verdict: str, findings: list[dict]) -> dict:
    head = re.search(r"Reviewed head: ([0-9a-f]{40})", request.prompt)
    return {"verdict": verdict, "summary": "The candidate integrates the source as described.",
            "head_sha": head.group(1) if head else "", "findings": findings, "limitations": []}


def finding(request: ReviewRequest, severity: str) -> dict:
    return {"severity": severity, "file": first_file(request.prompt), "line_start": None, "line_end": None,
            "title": f"A {severity} observation", "explanation": "The constant is not documented.",
            "recommendation": "Document it."}


class ScriptedReviewer:  # pylint: disable=too-few-public-methods
    """Stands in for ClaudeCodeReviewer inside the real reviewer process."""

    def __init__(self, mode: str, model: str = "opus") -> None:
        self.mode, self.model = mode, model

    def review(self, request: ReviewRequest) -> ReviewResult:  # pylint: disable=too-many-return-statements
        mode = self.mode
        ran = "sonnet" if mode == "othermodel" else self.model
        details = {"auth": "claude.ai subscription (pro)", "model": ran, "reported_model": f"claude-{ran}-5-5",
                   "api_key_source": "none"}
        if mode == "nofacts":
            details = {"auth": "claude.ai subscription (pro)"}
        if mode in ("othermodel", "nofacts"):
            mode = "pass"
        (request.run_dir / "fake-pid").write_text(str(os.getpid()), encoding="utf-8")
        if mode == "nolaunch":
            os._exit(3)  # pylint: disable=protected-access
        (request.run_dir / "stdout.jsonl").write_text("", encoding="utf-8")   # the CLI has started
        (request.run_dir / "seen-prompt.txt").write_text(request.prompt, encoding="utf-8")
        if mode.startswith("slow:"):
            deadline = time.monotonic() + float(mode.split(":", 1)[1])
            while time.monotonic() < deadline:
                if stop_requested() or (request.cancelled and request.cancelled()):
                    return ReviewResult("cancelled", error="cancelled", details=details)
                time.sleep(0.05)
            mode = "pass"
        if mode in ("hang", "deaf"):
            while not (stop_requested() or (mode == "hang" and request.cancelled and request.cancelled())):
                time.sleep(0.05)
            return ReviewResult("failed", error="Claude Code exited with -15", details=details)
        if mode == "crash":
            os._exit(3)  # pylint: disable=protected-access
        if mode in ("timeout", "unavailable", "refused", "failed"):
            return ReviewResult(mode, error=f"the fake reviewer says {mode}", details=details)
        if mode == "invalid":
            return ReviewResult("ok", output={"verdict": "pass"}, details=details)
        if mode == "contradict":
            return ReviewResult("ok", output=answer(request, "pass", [finding(request, "major")]), details=details)
        verdict, findings = {"pass": ("pass", []), "notes": ("pass_with_notes", [finding(request, "minor"),
                                                                                finding(request, "info")]),
                             "changes": ("request_changes", [finding(request, "major"),
                                                             finding(request, "minor")])}[mode]
        return ReviewResult("ok", output=answer(request, verdict, findings), details=details)


def main() -> int:
    install_termination_handlers()
    config = default_config()
    integration = GitHubIntegrationConfig(enabled=True, known_authors=("demo@example.invalid",))
    review = dataclasses.replace(config.github.review,
                                 model=os.environ.get("FAKE_REVIEW_MODEL", config.github.review.model),
                                 independence=os.environ.get("FAKE_REVIEW_INDEPENDENCE",
                                                             config.github.review.independence))
    config = dataclasses.replace(config, github=dataclasses.replace(config.github, integration=integration,
                                                                    review=review))
    run_dir = Path(sys.argv[1])
    (run_dir / "fake-env.json").write_text(json.dumps(sorted(os.environ)), encoding="utf-8")
    return integration_exec(config, Path(os.environ["FAKE_STATE_DIR"]), run_dir,
                            reviewer=ScriptedReviewer(os.environ.get("FAKE_REVIEW_MODE", "pass"),
                                                      config.github.review.model))


if __name__ == "__main__":
    sys.exit(main())
