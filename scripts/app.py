#!/usr/bin/env python3
"""Native-mode test stack runner: stub + backend (uvicorn) + built frontend, no Docker.

Docker is not required to run the sandbox test environment. This script starts the
same three logical services docker-compose.test.yml would (stub, backend, frontend),
as plain subprocesses, isolated from any real dev stack by the absolute paths and
dummy credentials in .testenv/test.env (see scripts/testenvlib/envfile.py's
allowlist-based child_env()).

The one trap this exists to avoid: frontend/vite.config.ts serves /content from a
**hardcoded ../content** via sirv, so `npm run dev` would silently expose the real
content directory no matter what CONTENT_DIR is set to. This script therefore never
runs the dev server — it runs `npm run build` once (VITE_* confirmation-mode flags are
build-time) and serves frontend/dist/ from a small stdlib static+proxy handler that
forwards /api/, /internal/, /content/ to the backend, mirroring docker/nginx.conf.

Usage:
    python scripts/app.py start [--backend-port 48000] [--frontend-port 48001]
                                 [--stub-port 48002] [--simple-confirm] [--no-build]
    python scripts/app.py stop
    python scripts/app.py status
    python scripts/app.py logs [backend|frontend|stub] [--follow]
"""

from __future__ import annotations

import argparse
import http.client
import mimetypes
import os
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout.reconfigure(line_buffering=True)  # see testenv.py for why

import testenv_config  # noqa: E402
import testenv_seed  # noqa: E402
from testenvlib import paths, pyenv  # noqa: E402
from testenvlib.envfile import child_env, load_env_file  # noqa: E402
from testenvlib.proc import (  # noqa: E402
    is_running,
    load_pids,
    port_is_free,
    save_pids,
    start_process,
    stop_process,
    wait_for_http,
)

RED = "\033[91m"
YELLOW = "\033[93m"
GREEN = "\033[92m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"

_PROXIED_PREFIXES = ("/api/", "/internal/", "/content/")

THIS_FILE = Path(__file__).resolve()

# ---------------------------------------------------------------------------
# Frontend: static frontend/dist/ + reverse proxy to the backend (mirrors nginx.conf)
# ---------------------------------------------------------------------------


class _FrontendHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    dist_dir: Path
    backend_host: str
    backend_port: int

    def log_message(self, format_: str, *args: object) -> None:  # noqa: A002
        sys.stderr.write(f"[frontend] {self.address_string()} {format_ % args}\n")

    def _is_proxied(self, path: str) -> bool:
        return any(path == p.rstrip("/") or path.startswith(p) for p in _PROXIED_PREFIXES)

    def _proxy(self) -> None:
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else None
        headers = {k: v for k, v in self.headers.items() if k.lower() not in ("host", "connection")}

        conn = http.client.HTTPConnection(self.backend_host, self.backend_port, timeout=300)
        try:
            conn.request(self.command, self.path, body=body, headers=headers)
            response = conn.getresponse()
            response_body = response.read()
        except (ConnectionError, OSError, TimeoutError) as exc:
            self._send_text(502, f"Bad gateway: backend unreachable ({exc})")
            return
        finally:
            conn.close()

        self.send_response(response.status)
        for key, value in response.getheaders():
            if key.lower() in ("transfer-encoding", "connection"):
                continue
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(response_body)

    def _send_text(self, status: int, text: str) -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_static(self) -> None:
        request_path = urlsplit(self.path).path
        candidate = self.dist_dir / request_path.lstrip("/")
        try:
            candidate = candidate.resolve()
            candidate.relative_to(self.dist_dir.resolve())
        except (ValueError, OSError):
            candidate = self.dist_dir / "index.html"

        if candidate.is_dir():
            candidate = candidate / "index.html"
        if not candidate.is_file():
            # SPA client-side routing fallback, mirroring nginx's try_files ... /index.html
            candidate = self.dist_dir / "index.html"
        if not candidate.is_file():
            self._send_text(404, "not found")
            return

        data = candidate.read_bytes()
        content_type = mimetypes.guess_type(str(candidate))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _handle(self) -> None:
        path = urlsplit(self.path).path
        if self._is_proxied(path):
            self._proxy()
        else:
            self._serve_static()

    def do_GET(self) -> None:  # noqa: N802
        self._handle()

    def do_POST(self) -> None:  # noqa: N802
        self._handle()

    def do_PUT(self) -> None:  # noqa: N802
        self._handle()

    def do_PATCH(self) -> None:  # noqa: N802
        self._handle()

    def do_DELETE(self) -> None:  # noqa: N802
        self._handle()


