"""Real Claude Code and Codex launchers (Phase 3; brokered and network-isolated since W1, D-20/D-21).

A launcher turns one task into a command line for the installed CLI, the files that command
needs, and the sandbox mounts it needs; `RealCliRunner` runs it under bubblewrap
(sandbox.py) and supervision (supervisor.py), then reports what changed.

W1 — credentials and network. The sandbox has its own empty network namespace and no reusable
credential: no login file is mounted and no API key is forwarded. The CLI's only route to a
model is the host-side broker (broker.py), whose endpoint the sandbox bootstrap opens inside
the sandbox's namespace (netns_channel.py). The CLI receives the broker's base URL and a
per-run token through the channel at exec time — never on a command line or in a file.

Fail closed:

  * `probe_runner()` reads the installed CLI's `--version` and `--help`. A missing required
    flag or a version older than the verified one (MIN_VERSIONS) makes the runner
    *unavailable* — it never falls back to weaker flags;
  * no launcher may emit a bypass flag (`FORBIDDEN_ARGS`); building such a command raises;
  * without a working bwrap, a sandbox dependency (SANDBOX_DEPENDENCIES) or a broker with an
    upstream credential, a real runner is unavailable — there is no unconfined, networked or
    credential-mounting mode;
  * a sandbox that does not hand over a network-isolated broker endpoint fails the run.

Claude Code (Backend, CLI, Coordination):
  `claude -p --output-format stream-json --verbose --restricted --tools <list>
   --settings <generated> --strict-mcp-config --permission-mode acceptEdits
   --permission-prompts none --no-session-persistence --disable-slash-commands
   --max-budget-usd <n> [--model <m>]`, prompt on stdin.
  `--restricted` ignores the user, project and local settings files — including the
  repository's `.claude/settings.json` (`defaultMode: bypassPermissions`), which is never
  edited — and refuses bypassPermissions. The generated settings allow file tools and only
  the role's Bash command prefixes (runtime.role_commands); with `--permission-prompts none`
  everything else is denied. `--strict-mcp-config` with no MCP config loads no MCP servers.
  Gateway mode: ANTHROPIC_BASE_URL + ANTHROPIC_AUTH_TOKEN (the broker's per-run token).
  Nested command isolation: the settings turn on Claude Code's own Bash sandbox in strict
  mode (`failIfUnavailable`, no unsandboxed retry, no auto-allow, a strict empty domain
  allowlist, the two broker variables removed from every command's environment). Each Bash
  command then runs in a further bubblewrap sandbox with its own network and PID namespaces
  and Unix sockets blocked, so it cannot reach the broker endpoint or read the runner's token.

Codex (Frontend):
  `codex exec --json --strict-config --sandbox workspace-write --ephemeral --ignore-user-config
   -C <repo> -o <file> -c <broker provider…> -c web_search="disabled"
   -c shell_environment_policy.inherit="core" -c sandbox_workspace_write.network_access=false
   [-m <m>] -`, prompt on stdin. The broker provider is a custom `responses` provider whose
  base URL is the broker endpoint and whose key variable (FC_BROKER_TOKEN) holds the per-run
  token. `--strict-config` refuses any override key this Codex version does not know.
  Codex keeps its own sandbox (bubblewrap) nested inside ours: its model-generated commands
  get no network and no socket at all, and our outer sandbox limits writes to the Frontend roots.

Both get a private, empty HOME. Nothing in it can authenticate to a provider.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .broker import ROUTES, AuthBroker, configured_credential
from .config import ProjectConfig
from .errors import BrokerError
from .netns_channel import BROKER_PORT, SANDBOX_PYTHON, BrokerChannel
from .paths import PathPolicy
from .providers import CliRunnerProfile, ProviderKind
from .runners import RunContext, RunRequest, RunResult
from .sandbox import Bind, SandboxBuilder, SandboxUnavailableError, check_bwrap, install_prefix
from .supervisor import supervise

FORBIDDEN_ARGS = frozenset({
    "--dangerously-skip-permissions", "--allow-dangerously-skip-permissions", "bypassPermissions",
    "--dangerously-bypass-approvals-and-sandbox", "danger-full-access", "--dangerously-bypass-hook-trust",
})

REQUIRED_FLAGS: Mapping[ProviderKind, tuple[str, ...]] = {
    ProviderKind.CLAUDE_CODE: (
        "--print", "--output-format", "stream-json", "--verbose", "--restricted", "--tools", "--settings",
        "--strict-mcp-config", "--permission-mode", "acceptEdits", "--permission-prompts",
        "--no-session-persistence", "--disable-slash-commands", "--max-budget-usd", "--model",
    ),
    ProviderKind.CODEX: (
        "--json", "--strict-config", "--sandbox", "workspace-write", "--ephemeral", "--ignore-user-config", "--cd",
        "--output-last-message", "--config", "--model",
    ),
}

# The versions whose security mechanisms were verified offline against the fake provider (W1):
# Claude Code's gateway mode and strict nested Bash sandbox, Codex's custom provider through
# `-c` with --ignore-user-config and --strict-config. An older CLI is unavailable.
MIN_VERSIONS: Mapping[ProviderKind, tuple[int, int, int]] = {
    ProviderKind.CLAUDE_CODE: (2, 1, 303),
    ProviderKind.CODEX: (0, 163, 0),
}

# Programs the sandbox itself needs (all under /usr, which is bound read-only): the system Python
# for the bootstrap, and for Claude Code's nested Bash sandbox bubblewrap and socat.
SANDBOX_DEPENDENCIES: Mapping[ProviderKind, tuple[str, ...]] = {
    ProviderKind.CLAUDE_CODE: (SANDBOX_PYTHON, "bwrap", "socat"),
    ProviderKind.CODEX: (SANDBOX_PYTHON,),
}
SANDBOX_PATH = "/usr/local/bin:/usr/bin:/bin"

BROKER_URL = f"http://127.0.0.1:{BROKER_PORT}"
CLAUDE_BROKER_ENV = ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN")
CODEX_PROVIDER_ID = "fc_broker"
CODEX_TOKEN_ENV = "FC_BROKER_TOKEN"

CLAUDE_FILE_TOOLS = ("Read", "Glob", "Grep", "Edit", "Write")
CLAUDE_DENY = ("WebFetch", "WebSearch", "Bash(git commit:*)", "Bash(git add:*)", "Bash(git push:*)",
               "Bash(git checkout:*)", "Bash(git switch:*)", "Bash(git reset:*)", "Bash(git stash:*)",
               "Bash(curl:*)", "Bash(wget:*)",
               # No login file exists in the sandbox (W1); these rules stay as defense in depth.
               "Read(~/.claude/**)", "Read(~/.codex/**)", "Read(~/.claude.json)")
# Claude Code's own Bash sandbox, strict (documented settings; verified with 2.1.303 nested in ours).
CLAUDE_SANDBOX: Mapping[str, Any] = {
    "enabled": True,
    "failIfUnavailable": True,          # exit at start-up instead of running commands unsandboxed
    "allowUnsandboxedCommands": False,  # the dangerouslyDisableSandbox retry is ignored
    "autoAllowBashIfSandboxed": False,  # only the role's command allowlist runs
    "network": {"allowedDomains": [], "strictAllowlist": True},
    "credentials": {"envVars": [{"name": name, "mode": "deny"} for name in CLAUDE_BROKER_ENV]},
}


# ---------------------------------------------------------------------------
# Capability probe (read-only: --version and --help, no model call)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RunnerProbe:
    """What the installed CLI supports."""

    name: str
    kind: ProviderKind
    executable: str
    resolved: str
    version: str
    missing_flags: tuple[str, ...] = ()
    error: str = ""

    @property
    def ok(self) -> bool:
        """True when the CLI exists, is recent enough and supports every required flag."""
        return bool(self.resolved) and not self.missing_flags and not self.error


def parse_version(text: str) -> tuple[int, int, int] | None:
    """The first `X.Y.Z` in a `--version` output."""
    found = re.search(r"(\d+)\.(\d+)\.(\d+)", text)
    return (int(found.group(1)), int(found.group(2)), int(found.group(3))) if found else None


def missing_dependencies(kind: ProviderKind) -> list[str]:
    """Sandbox programs (SANDBOX_DEPENDENCIES) that are not installed under /usr."""
    missing = []
    for tool in SANDBOX_DEPENDENCIES[kind]:
        present = os.access(tool, os.X_OK) if tool.startswith("/") else shutil.which(tool, path=SANDBOX_PATH)
        if not present:
            missing.append(tool)
    return missing


def probe_runner(profile: CliRunnerProfile, *, timeout: float = 30.0) -> RunnerProbe:
    """Verify the installed CLI, its version and its required flags. Never starts a model session."""
    resolved = shutil.which(profile.executable)
    if not resolved:
        return RunnerProbe(profile.name, profile.kind, profile.executable, "", "",
                           error=f"{profile.executable!r} is not installed")
    real = str(Path(resolved).resolve())
    help_args = ["--help"] if profile.kind is ProviderKind.CLAUDE_CODE else ["exec", "--help"]
    try:
        version = subprocess.run([real, "--version"], capture_output=True, text=True, timeout=timeout,
                                 check=False).stdout.strip()
        help_text = subprocess.run([real, *help_args], capture_output=True, text=True, timeout=timeout,
                                   check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return RunnerProbe(profile.name, profile.kind, profile.executable, real, "", error=str(exc))
    text = help_text.stdout + help_text.stderr
    missing = tuple(flag for flag in REQUIRED_FLAGS[profile.kind]
                    if not re.search(rf"(?<![\w-]){re.escape(flag)}(?![\w-])", text))
    parsed, minimum = parse_version(version), MIN_VERSIONS[profile.kind]
    error = "" if parsed and parsed >= minimum else (
        f"version {version or '(none)'!r} is older than the verified minimum {'.'.join(map(str, minimum))} "
        "(security mechanisms unverified)")
    return RunnerProbe(profile.name, profile.kind, profile.executable, real, version, missing, error)


# ---------------------------------------------------------------------------
# Launch plans
# ---------------------------------------------------------------------------

@dataclass
class LaunchSpec:
    """Everything needed to start one runner process inside the sandbox."""

    argv: list[str]
    stdin_path: Path
    env: dict[str, str]
    binds: list[Bind]
    shadow_writable: bool
    files: dict[str, str] = field(default_factory=dict)  # sandbox path -> description


def _check_forbidden(argv: Sequence[str]) -> None:
    bad = sorted(set(argv) & FORBIDDEN_ARGS)
    if bad:
        raise ValueError(f"refusing to build a runner command with bypass arguments {bad}")


def role_prompt(request: RunRequest, writable: Sequence[str]) -> str:
    """The task prompt with the role's operating rules."""
    roots = ", ".join(writable) or "(none)"
    return (
        f"You are the {request.role} agent working on feature task {request.task_id} in this repository.\n"
        f"You may write ONLY inside: {roots}. Everything else is read-only and writes there fail.\n"
        "Do not run Git commands that change state (add, commit, checkout, switch, branch, stash, reset, push);\n"
        "the coordinator commits your changes. Do not read, print or copy secrets or credentials.\n"
        "When you finish, reply with a short summary: files changed, checks you ran and their results, and\n"
        "anything that blocks you (for example an API another role must provide).\n\n"
        f"Task:\n{request.prompt}\n"
    )


