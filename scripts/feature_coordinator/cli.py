"""`python scripts/feature.py` — the feature coordinator's dry-run CLI (Phase 2).

Commands (all read-only unless stated):
    doctor [--apply]        state directory: resolution, filesystem suitability; with --apply,
                            create it (mode 0700) and run the two-process flock self-test
    config [--config FILE]  validate a project configuration (default: the documented example)
                            and check that it equals the built-in configuration
    providers               provider profiles per role; remote Qwen limits, capabilities and
                            transport gate (never connects; the endpoint address is not printed)
    plan SPEC               validate a feature spec (YAML/JSON): branch, task DAG, roles, gates
    status                  coordinator state: features, tasks, live and stale claims
    demo                    run the complete mock feature in a temporary repository

Phase 2 has no command that mutates the real checkout, pushes, or talks to GitHub or a model.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Sequence

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


def cmd_doctor(args: argparse.Namespace, config: ProjectConfig) -> int:
    """State directory diagnostics."""
    state_dir = resolve_state_dir(app_dir=config.state_app_dir)
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
    unknown = sorted({g for t in spec.tasks for g in t.required_gates} - set(config.gates))
    if unknown:
        raise CoordinatorError(f"unknown gates {unknown}")
    _print(f"dry run — feature {spec.slug!r}: branch {branch} from {spec.base_branch or config.base_branch}; "
           f"PR policy {config.pr_policy}")
    for task_id in order:
        task = tasks[task_id]
        provider = config.provider_for(task.role)
        _print(f"  {task_id:40s} role={task.role:12s} runner={provider.name:12s} after={list(task.depends_on)} "
               f"gates={list(task.required_gates)}")
    _print("nothing was written (Phase 2 dry run)")
    return 0


def cmd_status(_args: argparse.Namespace, config: ProjectConfig) -> int:
    """Read-only view of the coordinator state."""
    state_dir = resolve_state_dir(app_dir=config.state_app_dir)
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
        _print(f"claim {task_id}: role {record['role']} pid {record['identity']['pid']} "
               f"{'live' if alive else 'STALE (recoverable)'}")
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
    sub = parser.add_subparsers(dest="command", required=True)
    doctor = sub.add_parser("doctor", help="check (and with --apply, create) the state directory")
    doctor.add_argument("--apply", action="store_true", help="create the directory and run the flock self-test")
    doctor.set_defaults(func=cmd_doctor)
    cfg = sub.add_parser("config", help="validate a project configuration file")
    cfg.add_argument("--config", default=str(EXAMPLE_CONFIG), help="YAML file (default: the documented example)")
    cfg.set_defaults(func=cmd_config)
    sub.add_parser("providers", help="show provider profiles and gates (no connection)") \
        .set_defaults(func=cmd_providers)
    plan = sub.add_parser("plan", help="validate a feature spec (dry run)")
    plan.add_argument("spec", help="feature spec, YAML or JSON")
    plan.set_defaults(func=cmd_plan)
    sub.add_parser("status", help="show coordinator state (read-only)").set_defaults(func=cmd_status)
    sub.add_parser("demo", help="run the mock end-to-end feature in a temporary repository").set_defaults(func=cmd_demo)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point."""
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args, default_config()))
    except (CoordinatorError, OSError, ValueError) as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 1
