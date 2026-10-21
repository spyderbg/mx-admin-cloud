"""W1 compatibility of the INSTALLED Claude Code and Codex with the brokered, network-isolated launch.

The real CLIs run through the real launcher command lines, our sandbox and a broker — but the
broker's upstream is a local FAKE provider with a FAKE key, the sandbox has no route to any real
endpoint, and HOME is a fake one. No model is called, nothing is paid, no login is read.

The fake model asks for one shell command, a probe, so these tests also show what a command the
AGENT runs can reach inside the CLI's own nested sandbox (Claude Code's strict Bash sandbox,
Codex's workspace-write sandbox): not the broker, not the runner's token, no network, no socket.
Skipped when a CLI is not installed.
"""

# pylint: disable=missing-function-docstring,redefined-outer-name

from __future__ import annotations

import dataclasses
import json
import shutil
from pathlib import Path

import pytest

from feature_coordinator.config import ProjectConfig
from feature_coordinator.demo import init_demo_repo
from feature_coordinator.launchers import RealCliRunner, run_context
from feature_coordinator.operations import SELF_TEST_FILES
from feature_coordinator.paths import PathPolicy, ScopedWorkspace
from feature_coordinator.runners import RunRequest

from fc_fake_provider import FakeProvider
from fc_helpers import FAKE_HOME_FILES, FAKE_UPSTREAM_KEY, fake_broker_factory

pytestmark = pytest.mark.usefixtures("needs_bwrap")

PROBE = r"""
import json, os, socket
out = {"broker_variables": sorted(k for k in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "FC_BROKER_TOKEN",
                                              "ANTHROPIC_API_KEY", "OPENAI_API_KEY") if k in os.environ)}
def attempt(fn):
    try:
        return fn()
    except OSError as exc:
        return "errno:%s" % exc.errno
def tcp(host, port):
    def go():
        with socket.create_connection((host, port), timeout=2):
            return "connected"
    return attempt(go)
def unix():
    def go():
        with socket.socket(socket.AF_UNIX) as sock:
            sock.connect("\0fc-compat")
            return "connected"
    return attempt(go)
out["broker_endpoint"] = tcp("127.0.0.1", 47613)
out["external"] = tcp("192.0.2.1", 443)
out["dns"] = attempt(lambda: "resolved:" + socket.getaddrinfo("example.com", 443)[0][4][0])
out["unix_socket"] = unix()
pids = [p for p in os.listdir("/proc") if p.isdigit()]
out["visible_pids"] = len(pids)
leaks = []
for pid in pids:
    try:
        data = open("/proc/%s/environ" % pid, "rb").read()
    except OSError:
        continue
    if b"ANTHROPIC_AUTH_TOKEN=" in data or b"FC_BROKER_TOKEN=" in data:
        leaks.append(pid)
out["token_in_proc"] = leaks
out["home_logins"] = [p for p in json.loads(os.environ.get("FC_PROBE_PATHS", "[]")) if os.path.lexists(p)]
print("PROBE" + json.dumps(out))
"""


def installed(executable: str) -> None:
    if not shutil.which(executable):
        pytest.skip(f"{executable} is not installed")


@pytest.fixture
def repo(tmp_path: Path, fake_home: Path) -> Path:
    path = tmp_path / "repo"
    init_demo_repo(path, SELF_TEST_FILES)
    paths = json.dumps([str(fake_home / rel) for rel in FAKE_HOME_FILES])
    (path / "fc_probe.py").write_text(f"import os\nos.environ['FC_PROBE_PATHS'] = {paths!r}\n" + PROBE,
                                      encoding="utf-8")
    return path


def probe_config(config: ProjectConfig) -> ProjectConfig:
    """The built-in configuration, plus the probe on the Backend allowlist (test only)."""
    commands = dict(config.runtime.role_commands)
    commands["backend"] = (*commands["backend"], "python3 fc_probe.py:*")
    return dataclasses.replace(config, runtime=dataclasses.replace(config.runtime, role_commands=commands))


def run(repo: Path, config: ProjectConfig, provider: str, role: str, prompt: str, tmp: Path, brokers: list,  # pylint: disable=too-many-arguments
        port: int):
    runner = RealCliRunner(config.providers[provider], config,  # type: ignore[arg-type]
                           broker_factory=fake_broker_factory(port, brokers))
    request = RunRequest(f"compat.{role}", role, prompt, ScopedWorkspace(PathPolicy(repo, config), role),
                         context=run_context(tmp / "runs", f"compat.{role}", timeout_seconds=240))
    return runner.run(request)


def probe_output(text: str) -> dict:
    """The probe's JSON, as quoted (escaped once) inside the fake model's final answer."""
    return json.JSONDecoder().raw_decode(text.split("PROBE", 1)[1].replace('\\"', '"'))[0]


