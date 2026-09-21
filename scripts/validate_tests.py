#!/usr/bin/env python3
"""Run backend and frontend unit tests and display a summary table."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from collections import defaultdict
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


def _resolve_paths() -> tuple[Path, Path, Path, Path]:
    script_dir = Path(__file__).resolve().parent
    repo_root = script_dir.parent
    backend_dir = repo_root / "backend"
    frontend_dir = repo_root / "frontend"
    repo_venv = repo_root / ".venv"
    backend_venv = backend_dir / ".venv"

    if repo_venv.is_dir():
        venv_dir = repo_venv
    elif backend_venv.is_dir():
        venv_dir = backend_venv
    else:
        venv_dir = backend_venv

    return repo_root, backend_dir, frontend_dir, venv_dir


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


def _parse_pytest_summary(output: str) -> TestResult:
    result: TestResult = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0, "total": 0, "time": ""}

    for line in output.splitlines():
        stripped = line.strip()
        if not (stripped.startswith("===") and stripped.endswith("===")):
            continue
        if not re.search(r"\d+\s+(passed|failed|error|skipped)", stripped):
            continue

        time_match = re.search(r"in\s+([\d.]+)\s*s", stripped)
        if time_match:
            result["time"] = time_match.group(1) + "s"

        counts = re.findall(r"(\d+)\s+(passed|failed|error|skipped)", stripped)
        for count_str, label in counts:
            result[label] = int(count_str)

        result["total"] = int(result["passed"]) + int(result["failed"]) + int(result["errors"])
        break

    return result


def _run_backend_tests(backend_dir: Path, venv_python: Path, *, junit_xml: Path | None = None) -> TestResult:
    print(f"\n{BOLD}── Backend Tests ──{RESET}\n")

    env = os.environ.copy()
    env["PYTHONPATH"] = str(backend_dir / "src")

    command = [str(venv_python), "-m", "pytest"]
    if junit_xml is not None:
        junit_xml.parent.mkdir(parents=True, exist_ok=True)
        command.append(f"--junit-xml={junit_xml}")

    stdout, stderr, rc = _run_capture(
        command,
        cwd=backend_dir,
        env=env,
    )

    if stdout:
        print(stdout)
    if stderr:
        print(stderr, file=sys.stderr)

    result = _parse_pytest_summary(stdout)
    return result


def _run_frontend_tests(frontend_dir: Path) -> TestResult | None:
    print(f"\n{BOLD}── Frontend Tests ──{RESET}\n")

    package_json = frontend_dir / "package.json"
    if not package_json.exists():
        print("  No package.json found. Skipping.\n")
        return None

    with open(package_json) as f:
        pkg = json.load(f)

    scripts = pkg.get("scripts", {})
    if "test" not in scripts:
        print("  No test script defined in package.json. Skipping.\n")
        return None

    npm = shutil.which("npm")
    if not npm:
        print("  npm not found. Skipping.\n")
        return None

    print("  Running via npm test...\n")
    stdout, stderr, rc = _run_capture(
        [npm, "test", "--silent"],
        cwd=frontend_dir,
    )

    if stdout:
        print(stdout)
    if stderr:
        print(stderr, file=sys.stderr)

    result = _parse_pytest_summary(stdout)
    return result


# ── table display ──────────────────────────────────────────────────────────────

def _box_row(cells: list[str], widths: list[int], color: str = "") -> str:
    parts = [f" {color}{cell.ljust(w)}{RESET} " for cell, w in zip(cells, widths)]
    return "│" + "│".join(parts) + "│"


def _box_sep(widths: list[int], left: str, mid: str, right: str) -> str:
    return left + mid.join("─" * (w + 2) for w in widths) + right


def _fmt_result(r: TestResult | None) -> list[str]:
    if r is None:
        return ["N/A", "N/A", "N/A", "N/A", "N/A", "N/A"]
    return [
        str(r["total"]),
        str(r["passed"]),
        str(r["failed"]),
        str(r["errors"]),
        str(r["skipped"]),
        str(r["time"]) if r["time"] else "—",
    ]


def _has_failures(r: TestResult | None) -> bool:
    if r is None:
        return False
    return int(r["failed"]) > 0 or int(r["errors"]) > 0


def _print_summary_table(backend_result: TestResult | None, frontend_result: TestResult | None) -> None:
    headers = ["", "Total", "Passed", "Failed", "Errors", "Skipped", "Time"]

    rows = [
        ["Backend"] + _fmt_result(backend_result),
        ["Frontend"] + _fmt_result(frontend_result),
    ]

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

    for row in rows:
        if row[0] == "Backend":
            color = RED if _has_failures(backend_result) else GREEN
        elif row[0] == "Frontend":
            color = RED if _has_failures(frontend_result) else GREEN
        else:
            color = DIM
        print(_box_row(row, widths, color))

    print(_box_sep(widths, "└", "┴", "┘"))
    print()


# ── main ───────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--junit-xml", type=Path, default=None,
        help="also write a JUnit XML report here (for scripts/issues.py sync --pytest) — the human table is unaffected",
    )
    args = parser.parse_args()

    _, backend_dir, frontend_dir, venv_dir = _resolve_paths()
    exit_code = 0

    try:
        venv_python = _ensure_venv(backend_dir, venv_dir)

        print("Installing backend dependencies...")
        _run([str(venv_python), "-m", "pip", "install", "-r", "requirements.txt", "-q"], cwd=backend_dir)

        backend_result = _run_backend_tests(backend_dir, venv_python, junit_xml=args.junit_xml)
        if _has_failures(backend_result):
            exit_code = 1

        frontend_result = _run_frontend_tests(frontend_dir)
        if _has_failures(frontend_result):
            exit_code = 1

        _print_summary_table(backend_result, frontend_result)

        if exit_code == 0:
            print(f"{GREEN}All tests passed.{RESET}")
        else:
            print(f"{RED}Some tests failed.{RESET}", file=sys.stderr)

        return exit_code

    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        return exc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