def _home() -> Path:
    return Path(os.environ.get("HOME", str(Path.home())))


class ClaudeCodeLauncher:
    """Builds the Claude Code invocation for one task."""

    kind = ProviderKind.CLAUDE_CODE

    def __init__(self, profile: CliRunnerProfile, config: ProjectConfig, executable: str) -> None:
        self.profile = profile
        self.config = config
        self.executable = executable

    def settings(self, role: str) -> dict[str, Any]:
        """The generated Claude Code settings: file tools plus the role's Bash prefixes, deny the
        rest; every Bash command in Claude Code's strict nested sandbox."""
        commands = self.config.runtime.role_commands.get(role, ())
        return {"permissions": {"allow": [*CLAUDE_FILE_TOOLS, *(f"Bash({c})" for c in commands)],
                                "deny": list(CLAUDE_DENY), "defaultMode": "acceptEdits"},
                "sandbox": json.loads(json.dumps(CLAUDE_SANDBOX))}

    @staticmethod
    def broker_environment(token: str) -> dict[str, str]:
        """Gateway mode: the broker endpoint and this run's token (delivered through the channel)."""
        return {"ANTHROPIC_BASE_URL": BROKER_URL, "ANTHROPIC_AUTH_TOKEN": token}

    def prepare(self, request: RunRequest, run_dir: Path, writable: Sequence[str]) -> LaunchSpec:
        """Write the settings and prompt files and return the launch spec."""
        home = run_dir / "home"
        fc_dir = home / ".fc"
        fc_dir.mkdir(parents=True, exist_ok=True)
        settings_host = fc_dir / "claude-settings.json"
        settings_host.write_text(json.dumps(self.settings(request.role), indent=2), encoding="utf-8")
        prompt_path = run_dir / "prompt.txt"
        prompt_path.write_text(role_prompt(request, writable), encoding="utf-8")
        tools = list(CLAUDE_FILE_TOOLS)
        if self.config.runtime.role_commands.get(request.role):
            tools.append("Bash")
        settings_sandbox = str(_home() / ".fc" / "claude-settings.json")
        argv = [self.executable, "-p", "--output-format", "stream-json", "--verbose", "--restricted",
                "--tools", ",".join(tools), "--settings", settings_sandbox, "--strict-mcp-config",
                "--permission-mode", "acceptEdits", "--permission-prompts", "none", "--no-session-persistence",
                "--disable-slash-commands", "--max-budget-usd", f"{self.config.runtime.claude_max_budget_usd:g}"]
        if self.profile.model:
            argv += ["--model", self.profile.model]
        _check_forbidden(argv)
        env = {"DISABLE_AUTOUPDATER": "1", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"}
        binds = [Bind(str(install_prefix(self.executable)), str(install_prefix(self.executable)), "ro")]
        return LaunchSpec(argv, prompt_path, env, binds, shadow_writable=False,
                          files={settings_sandbox: "generated Claude Code settings"})

    @staticmethod
    def parse(stdout_path: Path) -> tuple[bool, str, dict[str, Any]]:
        """(ok, summary, details) from the stream-json output."""
        result: dict[str, Any] | None = None
        for line in stdout_path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict) and event.get("type") == "result":
                result = event
        if result is None:
            return False, "no result event in the Claude Code output", {}
        details = {k: result.get(k) for k in ("subtype", "num_turns", "total_cost_usd", "session_id",
                                              "duration_ms") if k in result}
        details["permission_denials"] = [d.get("tool_name") for d in result.get("permission_denials", ())
                                         if isinstance(d, dict)]
        return (not result.get("is_error", False)), str(result.get("result", ""))[:4000], details


