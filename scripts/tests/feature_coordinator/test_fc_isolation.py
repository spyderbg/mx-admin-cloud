"""W1 security acceptance tests: credential isolation (D-20), network isolation (D-21) and safe failure.

Every run is a REAL bubblewrap sandbox exactly as a real runner gets it (RealCliRunner, the
broker channel, a broker), with the fake agent standing in for the CLI, a fake HOME full of
FAKE logins, FAKE API keys in the worker's environment and a fake provider behind the broker.
No real credential is read or needed, and nothing leaves 127.0.0.1.
"""

# pylint: disable=missing-function-docstring,redefined-outer-name

from __future__ import annotations

import dataclasses
import json
import os
import socket
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from feature_coordinator.config import ProjectConfig
from feature_coordinator.demo import init_demo_repo
from feature_coordinator.gates import GateContext, SandboxedGateRunner
from feature_coordinator.isolation_check import TEST_NET, isolation_self_test
from feature_coordinator.launchers import RealCliRunner, missing_dependencies, run_context
from feature_coordinator.netns_channel import BrokerChannel
from feature_coordinator.operations import SELF_TEST_FILES
from feature_coordinator.paths import PathPolicy, ScopedWorkspace
from feature_coordinator.providers import ProviderKind
from feature_coordinator.runners import RunRequest
from feature_coordinator.sandbox import Bind, SandboxPlan

from fc_fake_provider import FakeProvider
from fc_helpers import (
    FAKE_ENV_KEY,
    FAKE_HOME_FILES,
    FAKE_HOST_LOGIN,
    FAKE_UPSTREAM_KEY,
    actions_prompt,
    fake_broker_factory,
)

pytestmark = pytest.mark.usefixtures("needs_bwrap")

MARKERS = [FAKE_HOST_LOGIN, FAKE_ENV_KEY, FAKE_UPSTREAM_KEY]
ROLE_OF = {"claude": "backend", "codex": "frontend"}
MODEL_CALL = {"claude": {"path": "/v1/messages"},
              "codex": {"path": "/v1/responses", "body": {"model": "m", "input": [], "stream": True}}}
HOST_SECRETS = {"ANTHROPIC_API_KEY": FAKE_ENV_KEY, "OPENAI_API_KEY": FAKE_ENV_KEY, "CODEX_API_KEY": FAKE_ENV_KEY,
                "CLAUDE_CODE_OAUTH_TOKEN": FAKE_ENV_KEY, "GH_TOKEN": FAKE_ENV_KEY, "GITHUB_TOKEN": FAKE_ENV_KEY,
                "AGENT_QWEN_API_KEY": FAKE_ENV_KEY, "HTTPS_PROXY": "http://127.0.0.1:3128",
                "DISPLAY": ":0", "WAYLAND_DISPLAY": "wayland-0", "SSH_AUTH_SOCK": "/run/user/1000/ssh-agent",
                "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus", "XDG_RUNTIME_DIR": "/run/user/1000"}


@pytest.fixture
def repo3(tmp_path: Path) -> Path:
    path = tmp_path / "projects" / "repo3"
    init_demo_repo(path, SELF_TEST_FILES)
    return path


