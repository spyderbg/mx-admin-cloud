"""The `native-cli` execution backend (W2): the installed Claude Code / Codex CLIs, run directly
in the selected workspace with the operator's EXISTING SUBSCRIPTION logins.

THIS IS NOT A SECURITY SANDBOX. The CLI and every command it runs have the operator's full
file-system and network access. Role write scopes are checked AFTER the run (and Claude Code's
own permission rules apply while it runs); they do not prevent access outside the workspace.
For OS-level containment use the `bwrap` backend (launchers.RealCliRunner); W1 (PR #20)
explores credential and network isolation separately. Opt-in `runtime.native_hardening`
(native_hardening.py, W1 decision A) confines the agent TOOLS — Claude Code without Bash, Codex
commands in a credential-free, offline permission profile — and fails closed when that cannot be
enforced; the CLI processes themselves stay unconfined.

What the backend guarantees:

Subscription authentication — fail closed, never a silent switch to API billing:
  * the CLI's environment is built from an ALLOWLIST (NATIVE_ENV_KEEP). API keys, auth tokens,
    alternative base URLs, cloud-provider switches and the variables of an enclosing Claude Code
    session are never passed, whatever the operator's shell holds;
  * before a run, the CLI's own status command, run with that environment, must report the
    subscription login: `claude auth status --json` → logged in, `authMethod` "claude.ai",
    first-party API; `codex login status` → "Logged in using ChatGPT". An API-key login is
    refused; anything not recognized needs operator confirmation (the run is refused). Only the
    method is recorded — never the raw output, which can carry an e-mail address or a masked key;
  * Claude Code runs with `--restricted` (user/project settings — including an `apiKeyHelper` —
    are ignored); Codex with `--ignore-user-config`, `--strict-config` and
    `forced_login_method="chatgpt"`;
  * after a run, Claude Code's `init` event must show no API key (`apiKeySource: none`) and a
    model of the requested family; otherwise the run fails ("model substitution").

Supervision: the existing supervisor (process group, heartbeats, operator stop, timeout,
SIGTERM → grace → SIGKILL). Without a PID namespace, descendants that leave the process group
are found by a per-run environment tag (FC_RUN_TAG) and killed afterwards (best effort).
W3: the tag is recorded in the task's claim before the agent starts, and the agent's process
identity (pid + start time + boot id) right after, so recovery can tell whether an agent of an
interrupted run still lives (claims.recover() then refuses to reclaim the task).

Acceptance (unchanged rules, workspace-wide here): every path the run changed anywhere in the
checkout — tracked, untracked, and protected ignored files such as `.env` — must be writable by
the role, and the Git state must be untouched: same branch, no ref or stash change, nothing
staged, and new commits on the branch only from the Git coordinator (its `Feature-Task:`
trailer). Otherwise the run is REJECTED and the changes are LEFT IN PLACE for the operator
(never silently discarded). Only the Git coordinator commits, from the role's own paths.

Concurrent tasks share the checkout. A change inside the roots of ANOTHER role that held a claim
during this run (RunContext.concurrent_roles) is that role's to judge and commit, so it is not
held against this run. Without OS isolation the backend cannot tell which agent wrote such a
change: a write by one agent into another running role's paths would be committed as that
role's work (a documented limitation; the `bwrap` backend has no such ambiguity).
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import stat
import subprocess
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from .config import ProjectConfig
from .errors import CoordinatorError, GitConfigExecutionError
from .gitsafe import assert_no_execution_config, git_argv, hardened_env
from .launchers import LAUNCHERS, RunnerProbe, probe_runner, snapshot
from .locks import ProcessIdentity
from .native_hardening import (
    agent_config_anchors,
    claude_init_problems,
    codex_enforcement_error,
    git_control_digest,
    preflight,
)
from .native_hardening import summary as hardening_summary
from .paths import PathPolicy, matches
from .providers import CliRunnerProfile, ProviderKind, model_family
from .python_env import PythonInterpreter
from .runners import RunContext, RunRequest, RunResult
from .sandbox import SandboxBuilder
from .supervisor import RUN_TAG_ENV, kill_tagged, supervise
from .workspace import git_operation_in_progress

BACKEND = "native-cli"
RUN_TAG = RUN_TAG_ENV
# Passed through from the operator's environment, when set. Nothing else is.
NATIVE_ENV_KEEP = (
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LANGUAGE", "LC_ALL", "LC_CTYPE", "LC_MESSAGES", "TZ",
    "TMPDIR", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_RUNTIME_DIR",
    "CLAUDE_CONFIG_DIR", "CODEX_HOME",  # where the CLIs keep their own login (never read by the coordinator)
    "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "https_proxy", "http_proxy", "no_proxy",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "NODE_EXTRA_CA_CERTS",
)
NATIVE_ENV_SET = {"TERM": "dumb", "NO_COLOR": "1", "GIT_TERMINAL_PROMPT": "0", "PYTHONDONTWRITEBYTECODE": "1"}
CLAUDE_API_KEY_METHODS = ("api_key", "apikey", "api-key", "console", "apiKeyHelper")


class SubscriptionAuthError(CoordinatorError):
    """The CLI is not authenticated with the operator's subscription (or that cannot be determined)."""