class CodexLauncher:
    """Builds the Codex invocation for one task."""

    kind = ProviderKind.CODEX

    def __init__(self, profile: CliRunnerProfile, config: ProjectConfig, executable: str) -> None:
        self.profile = profile
        self.config = config
        self.executable = executable

    def prepare(self, request: RunRequest, run_dir: Path, writable: Sequence[str]) -> LaunchSpec:
        """Write the prompt file and return the launch spec."""
        (run_dir / "home" / ".codex").mkdir(parents=True, exist_ok=True)
        (run_dir / "home" / ".fc").mkdir(parents=True, exist_ok=True)
        prompt_path = run_dir / "prompt.txt"
        prompt_path.write_text(role_prompt(request, writable), encoding="utf-8")
        last_message = str(_home() / ".fc" / "last-message.txt")
        repo = str(request.workspace.policy.root)
        provider = f"model_providers.{CODEX_PROVIDER_ID}"
        argv = [self.executable, "exec", "--json", "--strict-config", "--sandbox", "workspace-write", "--ephemeral",
                "--ignore-user-config", "-C", repo, "-o", last_message,
                "-c", f'model_provider="{CODEX_PROVIDER_ID}"',
                "-c", f'{provider}.name="feature-coordinator broker"',
                "-c", f'{provider}.base_url="{BROKER_URL}/v1"',
                "-c", f'{provider}.env_key="{CODEX_TOKEN_ENV}"',
                "-c", f'{provider}.wire_api="responses"',
                "-c", 'web_search="disabled"',
                "-c", 'shell_environment_policy.inherit="core"',
                "-c", "shell_environment_policy.ignore_default_excludes=false",
                "-c", "sandbox_workspace_write.network_access=false"]
        if self.profile.model:
            argv += ["-m", self.profile.model]
        argv.append("-")
        _check_forbidden(argv)
        env = {"CODEX_HOME": str(_home() / ".codex")}
        binds = [Bind(str(install_prefix(self.executable)), str(install_prefix(self.executable)), "ro")]
        return LaunchSpec(argv, prompt_path, env, binds, shadow_writable=True,
                          files={last_message: "Codex last message"})

    @staticmethod
    def broker_environment(token: str) -> dict[str, str]:
        """The custom provider's key variable holds this run's token (delivered through the channel)."""
        return {CODEX_TOKEN_ENV: token}

    @staticmethod
    def parse(stdout_path: Path) -> tuple[bool, str, dict[str, Any]]:
        """(ok, summary, details) from the JSONL event stream."""
        failures: list[str] = []
        completed = False
        messages: list[str] = []
        for line in stdout_path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            kind = str(event.get("type", ""))
            if kind in ("turn.failed", "error"):
                failures.append(json.dumps(event.get("error", event))[:500])
            elif kind == "turn.completed":
                completed = True
            item = event.get("item")
            if kind == "item.completed" and isinstance(item, dict) and item.get("type") == "agent_message":
                messages.append(str(item.get("text", "")))
        details = {"events_completed": completed, "failures": failures}
        summary = (messages[-1] if messages else "")[:4000]
        return (completed and not failures), summary or "; ".join(failures), details