@pytest.fixture
def host(fake_home: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The worker's environment carries FAKE API keys and desktop-session variables."""
    for name, value in HOST_SECRETS.items():
        monkeypatch.setenv(name, value)
    return fake_home


@pytest.fixture
def provider():
    with FakeProvider() as fake:
        yield fake


def run_agent(repo: Path, config: ProjectConfig, provider_name: str, actions: list[dict], tmp: Path, *,  # pylint: disable=too-many-arguments
              brokers: list | None = None, port: int | None = None, runner_kwargs: dict | None = None,
              **ctx: Any):
    role = ROLE_OF[provider_name]
    runner = RealCliRunner(config.providers[provider_name], config,  # type: ignore[arg-type]
                           **({"broker_factory": fake_broker_factory(port, brokers)} | (runner_kwargs or {})))
    request = RunRequest(f"w1.{role}", role, actions_prompt(actions), ScopedWorkspace(PathPolicy(repo, config), role),
                         context=run_context(tmp / "state" / "runs", f"w1.{role}", **ctx))
    return runner.run(request)


def outcomes(result) -> dict[str, dict]:
    return {r["action"]: r for r in json.loads(result.transcript[0])["report"]}


def env_keys(result) -> list[str]:
    return json.loads(result.transcript[0])["env_keys"]


def run_files(result) -> dict[str, str]:
    root = Path(result.details["run_dir"])
    return {str(p.relative_to(root)): p.read_text(encoding="utf-8", errors="replace")
            for p in root.rglob("*") if p.is_file()}


# -- D-20: credential isolation -------------------------------------------------------------------------

@pytest.mark.parametrize("provider_name", ["claude", "codex"])
def test_no_credential_reaches_the_runner_sandbox(provider_name, repo3, agent_config, host, provider, tmp_path) -> None:
    """Credential files are not mounted; API keys are not forwarded; nothing is in the environment,
    in any process's arguments or environment (/proc), in files the sandbox can read, or in the
    generated settings; the broker's upstream key stays outside; the run's own token is in no
    command line and no file."""
    brokers: list = []
    probe_paths = [str(host / rel) for rel in FAKE_HOME_FILES]
    actions = [{"do": "exists", "label": "home credentials", "paths": probe_paths},
               {"do": "http", "label": "model request", **MODEL_CALL[provider_name]},
               {"do": "scan", "label": "scan", "markers": MARKERS, "session_token": True,
                "roots": [str(host), str(repo3), "/tmp", "/run", "/etc"]}]
    result = run_agent(repo3, agent_config, provider_name, actions, tmp_path, brokers=brokers, port=provider.port)
    seen = outcomes(result)
    assert result.status in ("completed", "rejected"), result.error
    assert seen["home credentials"]["value"] == []                         # not mounted
    assert seen["scan"]["value"]["hits"] == []                             # env, /proc, files: nothing
    assert seen["model request"]["value"]["status"] == 200                 # the broker path works
    assert not set(env_keys(result)) & set(HOST_SECRETS)                   # nothing forwarded
    upstream = provider.requests[0]["headers"]
    assert FAKE_UPSTREAM_KEY in upstream.get("x-api-key", upstream.get("authorization", ""))
    token = brokers[0].session.token
    for name, text in run_files(result).items():                           # settings, sandbox.json, logs
        for secret in ((token,) if name == "prompt.txt" else (*MARKERS, token)):  # the prompt carries the markers
            assert secret not in text, (name, secret[:12])
    assert brokers[0].describe()["revoked"] is True


def test_agent_written_python_code_cannot_read_credentials(repo3, agent_config, host, provider, tmp_path) -> None:
    code = (
        "import glob, os, json\n"
        f"markers = {MARKERS!r}\n"
        "found = []\n"
        f"for path in glob.glob({str(host)!r} + '/**', recursive=True) + glob.glob('/proc/*/environ'):\n"
        "    try:\n"
        "        data = open(path, 'rb').read()\n"
        "    except OSError:\n"
        "        continue\n"
        "    found += [path for m in markers if m.encode() in data]\n"
        "found += [k for k, v in os.environ.items() if any(m in v for m in markers)]\n"
        "print('FOUND=' + json.dumps(found))\n")
    actions = [{"do": "write", "path": "backend/src/steal.py", "text": code},
               {"do": "exec", "label": "agent code", "argv": ["/usr/bin/python3", "backend/src/steal.py"]}]
    result = run_agent(repo3, agent_config, "claude", actions, tmp_path, port=provider.port)
    assert result.status == "completed", result.error
    assert outcomes(result)["agent code"]["value"].strip() == "FOUND=[]"


@pytest.mark.usefixtures("host")
def test_broker_authorization_is_enforced_for_the_runner(repo3, agent_config, provider, tmp_path) -> None:
    brokers: list = []
    actions = [{"do": "http", "label": "no token", "token": None},
               {"do": "http", "label": "wrong token", "token": "not-the-session-token"},
               {"do": "http", "label": "upstream key as token", "token": FAKE_UPSTREAM_KEY},
               {"do": "http", "label": "unapproved operation", "path": "/v1/models", "method": "GET"},
               {"do": "http", "label": "other provider's operation", "path": "/v1/responses"},
               {"do": "http", "label": "approved"}]
    result = run_agent(repo3, agent_config, "claude", actions, tmp_path, brokers=brokers, port=provider.port)
    status = {k: v["value"]["status"] for k, v in outcomes(result).items()}
    assert status == {"no token": 401, "wrong token": 401, "upstream key as token": 401, "unapproved operation": 403,
                      "other provider's operation": 403, "approved": 200}
    assert len(provider.requests) == 1                                     # only the approved request left
    broker = brokers[0]
    assert broker.session.admit(f"Bearer {broker.session.token}") == "revoked"   # dead after the run
    log = (Path(result.details["run_dir"]) / "broker.log").read_text(encoding="utf-8")
    assert [json.loads(line)["status"] for line in log.splitlines()] == [401, 401, 401, 403, 403, 200]


# -- D-21: network isolation --------------------------------------------------------------------------------

def test_the_runner_sandbox_has_no_network_but_the_broker(repo3, agent_config, host, tmp_path) -> None:
    loopback = socket.create_server(("127.0.0.1", 0))
    anywhere = socket.create_server(("0.0.0.0", 0))
    abstract = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    abstract.bind(f"\0fc-w1-desktop-{os.getpid()}")
    abstract.listen(2)
    x11 = sorted(os.listdir("/tmp/.X11-unix")) if os.path.isdir("/tmp/.X11-unix") else []
    try:
        actions = [{"do": "connect", "label": "external", "host": TEST_NET, "port": 443},
                   {"do": "resolve", "label": "dns", "host": "example.com"},
                   {"do": "connect", "label": "host localhost", "host": "127.0.0.1",
                    "port": loopback.getsockname()[1]},
                   {"do": "connect_unix", "label": "desktop abstract socket", "path": f"@fc-w1-desktop-{os.getpid()}"},
                   *[{"do": "connect_unix", "label": f"x11 {n}", "path": f"@/tmp/.X11-unix/{n}"} for n in x11],
                   {"do": "exists", "label": "session paths",
                    "paths": ["/tmp/.X11-unix", "/run/user", "/var/run/dbus", str(host / ".ssh")]},
                   {"do": "connect", "label": "broker", "host": "127.0.0.1", "port": 47613}]
        lan = _lan_ip()
        if lan:
            actions.append({"do": "connect", "label": "host LAN", "host": lan, "port": anywhere.getsockname()[1]})
        result = run_agent(repo3, agent_config, "claude", actions, tmp_path)
    finally:
        for sock in (loopback, anywhere, abstract):
            sock.close()
    seen = outcomes(result)
    assert "Errno 101" in seen["external"]["error"]                        # no route at all
    assert not seen["dns"]["ok"] and not seen["host localhost"]["ok"]
    assert not seen["desktop abstract socket"]["ok"] and seen["session paths"]["value"] == []
    assert all(not seen[f"x11 {n}"]["ok"] for n in x11)
    assert not lan or not seen["host LAN"]["ok"]
    assert seen["broker"]["ok"]                                            # the one route, in its own namespace
    assert not {"DISPLAY", "WAYLAND_DISPLAY", "SSH_AUTH_SOCK", "DBUS_SESSION_BUS_ADDRESS", "HTTPS_PROXY"} & \
        set(env_keys(result))


def _lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
            udp.connect((TEST_NET, 9))
            address = udp.getsockname()[0]
    except OSError:
        return ""
    return "" if address.startswith("127.") else address


def test_worker_isolation_self_test_passes(config: ProjectConfig, host: Path) -> None:
    del host
    report = isolation_self_test(config)
    assert report.ok, report.failures
    assert {"broker endpoint (the runner's only route)", "host localhost service", "external host (TEST-NET-1)",
            "X11 display sockets", "credential and session paths", "other projects next to the repository"} <= \
        set(report.checks)


@pytest.mark.usefixtures("host")
def test_missing_network_isolation_blocks_execution(repo3, agent_config, tmp_path,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """If a sandbox ever shared the worker's network namespace, the handshake refuses it and the
    runner never starts — and the isolation self-test fails."""
    original = SandboxPlan.argv

    def shared(self, command):
        argv = original(self, command)
        return [*argv[:4], "--share-net", *argv[4:]]

    monkeypatch.setattr(SandboxPlan, "argv", shared)
    result = run_agent(repo3, agent_config, "claude", [{"do": "write", "path": "backend/src/ran.py", "text": "x\n"}],
                       tmp_path)
    assert result.status == "failed" and "network isolation" in result.error
    assert not (repo3 / "backend/src/ran.py").exists()
    report = isolation_self_test(agent_config)
    assert not report.ok and "network isolation" in " ".join(report.failures)


def test_offline_gates_stay_offline(repo3: Path, config: ProjectConfig, tmp_path: Path) -> None:
    server = socket.create_server(("127.0.0.1", 0))
    script = ("import socket\n"
              f"for target in (('127.0.0.1', {server.getsockname()[1]}), ('{TEST_NET}', 443), ('127.0.0.1', 47613)):\n"
              "    try:\n"
              "        socket.create_connection(target, timeout=2); print('CONNECTED')\n"
              "    except OSError as exc:\n"
              "        print('refused', exc.errno)\n")
    gates = SandboxedGateRunner(config, tmp_path / "logs")
    result = gates.run_command("net-probe", ["python3", "-c", script], GateContext(repo3), cwd=repo3)
    server.close()
    log = (Path(result.log) / "stdout.log").read_text(encoding="utf-8")
    assert result.status == "passed" and "CONNECTED" not in log and log.count("refused") == 3


# -- failure handling ----------------------------------------------------------------------------------------

@pytest.mark.usefixtures("host")
def test_no_broker_credential_means_unavailable_before_any_sandbox(repo3, agent_config, tmp_path) -> None:
    result = run_agent(repo3, agent_config, "claude", [], tmp_path, runner_kwargs={"broker_factory": None})
    assert result.status == "unavailable" and "D-20" in result.error
    assert not list((tmp_path / "state" / "runs").rglob("sandbox.json"))   # nothing was started


@pytest.mark.usefixtures("host")
def test_missing_sandbox_dependency_means_unavailable(repo3, agent_config, tmp_path) -> None:
    assert not missing_dependencies(ProviderKind.CLAUDE_CODE) and not missing_dependencies(ProviderKind.CODEX)
    result = run_agent(repo3, agent_config, "claude", [], tmp_path,
                       runner_kwargs={"dependency_check": lambda kind: ["socat"]})
    assert result.status == "unavailable" and "socat" in result.error


@pytest.mark.usefixtures("host")
def test_sandbox_start_up_failure_fails_the_run(repo3, agent_config, tmp_path,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    brokers: list = []
    monkeypatch.setattr(BrokerChannel, "bind", staticmethod(
        lambda: Bind(str(tmp_path / "no-such-bootstrap.py"), "/run/fc-broker/bootstrap.py", "ro")))
    result = run_agent(repo3, agent_config, "claude", [{"do": "write", "path": "backend/src/ran.py", "text": "x\n"}],
                       tmp_path, brokers=brokers)
    assert result.status == "failed" and "sandbox start-up failed" in result.error
    assert not (repo3 / "backend/src/ran.py").exists() and brokers[0].session.revoked


@pytest.mark.usefixtures("host")
def test_broker_crash_mid_run_fails_closed(repo3, agent_config, provider, tmp_path) -> None:
    brokers: list = []
    actions = [{"do": "http", "label": "before", "required": True},
               {"do": "write", "path": "backend/.ready", "text": "1"},
               {"do": "wait_for", "path": "backend/.go", "timeout": 30},
               {"do": "http", "label": "after", "required": True}]
    box: dict = {}
    thread = threading.Thread(target=lambda: box.update(result=run_agent(
        repo3, agent_config, "claude", actions, tmp_path, brokers=brokers, port=provider.port)))
    thread.start()
    assert _wait((repo3 / "backend/.ready").exists)
    brokers[0].stop()                                                      # the broker dies mid-run
    (repo3 / "backend/.go").write_text("1", encoding="utf-8")
    thread.join(60)
    result = box["result"]
    seen = outcomes(result)
    assert seen["before"]["value"]["status"] == 200 and not seen["after"]["ok"]
    assert result.status == "failed" and len(provider.requests) == 1      # no fallback route


@pytest.mark.usefixtures("host")
def test_runner_crash_is_reported_and_revokes_the_session(repo3, agent_config, provider, tmp_path) -> None:
    brokers: list = []
    result = run_agent(repo3, agent_config, "claude", [{"do": "http"}, {"do": "crash"}], tmp_path,
                       brokers=brokers, port=provider.port)
    assert result.status == "failed" and brokers[0].session.revoked
    assert brokers[0].session.admit(f"Bearer {brokers[0].session.token}") == "revoked"


@pytest.mark.parametrize("outcome", ["cancelled", "timeout"])
@pytest.mark.usefixtures("host")
def test_cancellation_and_timeout_revoke_the_session(outcome, repo3, agent_config, tmp_path) -> None:
    brokers: list = []
    started = time.monotonic()
    ctx = {"cancelled": lambda: time.monotonic() - started > 2} if outcome == "cancelled" else {"timeout_seconds": 2}
    config = dataclasses.replace(agent_config, runtime=dataclasses.replace(agent_config.runtime,
                                                                           terminate_grace_seconds=1))
    result = run_agent(repo3, config, "claude", [{"do": "sleep", "seconds": 60}], tmp_path, brokers=brokers, **ctx)
    assert result.status == outcome and brokers[0].session.revoked and time.monotonic() - started < 30


def _wait(predicate, timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_the_probes_detect_a_reintroduced_mount_or_forwarded_key(repo3, agent_config, host, provider, tmp_path,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    """Control: re-create the pre-W1 exposure (a login file bound read-only, an API key forwarded)
    and check that the same probes report it — the passing tests above are not vacuous."""
    from feature_coordinator.launchers import ClaudeCodeLauncher  # pylint: disable=import-outside-toplevel
    original = ClaudeCodeLauncher.prepare
    login = str(host / ".claude/.credentials.json")

    def exposed(self, request, run_dir, writable):
        spec = original(self, request, run_dir, writable)
        spec.env["ANTHROPIC_API_KEY"] = FAKE_ENV_KEY
        spec.binds.append(Bind(login, login, "ro"))
        return spec

    monkeypatch.setattr(ClaudeCodeLauncher, "prepare", exposed)
    actions = [{"do": "exists", "label": "home credentials", "paths": [login]},
               {"do": "scan", "label": "scan", "markers": MARKERS, "roots": [str(host)]}]
    seen = outcomes(run_agent(repo3, agent_config, "claude", actions, tmp_path, port=provider.port))
    hits = seen["scan"]["value"]["hits"]
    assert seen["home credentials"]["value"] == [login]
    assert f"{login}:0" in hits and "env:1" in hits and any(h.startswith("/proc/") and h.endswith(":1") for h in hits)


# -- the operator's eligibility check ------------------------------------------------------------------------

def test_worker_check_is_ineligible_without_a_broker_credential(host, capsys: pytest.CaptureFixture) -> None:
    from feature_coordinator.cli import main as cli_main  # pylint: disable=import-outside-toplevel
    del host
    assert cli_main(["worker-check"]) == 0                                 # dry run
    assert cli_main(["worker-check", "--apply"]) == 1
    out = capsys.readouterr().out
    assert "INELIGIBLE" in out and "D-20" in out and FAKE_UPSTREAM_KEY not in out


def test_worker_check_is_eligible_only_with_an_operator_credential(host, tmp_path: Path,
                                                                   capsys: pytest.CaptureFixture) -> None:
    import shutil  # pylint: disable=import-outside-toplevel
    import yaml  # pylint: disable=import-outside-toplevel
    from feature_coordinator.cli import EXAMPLE_CONFIG, main as cli_main  # pylint: disable=import-outside-toplevel
    del host
    if not (shutil.which("claude") and shutil.which("codex")):
        pytest.skip("needs the installed claude and codex CLIs (probed with --version/--help only)")
    key = tmp_path / "keys" / "fake.key"
    key.parent.mkdir()
    key.write_text(FAKE_UPSTREAM_KEY + "\n", encoding="ascii")
    key.chmod(0o600)
    data = yaml.safe_load(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
    data["broker"].update(credential_source="api-key-file", api_key_files={"claude": str(key), "codex": str(key)})
    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    assert cli_main(["--project-config", str(config_file), "worker-check", "--apply"]) == 0
    out = capsys.readouterr().out
    assert "Worker: ELIGIBLE" in out and FAKE_UPSTREAM_KEY not in out
    key.chmod(0o644)                                                       # a readable key file is refused
    assert cli_main(["--project-config", str(config_file), "worker-check", "--apply"]) == 1
