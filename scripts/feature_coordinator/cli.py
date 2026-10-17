"""`python scripts/feature.py` — the feature coordinator's CLI (Phases 2–4).

Global options: --repo PATH (default: this checkout), --state-dir PATH (default: the D-12
state directory), --project-config FILE (default: the built-in configuration),
--tracker local|github (default: `local` for feature operations, `github` for the `github`
commands). Every command that changes something is a DRY RUN unless --apply is given.

Inspection:
    doctor [--apply]        state directory checks; --apply creates it and runs the flock self-test
    config [--config FILE]  validate a configuration; check it equals the built-in one
    providers               provider per role; remote Qwen limits and gates (never connects)
    runners                 probe the installed claude/codex CLIs (--version/--help only) and bwrap
    sandbox-check [--role R] [--apply]
                            show each role's sandbox; --apply runs the containment self-test
                            in a DISPOSABLE repository
    status                  features, tasks, claims (live / stale / heartbeat age), cancel requests
    demo                    the mock end-to-end feature in a temporary repository
Gates (always sandboxed: checkout read-only, no network):
    gate NAME [--base REF] [--apply]      run one gate against --repo
    lint-report NAME [--apply]            current Pylint findings of a lint gate, by symbol
Feature operations (on --repo / --state-dir):
    plan SPEC [--apply]     validate (and record) a feature and its tasks
    activate FEATURE [--apply]            create the feature branch (requires quiescence)
    launch TASK [--apply]   run one task with its real runner, contained and supervised
    stop TASK               ask the running worker to stop; the task returns to ready
    recover [--apply]       reclaim tasks of dead workers; report unresponsive ones
    integrate FEATURE [--apply]           the quiescent integration barrier → PR candidate
    hold TASK --reason R [--apply] / release TASK [--apply]
                            human escalation (needs-human); only a release resumes the task
GitHub Issues (Phase 4; see `feature.py github --help`):
    github status | labels | sync | reconcile | inspect | owned | deps | test-lifecycle | cleanup-test

No command pushes or opens a pull request, and nothing is sent to the remote Qwen server.
GitHub Issue writes need --apply and stay within github.write_scope (test features only in Phase 4).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Sequence

from .claims import heartbeat_age
from .config import ProjectConfig, default_config, load_config
from .errors import CoordinatorError
from .locks import ProcessIdentity
from .model import Task, validate_dag
from .providers import OpenAICompatibleProfile, check_transport, resolve_endpoint
from .statedir import check_filesystem, ensure_state_dir, flock_self_test, mount_for, resolve_state_dir
from .store import StateStore
from .workflow import FeatureSpec

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_CONFIG = REPO_ROOT / "docs_agents" / "coordination" / "project-config.example.yaml"


def _print(text: str = "") -> None:
    sys.stdout.write(text + "\n")


def _state_dir(args: argparse.Namespace, config: ProjectConfig) -> Path:
    return Path(args.state_dir) if getattr(args, "state_dir", None) else resolve_state_dir(app_dir=config.state_app_dir)


def cmd_doctor(args: argparse.Namespace, config: ProjectConfig) -> int:
    """State directory diagnostics."""
    state_dir = _state_dir(args, config)
    mount = mount_for(state_dir)
    _print(f"state directory: {state_dir} ({'exists' if state_dir.exists() else 'not created yet'})")
    _print(f"filesystem:      {mount.fs_type} on {mount.mount_point}")
    check_filesystem(state_dir, allowed=config.allowed_state_filesystems)
    _print("suitability:     OK (local filesystem with flock support)")
    if not args.apply:
        _print("dry run: pass --apply to create the directory (0700) and run the flock self-test")
        return 0
    ensure_state_dir(state_dir, allowed=config.allowed_state_filesystems)
    _print("directory:       ready (mode 0700, owner checked, subdirectories present)")
    flock_self_test(state_dir)
    _print("flock self-test: OK (a second process was excluded, then admitted after release)")
    return 0


def cmd_config(args: argparse.Namespace, config: ProjectConfig) -> int:
    """Validate a configuration file against the built-in configuration."""
    loaded = load_config(Path(args.config))
    same = loaded == config
    _print(f"{args.config}: valid; {'identical to' if same else 'DIFFERENT from'} the built-in configuration")
    for name, role in loaded.roles.items():
        _print(f"  {name:12s} provider={role.provider:12s} owned={list(role.owned)} excluded={list(role.excluded)}")
    return 0 if same else 1


def cmd_providers(_args: argparse.Namespace, config: ProjectConfig) -> int:
    """Provider per role; remote endpoint gates without connecting."""
    for name, role in config.roles.items():
        profile = config.providers[role.provider]
        _print(f"{name:12s} → {profile.name} ({profile.kind.value})")
    for profile in config.providers.values():
        if not isinstance(profile, OpenAICompatibleProfile):
            continue
        _print(f"\n{profile.name}: model {profile.default_model} ({profile.display_name})")
        _print(f"  context {profile.context_window_tokens}, output {profile.max_output_tokens}, declared input "
               f"{profile.declared_max_input_tokens} → hard input {profile.hard_max_input_tokens}, working cap "
               f"{profile.effective_max_input_tokens}")
        declared = sorted(k for k, v in profile.declared_capabilities.items() if v)
        _print(f"  declared capabilities: {declared}; validated: {sorted(profile.validated_capabilities) or 'none'}")
        try:
            endpoint = resolve_endpoint(profile, os.environ)
        except CoordinatorError as exc:
            _print(f"  endpoint: {exc}")
            continue
        _print(f"  endpoint: {profile.base_url_env} is set "
               f"(scheme {endpoint.scheme}, loopback {endpoint.is_loopback}); "
               f"model {endpoint.model}; API key {'set' if endpoint.api_key else 'not set'}")
        try:
            check_transport(profile, endpoint, carries_code=True)
            _print("  transport gate for code-bearing requests: allowed")
        except CoordinatorError as exc:
            _print(f"  transport gate for code-bearing requests: BLOCKED — {exc}")
    return 0


def cmd_plan(args: argparse.Namespace, config: ProjectConfig) -> int:
    """Validate a feature spec without writing anything."""
    path = Path(args.spec)
    text = path.read_text(encoding="utf-8")
    if path.suffix in (".yaml", ".yml"):
        import yaml  # pylint: disable=import-outside-toplevel
        data = yaml.safe_load(text)
    else:
        data = json.loads(text)
    spec = FeatureSpec.from_mapping(data)
    branch = config.branch_for(spec.slug)
    tasks = {f"{spec.slug}.{t.key}": Task(id=f"{spec.slug}.{t.key}", feature_id=spec.slug, role=t.role, title=t.title,
                                          depends_on=tuple(f"{spec.slug}.{d}" for d in t.depends_on),
                                          required_gates=t.required_gates)
             for t in spec.tasks}
    order = validate_dag(tasks)
    unknown = sorted({g for t in spec.tasks for g in t.required_gates} - set(config.gate_names))
    if unknown:
        raise CoordinatorError(f"unknown gates {unknown}")
    _print(f"dry run — feature {spec.slug!r}: branch {branch} from {spec.base_branch or config.base_branch}; "
           f"PR policy {config.pr_policy}")
    for task_id in order:
        task = tasks[task_id]
        provider = config.provider_for(task.role)
        _print(f"  {task_id:40s} role={task.role:12s} runner={provider.name:12s} after={list(task.depends_on)} "
               f"gates={list(task.required_gates)}")
    if not getattr(args, "apply", False):
        _print("dry run — nothing was written (pass --apply to record the feature)")
        return 0
    from .operations import build_coordinator  # pylint: disable=import-outside-toplevel
    state_dir = _ensure_state(args, config)
    coord = build_coordinator(config, Path(args.repo), state_dir, tracker_kind=args.tracker or "local")
    feature = coord.plan(spec)
    _print(f"recorded feature {feature.id} ({feature.state.value}) with {len(feature.task_ids)} task(s)")
    board = coord.last_board_sync
    if board is not None:
        _print(f"project board: {len(board.added) + len(board.adopted)} item(s) added; "
               + ("ok" if board.ok else f"deferred: {board.deferred or board.protected}"))
    return 0


def _ensure_state(args: argparse.Namespace, config: ProjectConfig) -> Path:
    return ensure_state_dir(_state_dir(args, config), allowed=config.allowed_state_filesystems)


def cmd_status(args: argparse.Namespace, config: ProjectConfig) -> int:
    """Read-only view of the coordinator state."""
    state_dir = _state_dir(args, config)
    if not (state_dir / "features").is_dir():
        _print(f"no coordinator state yet at {state_dir}")
        return 0
    store = StateStore(state_dir)
    if not store.list_features() and not store.list_claims():
        _print(f"no features or claims recorded in {state_dir}")
    for feature in store.list_features():
        _print(f"{feature.id}: {feature.state.value} on {feature.branch}")
        for task in store.load_tasks(feature).values():
            _print(f"  {task.id:40s} {task.state.value:12s} {task.blocked_reason}")
    for task_id, record in store.list_claims().items():
        alive = ProcessIdentity.from_dict(record["identity"]).is_alive()
        age = heartbeat_age(record)
        stale = age is not None and age > config.runtime.stale_heartbeat_seconds
        state = ("live, UNRESPONSIVE" if stale else "live") if alive else "STALE (recoverable)"
        _print(f"claim {task_id}: role {record['role']} pid {record['identity']['pid']} {state}; "
               f"heartbeat {age:.0f}s ago" if age is not None else f"claim {task_id}: {state}")
        if store.get_flag(f"cancel-{task_id}"):
            _print(f"  stop requested for {task_id}")
    return 0


def cmd_demo(_args: argparse.Namespace, _config: ProjectConfig) -> int:
    """Run the mock end-to-end feature in a temporary repository."""
    from .demo import run_demo  # pylint: disable=import-outside-toplevel
    report = run_demo()
    for step in report["steps"]:
        _print(f"- {step}")
    _print(f"\nbranches {report['branches']}; commits on the feature branch: {len(report['commits'])}; "
           f"pull requests: {report['pull_requests']}; all tasks verified: {report['all_verified']}; "
           f"foreign bug-workflow issue untouched: {report['foreign_issue_untouched']}")
    return 0 if report["all_verified"] and report["pull_requests"] == 1 else 1


def build_parser() -> argparse.ArgumentParser:
    """The argument parser."""
    parser = argparse.ArgumentParser(prog="feature.py", description=__doc__.split("\n\n", 1)[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    parser.add_argument("--repo", default=str(REPO_ROOT), help="the checkout to operate on")
    parser.add_argument("--state-dir", default=None, help="coordinator state directory (default: D-12 location)")
    parser.add_argument("--project-config", default=None, help="project configuration YAML (default: built-in)")
    parser.add_argument("--tracker", choices=("local", "github"), default=None,
                        help="issue tracker: the local JSON tracker or real GitHub Issues "
                             "(default: local; `github` for the github commands)")
    sub = parser.add_subparsers(dest="command", required=True)
    doctor = sub.add_parser("doctor", help="check (and with --apply, create) the state directory")
    doctor.add_argument("--apply", action="store_true", help="create the directory and run the flock self-test")
    doctor.set_defaults(func=cmd_doctor)
    cfg = sub.add_parser("config", help="validate a project configuration file")
    cfg.add_argument("--config", default=str(EXAMPLE_CONFIG), help="YAML file (default: the documented example)")
    cfg.set_defaults(func=cmd_config)
    sub.add_parser("providers", help="show provider profiles and gates (no connection)") \
        .set_defaults(func=cmd_providers)
    plan = sub.add_parser("plan", help="validate (and with --apply, record) a feature spec")
    plan.add_argument("spec", help="feature spec, YAML or JSON")
    plan.add_argument("--apply", action="store_true", help="record the feature and its tasks")
    plan.set_defaults(func=cmd_plan)
    from .commands import register  # pylint: disable=import-outside-toplevel
    register(sub)
    from .github_commands import register as register_github  # pylint: disable=import-outside-toplevel
    from .project_commands import register as register_project  # pylint: disable=import-outside-toplevel
    register_project(register_github(sub))
    sub.add_parser("status", help="show coordinator state (read-only)").set_defaults(func=cmd_status)
    sub.add_parser("demo", help="run the mock end-to-end feature in a temporary repository").set_defaults(func=cmd_demo)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point."""
    args = build_parser().parse_args(argv)
    try:
        config = load_config(Path(args.project_config)) if args.project_config else default_config()
        return int(args.func(args, config))
    except (CoordinatorError, OSError, ValueError) as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 1
