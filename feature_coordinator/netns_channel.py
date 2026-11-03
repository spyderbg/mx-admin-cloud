"""The broker channel: how a network-isolated runner sandbox gets its model endpoint (W1, D-21).

bubblewrap gives every runner sandbox a new, empty network namespace (`--unshare-all`; the
host network is never shared). The runner still has to reach its model, so:

  1. the worker creates a Unix socketpair and passes one end into the sandbox as an INHERITED
     FILE DESCRIPTOR — not a path, so nothing in the sandbox's file system leads to it;
  2. the sandbox's first process, sandbox_bootstrap.py (run by the system Python), binds a
     TCP listener to 127.0.0.1:BROKER_PORT inside the new namespace and sends the listening
     socket back over the socketpair (SCM_RIGHTS), keeping no copy;
  3. the worker checks that it received exactly that listener and that the listener lives in
     a DIFFERENT network namespace than the worker (SO_NETNS_COOKIE). If not, the run is
     refused: no network isolation, no run. The broker then accepts on it from the host;
  4. the worker sends the runner's broker environment (base URL, per-run token) over the
     socketpair; the bootstrap closes it and execs the runner with that environment.

Consequences: the per-run token never appears on a command line (bwrap's or the runner's), in a
file or in sandbox.json; no port is opened on the host; and only processes in the runner's own
namespace can connect. The nested command sandboxes of Claude Code and Codex get namespaces of
their own, so the commands an agent runs cannot reach the endpoint at all.
"""

from __future__ import annotations

import json
import os
import socket
import struct
from pathlib import Path
from typing import Mapping, Sequence

from .errors import BrokerChannelError
from .sandbox import Bind

BROKER_PORT = 47613
BOOTSTRAP = Path(__file__).with_name("sandbox_bootstrap.py")
BOOTSTRAP_IN_SANDBOX = "/run/fc-broker/bootstrap.py"
SANDBOX_PYTHON = "/usr/bin/python3"
SO_NETNS_COOKIE = getattr(socket, "SO_NETNS_COOKIE", 71)  # Linux 5.14+; the stdlib may lack the name


def netns_cookie(sock: socket.socket) -> int:
    """The kernel's identifier of the network namespace *sock* belongs to."""
    return struct.unpack("Q", sock.getsockopt(socket.SOL_SOCKET, SO_NETNS_COOKIE, 8))[0]


def own_netns_cookie() -> int:
    """The worker's own network namespace."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        return netns_cookie(probe)


class BrokerChannel:
    """One run's socketpair. Use: `bind()` into the plan, `command()` around the runner argv,
    pass `child_fd` to the process, then `started()`, `receive_listener()`, `send_environment()`."""

    def __init__(self, port: int = BROKER_PORT) -> None:
        self.port = port
        self._host, self._child = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        self._child.set_inheritable(True)

    @property
    def child_fd(self) -> int:
        """The descriptor the sandboxed bootstrap inherits."""
        return self._child.fileno()

    @staticmethod
    def bind() -> Bind:
        """The bootstrap script, bound read-only into the sandbox."""
        return Bind(str(BOOTSTRAP), BOOTSTRAP_IN_SANDBOX, "ro")

    def command(self, argv: Sequence[str]) -> list[str]:
        """*argv* started through the bootstrap (inside the sandbox)."""
        return [SANDBOX_PYTHON, "-I", "-S", BOOTSTRAP_IN_SANDBOX, str(self.child_fd), str(self.port), "--", *argv]

    def started(self) -> None:
        """Close the worker's copy of the sandbox end (the process has inherited it), so a
        sandbox that dies before the handshake is seen as end-of-file, not as a hang."""
        self._child.close()

    def receive_listener(self, timeout: float) -> socket.socket:
        """The sandbox's broker listener, verified. Raises BrokerChannelError."""
        self._host.settimeout(timeout)
        try:
            msg, fds, _flags, _addr = socket.recv_fds(self._host, 16, 4)
        except OSError as exc:
            raise BrokerChannelError(f"the runner sandbox did not hand over its broker endpoint: {exc}") from exc
        if not fds:
            raise BrokerChannelError("the runner sandbox exited before handing over its broker endpoint "
                                     "(sandbox start-up failed)")
        for extra in fds[1:]:
            os.close(extra)
        listener = socket.socket(fileno=fds[0])
        try:
            self._verify(msg, listener)
        except BrokerChannelError:
            listener.close()
            raise
        return listener

    def _verify(self, msg: bytes, listener: socket.socket) -> None:
        try:
            listening = listener.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN)
            address = listener.getsockname()
            cookie = netns_cookie(listener)
            own = own_netns_cookie()
        except OSError as exc:
            raise BrokerChannelError(f"cannot verify the broker endpoint: {exc}") from exc
        if msg != b"L" or listener.family != socket.AF_INET or listener.type != socket.SOCK_STREAM \
                or not listening or address != ("127.0.0.1", self.port):
            raise BrokerChannelError(f"the sandbox handed over an unexpected socket ({address})")
        if cookie == own:
            raise BrokerChannelError("the runner sandbox shares the worker's network namespace; "
                                     "refusing to run without network isolation (D-21)")

    def send_environment(self, env: Mapping[str, str]) -> None:
        """Send the runner's broker environment and close the channel."""
        try:
            self._host.sendall(json.dumps(dict(env)).encode("utf-8"))
            self._host.shutdown(socket.SHUT_WR)
        except OSError as exc:
            raise BrokerChannelError(f"cannot pass the broker environment to the sandbox: {exc}") from exc
        finally:
            self._host.close()

    def close(self) -> None:
        """Release both ends (idempotent)."""
        self._host.close()
        self._child.close()