# ---------------------------------------------------------------------------
# Environment and subscription authentication
# ---------------------------------------------------------------------------

def native_environment(interpreter: PythonInterpreter | None = None, *, tag: str = "",
                       source: Mapping[str, str] | None = None) -> dict[str, str]:
    """The CLI's environment: the allowlist from *source* (default: os.environ), fixed settings,
    the interpreter's virtual environment first on PATH, and the run tag."""
    source = os.environ if source is None else source
    env = {key: source[key] for key in NATIVE_ENV_KEEP if source.get(key)}
    env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    env.update(NATIVE_ENV_SET)
    if interpreter is not None and interpreter.venv_bin:
        env["PATH"] = os.pathsep.join([interpreter.venv_bin, env["PATH"]])
    if tag:
        env[RUN_TAG] = tag
    return env


@dataclass(frozen=True)
class SubscriptionCheck:
    """What a CLI's status command says about its login (never the raw output)."""

    ok: bool
    method: str
    detail: str = ""


def _claude_status(data: Mapping[str, Any]) -> SubscriptionCheck:
    method, provider = str(data.get("authMethod", "")), str(data.get("apiProvider", ""))
    plan = str(data.get("subscriptionType") or "")
    if data.get("loggedIn") is not True:
        return SubscriptionCheck(False, "not logged in", "Claude Code is not logged in (run `claude` and log in "
                                                         "with the claude.ai subscription; not done automatically)")
    if method.lower() in (m.lower() for m in CLAUDE_API_KEY_METHODS):
        return SubscriptionCheck(False, f"API key ({method})", "Claude Code is using API-key authentication; "
                                 "only the claude.ai subscription is permitted (no pay-per-token billing)")
    if method == "claude.ai" and provider == "firstParty":
        return SubscriptionCheck(True, f"claude.ai subscription ({plan or 'plan not reported'})")
    return SubscriptionCheck(False, f"undetermined (authMethod {method or '?'}, apiProvider {provider or '?'})",
                             "cannot determine that Claude Code bills the subscription; operator confirmation "
                             "is required before any model request")


def _codex_status(text: str) -> SubscriptionCheck:
    if "Logged in using ChatGPT" in text:
        return SubscriptionCheck(True, "ChatGPT subscription")
    if "API key" in text:
        return SubscriptionCheck(False, "API key", "Codex is logged in with an API key; only the ChatGPT "
                                 "subscription is permitted (no pay-per-token billing)")
    if "Not logged in" in text:
        return SubscriptionCheck(False, "not logged in", "Codex is not logged in (run `codex login` with the "
                                                         "ChatGPT subscription; not done automatically)")
    return SubscriptionCheck(False, "undetermined", "cannot determine Codex's login method; operator "
                                                    "confirmation is required before any model request")


def check_subscription(kind: ProviderKind, executable: str, env: Mapping[str, str], *,
                       timeout: float = 60.0) -> SubscriptionCheck:
    """Ask the CLI's own status command (no model request, no credential read by us)."""
    argv = [executable, "auth", "status", "--json"] if kind is ProviderKind.CLAUDE_CODE else \
        [executable, "login", "status"]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, env=dict(env), timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return SubscriptionCheck(False, "undetermined", f"the status command failed: {type(exc).__name__}")
    if kind is ProviderKind.CODEX:
        return _codex_status(proc.stdout + proc.stderr)
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        return SubscriptionCheck(False, "undetermined", "`claude auth status --json` did not answer with JSON; "
                                                        "operator confirmation is required")
    return _claude_status(data if isinstance(data, dict) else {})


