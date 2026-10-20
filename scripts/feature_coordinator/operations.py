"""Phase 3 operations: real runners, the containment self-test and the operator commands.

`containment_self_test()` builds a DISPOSABLE repository, then, for every role, runs a small
probe program inside exactly the sandbox a real runner of that role would get. The probe
tries every kind of write that matters — its own roots, every other role's roots, `.git`,
protected and approval-required paths, the repository root, a path outside the repository,
a symlink into another role's tree, a generated output, a secret file, `git commit` — and
the results are compared with what the PathPolicy says. Any difference is a failure.
It never touches the real checkout.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .config import ProjectConfig
from .demo import init_demo_repo
from .gates import SandboxedGateRunner
from .http_transport import HttpChatTransport
from .issue_sync import IssueSync
from .issues import MockIssueTracker, TrackerClient
from .launchers import RealCliRunner
from .paths import PathPolicy
from .providers import CliRunnerProfile, OpenAICompatibleProfile
from .runners import OpenAICompatibleRunner, Runner
from .sandbox import SandboxBuilder, check_bwrap
from .store import StateStore
from .supervisor import supervise
from .workflow import FeatureCoordinator

SELF_TEST_FILES = {
    "backend/src/app.py": "ROUTES = []\n",
    "backend/data/users.db": "not a real database\n",
    "cli/src/commands.py": "COMMANDS = []\n",
    "frontend/src/view.ts": "export const views = []\n",
    "frontend/src/types/contentSchemas.ts": "// generated\n",
    "scripts/e2e_check.py": "print('ok')\n",
    "scripts/mxfix/fixer.py": "# independent bug workflow\n",
    "scripts/deploy.sh": "#!/bin/sh\n",
    "test_environment/README.md": "# test env\n",
    "content/foods/items.csv": "id\n",
    "docs_agents/backend/README.md": "# backend docs\n",
    "docs_agents/cli/README.md": "# cli docs\n",
    "docs_agents/frontend/README.md": "# frontend docs\n",
    "docs_agents/automation/README.md": "# automation docs\n",
    "docs_agents/coordination/README.md": "# coordination\n",
    "docs_agents/architecture/README.md": "# architecture\n",
    "README.md": "# repo\n",
    ".env.example": "KEY=\n",
}
UNTRACKED_SECRETS = {".env": "SECRET=do-not-read\n", "backend/.env.local": "SECRET=nested\n"}
WRITE_TARGETS = (
    "backend/src/probe.py", "backend/data/probe.db", "cli/src/probe.py", "frontend/src/probe.ts",
    "frontend/src/types/probeSchemas.ts", "scripts/probe.py", "scripts/mxfix/probe.py", "scripts/deploy.sh",
    "test_environment/probe.md", "content/probe.csv", "docs_agents/backend/probe.md", "docs_agents/cli/probe.md",
    "docs_agents/frontend/probe.md", "docs_agents/automation/probe.md", "docs_agents/coordination/probe.md",
    "docs_agents/architecture/probe.md", "README.md", "probe-at-root.txt", ".git/probe",
)

PROBE = r"""
import json, os, subprocess, sys
spec = json.loads(sys.argv[1])
out = {"writes": {}, "secret_read": None, "outside_write": None, "symlink_write": None, "git_commit": None}
for rel in spec["targets"]:
    try:
        os.makedirs(os.path.dirname(rel) or ".", exist_ok=True)
        with open(rel, "a", encoding="utf-8") as fh:
            fh.write("probe\n")
        out["writes"][rel] = "written"
    except OSError as exc:
        out["writes"][rel] = type(exc).__name__ + ":" + str(exc.errno)
try:
    out["secret_read"] = open(".env", encoding="utf-8").read()[:8]
except OSError as exc:
    out["secret_read"] = "denied:" + type(exc).__name__
try:
    with open(spec["outside"], "w", encoding="utf-8") as fh:
        fh.write("escape\n")
    out["outside_write"] = "written-inside-sandbox-only"
except OSError as exc:
    out["outside_write"] = "denied:" + type(exc).__name__
if spec.get("own"):
    link = os.path.join(spec["own"], "probe-link")
    try:
        os.symlink(os.path.abspath(spec["victim"]), link)
        with open(os.path.join(link, "via-symlink.txt"), "w", encoding="utf-8") as fh:
            fh.write("x\n")
        out["symlink_write"] = "written"
    except OSError as exc:
        out["symlink_write"] = "denied:" + type(exc).__name__
    finally:
        try:
            os.unlink(link)
        except OSError:
            pass
