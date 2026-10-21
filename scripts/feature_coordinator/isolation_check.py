"""Network and credential isolation self-test for runner sandboxes (W1: D-20, D-21).

`isolation_self_test()` runs a probe inside exactly the sandbox a real runner gets — the
Backend role's profile in a DISPOSABLE repository, its own network namespace, a broker
endpoint handed over through the channel (a stub answers it; nothing is forwarded anywhere)
— while the worker offers tempting targets on the host:

  * a TCP server on the host's 127.0.0.1, and one on all interfaces reached through the host's
    LAN address (when the host has one);
  * an abstract Unix socket (the kind the X11 display listens on, `@/tmp/.X11-unix/X0`), a
    path Unix socket in a host directory, and the host's real X11 display sockets if present;
  * TEST-NET-1 (192.0.2.1, never routed) standing for any external host, and a DNS lookup.

Inside, only the broker endpoint may answer. No credential or session path under the real
HOME (runner logins, SSH, GitHub, Git credentials, keyrings, browser profiles, cloud and
Docker credentials, the coordinator state), no runtime or display socket directory and no
display, bus, agent or key variable may be present, /proc must show only the sandbox, and
the directory around the repository must not show other projects. Any mismatch makes the
Worker INELIGIBLE for real agent execution.

Nothing here reads a credential: paths are only tested for existence, inside the sandbox.
"""

from __future__ import annotations

import ipaddress
import json
import os
import socket
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import ProjectConfig
from .demo import init_demo_repo
from .errors import BrokerError
from .netns_channel import BROKER_PORT, SANDBOX_PYTHON, BrokerChannel
from .sandbox import SandboxBuilder, check_bwrap
from .supervisor import supervise

TEST_NET = "192.0.2.1"  # RFC 5737 TEST-NET-1: documentation only, never routed
SENSITIVE_HOME_PATHS = (
    ".ssh", ".claude/.credentials.json", ".claude.json", ".codex/auth.json", ".config/gh", ".git-credentials",
    ".netrc", ".gnupg", ".local/share/keyrings", ".mozilla", ".config/google-chrome", ".config/chromium",
    ".aws", ".config/gcloud", ".docker/config.json", ".kube", ".pki")
SENSITIVE_SYSTEM_PATHS = ("/tmp/.X11-unix", "/var/run/docker.sock", "/run/docker.sock", "/run/dbus/system_bus_socket",
                          "/var/run/dbus")
SESSION_VARIABLES = ("DISPLAY", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS", "SSH_AUTH_SOCK", "XDG_RUNTIME_DIR",
                     "GNOME_KEYRING_CONTROL", "GPG_AGENT_INFO", "XAUTHORITY")

PROBE = r"""
import json, os, socket, sys
spec = json.loads(sys.argv[1])
out = {}
def outcome(fn):
    try:
        return fn()
    except OSError as exc:
        return "errno:%s" % exc.errno
def tcp(host, port):
    def go():
        with socket.create_connection((host, port), timeout=3) as conn:
            if port == spec["broker_port"]:
                conn.sendall(b"GET /fc-isolation-check HTTP/1.0\r\n\r\n")
                return "answered:" + conn.recv(64).decode("latin-1").split("\r\n")[0]
            return "connected"
    return outcome(go)
def unix(path):
    def go():
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(3)
            sock.connect("\0" + path[1:] if path.startswith("@") else path)
            return "connected"
    return outcome(go)
out["broker_endpoint"] = tcp("127.0.0.1", spec["broker_port"])
out["host_loopback"] = tcp("127.0.0.1", spec["loopback_port"])
out["host_lan"] = tcp(spec["lan_ip"], spec["lan_port"]) if spec["lan_ip"] else "not-applicable"
out["external_host"] = tcp(spec["test_net"], 443)
out["dns"] = outcome(lambda: "resolved:" + socket.getaddrinfo("example.com", 443)[0][4][0])
out["host_abstract_socket"] = unix(spec["abstract"])
out["host_path_socket"] = unix(spec["path_socket"])
out["x11_display"] = {name: unix(name) for name in spec["x11"]}
out["present_paths"] = [p for p in spec["paths"] if os.path.lexists(p)]
out["present_variables"] = sorted(k for k in os.environ if k in spec["variables"]
                                  or any(w in k.upper() for w in ("API_KEY", "TOKEN", "SECRET", "PASSWORD")))
out["visible_pids"] = len([p for p in os.listdir("/proc") if p.isdigit()])
out["around_repository"] = sorted(os.listdir(spec["repo_parent"]))
print(json.dumps(out))
"""


