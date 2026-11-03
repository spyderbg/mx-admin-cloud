"""Phase 3 operator commands for `python -m feature_coordinator` (registered by cli.build_parser)."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Any

from .broker import ROUTES, configured_credential
from .broker_config import ignored_settings
from .config import ProjectConfig
from .errors import BrokerUnavailableError, CoordinatorError
from .gates import GateContext, SandboxedGateRunner, gate_cwd
from .launchers import LAUNCHERS, missing_dependencies, probe_runner, version_floor_error
from .lint_baseline import lint_report
from .native_backend import NativeCliRunner, check_subscription

from .paths import PathPolicy, ScopedWorkspace
from .providers import CliRunnerProfile, OpenAICompatibleProfile
from .runners import RunRequest
from .sandbox import SandboxBuilder, SandboxUnavailableError, bwrap_version, check_bwrap
from .statedir import ensure_state_dir, resolve_state_dir

# What `runtime.native_hardening` covers (native_hardening.py), for `runners` and `worker-check`.
HARDENING_SCOPE = ("agent tools confined: Claude Code without Bash, Codex commands in the credential-free, "
                   "offline fc_worker profile; the CLI processes keep host network and their own logins")


def _print(text: str = "") -> None:
    sys.stdout.write(text + "\n")


def _state(args: argparse.Namespace, config: ProjectConfig) -> Path:
    path = Path(args.state_dir) if args.state_dir else resolve_state_dir(app_dir=config.state_app_dir)
    return ensure_state_dir(path, allowed=config.allowed_state_filesystems)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True,
                          env={"GIT_OPTIONAL_LOCKS": "0", "PATH": "/usr/bin:/bin"}).stdout.strip()


# -- inspection -----------------------------------------------------------------------

def cmd_runners(args: argparse.Namespace, config: ProjectConfig) -> int:
    """Probe the installed runner CLIs and bwrap; show the planned invocation per role (no model call)."""
    ok = True
    try:
        bwrap = check_bwrap()
        _print(f"bubblewrap: {bwrap_version(bwrap)} — namespaces available")
    except SandboxUnavailableError as exc:
        ok = False
        _print(f"bubblewrap: UNAVAILABLE — {exc}")
    repo = Path(args.repo)
    policy = PathPolicy(repo, config)
    probed: dict[str, Any] = {}
    native = config.runtime.execution_backend == "native-cli"
    _print(f"execution backend: {config.runtime.execution_backend}"
           f"{' — the installed CLIs with their subscription logins; NOT a security sandbox' if native else ''}")
    for role, role_cfg in config.roles.items():
        profile = config.providers[role_cfg.provider]
        if not role_cfg.enabled:
            _print(f"{role:12s} → {profile.name}: role DISABLED (roles.{role}.enabled: false)")
            continue
        if isinstance(profile, OpenAICompatibleProfile):
            _print(f"{role:12s} → {profile.name}: OpenAI-compatible; HTTP transport loopback-only "
                   "(live remote execution disabled in Phase 3)")
            continue
        assert isinstance(profile, CliRunnerProfile)
        probe = probed.setdefault(profile.name, probe_runner(profile))
        floor = "" if native or not probe.ok else version_floor_error(probe)  # W1: the bwrap backend's floor
        status = ("OK" if probe.ok and not floor else
                  f"UNAVAILABLE ({floor or probe.error or 'missing ' + ', '.join(probe.missing_flags)})")
        ok = ok and probe.ok and not floor
        _print(f"{role:12s} → {profile.name}: {probe.version or '-'} at {probe.resolved or '-'}: {status}; "
               f"model {config.model_for(role) or 'CLI default'}")
        if probe.ok and native:
            env = NativeCliRunner(profile, config, probe=probe).environment()  # a hardened Codex's own CODEX_HOME
            auth = probed.setdefault(f"auth:{profile.name}", check_subscription(profile.kind, probe.resolved, env))
            ok = ok and auth.ok
            _print(f"{'':15s}authentication: {auth.method}{'' if auth.ok else ' — REFUSED: ' + auth.detail}")
        if probe.ok:
            launcher = LAUNCHERS[profile.kind](profile, config, probe.resolved, native=native)
            roots = SandboxBuilder(repo, config, policy).writable_roots(role)
            request = RunRequest(f"example.{role}", role, "(task prompt)", ScopedWorkspace(policy, role))
            spec = launcher.prepare(request, Path(args.scratch or _state(args, config) / "tmp" / f"plan-{role}"), roots)
            _print(f"{'':15s}argv: {' '.join(spec.argv)}")
    if native and config.runtime.native_hardening.enabled:
        _print(f"native hardening: ON ({HARDENING_SCOPE}); checked again before every run, fail closed")
    if native:
        _print("W1 isolation (D-20/D-21): NOT APPLIED — the native-cli backend runs agents with the operator's "
               "files, logins and network; see `worker-check --apply` for the bwrap backend")
    else:
        for name, reason in _real_run_blockers(config, repo).items():
            _print(f"{name:12s} real runs: UNAVAILABLE — {reason}")
        _print("network: every runner sandbox has its own namespace; the model is reached only through the broker")
    for note in ignored_settings(config):
        _print(f"note: {note}")
    return 0 if ok else 1


def _real_run_blockers(config: ProjectConfig, repo: Path) -> dict[str, str]:
    """Per CLI provider used by a role: why a real run cannot start (sandbox dependencies, no broker
    credential). Checks the credential file's existence and permissions only; never prints it."""
    blockers: dict[str, str] = {}
    for role_cfg in config.roles.values():
        profile = config.providers[role_cfg.provider]
        if not isinstance(profile, CliRunnerProfile) or profile.name in blockers:
            continue
        missing = missing_dependencies(profile.kind)
        try:
            configured_credential(config.broker, profile.name, ROUTES[profile.kind], forbidden_roots=(repo,))
            credential = ""
        except BrokerUnavailableError as exc:
            credential = str(exc)
        reasons = [f"missing sandbox dependencies {missing}"] if missing else []
        reasons += [credential] if credential else []
        if reasons:
            blockers[profile.name] = "; ".join(reasons)
    return blockers