LAUNCHERS = {ProviderKind.CLAUDE_CODE: ClaudeCodeLauncher, ProviderKind.CODEX: CodexLauncher}


# ---------------------------------------------------------------------------
# The runner
# ---------------------------------------------------------------------------

def _fingerprint(path: Path) -> str | None:
    if path.is_symlink():
        return "symlink:" + os.readlink(path)
    if not path.exists():
        return None
    if path.is_dir():
        return "dir"
    from .gitops import file_digest  # pylint: disable=import-outside-toplevel
    return file_digest(path)


def snapshot(repo_root: Path, paths: Sequence[str]) -> dict[str, str | None]:
    """Content fingerprints of *paths* (None: absent)."""
    return {p: _fingerprint(repo_root / p) for p in paths}


def role_dirty_paths(repo_root: Path, roots: Sequence[str]) -> list[str]:
    """Modified, deleted or untracked (not ignored) paths under *roots* — read-only query."""
    if not roots:
        return []
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "LC_ALL": "C"}
    out = subprocess.run(["git", "-C", str(repo_root), "status", "--porcelain=v1", "-z", "--untracked-files=all",
                          "--", *roots], capture_output=True, text=True, env=env, check=True).stdout
    tokens = [t for t in out.split("\0") if t]
    paths: list[str] = []
    i = 0
    while i < len(tokens):
        entry = tokens[i]
        paths.append(entry[3:])
        i += 2 if entry[:1] in ("R", "C") else 1  # a rename carries its source path as the next token
    return sorted(set(paths))