@dataclass
class IsolationReport:
    """Observed vs. expected for each check; `ok` only when every check matched."""

    checks: dict[str, dict[str, Any]] = field(default_factory=dict)
    error: str = ""

    @property
    def ok(self) -> bool:
        """True when the probe ran and every check matched."""
        return not self.error and bool(self.checks) and all(c["ok"] for c in self.checks.values())

    @property
    def failures(self) -> list[str]:
        """One line per failed check."""
        lines = [f"{name}: observed {c['observed']!r}, expected {c['expected']}"
                 for name, c in self.checks.items() if not c["ok"]]
        return ([self.error] if self.error else []) + lines


class _HostTargets:  # pylint: disable=too-many-instance-attributes,too-few-public-methods
    """Listeners on the host that a contained process must NOT reach."""

    def __init__(self, base: Path) -> None:
        self.loopback = socket.create_server(("127.0.0.1", 0))
        self.anywhere = socket.create_server(("0.0.0.0", 0))
        self.abstract_name = f"@fc-isolation-check-{os.getpid()}-{os.urandom(4).hex()}"
        self.abstract = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.abstract.bind("\0" + self.abstract_name[1:])
        self.abstract.listen(4)
        self.path_socket = base / "host-only" / "service.sock"
        self.path_socket.parent.mkdir()
        self.path = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.path.bind(str(self.path_socket))
        self.path.listen(4)
        self.lan_ip = _lan_address()

    def close(self) -> None:
        """Stop every listener."""
        for sock in (self.loopback, self.anywhere, self.abstract, self.path):
            sock.close()


def _lan_address() -> str:
    """The host's outgoing non-loopback address (a UDP connect sends no packet), or ''."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
            udp.connect((TEST_NET, 9))
            address = udp.getsockname()[0]
    except OSError:
        return ""
    return "" if ipaddress.ip_address(address).is_loopback else address


def _x11_sockets() -> list[str]:
    """The host's X11 display socket names, as abstract and path names (names only)."""
    try:
        names = sorted(os.listdir("/tmp/.X11-unix"))
    except OSError:
        return []
    return [form for name in names for form in (f"@/tmp/.X11-unix/{name}", f"/tmp/.X11-unix/{name}")]


class _StubEndpoint:  # pylint: disable=too-few-public-methods
    """Answers on the handed-over listener in place of a broker (forwards nothing)."""

    def __init__(self, listener: socket.socket) -> None:
        self.listener = listener
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self.listener.accept()
            except OSError:
                return
            with conn:
                conn.sendall(b"HTTP/1.0 204 No Content\r\n\r\n")

    def close(self) -> None:
        """Stop answering."""
        self.listener.close()


def _expect(report: IsolationReport, name: str, observed: Any, ok: bool, expected: str) -> None:
    report.checks[name] = {"observed": observed, "expected": expected, "ok": ok}


def _refused(value: Any) -> bool:
    return isinstance(value, str) and value.startswith("errno:")


