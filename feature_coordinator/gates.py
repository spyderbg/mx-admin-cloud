"""Acceptance and integration gates: sandboxed, offline, with timeouts and logs (Phase 3).

Every gate runs in a bubblewrap sandbox built by SandboxBuilder with no writable repository
path at all: the checkout is read-only, there is no network, and HOME and /tmp are private.
A gate therefore cannot modify the checkout, production data or user content — a test
that tries (B-7) fails instead of writing.

  * a gate listed in `unsafe_gates` is not run against a real checkout; it is reported as
    `blocked` with the recorded reason (it never counts as passed);
  * a gate that exceeds its timeout is killed (whole sandbox) and reported as `timeout`;
  * stdout and stderr go to `<log_root>/<gate>.<stamp>/{stdout,stderr}.log`;
  * lint gates (D-19) are baseline-aware: see lint_baseline.py.

`python` resolves to the repository's `.venv/bin/python` when it exists; other tools
(`npm`, `node`) are resolved on the host and their install prefix is bound read-only.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from .config import ProjectConfig
from .model import EXIT_BLOCKED, EXIT_ERROR, EXIT_TIMEOUT, GateResult, utc_now
from .sandbox import Bind, SandboxBuilder, SandboxUnavailableError, check_bwrap, toolchain
from .supervisor import supervise

COMPONENT_PREFIXES = ("backend-", "cli-", "frontend-")
GATE_ENV = {"CI": "1", "NO_COLOR": "1", "npm_config_update_notifier": "false", "npm_config_fund": "false",
            "PYTHONDONTWRITEBYTECODE": "1"}


@dataclass(frozen=True)
class GateContext:
    """Where and against what a gate runs."""

    repo_root: Path
    head_sha: str = ""
    base_sha: str = ""
    feature_id: str = ""
    task_id: str = ""


def gate_cwd(repo_root: Path, name: str) -> Path:
    """`backend-*`, `cli-*` and `frontend-*` gates run in that directory, others at the root."""
    if name.startswith(COMPONENT_PREFIXES):
        return repo_root / name.split("-", 1)[0]
    return repo_root


class SandboxedGateRunner:
    """Runs configured gates for the FeatureCoordinator (the `gate_runner` it calls)."""

    def __init__(self, config: ProjectConfig, log_root: Path, *, bwrap: str | None = None,
                 timeouts: Mapping[str, float] | None = None) -> None:
        self.config = config
        self.log_root = log_root
        self.bwrap = bwrap
        self.timeouts = dict(timeouts or {})

    def __call__(self, name: str, context: GateContext) -> GateResult:
        if name in self.config.unsafe_gates:
            return GateResult(name, EXIT_BLOCKED, self.config.gates.get(name, ()), status="blocked",
                              summary=self.config.unsafe_gates[name])
        if name in self.config.lint_gates:
            from .lint_baseline import run_lint_gate  # pylint: disable=import-outside-toplevel
            return run_lint_gate(self, name, self.config.lint_gates[name], context)
        return self.run_command(name, self.config.gates[name], context, cwd=gate_cwd(context.repo_root, name))

    # -- sandboxed execution -------------------------------------------------------

    def resolve(self, argv: Sequence[str], repo_root: Path) -> tuple[list[str], list[Bind], list[str]]:
        """(argv with an absolute program, read-only binds for the toolchain, extra PATH entries)."""
        program, rest = argv[0], list(argv[1:])
        binds: list[Bind] = []
        path_dirs: list[str] = []
        if program in ("python", "python3"):
            venv = repo_root / ".venv" / "bin" / "python"
            if venv.exists():
                return [str(venv), *rest], binds, path_dirs
            prefix = Path(sys.prefix)
            if not str(prefix).startswith("/usr"):
                binds.append(Bind(str(prefix), str(prefix), "ro"))  # the coordinator's own venv
            return [sys.executable, *rest], binds, path_dirs
        found = shutil.which(program)
        if not found:
            raise FileNotFoundError(f"gate tool {program!r} is not installed")
        if str(Path(found).resolve()).startswith("/usr/"):
            return [found, *rest], binds, path_dirs
        executable, prefix = toolchain(found)
        binds.append(Bind(str(prefix), str(prefix), "ro"))
        path_dirs.append(str(Path(executable).parent))  # e.g. node next to npm
        return [executable, *rest], binds, path_dirs

    def run_command(self, name: str, argv: Sequence[str], context: GateContext, *, cwd: Path,  # pylint: disable=too-many-arguments
                    extra: Sequence[Bind] = (), env: Mapping[str, str] | None = None,
                    timeout: float | None = None) -> GateResult:
        """Run one command in the gate sandbox and turn its outcome into a GateResult."""
        stamp = re.sub(r"[^0-9]", "", utc_now())[:17]
        run_dir = self.log_root / f"{name}.{stamp}.{os.urandom(2).hex()}"
        try:
            bwrap = check_bwrap(self.bwrap)
            resolved, binds, path_dirs = self.resolve(argv, context.repo_root)
        except (SandboxUnavailableError, FileNotFoundError) as exc:
            return GateResult(name, EXIT_BLOCKED, tuple(argv), status="blocked", summary=str(exc))
        builder = SandboxBuilder(context.repo_root, self.config, bwrap=bwrap)
        sandbox_env = dict(GATE_ENV)
        sandbox_env["PATH"] = ":".join([*path_dirs, "/usr/local/bin", "/usr/bin", "/bin"])
        sandbox_env.update(env or {})
        plan = builder.plan(run_dir=run_dir, role=None, env=sandbox_env,
                            extra=[*binds, *extra], cwd=cwd, hide=[self.log_root.parent])
        stdout, stderr = run_dir / "stdout.log", run_dir / "stderr.log"
        try:
            sup = supervise(plan.argv(resolved), cwd=context.repo_root, env={"PATH": os.environ.get("PATH", "")},
                            stdout_path=stdout, stderr_path=stderr,
                            timeout_seconds=timeout or self.timeouts.get(name)
                            or self.config.runtime.gate_timeout_seconds,
                            grace_seconds=self.config.runtime.terminate_grace_seconds)
        except OSError as exc:
            return GateResult(name, EXIT_ERROR, tuple(argv), status="error", summary=str(exc), log=str(run_dir))
        finally:
            plan.discard_shadow()
        tail = _tail(stdout, stderr)
        if sup.status == "timeout":
            return GateResult(name, EXIT_TIMEOUT, tuple(argv), status="timeout", duration_seconds=sup.duration_seconds,
                              log=str(run_dir), summary=f"killed after {sup.duration_seconds:.0f}s; {tail}")
        code = sup.exit_code if sup.exit_code is not None else EXIT_ERROR
        return GateResult(name, code, tuple(argv), status="passed" if code == 0 else "failed",
                          duration_seconds=sup.duration_seconds, log=str(run_dir), summary=tail)


def _tail(*paths: Path, lines: int = 6) -> str:
    text: list[str] = []
    for path in paths:
        if path.exists():
            text += [ln for ln in path.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
    return " | ".join(text[-lines:])[:800]