BrokerFactory = Callable[[CliRunnerProfile, ProjectConfig, RunContext, Path], AuthBroker]


def broker_for_run(profile: CliRunnerProfile, config: ProjectConfig, ctx: RunContext, repo_root: Path) -> AuthBroker:
    """The production broker: the operator-configured upstream credential (none → raises
    BrokerUnavailableError) and the provider's approved HTTPS upstream. Its session expires
    shortly after the run's own deadline."""
    route = ROUTES[profile.kind]
    credential = configured_credential(config.broker, profile.name, route, forbidden_roots=(repo_root,))
    runtime = config.runtime
    ttl = (ctx.timeout_seconds or runtime.task_timeout_seconds) + runtime.terminate_grace_seconds + 60
    return AuthBroker(route, credential, config.broker, ttl_seconds=ttl, log_path=ctx.run_dir / "broker.log")


class _Handshake:  # pylint: disable=too-few-public-methods
    """supervise()'s on_start hook: take the sandbox's broker endpoint, start serving, send the
    runner's broker environment. On any failure the sandbox is killed at once."""

    def __init__(self, channel: BrokerChannel, broker: AuthBroker, environment: Mapping[str, str],
                 timeout: float) -> None:
        self.channel = channel
        self.broker = broker
        self.environment = environment
        self.timeout = timeout
        self.error = ""

    def __call__(self, pid: int) -> None:
        self.channel.started()
        try:
            self.broker.serve(self.channel.receive_listener(self.timeout))
            self.channel.send_environment(self.environment)
        except (BrokerError, OSError) as exc:
            self.error = str(exc)
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