def cmd_sandbox_check(args: argparse.Namespace, config: ProjectConfig) -> int:
    """Show each role's sandbox; --apply runs the containment self-test in a disposable repository."""
    repo = Path(args.repo)
    builder = SandboxBuilder(repo, config)
    roles = [args.role] if args.role else list(config.roles)
    for role in roles:
        roots = builder.writable_roots(role)
        overlays = builder.readonly_overlays(role, roots)
        codex = config.provider_for(role).kind.value == "codex"
        _print(f"{role:12s} writable: {roots}")
        _print(f"{'':12s} read-only inside them: {overlays or 'none'}; shadow root "
               f"{'writable (Codex nested sandbox; strays rejected)' if codex else 'read-only'}")
    secrets = builder.secret_files()
    _print(f"secret files hidden from every sandbox: {secrets or 'none'}")
    if not args.apply:
        _print("dry run — pass --apply to run the containment and isolation self-tests in a disposable repository")
        return 0
    return 0 if _self_tests(config) else 1


def _self_tests(config: ProjectConfig) -> bool:
    """The containment matrix and the network/credential isolation self-test (W1), printed."""
    from .isolation_check import isolation_self_test  # pylint: disable=import-outside-toplevel
    from .operations import containment_self_test  # pylint: disable=import-outside-toplevel
    report = containment_self_test(config)
    for role, result in report.roles.items():
        written = sorted(k for k, v in result["writes"].items() if v == "written")
        _print(f"[{role}] wrote {written}; secret {result['secret_read']}; symlink {result['symlink_write']}; "
               f"git commit {result['git_commit']}; strays {result['strays']}")
    for mismatch in report.mismatches:
        _print(f"MISMATCH: {mismatch}")
    _print(f"containment self-test: {'PASSED' if report.ok else 'FAILED'}")
    isolation = isolation_self_test(config)
    for name, check in isolation.checks.items():
        _print(f"[isolation] {'ok  ' if check['ok'] else 'FAIL'} {name}: {check['observed']}")
    for failure in isolation.failures:
        _print(f"ISOLATION FAILURE: {failure}")
    _print(f"isolation self-test: {'PASSED' if isolation.ok else 'FAILED'}")
    return report.ok and isolation.ok