def _evaluate(report: IsolationReport, out: dict[str, Any], targets: _HostTargets, repo: Path) -> None:
    _expect(report, "broker endpoint (the runner's only route)", out["broker_endpoint"],
            str(out["broker_endpoint"]).startswith("answered:HTTP/1.0 204"), "answered by the worker")
    _expect(report, "host localhost service", out["host_loopback"], _refused(out["host_loopback"]), "unreachable")
    _expect(report, "host LAN address", out["host_lan"],
            out["host_lan"] == "not-applicable" if not targets.lan_ip else _refused(out["host_lan"]), "unreachable")
    _expect(report, "external host (TEST-NET-1)", out["external_host"], out["external_host"] == "errno:101",
            "no route (ENETUNREACH)")
    _expect(report, "DNS", out["dns"], _refused(out["dns"]) or str(out["dns"]).startswith("errno:"), "no resolution")
    _expect(report, "host abstract Unix socket", out["host_abstract_socket"], _refused(out["host_abstract_socket"]),
            "unreachable")
    _expect(report, "host path Unix socket", out["host_path_socket"], _refused(out["host_path_socket"]), "absent")
    _expect(report, "X11 display sockets", out["x11_display"],
            all(_refused(v) for v in out["x11_display"].values()), "unreachable")
    _expect(report, "credential and session paths", out["present_paths"], not out["present_paths"], "none present")
    _expect(report, "display, bus, agent and key variables", out["present_variables"], not out["present_variables"],
            "none set")
    _expect(report, "process visibility", out["visible_pids"], out["visible_pids"] <= 4,
            "only the sandbox's own processes")
    _expect(report, "other projects next to the repository", out["around_repository"],
            out["around_repository"] == [repo.name], f"only {repo.name!r}")


def isolation_self_test(config: ProjectConfig, workdir: Path | None = None) -> IsolationReport:  # pylint: disable=too-many-locals
    """Run the isolation probe in a disposable repository. See the module docstring."""
    from .operations import SELF_TEST_FILES  # pylint: disable=import-outside-toplevel
    report = IsolationReport()
    bwrap = check_bwrap()
    home = Path(os.environ.get("HOME", str(Path.home())))
    with tempfile.TemporaryDirectory(prefix="fc-isolation-") as tmp:
        base = Path(workdir or tmp)
        repo = base / "projects" / "repo"
        init_demo_repo(repo, SELF_TEST_FILES)
        targets = _HostTargets(base)
        channel = BrokerChannel()
        stubs: list[_StubEndpoint] = []
        spec = {"broker_port": BROKER_PORT, "loopback_port": targets.loopback.getsockname()[1],
                "lan_ip": targets.lan_ip, "lan_port": targets.anywhere.getsockname()[1], "test_net": TEST_NET,
                "abstract": targets.abstract_name, "path_socket": str(targets.path_socket), "x11": _x11_sockets(),
                "paths": [str(home / p) for p in SENSITIVE_HOME_PATHS]
                + [str(home / ".local/state" / config.state_app_dir), f"/run/user/{os.getuid()}",
                   *SENSITIVE_SYSTEM_PATHS],
                "variables": list(SESSION_VARIABLES), "repo_parent": str(repo.parent)}

        def on_start(pid: int) -> None:
            del pid
            channel.started()
            stubs.append(_StubEndpoint(channel.receive_listener(config.broker.handshake_timeout_seconds)))
            channel.send_environment({})

        out_path = base / "run" / "probe.json"
        try:
            plan = SandboxBuilder(repo, config, bwrap=bwrap).plan(run_dir=base / "run", role="backend",
                                                                  extra=[channel.bind()])
            command = channel.command([SANDBOX_PYTHON, "-I", "-S", "-c", PROBE, json.dumps(spec)])
            supervise(plan.argv(command), cwd=repo, env={"PATH": os.environ.get("PATH", "")}, stdout_path=out_path,
                      stderr_path=out_path.with_suffix(".err"), timeout_seconds=60, pass_fds=(channel.child_fd,),
                      on_start=on_start)
            plan.discard_shadow()
            _evaluate(report, json.loads(out_path.read_text(encoding="utf-8")), targets, repo)
        except (BrokerError, OSError, ValueError) as exc:
            report.error = f"the isolation probe did not run: {exc}"
        finally:
            channel.close()
            for stub in stubs:
                stub.close()
            targets.close()
    return report
