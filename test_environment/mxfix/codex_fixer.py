"""CodexFixer — `codex exec --json --output-schema <schema>`, cwd pinned to the worktree.

Grounded in `codex exec --help` / `codex doctor --json` output from the actual
installed CLI (codex-cli 0.155.1), not assumed from the plan alone:

  - `-C/--cd <DIR>` pins the working root — used instead of `subprocess`'s own cwd= so
    the *agent* (not just the Python subprocess) understands the worktree as its root.
  - `-s/--sandbox workspace-write` lets it edit files and run commands inside that root
    without per-action interactive approval (there is no human present to approve
    anything) while still refusing escalation outside it — this fixer never passes
    `--dangerously-bypass-approvals-and-sandbox`.
  - The prompt is piped via stdin (`-` — the CLI reads stdin when no PROMPT argument or
    `-` is given), not passed as a CLI argument: issue bodies can be long, and stdin
    sidesteps both shell-escaping and OS argv-length limits entirely.
  - `--output-schema fix_result_schema.json` + `-o/--output-last-message <file>` gets
    the structured FixResult as a clean, isolated file — parsed from there rather than
    picked out of the `--json` event stream, which is saved as the raw_log instead.
  - `codex doctor --json` (`checks["auth.credentials"].status`) is what preflight()
    actually inspects — verified interactively before writing this: `overallStatus` and
    a `status: "fail"` per failing check are real, present fields.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from .fixer import FixerError, FixResult, FixTask, build_prompt

SCHEMA_PATH = Path(__file__).resolve().parent / "fix_result_schema.json"


class CodexFixer:
    name = "codex"

    def __init__(self, *, timeout_seconds: int = 1800) -> None:
        self.timeout_seconds = timeout_seconds

    def preflight(self) -> None:
        binary = shutil.which("codex")
        if not binary:
            raise FixerError("codex binary not found on PATH. Install it, or set [fixer] adapter = \"claude\" in testenv.toml.")

        try:
            result = subprocess.run(["codex", "doctor", "--json"], capture_output=True, text=True, timeout=30, check=False)
        except subprocess.TimeoutExpired as exc:
            raise FixerError("`codex doctor --json` timed out after 30s — is the codex CLI hanging?") from exc

        try:
            report = json.loads(result.stdout)
        except (ValueError, TypeError) as exc:
            raise FixerError(f"`codex doctor --json` did not return valid JSON: {exc}") from exc

        if report.get("overallStatus") != "ok":
            failing = [
                f"{check.get('id')}: {check.get('summary')}"
                for check in report.get("checks", {}).values()
                if check.get("status") == "fail"
            ]
            detail = "; ".join(failing) if failing else f"overallStatus={report.get('overallStatus')!r}"
            raise FixerError(f"codex doctor reports problems: {detail}. Run `codex doctor` for the full report.")

        auth_check = report.get("checks", {}).get("auth.credentials", {})
        if auth_check.get("status") != "ok":
            raise FixerError(f"codex is not authenticated ({auth_check.get('summary', 'unknown')}). Run `codex login`.")

    def run(self, task: FixTask) -> FixResult:
        raw_log = task.worktree / ".mxfix-codex.jsonl"
        last_message_path = task.worktree / ".mxfix-codex-result.json"
        prompt = build_prompt(task)

        cmd = [
            "codex", "exec",
            "--cd", str(task.worktree),
            "--sandbox", "workspace-write",
            "--json",
            "--output-schema", str(SCHEMA_PATH),
            "--output-last-message", str(last_message_path),
            "--skip-git-repo-check",
            "-",  # read the prompt from stdin
        ]

        try:
            result = subprocess.run(
                cmd,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                cwd=task.worktree,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raw_log.write_text((exc.stdout or "") + "\n--- TIMED OUT ---\n" + (exc.stderr or ""), encoding="utf-8")
            return FixResult(
                status="failed", files_changed=[],
                summary=f"codex exec timed out after {self.timeout_seconds}s",
                raw_log=raw_log,
            )

        raw_log.write_text(result.stdout + "\n--- stderr ---\n" + result.stderr, encoding="utf-8")

        if not last_message_path.is_file():
            return FixResult(
                status="failed", files_changed=[],
                summary=f"codex exec produced no structured result (exit {result.returncode}) — see {raw_log.name}",
                raw_log=raw_log,
            )

        try:
            data = json.loads(last_message_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return FixResult(
                status="failed", files_changed=[],
                summary=f"codex exec's final message was not valid JSON ({exc}) — see {raw_log.name}",
                raw_log=raw_log,
            )

        status = data.get("status")
        if status not in ("fixed", "failed", "no_change"):
            status = "failed"

        return FixResult(
            status=status,
            files_changed=list(data.get("files_changed", []) or []),
            summary=str(data.get("summary", "")),
            raw_log=raw_log,
        )