def _nested_sandbox_blocker(config: ProjectConfig) -> str:
    """'' when the CLIs' own command sandboxes can start inside a runner sandbox, else why not."""
    from .isolation_check import nested_sandbox_check  # pylint: disable=import-outside-toplevel
    try:
        nested = nested_sandbox_check(config)
    except SandboxUnavailableError as exc:
        return str(exc)
    _print(f"nested command sandbox: {'available' if nested.available else 'DENIED'} "
           f"(AppArmor label {nested.label or 'unreadable'!r}{'' if nested.available else '; ' + nested.detail})")
    if nested.available:
        return ""
    why = ("this host's bwrap-userns-restrict AppArmor policy runs sandboxed processes as unpriv_bwrap, which "
           "may not create namespaces" if nested.host_policy else nested.detail)
    return ("the CLIs' own command sandboxes cannot start inside a runner sandbox here (" + why + "); every bwrap "
            "run would fail closed. Do not weaken AppArmor; see w1-progress.md, blocker B1")


def cmd_worker_check(args: argparse.Namespace, config: ProjectConfig) -> int:
    """Is this Worker eligible for REAL agent execution? (W1: D-20, D-21; no model call)."""
    if not args.apply:
        _print("dry run — --apply checks: bubblewrap; each CLI's version and flags; the sandbox dependencies; a "
               "broker credential per CLI provider; the containment and isolation self-tests (disposable "
               "repository, no model call, no credential is read or printed)")
        return 0
    reasons: list[str] = []
    try:
        _print(f"bubblewrap: {bwrap_version(check_bwrap())}")
    except SandboxUnavailableError as exc:
        reasons.append(str(exc))
    if config.runtime.execution_backend != "bwrap":
        reasons.append(f"runtime.execution_backend is {config.runtime.execution_backend!r}: agents run without the "
                       "W1 sandbox (credentials and network of the operator); isolated execution needs `bwrap` "
                       "(an operator decision)")
        if config.runtime.native_hardening.enabled:
            _print(f"native hardening: ON ({HARDENING_SCOPE}) — not W1 isolation (D-21 for the CLI process)")
    for note in ignored_settings(config):
        _print(f"note: {note}")
    for name in sorted({config.providers[r.provider].name for r in config.roles.values()
                        if isinstance(config.providers[r.provider], CliRunnerProfile)}):
        probe = probe_runner(config.providers[name])  # type: ignore[arg-type]
        floor = version_floor_error(probe) if probe.ok else ""
        _print(f"{name}: {probe.version or '-'} at {probe.resolved or '-'}: "
               f"{'OK' if probe.ok and not floor else 'UNAVAILABLE'}")
        if not probe.ok or floor:
            reasons.append(f"{name}: {floor or probe.error or 'missing flags ' + ', '.join(probe.missing_flags)}")
    reasons += [f"{name}: {why}" for name, why in _real_run_blockers(config, Path(args.repo)).items()]
    nested = _nested_sandbox_blocker(config)
    if nested:
        reasons.append(nested)
    if not reasons and not _self_tests(config):
        reasons.append("a sandbox self-test failed (see above)")
    for reason in reasons:
        _print(f"BLOCKER: {reason}")
    _print("Worker: ELIGIBLE for real agent execution" if not reasons
           else "Worker: INELIGIBLE for real agent execution (fail closed)")
    return 0 if not reasons else 1


# -- gates ------------------------------------------------------------------------------

def _gate_context(args: argparse.Namespace, config: ProjectConfig) -> GateContext:
    repo = Path(args.repo)
    head = _git(repo, "rev-parse", "HEAD")
    base = args.base or _git(repo, "merge-base", config.base_branch, "HEAD")
    return GateContext(repo, head_sha=head, base_sha=_git(repo, "rev-parse", base))


def cmd_gate(args: argparse.Namespace, config: ProjectConfig) -> int:
    """Run one gate in the offline, read-only gate sandbox."""
    if args.name not in config.gate_names:
        raise CoordinatorError(f"unknown gate {args.name!r}; gates: {list(config.gate_names)}")
    context = _gate_context(args, config)
    if args.name in config.unsafe_gates:
        _print(f"{args.name}: BLOCKED — {config.unsafe_gates[args.name]}")
        return 1
    cwd = gate_cwd(context.repo_root, args.name)
    _print(f"{args.name}: in {cwd}, HEAD {context.head_sha[:12]}, base {context.base_sha[:12]}; "
           f"checkout read-only, no network, timeout {config.runtime.gate_timeout_seconds}s")
    if not args.apply:
        _print("dry run — pass --apply to run it")
        return 0
    result = SandboxedGateRunner(config, _state(args, config) / "logs")(args.name, context)
    _print(f"{args.name}: {result.status} (exit {result.exit_code}) in {result.duration_seconds:.1f}s; "
           f"log {result.log}")
    _print(f"  {result.summary}")
    return 0 if result.passed else 1


