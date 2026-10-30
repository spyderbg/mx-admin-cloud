"""Python interpreter discovery for role commands (W2).

Ubuntu 24.04 ships `python3` but no `python`, so a role allowlist such as `python -m pytest`
cannot run there. Role commands therefore name the placeholder `{python}`, which resolves to:

  * `runtime.python_interpreter` when it is an absolute path (it must exist and be executable);
  * `auto`: the workspace's virtual environment (`.venv/bin/python`, then `venv/bin/python`),
    else `python3` on the search path, else `python` on the search path.

No global `python` symlink is needed and nothing is installed. The resolved interpreter also
becomes the exact prefix of the Claude Code Bash permission rule, so the allowlist stays as
narrow as before: `{python} -m pytest:*` allows `<interpreter> -m pytest …`, nothing else.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .errors import CoordinatorError

PLACEHOLDER = "{python}"
VENV_CANDIDATES = (".venv/bin/python", "venv/bin/python")


class InterpreterNotFoundError(CoordinatorError):
    """No usable Python interpreter for the role commands."""


@dataclass(frozen=True)
class PythonInterpreter:
    """A resolved interpreter: the absolute path, and how it was found."""

    path: str
    source: str  # configured | workspace-venv | search-path

    @property
    def venv_bin(self) -> str | None:
        """The virtual environment's bin directory, when the interpreter belongs to one."""
        parent = Path(self.path).parent
        return str(parent) if (parent.parent / "pyvenv.cfg").is_file() else None


def discover_python(workspace: Path, configured: str = "auto", *, search_path: str | None = None) -> PythonInterpreter:
    """Resolve the interpreter for *workspace*. Raises InterpreterNotFoundError."""
    if configured != "auto":
        if not (os.path.isfile(configured) and os.access(configured, os.X_OK)):
            raise InterpreterNotFoundError(f"runtime.python_interpreter {configured!r} is not an executable file")
        return PythonInterpreter(configured, "configured")
    for rel in VENV_CANDIDATES:
        candidate = Path(workspace) / rel
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return PythonInterpreter(str(candidate), "workspace-venv")
    for name in ("python3", "python"):
        found = shutil.which(name, path=search_path if search_path is not None else os.environ.get("PATH", ""))
        if found:
            return PythonInterpreter(os.path.abspath(found), "search-path")
    raise InterpreterNotFoundError("no Python interpreter: no workspace .venv and neither python3 nor python on "
                                   "the search path; set runtime.python_interpreter")


def expand_command(command: str, interpreter: PythonInterpreter, workspace: Path) -> list[str]:
    """The Bash permission prefixes for one role command. `{python} …` yields the absolute
    interpreter form and, for an interpreter inside the workspace, its workspace-relative form."""
    if not command.startswith(PLACEHOLDER + " "):
        return [command]
    rest = command[len(PLACEHOLDER):]
    forms = [interpreter.path]
    try:
        forms.append(str(Path(interpreter.path).relative_to(Path(workspace).resolve())))
    except ValueError:
        pass
    if any(any(c.isspace() for c in form) for form in forms):
        raise InterpreterNotFoundError(f"interpreter path {interpreter.path!r} contains whitespace; "
                                       "set runtime.python_interpreter to a path without spaces")
    return [form + rest for form in dict.fromkeys(forms)]


def expand_commands(commands: Sequence[str], interpreter: PythonInterpreter | None, workspace: Path) -> list[str]:
    """Every role command expanded; a command needing {python} without an interpreter raises."""
    expanded: list[str] = []
    for command in commands:
        if command.startswith(PLACEHOLDER) and interpreter is None:
            raise InterpreterNotFoundError(f"role command {command!r} needs a Python interpreter")
        expanded += expand_command(command, interpreter, workspace) if interpreter else [command]
    return expanded
