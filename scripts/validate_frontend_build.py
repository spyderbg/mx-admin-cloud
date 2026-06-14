#!/usr/bin/env python3
"""Validate frontend TypeScript/Vue build in a cross-platform way."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from pathlib import Path


RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
GREEN  = "\033[92m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RESET  = "\033[0m"

_TSC_SEV: dict[str, tuple[str, str]] = {
    "error":   ("TypeScript Error", RED),
    "warning": ("TypeScript Warning", YELLOW),
}

_VITE_SEV: dict[str, tuple[str, str]] = {
    "error":   ("Build Error", RED),
    "warning": ("Build Warning", YELLOW),
}

_SEVERITY_ORDER = ["TypeScript Error", "Build Error", "TypeScript Warning", "Build Warning"]

from typing import TypeAlias
Issue: TypeAlias = tuple[str, int, str, str]


def _resolve_paths() -> tuple[Path, Path]:
    script_dir = Path(__file__).resolve().parent
    repo_root = script_dir.parent
    frontend_dir = repo_root / "frontend"
    return repo_root, frontend_dir


def _find_node() -> str:
    node = shutil.which("node")
    if not node:
        raise RuntimeError("Node.js is required but not found in PATH.")
    return node


def _find_npm() -> str:
    npm = shutil.which("npm")
    if not npm:
        raise RuntimeError("npm is required but not found in PATH.")
    return npm


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


def _parse_tsc_issues(stdout: str, stderr: str) -> list[Issue]:
    """Parse vue-tsc / tsc output into Issue tuples."""
    issues: list[Issue] = []
    combined = stdout + "\n" + stderr
    pattern = re.compile(
        r"^(.+?)\((\d+),\d+\):\s+(error|warning)\s+(TS\d+):\s+(.+)$"
    )
    alt_pattern = re.compile(
        r"^(.+?):(\d+):\d+[: -]+\s+(error|warning)\s+(TS\d+):\s+(.+)$"
    )
    for line in combined.splitlines():
        m = pattern.match(line.rstrip())
        if not m:
            m = alt_pattern.match(line.rstrip())
        if not m:
            continue
        filepath, lineno, sev_code, ts_code, message = m.groups()
        label, _ = _TSC_SEV.get(sev_code, ("Unknown", RESET))
        issues.append((filepath, int(lineno), f"[{ts_code}] {message.strip()}", sev_code))
    return issues


def _parse_vite_issues(stdout: str, stderr: str) -> list[Issue]:
    """Parse vite build output into Issue tuples."""
    issues: list[Issue] = []
    combined = stdout + "\n" + stderr

    file_line_pattern = re.compile(
        r"^(.+?):(\d+):(\d+):\s+(error|warning)[:\s]+(.+)$"
    )
    for line in combined.splitlines():
        m = file_line_pattern.match(line.rstrip())
        if m:
            filepath, lineno, col, sev_code, message = m.groups()
            issues.append((filepath, int(lineno), message.strip(), sev_code))
            continue

        x_error = re.match(
            r"^(✘|x)\s+\[ERROR\]\s+(.+)$", line.rstrip()
        )
        if x_error:
            issues.append(("vite", 0, x_error.group(2).strip(), "error"))

    return issues


def _print_summary(
    tsc_issues: list[Issue],
    vite_issues: list[Issue],
) -> None:
    tsc_errors = [i for i in tsc_issues if i[3] == "error"]
    tsc_warnings = [i for i in tsc_issues if i[3] == "warning"]
    vite_errors = [i for i in vite_issues if i[3] == "error"]
    vite_warnings = [i for i in vite_issues if i[3] == "warning"]

    counts: dict[str, int] = {
        "TypeScript Error":   len(tsc_errors),
        "Build Error":        len(vite_errors),
        "TypeScript Warning": len(tsc_warnings),
        "Build Warning":      len(vite_warnings),
    }
    total = sum(counts.values())

    rows = [
        [sev, str(cnt), "vue-tsc" if "TypeScript" in sev else "vite"]
        for sev, cnt in counts.items()
        if cnt > 0
    ]

    print()
    if not rows:
        print(f"{GREEN}{BOLD}  No issues found.{RESET}\n")
        return

    headers = ["Severity", "Count", "Stage"]
    widths = [
        max(len(headers[i]), max(len(r[i]) for r in rows))
        for i in range(3)
    ]
    title = "Validation Summary"
    full_w = sum(widths) + len(widths) * 3 - 1

    print(BOLD + "┌" + "─" * full_w + "┐" + RESET)
    print(BOLD + "│" + title.center(full_w) + "│" + RESET)
    print("├" + "─" * (widths[0] + 2) + "┬" + "─" * (widths[1] + 2) + "┬" + "─" * (widths[2] + 2) + "┤")
    print("│" + f" {headers[0].ljust(widths[0])} " + "│" + f" {headers[1].ljust(widths[1])} " + "│" + f" {headers[2].ljust(widths[2])} " + "│")
    print("├" + "─" * (widths[0] + 2) + "┼" + "─" * (widths[1] + 2) + "┼" + "─" * (widths[2] + 2) + "┤")
    for row in rows:
        label, _ = (
            _TSC_SEV.get(row[0][:5].lower(), (row[0], ""))
            if "TypeScript" in row[0]
            else _VITE_SEV.get(row[0][:5].lower(), (row[0], ""))
        )
        color = RED if "Error" in row[0] else (YELLOW if "Warning" in row[0] else DIM)
        print(f"│ {color}{row[0].ljust(widths[0])}{RESET} │ {color}{row[1].ljust(widths[1])}{RESET} │ {color}{row[2].ljust(widths[2])}{RESET} │")
    print("└" + "─" * (widths[0] + 2) + "┴" + "─" * (widths[1] + 2) + "┴" + "─" * (widths[2] + 2) + "┘")
    print(f"\n  Total issues: {BOLD}{total}{RESET}\n")


def _print_issues(
    tsc_issues: list[Issue],
    vite_issues: list[Issue],
) -> None:
    all_issues: dict[str, list[Issue]] = {
        "TypeScript Error":   [i for i in tsc_issues if i[3] == "error"],
        "TypeScript Warning": [i for i in tsc_issues if i[3] == "warning"],
        "Build Error":        [i for i in vite_issues if i[3] == "error"],
        "Build Warning":      [i for i in vite_issues if i[3] == "warning"],
    }

    for sev in reversed(_SEVERITY_ORDER):
        items = all_issues.get(sev, [])
        if not items:
            continue

        color = RED if "Error" in sev else YELLOW
        stage = "vue-tsc" if "TypeScript" in sev else "vite"
        print(f"{color}{BOLD}── {sev} ({stage}) ──{RESET}")
        for filepath, lineno, message, _code in items:
            print(f"  {color}{filepath}:{lineno}{RESET}  {message}")
        print()


def main() -> int:
    _, frontend_dir = _resolve_paths()

    tsc_issues: list[Issue] = []
    vite_issues: list[Issue] = []
    exit_code = 0

    try:
        _find_node()

        npm = _find_npm()

        node_version = subprocess.run(
            [_find_node(), "--version"], capture_output=True, text=True
        ).stdout.strip()
        npm_version = subprocess.run(
            [npm, "--version"], capture_output=True, text=True
        ).stdout.strip()
        print(f"  Node {node_version}  npm {npm_version}")

        if not (frontend_dir / "node_modules").is_dir():
            print("Installing frontend dependencies...")
            _run([npm, "install"], cwd=frontend_dir)
        else:
            print("node_modules found, skipping install.")

        print("Type-checking frontend sources with vue-tsc...")
        stdout, stderr, rc = _run_capture(
            ["npx", "vue-tsc", "--noEmit"], cwd=frontend_dir
        )
        if rc != 0:
            exit_code = rc
            tsc_issues = _parse_tsc_issues(stdout, stderr)

        print("Building frontend with Vite...")
        env = os.environ.copy()
        env["NODE_ENV"] = "production"
        stdout, stderr, rc = _run_capture(
            ["npx", "vite", "build"], cwd=frontend_dir, env=env
        )
        if rc != 0:
            exit_code = exit_code or rc
            vite_issues = _parse_vite_issues(stdout, stderr)

        _print_issues(tsc_issues, vite_issues)
        _print_summary(tsc_issues, vite_issues)

        if exit_code == 0:
            print(f"{GREEN}Validation completed successfully.{RESET}")
        else:
            print(f"{RED}Validation completed with issues.{RESET}", file=sys.stderr)

        return exit_code

    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        return exc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
