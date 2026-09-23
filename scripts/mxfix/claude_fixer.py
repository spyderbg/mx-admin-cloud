"""ClaudeFixer — `claude -p`, scoped to the worktree. Proves the Fixer interface isn't
codex-shaped: same FixTask in, same FixResult out, a completely different invocation
underneath.

Grounded in `claude -p --help` and one real (minimal, cheap) probe call against the
installed CLI (Claude Code 2.1.278) rather than assumed:

  - `--output-format json` returns one JSON envelope (`type: "result"`, `is_error`,
    `result: "<final text>"`, plus cost/turn/timing fields) — verified directly. There
    is no `--output-schema`-equivalent structured-output flag the way codex has one, so
    the prompt itself asks for a fenced ```json block in the final reply, parsed out of
    `result` the same way scripts/mxissues/fingerprint.py parses the ```mx-meta block
    embedded in an issue description — a pattern already used elsewhere in this project.
  - `--permission-mode bypassPermissions` is used instead of
    `--dangerously-skip-permissions`/`--allow-dangerously-skip-permissions`: it is a
    normal, documented mode value (not a "dangerously-" flag) for exactly this
    unattended-in-an-isolated-worktree case. The worktree directory is the sandbox
    boundary here — see test_environment/README.md's "Execution strategies" section for
    the residual risk this does NOT eliminate (a coding agent with Bash access is not
    fully containable by CLI flags alone).
  - The worktree is set via the subprocess's cwd (Claude Code uses its launch directory
    as the project root; there is no separate -C/--cd flag the way codex has one).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess

from .fixer import FixerError, FixResult, FixTask, build_prompt

_JSON_BLOCK_RE = re.compile(r"```json\s*\n(.*?)\n```", re.DOTALL)

_RESULT_JSON_INSTRUCTIONS = """
When you are done (whether fixed, failed, or no change was needed), end your reply with
a fenced ```json block containing exactly these keys and nothing else:

```json
{"status": "fixed|failed|no_change", "files_changed": ["path/one", "path/two"], "summary": "a few sentences"}
```
"""


class ClaudeFixer:
    name = "claude"

    def __init__(self, *, timeout_seconds: int = 1800) -> None:
        self.timeout_seconds = timeout_seconds

    def preflight(self) -> None:
        binary = shutil.which("claude")
        if not binary:
            raise FixerError("claude binary not found on PATH. Install it, or set [fixer] adapter = \"codex\" in testenv.toml.")

        try:
            result = subprocess.run(["claude", "doctor"], capture_output=True, text=True, timeout=30, check=False)
        except subprocess.TimeoutExpired as exc:
            raise FixerError("`claude doctor` timed out after 30s — is the claude CLI hanging?") from exc

        if result.returncode != 0:
            raise FixerError(f"`claude doctor` exited {result.returncode}:\n{result.stdout}\n{result.stderr}")
        # Unlike codex doctor --json, claude doctor has no machine-readable auth field to
        # check here — a real auth failure will surface as a failed run() instead.

    def run(self, task: FixTask) -> FixResult:
        raw_log = task.worktree / ".mxfix-claude.json"
        prompt = build_prompt(task) + "\n" + _RESULT_JSON_INSTRUCTIONS

        cmd = [
            "claude", "-p",
            "--output-format", "json",
            "--permission-mode", "bypassPermissions",
            prompt,
        ]

        try:
            result = subprocess.run(
                cmd,
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
                summary=f"claude -p timed out after {self.timeout_seconds}s",
                raw_log=raw_log,
            )

        raw_log.write_text(result.stdout + "\n--- stderr ---\n" + result.stderr, encoding="utf-8")

        try:
            envelope = json.loads(result.stdout)
        except (ValueError, TypeError) as exc:
            return FixResult(
                status="failed", files_changed=[],
                summary=f"claude -p did not return valid JSON ({exc}, exit {result.returncode}) — see {raw_log.name}",
                raw_log=raw_log,
            )

        if envelope.get("is_error"):
            return FixResult(
                status="failed", files_changed=[],
                summary=f"claude -p reported an error (subtype={envelope.get('subtype')}) — see {raw_log.name}",
                raw_log=raw_log,
            )

        final_text = str(envelope.get("result", ""))
        match = _JSON_BLOCK_RE.search(final_text)
        if not match:
            return FixResult(
                status="failed", files_changed=[],
                summary=f"claude -p's reply had no ```json result block — see {raw_log.name}. Last message: {final_text[:300]}",
                raw_log=raw_log,
            )

        try:
            data = json.loads(match.group(1))
        except (ValueError, TypeError) as exc:
            return FixResult(
                status="failed", files_changed=[],
                summary=f"claude -p's ```json result block was not valid JSON ({exc}) — see {raw_log.name}",
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