# ---------------------------------------------------------------------------
# Workspace state: what a run changed, anywhere in the checkout
# ---------------------------------------------------------------------------

def _git(root: Path, *args: str, check: bool = True) -> str:
    # gitsafe: repository-provided fsmonitor/hooks/filters can never run during these queries.
    return subprocess.run(git_argv(root, *args), capture_output=True, text=True, check=check,
                          env=hardened_env()).stdout


@dataclass(frozen=True)
class GitState:
    """What only the Git coordinator may change."""

    branch: str                 # the checked-out branch ('' when detached)
    head: str
    refs: tuple[str, ...]       # every ref except the checked-out branch, with its object
    staged: tuple[str, ...]
    stash: str


@dataclass
class WorkspaceState:
    """Fingerprints of everything a run could change, taken before and after it."""

    dirty: dict[str, str | None]        # tracked and untracked (not ignored) changes
    protected_ignored: dict[str, str | None]  # ignored files the policy protects (e.g. .env)
    ignored: frozenset[str]             # every ignored entry (informational)
    git: GitState


def _status(root: Path) -> tuple[list[str], list[str]]:
    out = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignored=traditional")
    tokens = [t for t in out.split("\0") if t]
    dirty, ignored = [], []
    i = 0
    while i < len(tokens):
        entry = tokens[i]
        (ignored if entry.startswith("!!") else dirty).append(entry[3:])
        i += 2 if entry[:1] in ("R", "C") else 1
    return dirty, ignored


def git_state(root: Path) -> GitState:
    """The Git state an agent must not change (read-only queries)."""
    branch = _git(root, "symbolic-ref", "-q", "HEAD", check=False).strip()
    refs = tuple(line for line in _git(root, "for-each-ref", "--format=%(refname) %(objectname)").splitlines()
                 if line.split(" ", 1)[0] != branch)
    return GitState(branch, _git(root, "rev-parse", "HEAD").strip(), refs,
                    tuple(_git(root, "diff", "--cached", "--name-only").split()),
                    hashlib.sha256(_git(root, "stash", "list").encode()).hexdigest())


def git_problems(root: Path, before: GitState, after: GitState) -> list[str]:
    """How the Git state changed in ways only the Git coordinator may change it."""
    problems = []
    if after.branch != before.branch:
        problems.append(f"the checked-out branch changed ({before.branch or 'detached'} → "
                        f"{after.branch or 'detached'})")
    if after.refs != before.refs:
        problems.append("other refs (branches, tags) changed")
    if after.staged and after.staged != before.staged:
        problems.append(f"content was staged in the shared index {list(after.staged)[:10]}")
    if after.stash != before.stash:
        problems.append("the stash changed")
    if after.head != before.head and not problems:
        if subprocess.run(git_argv(root, "merge-base", "--is-ancestor", before.head, after.head),
                          capture_output=True, env=hardened_env(), check=False).returncode != 0:
            problems.append("the branch history was rewritten")
        else:
            for sha in _git(root, "rev-list", f"{before.head}..{after.head}").split():
                if not _git(root, "log", "-1", "--format=%(trailers:key=Feature-Task,valueonly)", sha).strip():
                    problems.append(f"commit {sha[:12]} was not made by the Git coordinator")
    return problems


def workspace_state(root: Path, config: ProjectConfig) -> WorkspaceState:
    """The current state of *root* (read-only Git queries; optional locks off)."""
    dirty, ignored = _status(root)
    protected = [p for p in ignored if not p.endswith("/") and
                 any(matches(pattern, p) for pattern in (*config.protected, *config.runtime.secret_patterns))]
    return WorkspaceState(snapshot(root, dirty), snapshot(root, protected), frozenset(ignored), git_state(root))


def changed_paths(before: WorkspaceState, after: WorkspaceState) -> tuple[list[str], list[str], list[str]]:
    """(changed paths, changed protected ignored files, new ignored entries) between two states.
    A path that is clean (equal to HEAD) on one side counts as `<clean>` there, so a path that
    became dirty, changed again, or was restored to HEAD during the run is reported."""
    changed = sorted(p for p in set(before.dirty) | set(after.dirty)
                     if before.dirty.get(p, "<clean>") != after.dirty.get(p, "<clean>"))
    protected = sorted(p for p in set(before.protected_ignored) | set(after.protected_ignored)
                       if before.protected_ignored.get(p, "<absent>") != after.protected_ignored.get(p, "<absent>"))
    return changed, protected, sorted(after.ignored - before.ignored)


