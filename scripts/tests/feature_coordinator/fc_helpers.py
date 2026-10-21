"""Process and Git helpers shared by the feature coordinator tests (temporary repos only)."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import uuid
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2]
TESTS_DIR = Path(__file__).resolve().parent


def git(repo: Path, *args: str) -> str:
    """Run git in *repo* and return its stdout (raises on failure)."""
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


def child_env() -> dict[str, str]:
    """The environment for worker processes: the coordinator package is importable."""
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(SCRIPTS_DIR), str(TESTS_DIR), env.get("PYTHONPATH", "")])
    return env


def run_py(code: str, *args: str, timeout: float = 60) -> subprocess.CompletedProcess[str]:
    """Run *code* in a separate Python process (a separate worker) and wait for it."""
    return subprocess.run([sys.executable, "-c", textwrap.dedent(code), *args], capture_output=True, text=True,
                          env=child_env(), timeout=timeout, check=False)


class Holder:
    """A separate process that runs *code*, prints 'ready' and then waits for stdin to close."""

    def __init__(self, code: str, *args: str) -> None:
        body = textwrap.dedent(code) + "\nimport sys\nprint('ready', flush=True)\nsys.stdin.read()\n"
        self.proc = subprocess.Popen(  # pylint: disable=consider-using-with
            [sys.executable, "-c", body, *args], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, env=child_env())
        line = self.proc.stdout.readline().strip() if self.proc.stdout else ""
        if line != "ready":
            err = self.proc.stderr.read() if self.proc.stderr else ""
            self.proc.kill()
            raise RuntimeError(f"holder failed to start: {line!r} {err}")

    @property
    def pid(self) -> int:
        """The holder process ID."""
        return self.proc.pid

    def stop(self) -> None:
        """Let the process exit normally."""
        if self.proc.poll() is None and self.proc.stdin:
            self.proc.stdin.close()
            self.proc.wait(timeout=30)

    def kill(self) -> None:
        """Crash the process (SIGKILL) and reap it."""
        self.proc.kill()
        self.proc.wait(timeout=30)


FAKE_AGENT = Path(__file__).with_name("fc_fake_agent.py")
# W1: unique per test session (shared with worker subprocesses through the environment), so a
# hit anywhere can only come from this run's fakes. None of these is a real credential.
_SESSION = os.environ.setdefault("FC_TEST_SESSION", uuid.uuid4().hex)
FAKE_UPSTREAM_KEY = f"FAKE-UPSTREAM-KEY-{_SESSION}"       # what the broker sends upstream
FAKE_HOST_LOGIN = f"FAKE-HOST-LOGIN-{_SESSION}"           # fake ~/.claude, ~/.codex, ~/.ssh, gh files
FAKE_ENV_KEY = f"FAKE-ENV-API-KEY-{_SESSION}"             # fake ANTHROPIC_API_KEY / OPENAI_API_KEY on the host
# The fake HOME (conftest.fake_home): runner logins, SSH, GitHub, Git, browser and keyring files.
FAKE_HOME_FILES = (".claude/.credentials.json", ".claude.json", ".codex/auth.json", ".ssh/id_ed25519",
                   ".config/gh/hosts.yml", ".git-credentials", ".netrc", ".mozilla/firefox/profiles.ini",
                   ".config/google-chrome/Default/Login Data", ".local/share/keyrings/login.keyring")


def install_fake_agent(directory: Path, name: str = "fake-agent") -> Path:
    """Copy the fake CLI into *directory* (not named `bin`, so only it is bound into sandboxes)."""
    import shutil  # pylint: disable=import-outside-toplevel
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / name
    shutil.copy(FAKE_AGENT, target)
    target.chmod(0o755)
    return target


def config_with_agents(config, claude: Path, codex: Path | None = None):
    """*config* with the claude and codex providers pointing at fake executables."""
    import dataclasses  # pylint: disable=import-outside-toplevel
    providers = dict(config.providers)
    providers["claude"] = dataclasses.replace(providers["claude"], executable=str(claude))
    providers["codex"] = dataclasses.replace(providers["codex"], executable=str(codex or claude))
    return dataclasses.replace(config, providers=providers)


def fake_broker_factory(upstream_port: int | None = None, created: list | None = None):
    """A broker factory for tests: the FAKE upstream key and a loopback fake provider (by default
    a port nothing listens on). Every broker it makes is appended to *created*."""
    from feature_coordinator.broker import ROUTES, AuthBroker, StaticCredential, Upstream  # pylint: disable=import-outside-toplevel

    def factory(profile, config, ctx, repo_root):
        del repo_root
        route = ROUTES[profile.kind]
        broker = AuthBroker(route, StaticCredential(*route.credential(FAKE_UPSTREAM_KEY)), config.broker,
                            ttl_seconds=(ctx.timeout_seconds or config.runtime.task_timeout_seconds) + 60,
                            upstream=Upstream("http", "127.0.0.1", upstream_port or 9),
                            allow_loopback_upstream=True, log_path=ctx.run_dir / "broker.log")
        if created is not None:
            created.append(broker)
        return broker
    return factory


def fake_runners(config, **kwargs):
    """build_runners() with a fake broker (see fake_broker_factory)."""
    from feature_coordinator.operations import build_runners  # pylint: disable=import-outside-toplevel
    return build_runners(config, broker_factory=fake_broker_factory(**kwargs))


def actions_prompt(actions) -> str:
    """A prompt carrying a fake-agent action list."""
    import json  # pylint: disable=import-outside-toplevel
    return "task\nFAKE-ACTIONS:" + json.dumps(actions)


# A worker process: one real (fake-agent) run of one task, like `feature.py launch --apply`.
# argv: repo state agent task prompt [runtime-overrides-json]
WORKER_SCRIPT = """
import dataclasses, json, sys
from pathlib import Path
from feature_coordinator.config import default_config
from feature_coordinator.demo import RecordingGates
from feature_coordinator.issues import MockIssueTracker
from feature_coordinator.operations import build_runners
from feature_coordinator.store import StateStore
from feature_coordinator.supervisor import install_termination_handlers
from feature_coordinator.workflow import FeatureCoordinator
from fc_helpers import fake_broker_factory
repo, state, agent, task, prompt = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], sys.argv[4], sys.argv[5]
overrides = json.loads(sys.argv[6]) if len(sys.argv) > 6 else {}
config = default_config()
providers = {**config.providers}
for name in ("claude", "codex"):
    providers[name] = dataclasses.replace(providers[name], executable=agent)