def _serve_frontend(*, dist_dir: Path, host: str, port: int, backend_host: str, backend_port: int) -> int:
    _FrontendHandler.dist_dir = dist_dir
    _FrontendHandler.backend_host = backend_host
    _FrontendHandler.backend_port = backend_port
    server = ThreadingHTTPServer((host, port), _FrontendHandler)
    print(f"[frontend] serving {dist_dir} on http://{host}:{port} -> backend http://{backend_host}:{backend_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


# ---------------------------------------------------------------------------
# start / stop / status / logs
# ---------------------------------------------------------------------------


def _ensure_testenv_ready(*, stub_port: int) -> dict[str, str]:
    if not (paths.TEST_CONTENT_DIR.exists() and any(paths.TEST_CONTENT_DIR.iterdir())):
        print(f"{DIM}.testenv/content is empty — seeding...{RESET}")
        rc = testenv_seed.seed(force=False)
        if rc != 0:
            raise SystemExit(rc)

    for directory in paths.TESTENV_SUBDIRS:
        directory.mkdir(parents=True, exist_ok=True)

    # Config is cheap to rebuild and must match *this* run's stub port every time.
    testenv_config.build(stub_base_url=f"http://127.0.0.1:{stub_port}", force=True)

    return load_env_file(paths.TEST_ENV_FILE)


def _build_frontend(*, simple_confirm: bool) -> None:
    frontend_dir = paths.FRONTEND_DIR
    if not (frontend_dir / "node_modules").is_dir():
        print(f"{DIM}frontend/node_modules missing — running npm install...{RESET}")
        subprocess.run(["npm", "install"], cwd=frontend_dir, check=True)

    mode = "simple" if simple_confirm else "complex"
    env = {
        **os.environ,
        "VITE_DEBUG_MODE": "true",
        "VITE_MOCK_DATA": "false",
        "VITE_ARCHIVE_CONFIRMATION_MODE": mode,
        "VITE_DELETE_ITEM_CONFIRMATION_MODE": mode,
    }
    print(f"{DIM}Building frontend (confirmation mode: {mode})...{RESET}")
    subprocess.run(["npm", "run", "build"], cwd=frontend_dir, check=True, env=env)


def cmd_start(args: argparse.Namespace) -> int:
    backend_port = args.backend_port
    frontend_port = args.frontend_port
    stub_port = args.stub_port

    pids = load_pids(paths.PID_FILE)
    running = {name: pid for name, pid in pids.items() if isinstance(pid, int) and is_running(pid)}
    if running:
        print(f"{YELLOW}Already running (pids: {running}). Run `stop` first.{RESET}")
        return 1

    for name, port in (("backend", backend_port), ("frontend", frontend_port), ("stub", stub_port)):
        if not port_is_free(port):
            print(f"{RED}Port {port} ({name}) is already in use. Pick another with --{name}-port.{RESET}")
            return 1

    test_env = _ensure_testenv_ready(stub_port=stub_port)

    db_path = str(Path(test_env["TEST_DATA_DIR"]) / "users.db")
    base_env = child_env(test_env)
    backend_env = {
        **base_env,
        "PYTHONPATH": str(paths.BACKEND_DIR),
        "CONTENT_DIR": test_env["TEST_CONTENT_DIR"],
        "ARCHIVE_DIR": test_env["TEST_ARCHIVE_DIR"],
        "CONFIG_DIR": test_env["TEST_CONFIG_DIR"],
        "OUTPUT_DIR": test_env["TEST_OUTPUT_DIR"],
        "DB_PATH": db_path,
        "API_BASE_URL": f"http://127.0.0.1:{backend_port}",
        "OPENAI_BASE_URL": f"http://127.0.0.1:{stub_port}/v1",
    }

    venv_python = pyenv.ensure_venv()

    print(f"{BOLD}Starting stub on :{stub_port} ...{RESET}")
    stub_proc = start_process(
        [sys.executable, str(paths.STUB_SERVER_SCRIPT), "--host", "127.0.0.1", "--port", str(stub_port)],
        cwd=paths.REPO_ROOT, env=base_env, log_path=paths.TEST_LOGS_DIR / "stub.log",
    )
    ready, detail = wait_for_http(f"http://127.0.0.1:{stub_port}/__health", timeout=15)
    if not ready:
        print(f"{RED}Stub did not become ready: {detail}{RESET}")
        stop_process(stub_proc.pid, name="stub")
        return 1

    print(f"{BOLD}Starting backend on :{backend_port} ...{RESET}")
    backend_proc = start_process(
        [str(venv_python), "-m", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", str(backend_port)],
        cwd=paths.BACKEND_DIR, env=backend_env, log_path=paths.TEST_LOGS_DIR / "backend.log",
    )
    ready, detail = wait_for_http(f"http://127.0.0.1:{backend_port}/openapi.json", timeout=60)
    if not ready:
        print(f"{RED}Backend did not become ready: {detail}{RESET}")
        print(f"{DIM}See {paths.testenv_relative(paths.TEST_LOGS_DIR / 'backend.log')}{RESET}")
        stop_process(backend_proc.pid, name="backend")
        stop_process(stub_proc.pid, name="stub")
        return 1

    if not args.no_build:
        _build_frontend(simple_confirm=args.simple_confirm)
    dist_dir = paths.FRONTEND_DIR / "dist"
    if not (dist_dir / "index.html").is_file():
        print(f"{RED}frontend/dist/index.html missing — build failed or was skipped.{RESET}")
        stop_process(backend_proc.pid, name="backend")
        stop_process(stub_proc.pid, name="stub")
        return 1

    print(f"{BOLD}Starting frontend on :{frontend_port} ...{RESET}")
    frontend_proc = start_process(
        [
            sys.executable, str(THIS_FILE), "_serve-frontend",
            "--dist", str(dist_dir), "--host", "127.0.0.1", "--port", str(frontend_port),
            "--backend-host", "127.0.0.1", "--backend-port", str(backend_port),
        ],
        cwd=paths.REPO_ROOT, env=base_env, log_path=paths.TEST_LOGS_DIR / "frontend.log",
    )
    ready, detail = wait_for_http(f"http://127.0.0.1:{frontend_port}/", timeout=20)
    if not ready:
        print(f"{RED}Frontend did not become ready: {detail}{RESET}")
        stop_process(frontend_proc.pid, name="frontend")
        stop_process(backend_proc.pid, name="backend")
        stop_process(stub_proc.pid, name="stub")
        return 1

    save_pids(paths.PID_FILE, {
        "stub": stub_proc.pid, "backend": backend_proc.pid, "frontend": frontend_proc.pid,
        "stub_port": stub_port, "backend_port": backend_port, "frontend_port": frontend_port,
    })

    print(f"\n{GREEN}{BOLD}Test stack is up (native mode):{RESET}")
    print(f"  Frontend: http://127.0.0.1:{frontend_port}")
    print(f"  Backend:  http://127.0.0.1:{backend_port}")
    print(f"  Stub:     http://127.0.0.1:{stub_port}  (/__health, /__calls, /__reset)")
    return 0


def cmd_stop(_args: argparse.Namespace) -> int:
    pids = load_pids(paths.PID_FILE)
    if not pids:
        print(f"{DIM}Nothing to stop (no PID file).{RESET}")
        return 0

    ok = True
    for name in ("frontend", "backend", "stub"):
        pid = pids.get(name)
        if not isinstance(pid, int):
            continue
        if is_running(pid):
            print(f"Stopping {name} (pid {pid}) ...")
            if not stop_process(pid, name=name):
                print(f"{RED}  ! {name} (pid {pid}) did not stop{RESET}")
                ok = False
        else:
            print(f"{DIM}{name} already stopped.{RESET}")

    if ok:
        paths.PID_FILE.unlink(missing_ok=True)
    return 0 if ok else 1


def cmd_status(_args: argparse.Namespace) -> int:
    pids = load_pids(paths.PID_FILE)
    if not pids:
        print("Not running (no PID file).")
        return 1

    all_up = True
    for name in ("stub", "backend", "frontend"):
        pid = pids.get(name)
        port = pids.get(f"{name}_port")
        alive = isinstance(pid, int) and is_running(pid)
        all_up = all_up and alive
        state = f"{GREEN}running{RESET}" if alive else f"{RED}stopped{RESET}"
        print(f"  {name:<9} pid={pid} port={port}  {state}")
    return 0 if all_up else 1


def cmd_logs(args: argparse.Namespace) -> int:
    services = [args.service] if args.service else ["stub", "backend", "frontend"]
    for name in services:
        log_path = paths.TEST_LOGS_DIR / f"{name}.log"
        print(f"{BOLD}== {name} ({paths.testenv_relative(log_path)}) =={RESET}")
        if not log_path.is_file():
            print(f"{DIM}  (no log yet){RESET}")
            continue
        if not args.follow:
            lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
            for line in lines[-args.lines:]:
                print(f"  {line}")

    if args.follow:
        if not args.service:
            print(f"{RED}--follow requires a single service.{RESET}")
            return 1
        log_path = paths.TEST_LOGS_DIR / f"{args.service}.log"
        log_path.touch(exist_ok=True)
        with log_path.open("r", encoding="utf-8", errors="replace") as fh:
            fh.seek(0, 2)
            try:
                while True:
                    line = fh.readline()
                    if line:
                        print(line, end="")
                    else:
                        time.sleep(0.3)
            except KeyboardInterrupt:
                pass
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_start = sub.add_parser("start", help="seed/configure if needed, start stub+backend+frontend")
    p_start.add_argument("--backend-port", type=int, default=paths.NATIVE_DEFAULT_BACKEND_PORT)
    p_start.add_argument("--frontend-port", type=int, default=paths.NATIVE_DEFAULT_FRONTEND_PORT)
    p_start.add_argument("--stub-port", type=int, default=paths.NATIVE_DEFAULT_STUB_PORT)
    p_start.add_argument("--simple-confirm", action="store_true", help="build the frontend with simple (not complex) confirmation modes")
    p_start.add_argument("--no-build", action="store_true", help="reuse the existing frontend/dist without rebuilding")
    p_start.set_defaults(func=cmd_start)

    p_stop = sub.add_parser("stop", help="stop all three native processes")
    p_stop.set_defaults(func=cmd_stop)

    p_status = sub.add_parser("status", help="show pid/port/running state for each service")
    p_status.set_defaults(func=cmd_status)

    p_logs = sub.add_parser("logs", help="print (or follow) native-mode process logs")
    p_logs.add_argument("service", nargs="?", choices=["backend", "frontend", "stub"])
    p_logs.add_argument("--follow", "-f", action="store_true")
    p_logs.add_argument("--lines", "-n", type=int, default=60)
    p_logs.set_defaults(func=cmd_logs)

    p_serve = sub.add_parser("_serve-frontend", help=argparse.SUPPRESS)
    p_serve.add_argument("--dist", required=True)
    p_serve.add_argument("--host", required=True)
    p_serve.add_argument("--port", type=int, required=True)
    p_serve.add_argument("--backend-host", required=True)
    p_serve.add_argument("--backend-port", type=int, required=True)
    p_serve.set_defaults(func=lambda a: _serve_frontend(
        dist_dir=Path(a.dist), host=a.host, port=a.port,
        backend_host=a.backend_host, backend_port=a.backend_port,
    ))

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