def _inert_anchor(path: Path) -> bool:
    """True when *path* is a trusted hardened-launcher rule anchor in inert form: absent, an empty
    regular file, or a directory (any real content an agent put *under* a directory anchor shows up
    as its own path and is judged separately). A symlink or a non-empty file is NOT inert — it is a
    real change and stays denied, so a genuine write to a protected path is never excused."""
    try:
        st = path.lstat()
    except OSError:
        return True  # absent — nothing was left behind
    if stat.S_ISLNK(st.st_mode):
        return False
    if stat.S_ISDIR(st.st_mode):
        return True
    return stat.S_ISREG(st.st_mode) and st.st_size == 0


# ---------------------------------------------------------------------------
# The runner
# ---------------------------------------------------------------------------

def _init_event(stdout_path: Path) -> dict[str, Any]:
    for line in stdout_path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("type") == "system" and event.get("subtype") == "init":
            return event
    return {}


def claude_session_problems(stdout_path: Path, requested_model: str) -> tuple[list[str], dict[str, Any]]:
    """Post-run checks of Claude Code's init event: no API key, the requested model family."""
    init = _init_event(stdout_path)
    if not init:
        return [], {}
    facts = {"reported_model": init.get("model"), "api_key_source": init.get("apiKeySource")}
    problems = []
    if init.get("apiKeySource") not in (None, "none"):
        problems.append(f"Claude Code used an API key ({init.get('apiKeySource')}), not the subscription")
    family = model_family(requested_model) if requested_model else ()
    reported = str(init.get("model") or "")
    if family and not any(f in reported for f in family):
        problems.append(f"model substitution: requested {requested_model!r}, Claude Code reported {reported!r}")
    elif requested_model and not family and reported and reported != requested_model:
        problems.append(f"model substitution: requested {requested_model!r}, Claude Code reported {reported!r}")
    return problems, facts


