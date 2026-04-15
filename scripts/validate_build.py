#!/usr/bin/env python3
"""Validate backend Python code in a cross-platform way."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def _resolve_paths() -> tuple[Path, Path, Path]:
    script_dir = Path(__file__).resolve().parent
    repo_root = script_dir.parent
    backend_dir = repo_root / "backend"
    repo_venv = repo_root / ".venv"
    backend_venv = backend_dir / ".venv"

    if repo_venv.is_dir():
        venv_dir = repo_venv
    elif backend_venv.is_dir():
        venv_dir = backend_venv
    else:
        venv_dir = backend_venv

    return repo_root, backend_dir, venv_dir


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


def _venv_python(venv_dir: Path) -> Path:
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _run(command: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    subprocess.run(command, cwd=str(cwd) if cwd else None, env=env, check=True)


def main() -> int:
    _, backend_dir, venv_dir = _resolve_paths()

    try:
        if not venv_dir.is_dir():
            python_cmd = _find_python_for_venv()
            print(f"Creating backend virtual environment at {venv_dir}")
            _run([*python_cmd, "-m", "venv", str(venv_dir)])

        venv_python = _venv_python(venv_dir)
        if not venv_python.is_file():
            raise RuntimeError(f"Error: virtual environment python not found at {venv_python}")

        print("Installing backend dependencies")
        _run([str(venv_python), "-m", "pip", "install", "-r", "requirements.txt"], cwd=backend_dir)

        print("Compiling backend Python sources")
        _run([str(venv_python), "-m", "compileall", "-q", "src"], cwd=backend_dir)

        print("Linting backend Python sources")
        env = os.environ.copy()
        env["PYTHONPATH"] = str(backend_dir / "src")
        _run([str(venv_python), "-m", "pylint", "src/"], cwd=backend_dir, env=env)

        print("Validation completed successfully.")
        return 0
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        return exc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
