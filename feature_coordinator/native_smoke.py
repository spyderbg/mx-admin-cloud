"""Bounded real-subscription smoke test of the native Worker (W2) — OPERATOR APPROVAL REQUIRED.

`python -m feature_coordinator subscription-smoke` (dry run) reports, without any model request:
  * the agent, model and exact native command line per role;
  * the authentication method each CLI's own status command reports (subscription or not);
  * the disposable repository, the expected changes and the expected usage.

`python -m feature_coordinator subscription-smoke --apply` then runs exactly one small feature in a NEW disposable
repository (test files only), with the local tracker (no GitHub), no gates and no PR:

  Backend  (Claude Code, the configured Backend model)   → backend/smoke/backend.txt
  CLI      (Claude Code, the configured CLI model)       → cli/smoke/cli.txt       depends on Backend
  Frontend (Codex, the configured Frontend model)        → frontend/smoke/frontend.txt  depends on Backend

Each task is launched at most once (no retries); CLI and Frontend run concurrently. The run fails
closed if a CLI is not logged in with the subscription, reports an API key, or substitutes a
model. Afterwards it checks the three files on the feature branch, the role scopes of every
commit, and renders the integration candidate (never opened as a PR).
"""

from __future__ import annotations

import dataclasses
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

from .config import ProjectConfig
from .demo import init_demo_repo
from .launchers import LAUNCHERS, probe_runner
from .native_backend import check_subscription, native_environment
from .paths import PathPolicy, ScopedWorkspace
from .providers import CliRunnerProfile
from .runners import RunRequest
from .statedir import ensure_state_dir
from .workflow import FeatureSpec, TaskSpec

SMOKE_SLUG = "subscription-smoke"
SMOKE_TASK_TIMEOUT_SECONDS = 300
SMOKE_CLAUDE_BUDGET_USD = 0.5
SMOKE_FILES = {"backend": ("backend/smoke/backend.txt", "backend ok"),
               "cli": ("cli/smoke/cli.txt", "cli ok"),
               "frontend": ("frontend/smoke/frontend.txt", "frontend ok")}
SEED_FILES = {"backend/README.md": "# backend (smoke test)\n", "cli/README.md": "# cli (smoke test)\n",
              "frontend/README.md": "# frontend (smoke test)\n", "README.md": "# disposable smoke-test repository\n"}


def smoke_prompt(role: str) -> str:
    """The bounded instruction for one role."""
    path, text = SMOKE_FILES[role]
    return (f"Create the file {path} containing exactly one line: {text}\n"
            "Do not create, change or delete anything else. Do not run any command. Then reply 'done'.")


def smoke_spec() -> FeatureSpec:
    """Backend first; CLI and Frontend depend on it (and may then run concurrently)."""
    return FeatureSpec(SMOKE_SLUG, "Native Worker subscription smoke test", (
        TaskSpec("backend", "backend", smoke_prompt("backend")),
        TaskSpec("cli", "cli", smoke_prompt("cli"), depends_on=("backend",)),
        TaskSpec("frontend", "frontend", smoke_prompt("frontend"), depends_on=("backend",))), test=True)


def smoke_config(config: ProjectConfig) -> ProjectConfig:
    """*config* bounded for the smoke test: native backend, short timeout, small Claude budget."""
    return dataclasses.replace(config, runtime=dataclasses.replace(
        config.runtime, execution_backend="native-cli", task_timeout_seconds=SMOKE_TASK_TIMEOUT_SECONDS,
        claude_max_budget_usd=SMOKE_CLAUDE_BUDGET_USD))


