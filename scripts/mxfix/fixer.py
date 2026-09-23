"""The Fixer interface — neither codex nor claude is hardcoded into strategy/*.py.

See test_environment/README.md's "Execution strategies" section for the full design. The prompt
constraints in PROMPT_CONSTRAINTS are the origin document's §3 example, verbatim —
"do not modify unrelated code, do not change public API unless necessary, do not
disable tests, do not weaken lint rules" is what keeps "make the test pass" from
becoming "delete the test." Enforcement of the last two lives in strategy/common.py's
diff review, not just the prompt — see implementation_plan.md risk #15.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

FixStatus = Literal["fixed", "failed", "no_change"]

# The origin document's §3 example, verbatim (docs/test_environment/initial_spec.md §3).
PROMPT_CONSTRAINTS = """Constraints:
- Do not modify unrelated code.
- Do not change public API unless necessary.
- Do not disable tests.
- Do not weaken lint rules."""


@dataclass(frozen=True)
class FixTask:
    issue_iid: int
    title: str
    body: str  # the issue's evidence, affected files, acceptance criteria — as filed by sync
    worktree: Path
    attempt: int  # 1-based; compared against [limits] max_fix_attempts by strategy/heal.py
    feedback: str | None  # previous-attempt check failures, a diff rejection, the fixer's own
    # reported failure, OR — on a status::needs-human issue picked back up because a human
    # commented (strategy/common.py's fresh_human_feedback()) — that comment, verbatim.
    # Whichever it is, fed back to the fixer exactly as-is; see build_prompt() below.


@dataclass(frozen=True)
class FixResult:
    status: FixStatus
    files_changed: list[str]
    summary: str
    raw_log: Path  # full JSONL/transcript, for a human to read when something looks wrong


class FixerError(Exception):
    """Raised by preflight() with a human-actionable hint, or by run() on a hard failure
    (not a normal "the fixer tried and failed" outcome — that's FixResult(status="failed")).
    """


class Fixer(Protocol):
    name: str

    def preflight(self) -> None:
        """Raise FixerError if this fixer cannot run at all (binary missing, not
        authenticated, ...). Called once before any issue is touched, so a broken fixer
        fails in the first second rather than after filing/transitioning a dozen issues.
        """
        ...

    def run(self, task: FixTask) -> FixResult:
        """Attempt one fix. Must not raise for an ordinary "couldn't fix it" outcome —
        that's FixResult(status="failed"); reserve exceptions for genuinely unexpected
        failures (the subprocess itself crashing, unparseable output, ...).
        """
        ...


def build_prompt(task: FixTask) -> str:
    """The origin document's §3 template, filled in from *task*."""
    lines = [
        f"Fix issue #{task.issue_iid}.",
        "",
        "Problem:",
        task.title,
        "",
        task.body.strip(),
        "",
        PROMPT_CONSTRAINTS,
    ]
    if task.feedback:
        lines += [
            "",
            f"Attempt {task.attempt}. Feedback from the previous attempt (a failed check, a "
            "rejected change, or a human reviewer's comment — whichever it is, address it; "
            "don't just retry the same change unchanged):",
            task.feedback.strip(),
        ]
    return "\n".join(lines)
