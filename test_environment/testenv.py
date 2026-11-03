#!/usr/bin/env python3
"""Entry point for the sandbox test environment (Workstream 1).

Wraps two interchangeable run modes behind one CLI: Docker (the real images, real
nginx, real Postgres-free sqlite, the works) and native (plain subprocesses via
app.py — the default on a machine without Docker, which is exactly this one). See
docs/test_environment/implementation_plan.md §1 for the full mode comparison.

    testenv.py doctor    mode? compose >= 2.24? ports free? dev stack up? real keys?
    testenv.py seed      (re)build .testenv/{content,config,archive} + test.env
    testenv.py up        [--build] [--simple-confirm]   seed if absent, start, wait
    testenv.py down      [--purge]                       tear down (docker: -v --remove-orphans)
    testenv.py reset     down + rm -rf .testenv + seed + up   (the workhorse)
    testenv.py logs | status | shell <service>
    testenv.py e2e [--base-url URL] [-- <playwright args>]
    testenv.py run [--strategy report|heal|batch|work] [--max-issues N] [--dry-run] [--print-config]
                    Workstream 3 — report checks+files issues; heal/batch also check
                    first then fix; work skips straight to fixing the tracker's existing
                    backlog. Driven by testenv.toml. See test_environment/README.md's
                    "Execution strategies" section.

A global --mode docker|native flag (default: autodetect) applies to every subcommand —
it must come BEFORE the subcommand name: `testenv.py --mode docker up`, not
`testenv.py up --mode docker`.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
# Line-buffer stdout even when redirected to a file/pipe: this script and its
# subprocess children (app.py, docker compose) interleave output, and Python's default
# full-buffering-on-a-pipe would otherwise delay this process's own prints until exit,
# making a redirected log read out of chronological order.
sys.stdout.reconfigure(line_buffering=True)

import testenv_config  # noqa: E402
import testenv_seed  # noqa: E402
from testenvlib import paths, pyenv  # noqa: E402
from testenvlib.dockerutil import find_docker  # noqa: E402
from testenvlib.envfile import child_env, find_leaked_secrets, load_env_file  # noqa: E402
from testenvlib.proc import port_is_free, wait_for_http  # noqa: E402

RED = "\033[91m"
YELLOW = "\033[93m"
GREEN = "\033[92m"
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[96m"
RESET = "\033[0m"

APP_PY = paths.TEST_ENVIRONMENT_DIR / "app.py"
BASE_COMPOSE = paths.DOCKER_DIR / "docker-compose.yml"
TEST_COMPOSE = paths.DOCKER_DIR / "docker-compose.test.yml"


# ---------------------------------------------------------------------------
# Mode detection
# ---------------------------------------------------------------------------

def _docker_available() -> tuple[bool, str]:
    docker_bin, via_fallback = find_docker()
    if not docker_bin:
        return False, "docker binary not found on PATH or in the usual Docker Desktop install locations"
    try:
        result = subprocess.run([docker_bin, "info"], capture_output=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"docker daemon did not respond ({exc})"
    if result.returncode != 0:
        return False, "docker daemon not responding (is Docker Desktop running?)"
    detail = "docker daemon responded"
    if via_fallback:
        detail += f" (found at {docker_bin}, not on PATH — new shells may need `export PATH=\"$HOME/.docker/bin:$PATH\"`)"
    return True, detail


def _compose_version() -> tuple[bool, str]:
    docker_bin, _ = find_docker()
    if not docker_bin:
        return False, "docker binary not found"
    try:
        result = subprocess.run([docker_bin, "compose", "version", "--short"], capture_output=True, timeout=5, text=True, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"could not run `docker compose version` ({exc})"
    if result.returncode != 0:
        return False, "docker compose plugin not available"
    version_str = result.stdout.strip()
    parts = version_str.lstrip("v").split(".")
    try:
        major, minor = int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        return False, f"could not parse compose version {version_str!r}"
    ok = (major, minor) >= (2, 24)
    return ok, f"docker compose {version_str} ({'>=' if ok else '<'} 2.24, needed for the ports !override tag)"


def resolve_mode(requested: str | None) -> str:
    if requested in ("docker", "native"):
        return requested
    available, _ = _docker_available()
    return "docker" if available else "native"


def _docker_cmd() -> list[str]:
    docker_bin, _ = find_docker()
    if not docker_bin:
        raise SystemExit(f"{RED}docker not found — run `testenv.py doctor` for details.{RESET}")
    return [docker_bin]


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------

def cmd_doctor(args: argparse.Namespace) -> int:
    mode = resolve_mode(args.mode)
    ok = True

    print(f"{BOLD}mode:{RESET} {mode}", end=" ")
    if args.mode:
        print(f"{DIM}(forced via --mode){RESET}")
    else:
        available, reason = _docker_available()
        print(f"{DIM}(autodetected: {reason}){RESET}")

    if mode == "docker":
        compose_ok, compose_detail = _compose_version()
        marker = f"{GREEN}OK{RESET}" if compose_ok else f"{RED}FAIL{RESET}"
        print(f"[{marker}] {compose_detail}")
        ok = ok and compose_ok

        for name in ("kiddo-backend", "kiddo-frontend"):
            result = subprocess.run(
                [*_docker_cmd(), "inspect", "-f", "{{.State.Running}}", name],
                capture_output=True, text=True, check=False,
            )
            if result.returncode == 0 and result.stdout.strip() == "true":
                print(f"{YELLOW}  ! dev container '{name}' is running — the test stack uses different "
                      f"container names/ports, so this is informational, not a blocker.{RESET}")

        backend_port, frontend_port, stub_port = (
            paths.DOCKER_DEFAULT_BACKEND_PORT, paths.DOCKER_DEFAULT_FRONTEND_PORT, paths.DOCKER_DEFAULT_STUB_HOST_PORT,
        )
    else:
        venv = pyenv.venv_dir()
        venv_ok = pyenv.venv_ready(venv)
        marker = f"{GREEN}OK{RESET}" if venv_ok else f"{YELLOW}not created yet{RESET}"
        print(f"[{marker}] backend venv: {paths.testenv_relative(venv)} (created on first `up`/`app.py start`)")

        backend_port, frontend_port, stub_port = (
            paths.NATIVE_DEFAULT_BACKEND_PORT, paths.NATIVE_DEFAULT_FRONTEND_PORT, paths.NATIVE_DEFAULT_STUB_PORT,
        )

    for name, port in (("backend", backend_port), ("frontend", frontend_port), ("stub", stub_port)):
        free = port_is_free(port)
        marker = f"{GREEN}free{RESET}" if free else f"{RED}IN USE{RESET}"
        print(f"[{marker}] port {port} ({name})")
        if not free:
            ok = False

    # -- secret scan: build the exact child env `up` would use, and check it ------
    test_env = load_env_file(paths.TEST_ENV_FILE)
    env = child_env(test_env)
    real_dotenv = load_env_file(paths.REPO_ROOT / ".env")
    problems = find_leaked_secrets(env, real_dotenv)
    if not test_env:
        print(f"{DIM}  (no .testenv/test.env yet — run \"seed\" first to fully check for key leakage){RESET}")
    if problems:
        ok = False
        print(f"{RED}{BOLD}[FAIL] possible real credentials in the test child environment:{RESET}")
        for problem in problems:
            print(f"  {RED}- {problem}{RESET}")
    else:
        print(f"{GREEN}[OK] no real-looking keys in the test child environment{RESET}")

    if not real_dotenv:
        print(f"{DIM}  (repo .env not found — nothing to cross-check dummy values against){RESET}")

    print()
    if ok:
        print(f"{GREEN}{BOLD}doctor: all checks passed.{RESET}")
    else:
        print(f"{RED}{BOLD}doctor: one or more checks failed — see above.{RESET}")
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# seed
# ---------------------------------------------------------------------------

def cmd_seed(args: argparse.Namespace) -> int:
    if args.check_ts:
        return testenv_seed.check_timestamps()
    rc = testenv_seed.seed(force=args.force)
    if rc != 0:
        return rc
    stub_base = (
        f"http://stub:{paths.DOCKER_STUB_CONTAINER_PORT}" if resolve_mode(args.mode) == "docker"
        else f"http://127.0.0.1:{paths.NATIVE_DEFAULT_STUB_PORT}"
    )
    return testenv_config.build(stub_base_url=stub_base, force=True)


# ---------------------------------------------------------------------------
# up / down / reset
# ---------------------------------------------------------------------------

def _compose_base_cmd() -> list[str]:
    return [
        *_docker_cmd(), "compose",
        "-f", str(BASE_COMPOSE), "-f", str(TEST_COMPOSE),
        "--project-directory", str(paths.DOCKER_DIR),
        "--env-file", str(paths.TEST_ENV_FILE),
        "-p", paths.DOCKER_PROJECT_NAME,
    ]


def _docker_child_env(
        *,
        confirm_mode: str,
        backend_port: int | None = None,
        frontend_port: int | None = None,
        stub_port: int | None = None,
) -> dict[str, str]:
    test_env = load_env_file(paths.TEST_ENV_FILE)
    env = child_env(test_env)
    env.update({
        "TEST_BACKEND_PORT": str(backend_port or paths.DOCKER_DEFAULT_BACKEND_PORT),
        "TEST_FRONTEND_PORT": str(frontend_port or paths.DOCKER_DEFAULT_FRONTEND_PORT),
        "TEST_STUB_PORT": str(stub_port or paths.DOCKER_DEFAULT_STUB_HOST_PORT),
        "TEST_CONFIRM_MODE": confirm_mode,
    })

    # docker compose shells out to credential helpers (docker-credential-desktop, ...)
    # that live next to the `docker` binary itself — if docker was only found via the
    # Docker Desktop fallback (not on the invoking shell's PATH), those helpers would be
    # unfindable by the child process even though `docker` itself resolved fine, and
    # `docker pull` fails with "error getting credentials". Prepend its directory.
    docker_bin, via_fallback = find_docker()
    if via_fallback and docker_bin:
        docker_dir = str(Path(docker_bin).parent)
        env["PATH"] = f"{docker_dir}:{env.get('PATH', '')}"

    return env


def cmd_up(args: argparse.Namespace) -> int:
    mode = resolve_mode(args.mode)
    backend_port = getattr(args, "backend_port", None)
    frontend_port = getattr(args, "frontend_port", None)
    stub_port = getattr(args, "stub_port", None)

    if not (paths.TEST_CONTENT_DIR.exists() and any(paths.TEST_CONTENT_DIR.iterdir())):
        print(f"{DIM}Seeding .testenv/ ...{RESET}")
        rc = testenv_seed.seed(force=False)
        if rc != 0:
            return rc

    if mode == "native":
        cmd = [sys.executable, str(APP_PY), "start"]
        if args.simple_confirm:
            cmd.append("--simple-confirm")
        if backend_port:
            cmd += ["--backend-port", str(backend_port)]
        if frontend_port:
            cmd += ["--frontend-port", str(frontend_port)]
        if stub_port:
            cmd += ["--stub-port", str(stub_port)]
        result = subprocess.run(cmd, check=False)
        return result.returncode

    confirm_mode = "simple" if args.simple_confirm else "complex"
    testenv_config.build(stub_base_url=f"http://stub:{paths.DOCKER_STUB_CONTAINER_PORT}", force=True)

    env = _docker_child_env(
        confirm_mode=confirm_mode, backend_port=backend_port, frontend_port=frontend_port, stub_port=stub_port,
    )
    cmd = _compose_base_cmd() + ["up", "-d"]
    if args.build:
        cmd.append("--build")
    print(f"{BOLD}{' '.join(cmd)}{RESET}")
    result = subprocess.run(cmd, cwd=paths.REPO_ROOT, env=env, check=False)
    if result.returncode != 0:
        return result.returncode

    backend_port = int(env["TEST_BACKEND_PORT"])
    frontend_port = int(env["TEST_FRONTEND_PORT"])
    stub_port = int(env["TEST_STUB_PORT"])

    print(f"{DIM}Waiting for stub...{RESET}")
    ready, detail = wait_for_http(f"http://127.0.0.1:{stub_port}/__health", timeout=30)
    if not ready:
        print(f"{RED}Stub not ready: {detail}{RESET}")
        return 1
    print(f"{DIM}Waiting for backend...{RESET}")
    ready, detail = wait_for_http(f"http://127.0.0.1:{backend_port}/openapi.json", timeout=90)
    if not ready:
        print(f"{RED}Backend not ready: {detail}{RESET}")
        return 1
    print(f"{DIM}Waiting for frontend...{RESET}")
    ready, detail = wait_for_http(f"http://127.0.0.1:{frontend_port}/", timeout=60)
    if not ready:
        print(f"{RED}Frontend not ready: {detail}{RESET}")
        return 1

    print(f"\n{GREEN}{BOLD}Test stack is up (docker mode):{RESET}")
    print(f"  Frontend: http://127.0.0.1:{frontend_port}")
    print(f"  Backend:  http://127.0.0.1:{backend_port}")
    print(f"  Stub:     http://127.0.0.1:{stub_port}")
    return 0


def cmd_down(args: argparse.Namespace) -> int:
    mode = resolve_mode(args.mode)
    if mode == "native":
        result = subprocess.run([sys.executable, str(APP_PY), "stop"], check=False)
        rc = result.returncode
    else:
        env = _docker_child_env(confirm_mode="complex")
        cmd = _compose_base_cmd() + ["down", "-v", "--remove-orphans"]
        result = subprocess.run(cmd, cwd=paths.REPO_ROOT, env=env, check=False)
        rc = result.returncode

    if args.purge and paths.TESTENV_DIR.exists():
        print(f"{DIM}Purging {paths.testenv_relative(paths.TESTENV_DIR)} ...{RESET}")
        shutil.rmtree(paths.TESTENV_DIR)
    return rc


def cmd_reset(args: argparse.Namespace) -> int:
    down_args = argparse.Namespace(mode=args.mode, purge=False)
    cmd_down(down_args)

    if paths.TESTENV_DIR.exists():
        print(f"{DIM}Removing {paths.testenv_relative(paths.TESTENV_DIR)} ...{RESET}")
        shutil.rmtree(paths.TESTENV_DIR)

    rc = testenv_seed.seed(force=False)
    if rc != 0:
        return rc

    up_args = argparse.Namespace(
        mode=args.mode, build=True, simple_confirm=args.simple_confirm,
        backend_port=getattr(args, "backend_port", None),
        frontend_port=getattr(args, "frontend_port", None),
        stub_port=getattr(args, "stub_port", None),
    )
    return cmd_up(up_args)


# ---------------------------------------------------------------------------
# logs / status / shell
# ---------------------------------------------------------------------------

def cmd_logs(args: argparse.Namespace) -> int:
    mode = resolve_mode(args.mode)
    if mode == "native":
        cmd = [sys.executable, str(APP_PY), "logs"]
        if args.service:
            cmd.append(args.service)
        if args.follow:
            cmd.append("--follow")
        return subprocess.run(cmd, check=False).returncode

    env = _docker_child_env(confirm_mode="complex")
    cmd = _compose_base_cmd() + ["logs"]
    if args.follow:
        cmd.append("-f")
    if args.service:
        cmd.append({"backend": "backend", "frontend": "frontend", "stub": "stub"}[args.service])
    return subprocess.run(cmd, cwd=paths.REPO_ROOT, env=env, check=False).returncode


def cmd_status(args: argparse.Namespace) -> int:
    mode = resolve_mode(args.mode)
    if mode == "native":
        return subprocess.run([sys.executable, str(APP_PY), "status"], check=False).returncode

    env = _docker_child_env(confirm_mode="complex")
    cmd = _compose_base_cmd() + ["ps"]
    return subprocess.run(cmd, cwd=paths.REPO_ROOT, env=env, check=False).returncode


def cmd_shell(args: argparse.Namespace) -> int:
    mode = resolve_mode(args.mode)
    if mode == "native":
        target = {"backend": paths.BACKEND_DIR, "frontend": paths.FRONTEND_DIR, "stub": paths.STUB_PROVIDER_DIR}[args.service]
        print(f"{YELLOW}Native mode has no containers to exec into.{RESET}")
        print(f"The {args.service} process's own directory is {paths.testenv_relative(target)}; "
              f"use `python test_environment/app.py logs {args.service} --follow` to watch it.")
        return 0

    env = _docker_child_env(confirm_mode="complex")
    container = {"backend": "backend", "frontend": "frontend", "stub": "stub"}[args.service]
    shell = "sh"
    cmd = _compose_base_cmd() + ["exec", container, shell]
    return subprocess.run(cmd, cwd=paths.REPO_ROOT, env=env, check=False).returncode


# ---------------------------------------------------------------------------
# e2e
# ---------------------------------------------------------------------------

E2E_DIR = paths.E2E_DIR


def _stack_is_up(mode: str) -> bool:
    if mode == "native":
        from testenvlib.proc import is_running, load_pids  # noqa: PLC0415

        pids = load_pids(paths.PID_FILE)
        return bool(pids) and all(
            isinstance(pids.get(name), int) and is_running(pids[name])
            for name in ("stub", "backend", "frontend")
        )

    env = _docker_child_env(confirm_mode="complex")
    cmd = _compose_base_cmd() + ["ps", "--status", "running", "--format", "{{.Name}}"]
    result = subprocess.run(cmd, cwd=paths.REPO_ROOT, env=env, capture_output=True, text=True, check=False)
    running = set(result.stdout.split())
    return {paths.DOCKER_BACKEND_CONTAINER, paths.DOCKER_FRONTEND_CONTAINER, paths.DOCKER_STUB_CONTAINER} <= running


def cmd_e2e(args: argparse.Namespace) -> int:
    mode = resolve_mode(args.mode)

    if not _stack_is_up(mode):
        print(f"{DIM}Stack not running — starting it first ({mode} mode)...{RESET}")
        up_args = argparse.Namespace(mode=args.mode, build=False, simple_confirm=False)
        rc = cmd_up(up_args)
        if rc != 0:
            return rc

    base_url = args.base_url
    if not base_url:
        port = paths.NATIVE_DEFAULT_FRONTEND_PORT if mode == "native" else paths.DOCKER_DEFAULT_FRONTEND_PORT
        base_url = f"http://127.0.0.1:{port}"

    # The stub is not behind the frontend's /api /internal /content proxy — specs that
    # assert on /__calls (e.g. "exactly 3 translate + 4 TTS calls") need a direct URL.
    stub_url = args.stub_url
    if not stub_url:
        port = paths.NATIVE_DEFAULT_STUB_PORT if mode == "native" else paths.DOCKER_DEFAULT_STUB_HOST_PORT
        stub_url = f"http://127.0.0.1:{port}"

    if not (E2E_DIR / "node_modules").is_dir():
        print(f"{DIM}Installing test_environment/e2e dependencies...{RESET}")
        subprocess.run(["npm", "install"], cwd=E2E_DIR, check=True)

    print(f"{DIM}Ensuring Playwright's Chromium is installed...{RESET}")
    subprocess.run(["npx", "playwright", "install", "chromium", "--with-deps"], cwd=E2E_DIR, check=True)

    env = {**__import__("os").environ, "E2E_BASE_URL": base_url, "E2E_STUB_URL": stub_url}
    cmd = ["npx", "playwright", "test", *args.pw_args]
    print(f"{BOLD}{' '.join(cmd)}{RESET}  (E2E_BASE_URL={base_url}, E2E_STUB_URL={stub_url})")
    result = subprocess.run(cmd, cwd=E2E_DIR, env=env, check=False)
    return result.returncode


# ---------------------------------------------------------------------------
# run (Workstream 3 — selectable execution strategies)
# ---------------------------------------------------------------------------

def cmd_run(args: argparse.Namespace) -> int:
    # Imported lazily, here only: python-gitlab/httpx are Workstream 2/3 dependencies
    # (test_environment/requirements.txt, NOT backend/requirements.txt) — every other testenv.py
    # subcommand must keep working in an environment that only ever installed
    # Workstream 1's deps.
    from mxfix import git_target, worktree  # noqa: PLC0415
    from mxfix.claude_fixer import ClaudeFixer  # noqa: PLC0415
    from mxfix.codex_fixer import CodexFixer  # noqa: PLC0415
    from mxfix.config import ConfigError, load_config, render_config  # noqa: PLC0415
    from mxfix.fixer import FixerError  # noqa: PLC0415
    from mxfix.strategy import batch as batch_strategy  # noqa: PLC0415
    from mxfix.strategy import heal as heal_strategy  # noqa: PLC0415
    from mxfix.strategy import report as report_strategy  # noqa: PLC0415
    from mxfix.strategy import work as work_strategy  # noqa: PLC0415
    from mxissues import trackers  # noqa: PLC0415
    from mxissues.tracker import TrackerError  # noqa: PLC0415
    from testenvlib.envfile import load_env_file  # noqa: PLC0415

    # Every testenv.toml setting has a --<setting> CLI flag (argparse leaves each at its
    # own default=None when not passed) — only the ones actually given end up in
    # overrides, so an unset flag never clobbers testenv.toml/testenv.local.toml with a
    # spurious default. This dict's shape mirrors testenv.toml's own [section] nesting;
    # see mxfix.config.load_config()'s docstring for how it gets merged in.
    limits_overrides = {
        k: v for k, v in {
            "max_fix_attempts": args.max_fix_attempts,
            "concurrency": args.concurrency,
            "severity_threshold": args.severity_threshold,
            "max_new_issues": args.max_new_issues,
        }.items() if v is not None
    }
    fixer_overrides = {
        k: v for k, v in {"adapter": args.adapter, "timeout_seconds": args.timeout_seconds}.items() if v is not None
    }
    git_overrides = {
        k: v for k, v in {
            "base_branch": args.base_branch,
            "branch_prefix": args.branch_prefix,
            "push": args.push,
            "open_pr": args.open_pr,
        }.items() if v is not None
    }
    overrides: dict = {}
    if args.strategy is not None:
        overrides["strategy"] = args.strategy
    if limits_overrides:
        overrides["limits"] = limits_overrides
    if fixer_overrides:
        overrides["fixer"] = fixer_overrides
    if git_overrides:
        overrides["git"] = git_overrides
    if args.tracker is not None:
        overrides["issues"] = {"tracker": args.tracker}

    try:
        config = load_config(overrides=overrides)
    except ConfigError as exc:
        print(f"{RED}{exc}{RESET}", file=sys.stderr)
        return 1

    if args.print_config:
        print(render_config(config))
        return 0

    clean, dirty_detail = worktree.repo_is_clean()
    if not clean:
        print(f"{RED}Refusing to start — the main working tree has uncommitted changes:{RESET}\n{dirty_detail}", file=sys.stderr)
        print(f"{DIM}`report`'s checks would run against a mix of committed and uncommitted code, and heal/batch/work "
              f"verify fixes inside fresh worktrees branched off [git] base_branch — uncommitted local changes here "
              f"would never be reflected there either way. Commit or stash first.{RESET}", file=sys.stderr)
        return 1

    try:
        tracker = trackers.connect(config.issues)
    except TrackerError as exc:
        print(f"{RED}{exc}{RESET}", file=sys.stderr)
        return 1
    print(f"{DIM}issue tracker: {tracker.display_name} ({', '.join(f'{k}={v}' for k, v in tracker.describe())}){RESET}")

    if config.strategy == "report":
        outcome = report_strategy.run(config, tracker, repo_root=paths.REPO_ROOT, mode=args.mode, apply=not args.dry_run)
        status = GREEN + "green" + RESET if outcome.check_run.green else RED + "red" + RESET
        print(f"\n{BOLD}report: {outcome.sync_report.summary()}{RESET}")
        print(
            f"check suite: {status} (backend={'ok' if outcome.check_run.backend_ok else 'FAIL'}, "
            f"tests={'ok' if outcome.check_run.tests_ok else 'FAIL'}, frontend={'ok' if outcome.check_run.frontend_ok else 'FAIL'}, "
            f"e2e={'ok' if outcome.check_run.e2e_ok else 'FAIL'})",
        )
        return 0

    # heal / batch / work write code, push branches, and open PRs — materially higher
    # blast radius than report, hence the extra preflight beyond report's.
    fixer = CodexFixer(timeout_seconds=config.fixer.timeout_seconds) if config.fixer.adapter == "codex" else ClaudeFixer(timeout_seconds=config.fixer.timeout_seconds)

    try:
        fixer.preflight()  # cheap and side-effect-free — run even in --dry-run so a dry
                            # run is a genuine preview of whether the real thing would work
    except FixerError as exc:
        print(f"{RED}Refusing to start {config.strategy} — fixer '{fixer.name}' preflight failed: {exc}{RESET}", file=sys.stderr)
        return 1

    owner_repo = ""
    github_token = ""
    if config.git.push and config.git.open_pr:
        try:
            owner_repo = git_target.github_owner_repo(cwd=paths.REPO_ROOT)
        except git_target.GitTargetError as exc:
            print(f"{RED}Refusing to start {config.strategy} — [git] open_pr is true but {exc}{RESET}", file=sys.stderr)
            return 1
        github_token = load_env_file(paths.REPO_ROOT / ".env").get("GITHUB_PAT", "")
        if not github_token and not args.dry_run:
            print(
                f"{RED}Refusing to start {config.strategy} for real — [git] open_pr is true but GITHUB_PAT is not "
                f"set in .env. Add it, or set open_pr = false / push = false in testenv.toml, or pass --dry-run.{RESET}",
                file=sys.stderr,
            )
            return 1

    strategy_kwargs = dict(
        config=config, tracker=tracker, fixer=fixer,
        owner_repo=owner_repo, github_token=github_token, repo_root=paths.REPO_ROOT,
        mode=args.mode, max_issues=args.max_issues, dry_run=args.dry_run,
    )
    strategy_runners = {"heal": heal_strategy, "batch": batch_strategy, "work": work_strategy}
    outcome = strategy_runners[config.strategy].run(**strategy_kwargs)

    # work has no initial_check/sync_report — it deliberately skips that step (see
    # strategy/work.py); heal/batch always have both.
    initial_check = getattr(outcome, "initial_check", None)
    if initial_check is not None:
        initial_status = GREEN + "green" + RESET if initial_check.green else RED + "red" + RESET
        print(
            f"\n{BOLD}{config.strategy}: initial check suite: {initial_status}{RESET} "
            f"(backend={'ok' if initial_check.backend_ok else 'FAIL'}, "
            f"tests={'ok' if initial_check.tests_ok else 'FAIL'}, "
            f"frontend={'ok' if initial_check.frontend_ok else 'FAIL'}, "
            f"e2e={'ok' if initial_check.e2e_ok else 'FAIL'})",
        )
    sync_report = getattr(outcome, "sync_report", None)
    if sync_report is not None:
        print(f"{BOLD}{config.strategy}: sync: {sync_report.summary()}{RESET}")
    print(f"\n{BOLD}{config.strategy}: done.{RESET}")
    for issue_outcome in getattr(outcome, "issues_attempted", []):
        marker = {"fixed": GREEN, "needs-human": RED, "dry-run": DIM}[issue_outcome.status]
        print(f"  {marker}#{issue_outcome.iid:<5} {issue_outcome.status:<12}{RESET} {issue_outcome.detail}")
    for iid in getattr(outcome, "landed", []):
        print(f"  {GREEN}#{iid:<5} landed{RESET}")
    for iid, reason in getattr(outcome, "returned", []):
        print(f"  {YELLOW}#{iid:<5} returned to open{RESET}  {reason}")
    for iid in getattr(outcome, "merge_conflicts", []):
        print(f"  {YELLOW}#{iid:<5} merge conflict{RESET}")
    return 0


# ---------------------------------------------------------------------------
# argparse wiring
# ---------------------------------------------------------------------------

def main() -> int:
    # --mode must come BEFORE the subcommand (`testenv.py --mode docker up`, not
    # `testenv.py up --mode docker`) — it is defined ONLY on the top-level parser.
    # Giving every subparser its own --mode via a shared parent looks like it would
    # let the flag work in both positions, but argparse applies each subparser's own
    # (unset) default after the top-level parser runs, silently clobbering a value
    # already parsed there back to None. Simpler and correct: one definition, top-level
    # only — the reverse order fails with a clean argument-parsing error instead.
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=["docker", "native"], default=None, help="default: autodetect (docker if the daemon answers); must precede the subcommand")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="mode, compose version, ports, secret scan").set_defaults(func=cmd_doctor)

    p_seed = sub.add_parser("seed", help="(re)build .testenv/{content,config,archive} + test.env")
    p_seed.add_argument("--force", action="store_true")
    p_seed.add_argument("--check-ts", action="store_true", help="report drift vs. the last seed's manifest; do not reseed")
    p_seed.set_defaults(func=cmd_seed)

    p_up = sub.add_parser("up", help="seed if absent, start the stack, wait for readiness")
    p_up.add_argument("--build", action="store_true", help="docker mode: force an image rebuild")
    p_up.add_argument("--simple-confirm", action="store_true")
    p_up.add_argument("--backend-port", type=int, default=None, help="default: mode's standard port (48000 native / 48100 docker)")
    p_up.add_argument("--frontend-port", type=int, default=None, help="default: mode's standard port (48001 native / 48101 docker)")
    p_up.add_argument("--stub-port", type=int, default=None, help="default: mode's standard port (48002 native / 48102 docker)")
    p_up.set_defaults(func=cmd_up)

    p_down = sub.add_parser("down", help="tear down the stack")
    p_down.add_argument("--purge", action="store_true", help="also delete .testenv/ entirely")
    p_down.set_defaults(func=cmd_down)

    p_reset = sub.add_parser("reset", help="down + rm -rf .testenv + seed + up (the workhorse)")
    p_reset.add_argument("--simple-confirm", action="store_true")
    p_reset.add_argument("--backend-port", type=int, default=None)
    p_reset.add_argument("--frontend-port", type=int, default=None)
    p_reset.add_argument("--stub-port", type=int, default=None)
    p_reset.set_defaults(func=cmd_reset)

    p_logs = sub.add_parser("logs", help="stream logs")
    p_logs.add_argument("service", nargs="?", choices=["backend", "frontend", "stub"])
    p_logs.add_argument("--follow", "-f", action="store_true")
    p_logs.set_defaults(func=cmd_logs)

    sub.add_parser("status", help="show what's running").set_defaults(func=cmd_status)

    p_shell = sub.add_parser("shell", help="open a shell in a running service (docker mode)")
    p_shell.add_argument("service", choices=["backend", "frontend", "stub"])
    p_shell.set_defaults(func=cmd_shell)

    p_e2e = sub.add_parser("e2e", help="ensure the stack is up, then run the Playwright suite")
    p_e2e.add_argument("--base-url", default=None, help="default: the resolved mode's standard frontend URL")
    p_e2e.add_argument("--stub-url", default=None, help="default: the resolved mode's standard stub URL")
    p_e2e.add_argument("pw_args", nargs=argparse.REMAINDER, help="passed through to `playwright test` after --")
    p_e2e.set_defaults(func=cmd_e2e)

    p_run = sub.add_parser("run", help="Workstream 3: report|heal|batch|work, driven by testenv.toml — see test_environment/README.md")
    p_run.add_argument("--strategy", choices=["report", "heal", "batch", "work"], default=None, help="overrides testenv.toml's top-level [strategy] for this invocation only")
    p_run.add_argument("--max-issues", type=int, default=None, metavar="N", help="cap how many issues this invocation works on; default: unlimited for heal/work, [limits] concurrency for batch (no testenv.toml equivalent — CLI-only)")
    # One --<setting> per testenv.toml key below, every one default=None so an unset
    # flag never overrides testenv.toml/testenv.local.toml with a spurious value — see
    # cmd_run()'s overrides dict for exactly how each maps back to its [section] key.
    p_run.add_argument("--max-fix-attempts", type=int, default=None, metavar="N", help="overrides [limits] max_fix_attempts")
    p_run.add_argument("--concurrency", type=int, default=None, metavar="N", help="overrides [limits] concurrency")
    p_run.add_argument("--severity-threshold", choices=["high", "medium", "low"], default=None, help="overrides [limits] severity_threshold")
    p_run.add_argument("--max-new-issues", type=int, default=None, metavar="N", help="overrides [limits] max_new_issues")
    p_run.add_argument("--adapter", choices=["codex", "claude"], default=None, help="overrides [fixer] adapter")
    p_run.add_argument("--timeout-seconds", type=int, default=None, metavar="N", help="overrides [fixer] timeout_seconds")
    p_run.add_argument("--base-branch", default=None, metavar="NAME", help="overrides [git] base_branch")
    p_run.add_argument("--branch-prefix", default=None, metavar="PREFIX", help="overrides [git] branch_prefix")
    p_run.add_argument("--push", dest="push", action="store_true", default=None, help="overrides [git] push = true")
    p_run.add_argument("--no-push", dest="push", action="store_false", help="overrides [git] push = false")
    p_run.add_argument("--open-pr", dest="open_pr", action="store_true", default=None, help="overrides [git] open_pr = true")
    p_run.add_argument("--no-open-pr", dest="open_pr", action="store_false", help="overrides [git] open_pr = false")
    p_run.add_argument("--tracker", choices=["gitlab", "github"], default=None, help="overrides [issues] tracker")
    p_run.add_argument("--dry-run", action="store_true", help="checks still run and sync still previews, but no fixer, worktree, issue-tracker or GitHub write happens")
    p_run.add_argument("--print-config", action="store_true", help="print the fully-merged testenv.toml + testenv.local.toml + CLI config and exit; runs nothing")
    p_run.set_defaults(func=cmd_run)

    args = parser.parse_args()
    if getattr(args, "pw_args", None) and args.pw_args and args.pw_args[0] == "--":
        args.pw_args = args.pw_args[1:]
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
