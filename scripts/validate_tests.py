#!/usr/bin/env python3
"""Run the backend, CLI and frontend unit tests and display a summary table.

Every suite must run: one that cannot (npm missing, no "test" script), crashes, exits non-zero or
runs zero tests counts as a failure, so a skipped suite never reads as a pass.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TypeAlias


RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
GREEN  = "\033[92m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RESET  = "\033[0m"

TestResult: TypeAlias = dict[str, int | str]


def _resolve_paths() -> tuple[Path, Path, Path, Path, Path]:
    script_dir = Path(__file__).resolve().parent
    repo_root = script_dir.parent
    backend_dir = repo_root / "backend"
    cli_dir = repo_root / "cli"
    frontend_dir = repo_root / "frontend"
    repo_venv = repo_root / ".venv"
    backend_venv = backend_dir / ".venv"

    if repo_venv.is_dir():
        venv_dir = repo_venv
    elif backend_venv.is_dir():
        venv_dir = backend_venv
    else:
        venv_dir = backend_venv

    return repo_root, backend_dir, cli_dir, frontend_dir, venv_dir


def _venv_python(venv_dir: Path) -> Path:
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _run(command: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    subprocess.run(command, cwd=str(cwd) if cwd else None, env=env, check=True)


def _run_capture(
    command: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> tuple[str, str, int]:
    result = subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        env=env,
        capture_output=True,
        text=True,
    )
    return result.stdout, result.stderr, result.returncode


def _find_python_for_venv() -> list[str]:
    candidates: list[list[str]] = []

    if sys.version_info >= (3, 10):
        candidates.append([sys.executable])

    for command in (["python3.10"], ["python3"], ["py", "-3.10"], ["py", "-3"]):
        executable = shutil.which(command[0])
        if executable:
            candidates.append([executable, *command[1:]])

    seen: set[tuple[str, ...]] = set()
    for candidate in candidates:
        key = tuple(candidate)
        if key in seen:
            continue
        seen.add(key)
        try:
            subprocess.run(
                [*candidate, "--version"],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return candidate
        except (subprocess.CalledProcessError, OSError):
            continue

    raise RuntimeError("Error: python3.10, python3, or py launcher with Python 3.10+ is required.")


def _ensure_venv(backend_dir: Path, venv_dir: Path) -> Path:
    if not venv_dir.is_dir():
        python_cmd = _find_python_for_venv()
        print(f"Creating backend virtual environment at {venv_dir}")
        _run([*python_cmd, "-m", "venv", str(venv_dir)])

    venv_python = _venv_python(venv_dir)
    if not venv_python.is_file():
        raise RuntimeError(f"Error: virtual environment python not found at {venv_python}")
    return venv_python


def _new_result(returncode: int = 0) -> TestResult:
    return {"passed": 0, "failed": 0, "errors": 0, "skipped": 0, "total": 0, "time": "", "returncode": returncode}


# Outcome words in pytest's final summary line, mapped to TestResult keys ("error" and "errors" both occur).
_PYTEST_OUTCOMES = {"passed": "passed", "failed": "failed", "error": "errors", "errors": "errors", "skipped": "skipped"}


def _parse_pytest_summary(output: str, returncode: int) -> TestResult:
    """Read pytest's final summary line, with or without its "===" frame (-q drops it).

    e.g. "=== 3 failed, 1007 passed, 1 warning in 31.28s ===", "4 errors in 0.17s",
    "1010 passed in 75.12s (0:01:15)".
    """
    result = _new_result(returncode)

    for line in reversed(output.splitlines()):
        stripped = line.strip().strip("=").strip()
        time_match = re.search(r"\bin\s+([\d.]+)s\b", stripped)
        counts = re.findall(r"(\d+)\s+(passed|failed|errors?|skipped)\b", stripped)
        if not time_match or not (counts or stripped.startswith("no tests ran")):
            continue

        result["time"] = time_match.group(1) + "s"
        for count_str, outcome in counts:
            key = _PYTEST_OUTCOMES[outcome]
            result[key] = int(result[key]) + int(count_str)
        break

    result["total"] = int(result["passed"]) + int(result["failed"]) + int(result["errors"])
    return result


def _parse_node_test_summary(output: str, returncode: int) -> TestResult:
    """Read the summary node:test prints ("ℹ pass 53" with the spec reporter, "# pass 53" with TAP).

    Sums every summary found, in case the "test" script runs node --test more than once.
    """
    result = _new_result(returncode)
    duration_ms = 0.0
    found = False

    for line in output.splitlines():
        match = re.match(r"^\s*(?:ℹ|#)\s+(pass|fail|cancelled|skipped|todo|duration_ms)\s+([\d.]+)\s*$", line)
        if not match:
            continue
        found = True
        label, value = match.groups()
        if label == "duration_ms":
            duration_ms += float(value)
        elif label == "pass":
            result["passed"] = int(result["passed"]) + int(value)
        elif label in ("fail", "cancelled"):
            result["failed"] = int(result["failed"]) + int(value)
        else:  # skipped, todo
            result["skipped"] = int(result["skipped"]) + int(value)

    if found:
        result["time"] = f"{duration_ms / 1000:.2f}s"
    result["total"] = int(result["passed"]) + int(result["failed"]) + int(result["errors"])
    return result


def _print_output(stdout: str, stderr: str) -> None:
    if stdout:
        print(stdout)
    if stderr:
        print(stderr, file=sys.stderr)


def _run_pytest(project_dir: Path, venv_python: Path, *, env: dict[str, str], junit_xml: Path | None = None) -> TestResult:
    command = [str(venv_python), "-m", "pytest"]
    if junit_xml is not None:
        junit_xml.parent.mkdir(parents=True, exist_ok=True)
        command.append(f"--junit-xml={junit_xml}")

    stdout, stderr, rc = _run_capture(command, cwd=project_dir, env=env)
    _print_output(stdout, stderr)
    return _parse_pytest_summary(stdout, rc)


def _run_backend_tests(backend_dir: Path, venv_python: Path, *, junit_xml: Path | None = None) -> TestResult:
    print(f"\n{BOLD}── Backend Tests ──{RESET}\n")

    env = os.environ.copy()
    env["PYTHONPATH"] = str(backend_dir / "src")
    return _run_pytest(backend_dir, venv_python, env=env, junit_xml=junit_xml)


def _run_cli_tests(cli_dir: Path, venv_python: Path) -> TestResult:
    """Run cli/tests; cli/pyproject.toml puts cli/src on the path, so the package need not be installed."""
    print(f"\n{BOLD}── CLI Tests ──{RESET}\n")

    env = os.environ.copy()
    env.pop("PYTHONPATH", None)  # never let a backend path shadow the CLI's modules
    return _run_pytest(cli_dir, venv_python, env=env)


def _run_frontend_tests(frontend_dir: Path) -> TestResult | None:
    """Run "npm test"; None (a failure) when it cannot run at all."""
    print(f"\n{BOLD}── Frontend Tests ──{RESET}\n")

    package_json = frontend_dir / "package.json"
    if not package_json.exists():
        print(f"  {RED}No package.json found in {frontend_dir}.{RESET}\n")
        return None

    with open(package_json, encoding="utf-8") as f:
        pkg = json.load(f)

    scripts = pkg.get("scripts", {})
    if "test" not in scripts:
        print(f"  {RED}No \"test\" script in package.json.{RESET}\n")
        return None

    npm = shutil.which("npm")
    if not npm:
        print(f"  {RED}npm is required but not found in PATH.{RESET}\n")
        return None

    if not (frontend_dir / "node_modules").is_dir():
        print("Installing frontend dependencies...")
        _run([npm, "install"], cwd=frontend_dir)

    print("  Running via npm test...\n")
    stdout, stderr, rc = _run_capture([npm, "test", "--silent"], cwd=frontend_dir)
    _print_output(stdout, stderr)
    return _parse_node_test_summary(stdout, rc)


# ── table display ──────────────────────────────────────────────────────────────

def _box_row(cells: list[str], widths: list[int], color: str = "") -> str:
    parts = [f" {color}{cell.ljust(w)}{RESET} " for cell, w in zip(cells, widths)]
    return "│" + "│".join(parts) + "│"


def _box_sep(widths: list[int], left: str, mid: str, right: str) -> str:
    return left + mid.join("─" * (w + 2) for w in widths) + right


def _fmt_result(r: TestResult | None) -> list[str]:
    if r is None:
        return ["not run", "—", "—", "—", "—", "—"]
    return [
        str(r["total"]),
        str(r["passed"]),
        str(r["failed"]),
        str(r["errors"]),
        str(r["skipped"]),
        str(r["time"]) if r["time"] else "—",
    ]


def _failure_reason(r: TestResult | None) -> str | None:
    """Why a suite counts as failed, or None when it passed."""
    if r is None:
        return "did not run"
    if int(r["failed"]) > 0 or int(r["errors"]) > 0:
        return f"{r['failed']} failed, {r['errors']} errors"
    if int(r["returncode"]) != 0:
        return f"exited with code {r['returncode']}"
    if int(r["total"]) == 0:
        return "ran no tests"
    return None


def _print_summary_table(results: list[tuple[str, TestResult | None]]) -> None:
    headers = ["", "Total", "Passed", "Failed", "Errors", "Skipped", "Time"]
    rows = [[name] + _fmt_result(result) for name, result in results]

    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    full_w = sum(widths) + len(widths) * 3 - 1
    title = "Tests Summary"

    print()
    print(BOLD + "┌" + "─" * full_w + "┐" + RESET)
    print(BOLD + "│" + title.center(full_w) + "│" + RESET)
    print(_box_sep(widths, "├", "┬", "┤"))
    print(_box_row(headers, widths))
    print(_box_sep(widths, "├", "┼", "┤"))

    for row, (_, result) in zip(rows, results):
        color = RED if _failure_reason(result) else GREEN
        print(_box_row(row, widths, color))

    print(_box_sep(widths, "└", "┴", "┘"))
    print()


# ── main ───────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--junit-xml", type=Path, default=None,
        help="also write a JUnit XML report of the backend tests here (for test_environment/issues.py "
             "sync --pytest) — the human table is unaffected",
    )
    args = parser.parse_args()

    _, backend_dir, cli_dir, frontend_dir, venv_dir = _resolve_paths()

    try:
        venv_python = _ensure_venv(backend_dir, venv_dir)

        print("Installing backend and CLI dependencies...")
        _run([str(venv_python), "-m", "pip", "install", "-r", "requirements.txt", "-q"], cwd=backend_dir)
        _run([str(venv_python), "-m", "pip", "install", "-r", "requirements.txt", "-q"], cwd=cli_dir)

        results: list[tuple[str, TestResult | None]] = [
            ("Backend", _run_backend_tests(backend_dir, venv_python, junit_xml=args.junit_xml)),
            ("CLI", _run_cli_tests(cli_dir, venv_python)),
            ("Frontend", _run_frontend_tests(frontend_dir)),
        ]

        _print_summary_table(results)

        failures = [(name, reason) for name, result in results if (reason := _failure_reason(result))]
        if not failures:
            print(f"{GREEN}All tests passed.{RESET}")
            return 0
        for name, reason in failures:
            print(f"{RED}{name}: {reason}.{RESET}", file=sys.stderr)
        print(f"{RED}Some tests failed.{RESET}", file=sys.stderr)
        return 1

    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        return exc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