def check_nested(probe: dict, *, cli_files: tuple[str, ...] = ()) -> None:
    assert probe["broker_variables"] == []                     # the runner's token never reaches a command
    assert probe["broker_endpoint"].startswith("errno:")       # own namespace: the broker is unreachable
    assert probe["external"].startswith("errno:") and probe["dns"].startswith("errno:")
    assert probe["unix_socket"] == "errno:1"                   # sockets blocked (seccomp)
    assert probe["visible_pids"] <= 4 and probe["token_in_proc"] == []
    assert [Path(p).name for p in probe["home_logins"]] == list(cli_files)  # no host login file


def no_secret_in_run_files(run_dir: Path, token: str) -> None:
    """Nothing the CLI or the coordinator wrote (private HOME, settings, logs) holds a secret value."""
    for path in run_dir.rglob("*"):
        if path.is_file():
            data = path.read_bytes()
            assert token.encode() not in data and FAKE_UPSTREAM_KEY.encode() not in data, path


def test_installed_claude_code_works_brokered_and_runs_commands_nested(repo, config, tmp_path) -> None:
    installed("claude")
    brokers: list = []
    with FakeProvider() as fake:
        result = run(repo, probe_config(config), "claude", "backend", "Run the probe.\nFAKE-TOOL:python3 fc_probe.py\n",
                     tmp_path, brokers, fake.port)
    assert result.status == "completed", (result.error, result.details)
    run_dir = Path(result.details["run_dir"])
    events = [json.loads(line) for line in (run_dir / "stdout.jsonl").read_text(encoding="utf-8").splitlines()
              if line.startswith("{")]
    init = next(e for e in events if e.get("type") == "system" and e.get("subtype") == "init")
    assert init["mcp_servers"] == [] and init["permissionMode"] == "acceptEdits" and init["apiKeySource"] == "none"
    assert sorted(init["tools"]) == ["Bash", "Edit", "Glob", "Grep", "Read", "Write"]
    assert result.details["permission_denials"] == [] and result.transcript[0].startswith("TOOLRESULT:")
    # Claude Code creates its own (credential-free) ~/.claude.json in the private HOME.
    check_nested(probe_output(result.transcript[0]), cli_files=(".claude.json",))
    no_secret_in_run_files(run_dir, brokers[0].session.token)
    assert {(r["method"], r["path"], r["query"]) for r in fake.requests} == {("POST", "/v1/messages", "beta=true")}
    for request in fake.requests:                              # the upstream saw only the broker's key
        assert request["headers"]["x-api-key"] == FAKE_UPSTREAM_KEY and "authorization" not in request["headers"]
        assert brokers[0].session.token.encode() not in request["body"]
    assert brokers[0].describe()["refused"] == {} and brokers[0].session.revoked


def test_installed_claude_code_denies_commands_outside_the_role_allowlist(repo, config, tmp_path) -> None:
    installed("claude")
    with FakeProvider() as fake:
        result = run(repo, probe_config(config), "claude", "backend",
                     "Fetch it.\nFAKE-TOOL:python3 -c 'import urllib.request'\n", tmp_path, [], fake.port)
    assert result.status == "completed", result.error
    assert result.details["permission_denials"] == ["Bash"] and "PROBE" not in result.transcript[0]


def test_installed_codex_works_brokered_and_runs_commands_nested(repo, config, tmp_path) -> None:
    installed("codex")
    brokers: list = []
    with FakeProvider() as fake:
        result = run(repo, config, "codex", "frontend", "Run the probe.\nFAKE-SHELL:python3 fc_probe.py\n",
                     tmp_path, brokers, fake.port)
    assert result.status in ("completed", "rejected"), (result.error, result.details)
    assert not result.stray_writes and not result.changed_paths
    assert result.transcript[0].startswith("TOOLRESULT:")
    probe = probe_output(result.transcript[0])
    assert probe["unix_socket"] == "errno:1" and probe["broker_endpoint"] == "errno:1"   # no socket() at all
    check_nested(probe)
    no_secret_in_run_files(Path(result.details["run_dir"]), brokers[0].session.token)
    assert {(r["method"], r["path"]) for r in fake.requests} == {("POST", "/v1/responses")}
    for request in fake.requests:
        assert request["headers"]["authorization"] == f"Bearer {FAKE_UPSTREAM_KEY}"
        tools = [t.get("name") or t.get("type") for t in json.loads(request["body"]).get("tools", [])]
        assert "web_search" not in tools                         # no hosted web search
    assert brokers[0].describe()["refused"] == {} and brokers[0].session.revoked