def cmd_lint_report(args: argparse.Namespace, config: ProjectConfig) -> int:
    """Current findings of a lint gate's targets, counted by symbol (the D-19 baseline report)."""
    lint = config.lint_gates.get(args.name)
    if lint is None:
        raise CoordinatorError(f"{args.name!r} is not a lint gate; lint gates: {list(config.lint_gates)}")
    _print(f"{args.name}: pylint {list(lint.targets)} in {lint.root} (sandboxed, read-only, offline)")
    if not args.apply:
        _print("dry run — pass --apply to run it")
        return 0
    runner = SandboxedGateRunner(config, _state(args, config) / "logs")
    counts, result = lint_report(runner, args.name, lint, GateContext(Path(args.repo)))
    if result.status in ("blocked", "timeout", "error"):
        _print(f"{args.name}: {result.status} — {result.summary}")
        return 1
    _print(f"{sum(counts.values())} existing finding(s): " + (", ".join(f"{k} {v}" for k, v in counts.most_common())
                                                         or "none"))
    return 0


# -- feature operations ----------------------------------------------------------------------

def _coordinator(args: argparse.Namespace, config: ProjectConfig):
    from .operations import DetachedPublisher, build_coordinator  # pylint: disable=import-outside-toplevel
    state_dir = _state(args, config)
    kind = getattr(args, "tracker", None) or "local"
    publisher = DetachedPublisher.for_cli(Path(args.repo), state_dir, kind, getattr(args, "project_config", None))
    return build_coordinator(config, Path(args.repo), state_dir, tracker_kind=kind, publisher=publisher)


def cmd_activate(args: argparse.Namespace, config: ProjectConfig) -> int:
    """Create the feature branch from its base (requires every writer to be quiescent)."""
    coord = _coordinator(args, config)
    feature = coord.store.load_feature(args.feature)
    _print(f"{feature.id}: create {feature.branch} from {feature.base_branch} in {args.repo}")
    if not args.apply:
        _print("dry run — pass --apply to create the branch")
        return 0
    feature = coord.activate(feature.id)
    _print(f"{feature.id}: {feature.state.value} on {feature.branch} at {feature.base_sha[:12]}")
    return 0


def cmd_launch(args: argparse.Namespace, config: ProjectConfig) -> int:
    """Run one task with its real runner: claim, sandbox, supervise, commit, gates, release."""
    coord = _coordinator(args, config)
    task = coord.store.load_task(args.task)
    backend = config.runtime.execution_backend
    _print(f"{task.id}: role {task.role}, runner {config.provider_for(task.role).name}, model "
           f"{config.model_for(task.role) or 'CLI default'}, backend {backend}"
           f"{' (NOT a security sandbox)' if backend == 'native-cli' else ''}, state {task.state.value}")
    if not args.apply:
        _print("dry run — pass --apply to launch it (see `runners` for the exact command line)")
        return 0
    from .supervisor import install_termination_handlers  # pylint: disable=import-outside-toplevel
    install_termination_handlers()
    run = coord.run_task(task.id)
    _print(f"{task.id}: {run.state.value}; runner {run.runner.status} {run.runner.error}".rstrip())
    if run.commit:
        _print(f"  commit {run.commit[:12]}")
    if run.runner.details.get("run_dir"):
        _print(f"  run directory {run.runner.details['run_dir']}")
    return 0 if run.state.value in ("verified", "ready") else 1


def cmd_stop(args: argparse.Namespace, config: ProjectConfig) -> int:
    """Ask the worker running TASK to stop (SIGTERM → grace → SIGKILL of its sandbox)."""
    record = _coordinator(args, config).request_cancel(args.task, reason=args.reason)
    _print(f"stop requested for {args.task} (worker pid {record['identity']['pid']}); "
           "the task returns to ready when the worker has stopped")
    return 0


