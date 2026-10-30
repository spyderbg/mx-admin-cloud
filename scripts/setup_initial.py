#!/usr/bin/env python3
"""
Initial setup: create the Python venv, install backend/cli requirements,
and install frontend node modules.

Usage:
    python scripts/setup_initial.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
VENV_DIR = REPO_ROOT / '.venv'
BACKEND_REQS = REPO_ROOT / 'backend' / 'requirements.txt'
CLI_REQS = REPO_ROOT / 'cli' / 'requirements.txt'
TEST_ENVIRONMENT_REQS = REPO_ROOT / 'test_environment' / 'requirements.txt'
FRONTEND_DIR = REPO_ROOT / 'frontend'


def run(cmd: list[str], cwd: Path) -> None:
    print(f"+ {subprocess.list2cmdline(cmd)}", flush=True)
    subprocess.check_call(cmd, cwd=cwd)


def create_venv() -> None:
    if VENV_DIR.exists() and (VENV_DIR / 'bin' / 'python').exists():
        print(f"Venv already exists at {VENV_DIR}; reusing it.")
        return
    run([sys.executable, '-m', 'venv', str(VENV_DIR)], cwd=REPO_ROOT)


def upgrade_pip(pip: Path) -> None:
    run([str(pip), 'install', '--upgrade', 'pip'], cwd=REPO_ROOT)


def install_backend_requirements(pip: Path) -> None:
    if not BACKEND_REQS.exists():
        print(f"Warning: {BACKEND_REQS} not found, skipping.")
        return
    run([str(pip), 'install', '-r', str(BACKEND_REQS)], cwd=REPO_ROOT)


def install_cli_requirements(pip: Path) -> None:
    if not CLI_REQS.exists():
        print(f"Warning: {CLI_REQS} not found, skipping.")
        return
    run([str(pip), 'install', '-r', str(CLI_REQS)], cwd=REPO_ROOT)


def install_test_environment_requirements(pip: Path) -> None:
    # python-gitlab (test_environment/issues.py) — deliberately not in backend/requirements.txt,
    # which docker/backend.Dockerfile ships into the production image.
    if not TEST_ENVIRONMENT_REQS.exists():
        print(f"Warning: {TEST_ENVIRONMENT_REQS} not found, skipping.")
        return
    run([str(pip), 'install', '-r', str(TEST_ENVIRONMENT_REQS)], cwd=REPO_ROOT)


def install_frontend_modules() -> None:
    if (FRONTEND_DIR / 'package.json').exists():
        run(['npm', 'install'], cwd=FRONTEND_DIR)
    else:
        print(f"Warning: {FRONTEND_DIR / 'package.json'} not found, skipping frontend.")


def main() -> None:
    pip = VENV_DIR / 'bin' / 'pip'
    create_venv()
    upgrade_pip(pip)
    install_backend_requirements(pip)
    install_cli_requirements(pip)
    install_test_environment_requirements(pip)
    install_frontend_modules()
    print('\nSetup complete.')


if __name__ == '__main__':
    main()