def smoke_plan(config: ProjectConfig, workdir: Path) -> dict[str, Any]:
    """What --apply would do. Runs only `--version`, `--help` and the CLIs' status commands."""
    config = smoke_config(config)
    repo = workdir / "repo"
    roles: dict[str, Any] = {}
    auth_cache: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="fc-smoke-plan-") as tmp:
        sample = Path(tmp) / "repo"
        init_demo_repo(sample, SEED_FILES)
        for role, (path, text) in SMOKE_FILES.items():
            profile = config.provider_for(role)
            assert isinstance(profile, CliRunnerProfile)
            probe = probe_runner(profile)
            entry: dict[str, Any] = {"agent": profile.name, "kind": profile.kind.value,
                                     "model": config.model_for(role) or "CLI default", "cli": probe.version or "-",
                                     "cli_ok": probe.ok, "expected_change": f"{path} = '{text}'"}
            if probe.ok:
                if profile.name not in auth_cache:
                    auth_cache[profile.name] = check_subscription(profile.kind, probe.resolved, native_environment())
                entry["authentication"] = auth_cache[profile.name].method
                entry["authentication_ok"] = auth_cache[profile.name].ok
                launcher = LAUNCHERS[profile.kind](profile, config, probe.resolved, native=True)
                request = RunRequest(f"{SMOKE_SLUG}.{role}", role, smoke_prompt(role),
                                     ScopedWorkspace(PathPolicy(sample, config), role))
                run_dir = Path(tmp) / f"run-{role}"
                run_dir.mkdir()
                argv = launcher.prepare(request, run_dir, [role]).argv
                entry["argv"] = [str(repo) if a == str(sample) else a.replace(tmp, "<run dir>") for a in argv]
            roles[role] = entry
    return {"repository": str(repo), "state_dir": str(workdir / "state"), "roles": roles,
            "order": "backend, then cli and frontend concurrently", "attempts_per_task": 1,
            "task_timeout_seconds": SMOKE_TASK_TIMEOUT_SECONDS,
            "claude_max_budget_usd_per_run": SMOKE_CLAUDE_BUDGET_USD,
            "model_sessions": 3, "github": "none (local tracker)", "gates": "none", "pull_request": "none"}


def write_smoke_config(config_yaml: Path, base_yaml: Path) -> Path:
    """The documented example configuration, bounded for the smoke test (for the launch processes)."""
    import yaml  # pylint: disable=import-outside-toplevel
    data = yaml.safe_load(base_yaml.read_text(encoding="utf-8"))
    data["runtime"].update(execution_backend="native-cli", task_timeout_seconds=SMOKE_TASK_TIMEOUT_SECONDS,
                           claude_max_budget_usd=SMOKE_CLAUDE_BUDGET_USD)
    config_yaml.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return config_yaml


def run_smoke(config_yaml: Path, workdir: Path, echo: Callable[[str], None]) -> dict[str, Any]:  # pylint: disable=too-many-locals
    """Run the bounded smoke feature (REAL model requests). See the module docstring."""
    from .config import load_config  # pylint: disable=import-outside-toplevel
    from .operations import build_coordinator  # pylint: disable=import-outside-toplevel
    from .worker import FeatureWorker, cli_spawner  # pylint: disable=import-outside-toplevel
    config = load_config(config_yaml)
    repo = workdir / "repo"
    if repo.exists():
        raise FileExistsError(f"{repo} exists; the smoke test always uses a new disposable repository")
    state = ensure_state_dir(workdir / "state", allowed=config.allowed_state_filesystems)
    init_demo_repo(repo, SEED_FILES)
    coord = build_coordinator(config, repo, state, tracker_kind="local")
    feature = coord.activate(coord.plan(smoke_spec()).id)
    echo(f"disposable repository {repo}; feature branch {feature.branch}")
    worker = FeatureWorker(coord, cli_spawner(repo, state, project_config=str(config_yaml)), max_parallel=2,
                           max_attempts=1, echo=echo)
    report = worker.run(feature.id)
    files = {}
    for role, (path, text) in SMOKE_FILES.items():
        shown = subprocess.run(["git", "-C", str(repo), "show", f"{feature.branch}:{path}"], capture_output=True,
                               text=True, check=False)
        files[role] = {"path": path, "on_branch": shown.returncode == 0, "content_ok": shown.stdout.strip() == text}
    runs = {}
    for native in sorted((state / "runs").glob("*/native.json")):
        data = json.loads(native.read_text(encoding="utf-8"))
        task = native.parent.name.split(".2")[0]
        runs[native.parent.name] = {"task": task, "model": data["model"], "authentication": data["authentication"]}
    result: dict[str, Any] = {"repository": str(repo), "states": report.states,
                              "max_concurrency": report.max_concurrency, "files": files, "runs": runs,
                              "launches": [dataclasses.asdict(o) for o in report.launches], "skipped": report.skipped}
    if report.all_verified:
        candidate = coord.integrate(feature.id, gates=())
        result["integration"] = {"state": candidate.state.value, "pr_opened": False,
                                 "head": candidate.pull_request.head_sha if candidate.pull_request else None}
    result["passed"] = report.all_verified and all(f["on_branch"] and f["content_ok"] for f in files.values())
    return result