def cmd_recover(args: argparse.Namespace, config: ProjectConfig) -> int:
    """Reclaim tasks of provably dead workers; report live, unresponsive and (W3) orphaned ones.
    `recover TASK` (W3, one task from its checkpoints) is checkpoint_commands.py."""
    coord = _coordinator(args, config)
    if not args.apply:
        for task_id, record in coord.store.list_claims().items():
            from .locks import ProcessIdentity  # pylint: disable=import-outside-toplevel
            alive = ProcessIdentity.from_dict(record["identity"]).is_alive()
            _print(f"{task_id}: worker pid {record['identity']['pid']} {'alive' if alive else 'GONE (reclaimable)'}")
        _print("dry run — pass --apply to reclaim")
        return 0
    report = coord.claims.recover(stale_after_seconds=config.runtime.stale_heartbeat_seconds)
    _print(f"recovered {[r['task_id'] for r in report.recovered]}; live {[r['task_id'] for r in report.live]}; "
           f"unresponsive {[r['task_id'] for r in report.unresponsive]}; "
           f"unknown lock holders {report.unknown_lock_holders}")
    for record in report.orphaned:
        _print(f"NOT recovered {record['task_id']}: its worker is gone but agent process(es) {record['orphans']} of "
               f"the run still live (`recover {record['task_id']}` shows them; --terminate-orphans stops them)")
    return 0


def cmd_integrate(args: argparse.Namespace, config: ProjectConfig) -> int:
    """The quiescent integration barrier: all tasks verified, clean tree, gates → PR candidate."""
    coord = _coordinator(args, config)
    if not args.apply:
        _print(f"dry run — {args.feature}: would run integration gates and render the single PR candidate")
        return 0
    feature = coord.integrate(args.feature)
    _print(f"{feature.id}: {feature.state.value}; PR candidate {feature.pull_request.head} → "
           f"{feature.pull_request.base} at {feature.pull_request.head_sha[:12]} (not opened)")
    return 0


def register(sub: Any) -> None:
    """Add the Phase 3 commands to the CLI's subparsers."""
    runners = sub.add_parser("runners", help="probe the claude/codex CLIs and bwrap (no model call)")
    runners.add_argument("--scratch", default=None, help="directory for the generated example files")
    runners.set_defaults(func=cmd_runners)
    check = sub.add_parser("sandbox-check", help="show role sandboxes; --apply runs the containment and "
                                                 "isolation self-tests")
    check.add_argument("--role", default=None)
    check.add_argument("--apply", action="store_true")
    check.set_defaults(func=cmd_sandbox_check)
    worker = sub.add_parser("worker-check", help="is this Worker eligible for real agent execution? (W1)")
    worker.add_argument("--apply", action="store_true")
    worker.set_defaults(func=cmd_worker_check)
    gate = sub.add_parser("gate", help="run one gate in the offline read-only sandbox")
    gate.add_argument("name")
    gate.add_argument("--base", default=None, help="base commit for lint gates (default: merge-base with base)")
    gate.add_argument("--apply", action="store_true")
    gate.set_defaults(func=cmd_gate)
    report = sub.add_parser("lint-report", help="current findings of a lint gate, by symbol")
    report.add_argument("name")
    report.add_argument("--apply", action="store_true")
    report.set_defaults(func=cmd_lint_report)
    for name, func, target, helptext in (
            ("activate", cmd_activate, "feature", "create the feature branch"),
            ("launch", cmd_launch, "task", "run one task with its real runner"),
            ("integrate", cmd_integrate, "feature", "run the integration barrier")):
        parser = sub.add_parser(name, help=helptext)
        parser.add_argument(target)
        parser.add_argument("--apply", action="store_true")
        parser.set_defaults(func=func)
    stop = sub.add_parser("stop", help="ask the worker running a task to stop")
    stop.add_argument("task")
    stop.add_argument("--reason", default="operator stop")
    stop.set_defaults(func=cmd_stop)
    recover = sub.add_parser("recover", help="reclaim tasks of dead workers; W3: recover one task from its checkpoints")
    recover.add_argument("--apply", action="store_true")
    recover.set_defaults(func=cmd_recover)  # checkpoint_commands.register() adds `recover TASK`