class NativeCliRunner:  # pylint: disable=too-many-instance-attributes,too-few-public-methods
    """Runs a real Claude Code or Codex process for one task, natively, with the subscription login."""

    backend = BACKEND

    def __init__(self, profile: CliRunnerProfile, config: ProjectConfig, *, probe: RunnerProbe | None = None,  # pylint: disable=too-many-arguments
                 auth_check: Callable[..., SubscriptionCheck] = check_subscription,
                 hardening_check: Callable[..., str] = preflight,
                 enforcement_check: Callable[..., str] = codex_enforcement_error) -> None:
        self.profile = profile
        self.name = profile.name
        self.kind = profile.kind
        self.config = config
        self.auth_check = auth_check
        self.hardening_check = hardening_check      # native hardening: host / version / policy preflight
        self.enforcement_check = enforcement_check  # native hardening: Codex permission-profile self-test
        self._probe = probe
        self._auth: SubscriptionCheck | None = None

    @property
    def hardened(self) -> bool:
        """True when `runtime.native_hardening` is enabled (native_hardening.py)."""
        return self.config.runtime.native_hardening.enabled

    def environment(self, interpreter: PythonInterpreter | None = None, tag: str = "") -> dict[str, str]:
        """native_environment(), with a hardened Codex's Worker-dedicated CODEX_HOME."""
        env = native_environment(interpreter, tag=tag)
        hardening = self.config.runtime.native_hardening
        if self.hardened and self.kind is ProviderKind.CODEX and hardening.codex_home:
            env["CODEX_HOME"] = hardening.codex_home
        return env

    def probe(self) -> RunnerProbe:
        """The (cached) capability probe: --version and --help only."""
        if self._probe is None:
            self._probe = probe_runner(self.profile)
        return self._probe

    def subscription(self) -> SubscriptionCheck:
        """The (cached) subscription check, with exactly the environment runs get."""
        if self._auth is None:
            self._auth = self.auth_check(self.kind, self.probe().resolved, self.environment())
        return self._auth

    def _unavailable(self) -> str:
        probe = self.probe()
        if not probe.ok:
            return probe.error or f"{probe.resolved} lacks required flags {list(probe.missing_flags)}"
        auth = self.subscription()
        return "" if auth.ok else f"subscription authentication: {auth.detail}"

    def run(self, request: RunRequest) -> RunResult:  # pylint: disable=too-many-locals,too-many-return-statements
        """Launch the CLI for one task in the workspace, supervise it and judge what it changed."""
        reason = self._unavailable()
        if reason:
            return RunResult(self.name, "unavailable", error=reason)
        ctx = request.context
        if ctx is None:
            return RunResult(self.name, "failed", error="a real runner needs a RunContext (run directory)")
        policy: PathPolicy = request.workspace.policy
        root = policy.root
        launcher = LAUNCHERS[self.kind](self.profile, self.config, self.probe().resolved, native=True)
        try:
            interpreter = launcher.interpreter(request.role, root)
        except CoordinatorError as exc:
            return RunResult(self.name, "unavailable", error=str(exc))
        ongoing = git_operation_in_progress(root)
        if ongoing:
            return RunResult(self.name, "failed", error=f"the workspace has an unfinished Git operation {ongoing}")
        try:  # gitsafe: refuse a checkout whose own Git config would run a program (both backends)
            assert_no_execution_config(root, where="the workspace")
        except GitConfigExecutionError as exc:
            return RunResult(self.name, "rejected", error=str(exc))
        hardening = self.config.runtime.native_hardening
        if self.hardened:  # fail closed: never a silent fallback to the unhardened command line
            reason = self.hardening_check(self.kind, self.probe().version, hardening, env=self.environment(),
                                          workspace=root)
            if reason:
                return RunResult(self.name, "unavailable", error=f"native hardening: {reason}")
        writable = SandboxBuilder(root, self.config, policy).writable_roots(request.role, request.workspace.grants)
        spec = launcher.prepare(request, ctx.run_dir, writable)
        tag = secrets.token_hex(8)
        env = {**self.environment(interpreter, tag=tag), **spec.env}
        if self.hardened and self.kind is ProviderKind.CODEX:
            reason = self.enforcement_check(self.probe().resolved, launcher.profile_args(root, ctx.run_dir),
                                            ctx.run_dir / "selftest", env)
            if reason:
                return RunResult(self.name, "unavailable", error=f"native hardening: {reason}")
        model = launcher.model(request.role)
        (ctx.run_dir / "native.json").write_text(json.dumps({
            "backend": BACKEND, "security_sandbox": False, "hardening": hardening_summary(self.kind, hardening),
            "argv": spec.argv, "cwd": str(root), "run_tag": tag,
            "environment_names": sorted(env), "model": model or "CLI default", "writable": writable,
            "authentication": self.subscription().method}, indent=2), encoding="utf-8")
        before = self._state(root, ctx)
        control = git_control_digest(root) if self.hardened else ""
        sup = self._supervise(spec.argv, root, env, spec.stdin_path, ctx, tag)
        leftovers = kill_tagged(RUN_TAG, tag)
        result = self._judge(request, ctx, launcher, model, before, sup, leftovers, writable)
        return self._judge_hardened(result, root, control, launcher, request.role) if self.hardened else result

    def _judge_hardened(self, result: RunResult, root: Path, control: str, launcher: Any,  # pylint: disable=too-many-arguments
                        role: str) -> RunResult:
        """Native hardening's own post-run checks: the Git control files are unchanged (an agent
        could otherwise run code in a later unsandboxed Git command, e.g. `core.fsmonitor`), and
        Claude Code ran with exactly the hardened tools and no MCP server."""
        result.details["hardening"] = hardening_summary(self.kind, self.config.runtime.native_hardening)
        problems = []
        if self.kind is ProviderKind.CLAUDE_CODE:
            problems = claude_init_problems(_init_event(Path(result.details["run_dir"]) / "stdout.jsonl"),
                                            launcher.tools(role))
        if git_control_digest(root) != control:
            result.status, result.error = "rejected", ("the run changed Git control files (.git/config, hooks or "
                                                       "info); left as is for the operator — review before any Git "
                                                       "command runs in this checkout")
        elif problems and result.status not in ("timeout", "cancelled", "rejected"):
            result.status, result.error = "failed", "native hardening: " + "; ".join(problems)
        return result

    def _state(self, root: Path, ctx: RunContext) -> WorkspaceState:
        """A workspace snapshot taken while no Git coordinator commit is in flight (a commit
        moves the branch first and refreshes the shared index after)."""
        with ctx.git_quiet() if ctx.git_quiet else nullcontext():
            return workspace_state(root, self.config)

    def _supervise(self, argv: list[str], root: Path, env: dict[str, str], stdin_path: Path, ctx: RunContext,  # pylint: disable=too-many-arguments
                   tag: str) -> Any:
        runtime = self.config.runtime
        beat: dict[str, Any] = {"run_dir": str(ctx.run_dir), "run_tag": tag}

        def on_start(pid: int) -> None:
            identity = ProcessIdentity.of_pid(pid)
            if identity is not None:
                beat["agent"] = identity.to_dict()
            if ctx.heartbeat:
                ctx.heartbeat(dict(beat))
        if ctx.heartbeat:
            ctx.heartbeat(dict(beat))  # the tag is known to recovery before the agent exists
        return supervise(
            argv, cwd=root, env=env, stdout_path=ctx.run_dir / "stdout.jsonl", stderr_path=ctx.run_dir / "stderr.log",
            stdin_path=stdin_path, timeout_seconds=ctx.timeout_seconds or runtime.task_timeout_seconds,
            heartbeat=(lambda: ctx.heartbeat(dict(beat))) if ctx.heartbeat else None,
            heartbeat_interval=runtime.heartbeat_interval_seconds, cancelled=ctx.cancelled,
            grace_seconds=runtime.terminate_grace_seconds, on_start=on_start)

    def _judge(self, request: RunRequest, ctx: RunContext, launcher: Any, model: str,  # pylint: disable=too-many-arguments,too-many-locals
               before: WorkspaceState, sup: Any, leftovers: list[int], writable: list[str]) -> RunResult:
        policy: PathPolicy = request.workspace.policy
        root, grants = policy.root, request.workspace.grants
        after = self._state(root, ctx)
        touched, protected_touched, new_ignored = changed_paths(before, after)
        concurrent = ctx.concurrent_roles() if ctx.concurrent_roles else set()
        not_ours = {p for p in touched if not policy.check(request.role, p, grants=grants).allowed}
        theirs = sorted(p for p in not_ours if policy.owner_of(p) in concurrent)  # protected paths have no owner
        denied = sorted((not_ours - set(theirs)) | set(protected_touched))
        # Hardened runs: the trusted CLI materialises inert, read-only agent-config anchors
        # (.claude, .mcp.json, .codex, .agents) in the shared checkout to carry its own permission
        # rules; concurrent roles see each other's. The agent is denied writing these, so an inert
        # instance is never an agent change — exclude it (a non-inert one, i.e. real content, stays
        # denied). This neither ignores the paths globally nor commits them (they are not writable).
        anchors = sorted(p for p in denied
                         if self.hardened and p in agent_config_anchors() and _inert_anchor(root / p))
        if anchors:
            denied = [p for p in denied if p not in set(anchors)]
        git_issues = git_problems(root, before.git, after.git)
        from .launchers import role_dirty_paths  # pylint: disable=import-outside-toplevel
        allowed = [p for p in role_dirty_paths(root, writable)
                   if policy.check(request.role, p, grants=grants).allowed]
        ok, summary, details = launcher.parse(ctx.run_dir / "stdout.jsonl")
        problems, facts = (claude_session_problems(ctx.run_dir / "stdout.jsonl", model)
                           if self.kind is ProviderKind.CLAUDE_CODE else ([], {}))
        details.update(facts, backend=BACKEND, security_sandbox=False, model_requested=model or "CLI default",
                       authentication=self.subscription().method, exit_code=sup.exit_code,
                       duration_seconds=round(sup.duration_seconds, 1), escalated_to_kill=sup.escalated_to_kill,
                       run_dir=str(ctx.run_dir), changed_during_run=touched, ignored_entries_created=new_ignored,
                       left_to_concurrent_roles=theirs, concurrent_roles=sorted(concurrent),
                       launcher_anchors_ignored=anchors, leftover_processes_killed=len(leftovers))
        result = RunResult(self.name, "completed", allowed, transcript=[summary] if summary else [], details=details)
        if sup.status in ("timeout", "cancelled"):
            result.status, result.error = sup.status, f"runner {sup.status} after {sup.duration_seconds:.0f}s"
        elif git_issues:
            result.status, result.error = "rejected", (f"the agent changed the Git state ({'; '.join(git_issues)}); "
                                                       "left as is for the operator — only the coordinator mutates Git")
        elif denied:
            result.status, result.error = "rejected", (f"changed paths the role may not write (left in place for "
                                                       f"review, not committed): {denied}")
        elif problems:
            result.status, result.error = "failed", "; ".join(problems)
        elif not sup.ok or not ok:
            result.status, result.error = "failed", f"runner exit {sup.exit_code}: {summary[:500]}"
        return result
