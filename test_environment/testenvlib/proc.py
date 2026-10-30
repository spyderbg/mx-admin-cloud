"""Process lifecycle helpers for native mode: start/stop with a PID file, HTTP readiness
polling. Mirrors the PID-file shape of backend/uvicorn_start.sh so `app.py stop` can
always find what it started, even across separate `python app.py start` / `... stop`
invocations.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import IO


def load_pids(pid_file: Path) -> dict[str, int]:
    if not pid_file.is_file():
        return {}
    try:
        data = json.loads(pid_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): int(v) for k, v in data.items()} if isinstance(data, dict) else {}


def save_pids(pid_file: Path, pids: dict[str, int]) -> None:
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(json.dumps(pids, indent=2, sort_keys=True), encoding="utf-8")


def is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else — treat as running
    return True


def open_log(log_path: Path) -> IO[bytes]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    return open(log_path, "ab")  # pylint: disable=consider-using-with


def start_process(
        cmd: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        log_path: Path,
) -> subprocess.Popen:
    """Start *cmd* detached from this process's stdio, appending output to *log_path*."""
    log_file = open_log(log_path)
    kwargs: dict = {}
    if sys.platform != "win32":
        kwargs["start_new_session"] = True
    return subprocess.Popen(  # pylint: disable=consider-using-with
        cmd,
        cwd=str(cwd),
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        **kwargs,
    )


def stop_process(pid: int, *, name: str, timeout: float = 10.0) -> bool:
    """SIGTERM *pid*, escalate to SIGKILL after *timeout*. Returns False if it never died."""
    if not is_running(pid):
        return True
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    except PermissionError:
        print(f"  ! no permission to stop {name} (pid {pid})", file=sys.stderr)
        return False

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_running(pid):
            return True
        time.sleep(0.2)

    try:
        os.kill(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    time.sleep(0.3)
    return not is_running(pid)


def wait_for_http(
        url: str,
        *,
        timeout: float = 60.0,
        interval: float = 0.5,
        expect_status: tuple[int, ...] = (200,),
) -> tuple[bool, str]:
    """Poll *url* until it answers with one of *expect_status*, or *timeout* elapses.

    Returns (ready, detail). Uses stdlib urllib only — no extra dependency, and no
    ambiguity from a proxy/session layer swallowing the real status code.
    """
    deadline = time.monotonic() + timeout
    last_error = "never attempted"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=min(5.0, interval * 4)) as response:  # noqa: S310
                status = response.status
                if status in expect_status:
                    return True, f"HTTP {status}"
                last_error = f"HTTP {status}"
        except urllib.error.HTTPError as exc:
            if exc.code in expect_status:
                return True, f"HTTP {exc.code}"
            last_error = f"HTTP {exc.code}"
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = str(exc.reason if isinstance(exc, urllib.error.URLError) else exc)
        time.sleep(interval)
    return False, last_error


def port_is_free(port: int, host: str = "127.0.0.1") -> bool:
    import socket  # pylint: disable=import-outside-toplevel

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) != 0
