"""Backend virtual environment resolution, shared by app.py and testenv.py doctor.

Mirrors the convention already established by scripts/validate_tests.py and
scripts/validate_build.py: prefer a repo-root .venv, fall back to backend/.venv,
create it if neither exists. Kept here rather than duplicated a third time.

Prefers the newest Python 3.11/3.12 interpreter available over whatever `python3`
happens to resolve to: the application Dockerfile pins python:3.11-slim, and several
backend dependencies (bcrypt, pydub) ship compiled wheels that lag brand-new Python
releases by months. A repo checked out on a machine whose only `python3` is a
same-day release is exactly when silently using it would bite hardest.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from testenvlib import paths

PREFERRED_VERSIONS = ("3.12", "3.11", "3.10")


def _candidate_interpreters() -> list[list[str]]:
    candidates: list[list[str]] = []
    for version in PREFERRED_VERSIONS:
        exe = shutil.which(f"python{version}")
        if exe:
            candidates.append([exe])
    if sys.version_info >= (3, 10):
        candidates.append([sys.executable])
    for name in ("python3.10", "python3"):
        exe = shutil.which(name)
        if exe:
            candidates.append([exe])
    return candidates


def find_python_for_venv() -> list[str]:
    seen: set[str] = set()
    for candidate in _candidate_interpreters():
        key = candidate[0]
        if key in seen:
            continue
        seen.add(key)
        try:
            subprocess.run([*candidate, "--version"], check=True, capture_output=True)
        except (subprocess.CalledProcessError, OSError):
            continue
        return candidate
    raise RuntimeError("No usable Python 3.10+ interpreter found to create a virtual environment.")


def venv_dir() -> Path:
    repo_venv = paths.REPO_ROOT / ".venv"
    backend_venv = paths.BACKEND_DIR / ".venv"
    if repo_venv.is_dir():
        return repo_venv
    if backend_venv.is_dir():
        return backend_venv
    return repo_venv  # created fresh at the repo root if neither exists yet


def venv_python(venv: Path) -> Path:
    if sys.platform == "win32":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def ensure_venv(*, install_requirements: bool = True) -> Path:
    """Return the venv's python executable, creating the venv (and installing
    backend + cli requirements) on first use.
    """
    venv = venv_dir()
    python_path = venv_python(venv)
    created = False
    if not python_path.is_file():
        interpreter = find_python_for_venv()
        print(f"Creating virtual environment at {paths.testenv_relative(venv)} ({interpreter[0]})")
        subprocess.run([*interpreter, "-m", "venv", str(venv)], check=True)
        created = True

    if install_requirements and created:
        _install(python_path, paths.BACKEND_DIR / "requirements.txt")
        cli_requirements = paths.REPO_ROOT / "cli" / "requirements.txt"
        if cli_requirements.is_file():
            _install(python_path, cli_requirements)

    return python_path


def _install(python_path: Path, requirements: Path) -> None:
    if not requirements.is_file():
        return
    print(f"Installing {paths.testenv_relative(requirements)} ...")
    subprocess.run([str(python_path), "-m", "pip", "install", "-q", "-r", str(requirements)], check=True)


def venv_ready(venv: Path) -> bool:
    return venv_python(venv).is_file()