class RealCliRunner:  # pylint: disable=too-many-instance-attributes,too-few-public-methods
    """Runs a real Claude Code or Codex process for one task: contained, network-isolated,
    brokered and supervised."""

    def __init__(self, profile: CliRunnerProfile, config: ProjectConfig, *, bwrap: str | None = None,  # pylint: disable=too-many-arguments
                 probe: RunnerProbe | None = None, broker_factory: BrokerFactory | None = None,
                 dependency_check: Callable[[ProviderKind], list[str]] = missing_dependencies) -> None:
        self.profile = profile
        self.name = profile.name
        self.kind = profile.kind
        self.config = config
        self.bwrap = bwrap
        self.broker_factory = broker_factory or broker_for_run
        self.dependency_check = dependency_check
        self._probe = probe

    def probe(self) -> RunnerProbe:
        """The (cached) capability probe."""
        if self._probe is None:
            self._probe = probe_runner(self.profile)
        return self._probe

    def _preflight(self) -> tuple[str, str]:
        """(bwrap path, '') when everything a run needs exists, else ('', reason)."""
        probe = self.probe()
        if not probe.ok:
            flags = f"{probe.resolved} lacks required flags {list(probe.missing_flags)}" if probe.missing_flags else ""
            return "", "; ".join(part for part in (flags, probe.error) if part)
        missing = self.dependency_check(self.kind)
        if missing:
            return "", f"missing sandbox dependencies {missing} (install them; there is no unsandboxed mode)"
        try:
            return check_bwrap(self.bwrap), ""
        except SandboxUnavailableError as exc:
            return "", str(exc)

    def run(self, request: RunRequest) -> RunResult:
        """Launch the CLI for one task inside its sandbox and report the outcome. The broker
        exists only for this call and is stopped (its token revoked) however the run ends."""
        bwrap, reason = self._preflight()
        if reason:
            return RunResult(self.name, "unavailable", error=reason)
        ctx = request.context
        if ctx is None:
            return RunResult(self.name, "failed", error="a real runner needs a RunContext (run directory)")
        try:
            broker = self.broker_factory(self.profile, self.config, ctx, request.workspace.policy.root)
        except BrokerError as exc:
            return RunResult(self.name, "unavailable", error=str(exc))
        try:
            return self._run(request, ctx, bwrap, broker)
        finally:
            broker.stop()

    def _run(self, request: RunRequest, ctx: RunContext, bwrap: str,  # pylint: disable=too-many-locals
             broker: AuthBroker) -> RunResult:
        policy: PathPolicy = request.workspace.policy
        builder = SandboxBuilder(policy.root, self.config, policy, bwrap=bwrap)
        writable = builder.writable_roots(request.role, request.workspace.grants)
        launcher = LAUNCHERS[self.kind](self.profile, self.config, self.probe().resolved)
        spec = launcher.prepare(request, ctx.run_dir, writable)
        channel = BrokerChannel()
        handshake = _Handshake(channel, broker, launcher.broker_environment(broker.session.token),
                               self.config.broker.handshake_timeout_seconds)
        try:
            plan = builder.plan(run_dir=ctx.run_dir, role=request.role, grants=request.workspace.grants,
                                shadow_writable=spec.shadow_writable, env=spec.env, extra=[*spec.binds, channel.bind()],
                                hide=[ctx.run_dir.parent.parent])
            argv = plan.argv(channel.command(spec.argv))
            (ctx.run_dir / "sandbox.json").write_text(json.dumps({
                "argv": argv, "writable": plan.writable, "readonly_overlays": plan.readonly_overlays,
                "masked": plan.masked, "hidden": plan.hidden, "network": "own namespace; broker endpoint only",
                "broker": {"endpoint": BROKER_URL, "upstream": broker.upstream.host}}, indent=2), encoding="utf-8")
            before = snapshot(policy.root, role_dirty_paths(policy.root, writable))
            runtime = self.config.runtime
            sup = supervise(
                argv, cwd=policy.root, env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
                stdout_path=ctx.run_dir / "stdout.jsonl", stderr_path=ctx.run_dir / "stderr.log",
                stdin_path=spec.stdin_path, timeout_seconds=ctx.timeout_seconds or runtime.task_timeout_seconds,
                heartbeat=(lambda: ctx.heartbeat({"run_dir": str(ctx.run_dir)})) if ctx.heartbeat else None,
                heartbeat_interval=runtime.heartbeat_interval_seconds, cancelled=ctx.cancelled,
                grace_seconds=runtime.terminate_grace_seconds, pass_fds=(channel.child_fd,), on_start=handshake)
        finally:
            channel.close()
        broker.stop()
        strays, residue = plan.stray_writes()
        plan.discard_shadow()
        after = role_dirty_paths(policy.root, writable)
        allowed = [p for p in after if policy.check(request.role, p, grants=request.workspace.grants).allowed]
        now = snapshot(policy.root, sorted(set(after) | set(before)))
        touched = sorted(p for p in now if now[p] != before.get(p, "<absent>") or p not in before)
        denied = sorted(p for p in touched if p not in allowed and now[p] is not None)
        ok, summary, details = launcher.parse(ctx.run_dir / "stdout.jsonl")
        details.update(exit_code=sup.exit_code, duration_seconds=round(sup.duration_seconds, 1),
                       escalated_to_kill=sup.escalated_to_kill, run_dir=str(ctx.run_dir),
                       changed_during_run=touched, preexisting=sorted(before),
                       shadow_residue=residue, broker=broker.describe())
        result = RunResult(self.name, "completed", allowed, transcript=[summary] if summary else [],
                           stray_writes=strays, details=details)
        if sup.status in ("timeout", "cancelled"):
            result.status, result.error = sup.status, f"runner {sup.status} after {sup.duration_seconds:.0f}s"
        elif handshake.error:
            result.status, result.error = "failed", f"sandbox start-up failed: {handshake.error}"
        elif strays:
            result.status, result.error = "rejected", f"wrote outside its roots (discarded): {strays}"
        elif denied:
            result.status, result.error = "rejected", f"changed paths the role may not commit: {denied}"
        elif not sup.ok or not ok:
            result.status, result.error = "failed", f"runner exit {sup.exit_code}: {summary[:500]}"
        return result


def run_context(run_root: Path, task_id: str, **kwargs: Any) -> RunContext:
    """A fresh run directory under *run_root* for one attempt at *task_id*."""
    from .model import utc_now  # pylint: disable=import-outside-toplevel
    stamp = re.sub(r"[^0-9]", "", utc_now())[:17]
    run_dir = run_root / f"{task_id}.{stamp}.{os.urandom(3).hex()}"
    run_dir.mkdir(parents=True, mode=0o700)
    return RunContext(run_dir=run_dir, **kwargs)