proc = subprocess.run(["git", "commit", "--allow-empty", "-q", "-m", "probe"], capture_output=True, text=True)
out["git_commit"] = "succeeded" if proc.returncode == 0 else "refused"
print(json.dumps(out))
"""


@dataclass
class SelfTestReport:
    """Per-role results of containment_self_test()."""

    roles: dict[str, dict[str, Any]] = field(default_factory=dict)
    mismatches: list[str] = field(default_factory=list)
    repo_unchanged_outside_roots: bool = True

    @property
    def ok(self) -> bool:
        """True when every observed result matched the policy."""
        return not self.mismatches and self.repo_unchanged_outside_roots


def _expected_writable(policy: PathPolicy, role: str, rel: str, shadow_writable: bool) -> bool:
    if rel == "probe-at-root.txt":
        return shadow_writable  # lands in the discarded shadow (Codex profile), else EROFS
    if rel.startswith(".git/"):
        return False
    return policy.check(role, rel).allowed


def _role_mismatches(policy: PathPolicy, role: str, result: Mapping[str, Any], *, own: str, victim: str) -> list[str]:
    """Differences between what the probe observed for *role* and what the policy says."""
    shadow_writable = result["shadow_writable"]
    found = []
    for rel, outcome in result["writes"].items():
        expected = _expected_writable(policy, role, rel, shadow_writable)
        if (outcome == "written") != expected:
            found.append(f"{role}: {rel} → {outcome}, policy says {'writable' if expected else 'not writable'}")
    if not str(result["secret_read"]).startswith("denied"):
        found.append(f"{role}: the untracked .env was readable")
    if result["git_commit"] != "refused":
        found.append(f"{role}: git commit succeeded inside the sandbox")
    if own and result["symlink_write"] != "denied:OSError":
        found.append(f"{role}: a write through a symlink into {victim} was {result['symlink_write']}")
    expected_strays = ["probe-at-root.txt"] if shadow_writable else []
    if result["strays"] != expected_strays:
        found.append(f"{role}: stray writes {result['strays']}, expected {expected_strays}")
    return found


def containment_self_test(config: ProjectConfig, workdir: Path | None = None) -> SelfTestReport:  # pylint: disable=too-many-locals
    """Run the containment matrix in a disposable repository. See the module docstring."""
    bwrap = check_bwrap()
    with tempfile.TemporaryDirectory(prefix="fc-containment-") as tmp:
        base = Path(workdir or tmp)
        repo = base / "repo"
        init_demo_repo(repo, SELF_TEST_FILES)
        for rel, text in UNTRACKED_SECRETS.items():
            (repo / rel).write_text(text, encoding="utf-8")
        before = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True,
                                check=True).stdout.strip()
        policy = PathPolicy(repo, config)
        builder = SandboxBuilder(repo, config, policy, bwrap=bwrap)
        report = SelfTestReport()
        for role in config.roles:
            roots = builder.writable_roots(role)
            own = next((r for r in roots if (repo / r).is_dir()), "")
            victim = "frontend/src" if role != "frontend" else "backend/src"
            spec = {"targets": list(WRITE_TARGETS), "outside": str(base / "outside.txt"), "own": own,
                    "victim": victim}
            # The Frontend role runs Codex, whose nested sandbox needs a writable shadow root.
            shadow_writable = config.provider_for(role).kind.value == "codex"
            plan = builder.plan(run_dir=base / "runs" / role, role=role, network=False,
                                shadow_writable=shadow_writable)
            out_path = base / "runs" / role / "probe.json"
            sup = supervise(plan.argv(["/usr/bin/python3", "-c", PROBE, json.dumps(spec)]), cwd=repo,
                            env={"PATH": os.environ.get("PATH", "")}, stdout_path=out_path,
                            stderr_path=out_path.with_suffix(".err"), timeout_seconds=60)
            strays, _residue = plan.stray_writes()
            plan.discard_shadow()
            try:
                result = json.loads(out_path.read_text(encoding="utf-8"))
            except ValueError:
                report.mismatches.append(f"{role}: probe failed ({sup.status}, exit {sup.exit_code}): "
                                         f"{out_path.with_suffix('.err').read_text(encoding='utf-8')[-300:]}")
                continue
            result.update(writable_roots=roots, strays=strays, shadow_writable=shadow_writable)
            report.roles[role] = result
            report.mismatches += _role_mismatches(policy, role, result, own=own, victim=victim)
            if (repo / "probe-at-root.txt").exists():
                report.mismatches.append(f"{role}: a root-level write reached the real checkout")
        after = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True,
                               check=True).stdout.strip()
        if (base / "outside.txt").exists() or after != before:
            report.repo_unchanged_outside_roots = False
            report.mismatches.append("a write escaped the sandbox (outside file or a new commit)")
        protected_changed = [rel for rel in ("backend/data/users.db", "content/foods/items.csv", "README.md",
                                             "scripts/mxfix/fixer.py", "scripts/deploy.sh")
                             if (repo / rel).read_text(encoding="utf-8") != SELF_TEST_FILES[rel]]
        if protected_changed:
            report.repo_unchanged_outside_roots = False
            report.mismatches.append(f"protected files changed: {protected_changed}")
        return report


def build_runners(config: ProjectConfig, env: Mapping[str, str] | None = None) -> dict[str, Runner]:
    """Real runners per role: Claude Code / Codex in bwrap; OpenAI-compatible over the loopback-only
    HTTP transport (remote execution stays disabled in Phase 3)."""
    runners: dict[str, Runner] = {}
    for name, role in config.roles.items():
        profile = config.providers[role.provider]
        if isinstance(profile, CliRunnerProfile):
            runners[name] = RealCliRunner(profile, config)
        elif isinstance(profile, OpenAICompatibleProfile):
            runners[name] = OpenAICompatibleRunner(profile, dict(env if env is not None else os.environ),
                                                   HttpChatTransport())
    return runners


TRACKERS = ("local", "github")


def make_tracker(kind: str, config: ProjectConfig, state_dir: Path) -> TrackerClient:
    """`local`: the JSON tracker in the state directory. `github`: the real GitHub adapter
    (the authenticated `gh` CLI; writes limited by `github.write_scope`)."""
    if kind == "github":
        from .github_api import GitHubClient  # pylint: disable=import-outside-toplevel
        return GitHubClient.from_config(config.github)
    if kind != "local":
        raise ValueError(f"unknown tracker {kind!r}; choose one of {TRACKERS}")
    return MockIssueTracker(state_dir / "tracker.json")


def make_board(kind: str, config: ProjectConfig, state_dir: Path, tracker: TrackerClient) -> Any:
    """The project board client for the tracker *kind*, or None when `github.project` is disabled.
    `github`: GraphQL through the same GitHubClient (one pacing and rate-limit budget);
    `local`: a JSON board next to the local tracker, created already set up (like the real one)."""
    if not config.github.project.enabled:
        return None
    if kind == "github":
        from .github_projects import ProjectsClient  # pylint: disable=import-outside-toplevel
        return ProjectsClient(tracker)  # type: ignore[arg-type]
    from .projects_local import LocalProjectBoard  # pylint: disable=import-outside-toplevel
    return LocalProjectBoard.for_config(config.github.project, lambda: tracker.list_issues(state="all"),
                                        repository=config.github.repository, path=state_dir / "project-board.json",
                                        configured=True)


class DetachedPublisher:
    """Shows a running task's state on GitHub without the worker making a GitHub request: it
    starts `feature.py github sync FEATURE --apply` as a separate process (no inherited file
    descriptors, so none of the worker's flocks; its own session) and never waits for it.
    Finished children are reaped on the next call; the output goes to <state>/logs/."""

    def __init__(self, argv_prefix: list[str], log_dir: Path) -> None:
        self.argv_prefix = list(argv_prefix)
        self.log_dir = log_dir
        self.procs: list[subprocess.Popen] = []

    @classmethod
    def for_cli(cls, repo: Path, state_dir: Path, tracker_kind: str,
                project_config: str | None = None) -> "DetachedPublisher":
        """A publisher that runs this checkout's feature.py with the same repository, state
        directory, tracker and configuration."""
        argv = [sys.executable, str(Path(__file__).resolve().parents[1] / "feature.py"), "--repo", str(repo),
                "--state-dir", str(state_dir), "--tracker", tracker_kind]
        if project_config:
            argv += ["--project-config", str(project_config)]
        return cls(argv, state_dir / "logs")

    def __call__(self, feature_id: str) -> None:
        self.reap()
        self.log_dir.mkdir(parents=True, exist_ok=True)
        with open(self.log_dir / f"publish-{feature_id}.log", "a", encoding="utf-8") as log:
            self.procs.append(subprocess.Popen(  # pylint: disable=consider-using-with
                [*self.argv_prefix, "github", "sync", feature_id, "--apply"], stdin=subprocess.DEVNULL,
                stdout=log, stderr=subprocess.STDOUT, close_fds=True, start_new_session=True))

    def reap(self) -> None:
        """Forget children that have exited."""
        self.procs = [p for p in self.procs if p.poll() is None]

    def wait(self, timeout: float = 120.0) -> list[int]:
        """Wait for every child (tests and the live check); returns their exit codes."""
        codes = [p.wait(timeout=timeout) for p in self.procs]
        self.procs = []
        return codes


def build_coordinator(config: ProjectConfig, repo: Path, state_dir: Path, *,  # pylint: disable=too-many-arguments
                      runners: Mapping[str, Runner] | None = None,
                      tracker: TrackerClient | None = None, tracker_kind: str = "local",
                      publisher: Any = None) -> FeatureCoordinator:
    """A coordinator with real runners, sandboxed gates, the given tracker (default: local JSON)
    and — when `github.project` is enabled — the project board of the same kind."""
    from .project_sync import ProjectSync  # pylint: disable=import-outside-toplevel
    store = StateStore(state_dir)
    tracker = tracker or make_tracker(tracker_kind, config, state_dir)
    sync = IssueSync(store, tracker, config)
    client = make_board(tracker_kind, config, state_dir, tracker)
    board = ProjectSync(store, client, sync, config) if client is not None else None
    return FeatureCoordinator(config, repo, store, tracker=tracker, runners=runners or build_runners(config),
                              gate_runner=SandboxedGateRunner(config, state_dir / "logs"), sync=sync, board=board,
                              publisher=publisher)
