"""W4.6.1 (R1): the independent AI reviewer — the Desktop Worker's Claude Code reads ONE Draft PR's diff, with the
approved specification and plan, and answers with structured findings (review_result.py validates them, review.py
decides the outcome, stores it and publishes it).

`ClaudeCodeReviewer` is the W4.5 planner's invocation (planner.py), reused as it is:

  * the environment is the native ALLOWLIST (`native_environment`): no API key, auth token, GitHub token,
    alternative base URL or cloud-provider switch is passed, whatever the operator's shell holds; before the
    request `claude auth status --json` must report the claude.ai subscription (`check_subscription`), else
    the reviewer is UNAVAILABLE — never a silent switch to API billing;
  * NO TOOLS (`--tools ""`), `--permission-mode dontAsk`, `--restricted`, `--strict-mcp-config` (no MCP
    server), `--disable-slash-commands`, `--no-session-persistence`, `--json-schema` for the answer; the
    working directory is an EMPTY directory under the state directory, never the checkout;
  * supervision (deadline, process group, cancellation) is the existing supervisor's.

After the run, everything the stream shows is checked, and a violation REFUSES the run (an ERROR review, never
retried): the `init` event must exist, offer no tool and no MCP server, use no API key (`apiKeySource: none`) and
the requested model family; no `tool_use` event and no permission denial may appear; the empty working directory
must still be empty. Only then is the answer parsed.

What no-tools mode does and does not give: the MODEL cannot read, write or run anything — it only sees the
prompt the coordinator assembled (review_context.py), so it cannot reach the GitHub login, the state directory
or the checkout, and it cannot post a comment, push or merge. The Claude Code PROGRAM itself is not confined:
it runs unsandboxed as the operator (it reads its own login under HOME and talks to its API). This reduces
exposure; it is not OS-level isolation (W1: native hardening, bwrap). The reviewer receives no GitHub
credential, and only trusted coordinator code (review.py) ever writes to GitHub.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from .config import ProjectConfig
from .github_api import sanitize
from .launchers import RunnerProbe, probe_runner
from .native_backend import SubscriptionCheck, check_subscription, claude_session_problems, native_environment
from .planner import MAX_RAW, parse_stream, planner_argv
from .providers import CliRunnerProfile, ProviderKind
from .supervisor import supervise

# ok: a parsed answer · unavailable: nothing ran (CLI, login) · cancelled / timeout: the run was stopped ·
# failed: the run produced no usable answer (retryable) · refused: the run broke a rule (never retried)
RESULT_STATUSES = ("ok", "unavailable", "cancelled", "timeout", "failed", "refused")


@dataclass
class ReviewRequest:  # pylint: disable=too-many-instance-attributes
    """One bounded reviewer invocation."""

    key: str
    prompt: str
    schema: Mapping[str, Any]
    run_dir: Path                     # this attempt's own directory (created by the caller's write-ahead step)
    timeout_seconds: float
    heartbeat: Callable[[], None] | None = None
    cancelled: Callable[[], bool] | None = None


@dataclass
class ReviewResult:
    """What one reviewer invocation produced (status: RESULT_STATUSES)."""

    status: str
    output: Any = None
    error: str = ""
    details: dict[str, Any] = field(default_factory=dict)


class Reviewer(Protocol):  # pylint: disable=too-few-public-methods
    """Reviews one PR (tests use fakes; the operator's command uses ClaudeCodeReviewer)."""

    def review(self, request: ReviewRequest) -> ReviewResult:
        """One bounded review request."""


def stream_problems(stdout_path: Path) -> list[str]:
    """Rule violations visible in Claude Code's stream-json output: a missing init event, offered tools or MCP
    servers, any tool use, any permission denial (a no-tools session has none of these)."""
    raw = stdout_path.read_bytes()[:MAX_RAW].decode("utf-8", errors="replace")
    events = []
    for line in raw.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            events.append(event)
    init = next((e for e in events if e.get("type") == "system" and e.get("subtype") == "init"), None)
    if init is None:
        return ["Claude Code reported no init event: the no-tools session cannot be confirmed"]
    problems = []
    tools = init.get("tools")
    if not isinstance(tools, list) or tools:
        problems.append(f"Claude Code offered tools {sanitize(tools, 200)}; the reviewer must have none")
    if init.get("mcp_servers"):
        problems.append("Claude Code loaded MCP servers; the reviewer must have none")
    for event in events:
        message = event.get("message") if event.get("type") == "assistant" else None
        content = message.get("content") if isinstance(message, dict) else None
        for item in content if isinstance(content, list) else ():
            if isinstance(item, dict) and item.get("type") in ("tool_use", "server_tool_use", "mcp_tool_use"):
                problems.append(f"the reviewer tried to use the tool {sanitize(item.get('name'), 80)!r}")
        if event.get("type") == "result" and event.get("permission_denials"):
            problems.append(f"Claude Code denied {len(event['permission_denials'])} permission request(s)")
    return problems


class ClaudeCodeReviewer:  # pylint: disable=too-few-public-methods
    """The Desktop Worker's reviewer (see the module docstring)."""

    def __init__(self, config: ProjectConfig, *, probe: Callable[[CliRunnerProfile], RunnerProbe] = probe_runner,
                 auth_check: Callable[..., SubscriptionCheck] = check_subscription,
                 run: Callable[..., Any] = supervise, environ: Mapping[str, str] | None = None) -> None:
        self.config = config
        self.cfg = config.github.review
        self._probe, self._auth_check, self._run, self._environ = probe, auth_check, run, environ

    @property
    def model(self) -> str:
        """The model passed to the CLI ('' = the CLI's default)."""
        return "" if self.cfg.model == "default" else self.cfg.model

    def review(self, request: ReviewRequest) -> ReviewResult:  # pylint: disable=too-many-return-statements
        """One bounded, subscription-only, no-tools review request (see the module docstring)."""
        profile = self.config.providers.get(self.cfg.provider)
        if not isinstance(profile, CliRunnerProfile) or profile.kind is not ProviderKind.CLAUDE_CODE:
            return ReviewResult("unavailable", error=f"provider {self.cfg.provider!r} is not Claude Code")
        probe = self._probe(profile)
        if not probe.ok:
            return ReviewResult("unavailable",
                                error=f"Claude Code is unavailable: {probe.error or probe.missing_flags}")
        env = native_environment(source=self._environ)
        auth = self._auth_check(ProviderKind.CLAUDE_CODE, probe.resolved, env)
        if not auth.ok:
            return ReviewResult("unavailable", error=auth.detail or auth.method, details={"auth": auth.method})
        work = request.run_dir / "empty"  # the reviewer's working directory: empty, outside the checkout
        work.mkdir(parents=True, exist_ok=True)
        if any(work.iterdir()):
            return ReviewResult("refused", error=f"the reviewer's working directory {work} is not empty")
        prompt = request.run_dir / "prompt.txt"
        prompt.write_text(request.prompt, encoding="utf-8")
        argv = planner_argv(probe.resolved, model=self.model, budget_usd=self.config.runtime.claude_max_budget_usd,
                            schema=request.schema)
        env = {**env, "DISABLE_AUTOUPDATER": "1", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"}
        stdout = request.run_dir / "stdout.jsonl"
        sup = self._run(argv, cwd=work, env=env, stdout_path=stdout, stderr_path=request.run_dir / "stderr.log",
                        stdin_path=prompt, timeout_seconds=request.timeout_seconds, heartbeat=request.heartbeat,
                        heartbeat_interval=10.0, cancelled=request.cancelled,
                        grace_seconds=self.config.runtime.terminate_grace_seconds)
        details: dict[str, Any] = {"auth": auth.method, "cli_version": probe.version,
                                   "model": self.model or "CLI default", "run_dir": str(request.run_dir),
                                   "status": sup.status, "exit_code": sup.exit_code,
                                   "duration_seconds": round(sup.duration_seconds, 1)}
        written = sorted(p.name for p in work.iterdir()) if work.is_dir() else ["(removed)"]
        if written:
            return ReviewResult("refused", error=f"the reviewer changed its empty working directory: {written[:5]}",
                                details=details)
        if sup.status in ("cancelled", "timeout"):
            return ReviewResult(sup.status, error=f"the reviewer run was {sup.status}", details=details)
        if not stdout.exists():
            return ReviewResult("failed", error="Claude Code produced no output", details=details)
        session, facts = claude_session_problems(stdout, self.model)
        details.update(facts)
        violations = session + stream_problems(stdout)
        if violations:
            return ReviewResult("refused", error="; ".join(violations), details=details)
        output, result, error = parse_stream(stdout)
        details.update(result)
        if error or not sup.ok:
            return ReviewResult("failed", error=error or f"Claude Code exited with {sup.exit_code}", details=details)
        return ReviewResult("ok", output=output, details=details)
