#!/usr/bin/python3
"""First process of a runner sandbox (W1, D-21): open the broker endpoint, take the environment, exec.

Run by the SYSTEM Python inside bubblewrap (`/usr/bin/python3 -I -S <this file> FD PORT -- ARGV…`),
standard library only. The coordinator never imports it; netns_channel.py describes the protocol.

  1. bind a TCP listener to 127.0.0.1:PORT — inside the sandbox's own network namespace;
  2. send that listening socket to the worker over the inherited channel FD (SCM_RIGHTS) and
     close this process's copy, so nothing in the sandbox can accept on it;
  3. receive the runner's broker environment (one JSON object of NAME → value) and close the
     channel; nothing is written to a file and nothing is put on a command line;
  4. exec the runner (an absolute path) with that environment added.

Any failure exits with EXIT_STARTUP before the runner starts; the worker reports a failed start.
"""

import json
import os
import re
import socket
import sys

EXIT_STARTUP = 97
MAX_ENV_BYTES = 65536
CHANNEL_TIMEOUT_SECONDS = 60.0
NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]{0,63}$")


def fail(message: str) -> None:
    """Report a start-up failure on stderr (no environment values) and exit."""
    sys.stderr.write(f"fc-sandbox-bootstrap: {message}\n")
    sys.exit(EXIT_STARTUP)


def receive_environment(channel: socket.socket) -> dict:
    """Read the JSON environment until the worker closes its side; validate names and values."""
    channel.settimeout(CHANNEL_TIMEOUT_SECONDS)
    data = b""
    while True:
        chunk = channel.recv(4096)
        if not chunk:
            break
        data += chunk
        if len(data) > MAX_ENV_BYTES:
            fail("the broker environment is too large")
    env = json.loads(data.decode("utf-8")) if data else None
    if not isinstance(env, dict):
        fail("the worker sent no broker environment")
    for name, value in env.items():
        if not NAME_RE.fullmatch(str(name)) or not isinstance(value, str) or "\x00" in value:
            fail("the broker environment has an invalid entry")
    return env


def main(argv: list) -> None:
    """See the module docstring."""
    if len(argv) < 5 or argv[3] != "--" or not argv[4].startswith("/"):
        fail("usage: bootstrap FD PORT -- /absolute/runner [args...]")
    env: dict = {}
    try:
        channel = socket.socket(fileno=int(argv[1]))
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", int(argv[2])))
            listener.listen(64)
            socket.send_fds(channel, [b"L"], [listener.fileno()])
        env = receive_environment(channel)
        channel.close()
    except (OSError, ValueError) as exc:
        fail(f"cannot set up the broker endpoint: {type(exc).__name__}")
    command = argv[4:]
    try:
        os.execve(command[0], command, {**os.environ, **env})
    except OSError as exc:
        fail(f"cannot start the runner: {exc.strerror}")


if __name__ == "__main__":
    main(sys.argv)