config = dataclasses.replace(config, providers=providers, runtime=dataclasses.replace(config.runtime, **overrides))
install_termination_handlers()
coord = FeatureCoordinator(config, repo, StateStore(state), tracker=MockIssueTracker(state / "tracker.json"),
                           runners=build_runners(config, broker_factory=fake_broker_factory()),
                           gate_runner=RecordingGates())
print("started", flush=True)
try:
    run = coord.run_task(task, prompt=prompt)
    print(json.dumps({"state": run.state.value, "runner": run.runner.status, "error": run.runner.error,
                      "commit": run.commit, "changed": run.runner.changed_paths}), flush=True)
except Exception as exc:
    print(json.dumps({"exception": type(exc).__name__, "error": str(exc)}), flush=True)
"""


def start_worker(repo: Path, state: Path, agent: Path, task: str, prompt: str, overrides=None):
    """Start WORKER_SCRIPT; returns the Popen once the worker printed 'started'."""
    import json  # pylint: disable=import-outside-toplevel
    args = [sys.executable, "-c", WORKER_SCRIPT, str(repo), str(state), str(agent), task, prompt]
    if overrides:
        args.append(json.dumps(overrides))
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,  # pylint: disable=consider-using-with
                            env=child_env())
    assert proc.stdout.readline().strip() == "started"
    return proc


def worker_result(proc, timeout: float = 120) -> dict:
    """The JSON line the worker printed last."""
    import json  # pylint: disable=import-outside-toplevel
    out, err = proc.communicate(timeout=timeout)
    lines = out.strip().splitlines()
    assert lines, err
    return json.loads(lines[-1])
