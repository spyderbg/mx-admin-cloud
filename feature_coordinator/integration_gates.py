"""W4.7 (M3): the validation gates of an integration candidate — which gates, run how and where, recorded how.

Which gates (gate_plan) — from the EXISTING configuration only; there is no second gate registry:

  * every role that owns a path the candidate changes (base tree → candidate tree; additions, modifications,
    deletions, both sides of a rename): that role's gates, `github.intake.role_gates[role]` — the gates an approved
    task of the role must pass (W4.3.1). A role without gates there is a MISSING gate (`role:<name>`);
  * a changed path that no role owns (`feature_coordinator/**`, the repository root, an approval-required path without
    a role): the gates named by `github.integration.unowned_gates` (default `coordinator-tests`);
  * always: the feature integration gates (workflow.DEFAULT_INTEGRATION_GATES — `contract-check`, `docs-check`), the
    combined checks every feature integration runs.

Every name resolves through the gate registry (`gates`, `lint_gates`); a name that is not configured is MISSING and a
gate in `unsafe_gates` is not run. Both are BLOCKED — never PASS.

How (CandidateGateRunner): the existing SandboxedGateRunner, unchanged — bubblewrap, the checkout read-only, no network,
private HOME and /tmp, timeouts, logs — with the candidate WORKSPACE as the repository root, so each gate runs against
exactly the candidate content. A gate's timeout is `github.integration.gate_timeouts[gate]` when listed (default:
coordinator-tests 1800 s), else `runtime.gate_timeout_seconds`; it is part of the gate's fingerprint. The workspace's
`.git` reads objects through the candidate repository and the checkout's object directory; exactly these two are bound
read-only (gates on the checkout see its `.git` read-only today; further alternates are never followed). Every bound
checkout directory (`.venv`, `node_modules`) must be a real directory inside the checkout — never a symlink, which a
bind would follow on the host — and no bind may contain the coordinator state directory or HOME (independent review of
M3, finding 1). Lint gates are the baseline-aware lint gates (lint_baseline.run_lint_gate) comparing the candidate
commit with base_sha; one with no changed Python file is SKIPPED (nothing to lint), one whose diff misses a Python file
the candidate changes is BLOCKED.
Toolchain (trusted_python): `python` is the checkout's `.venv` interpreter when there is one, otherwise the
coordinator's own interpreter — never an interpreter inside the candidate workspace (a candidate-controlled
`.venv/bin/python` is never executed as a gate tool; Codex review of M3). A gate that runs a known Python tool
(`python -m pytest`, `python -m pylint`) first checks, in the same sandbox with the same interpreter, that the tool is
installed (PYTHON_TOOLS): a missing tool is BLOCKED, not FAIL (Codex review of M3, F4) — only the tool itself is
checked, so an import error of the code under test still FAILs. A Node gate (npm, npx, node, …) in a directory with
`package.json` gets the checkout's `node_modules` (read-only, through a symlink in the workspace) only when the
candidate's `package-lock.json` equals the checkout's byte for byte, its `package.json` declares the same dependencies
as the checkout's, and every non-optional package the lock file lists is installed in the checkout — otherwise the
installed dependencies do not match the candidate and the gate is BLOCKED (the coordinator never installs
dependencies; versions and integrity of installed packages are not verified).

Results (integration.GATE_STATUS_RESULT): passed → PASS, failed → FAIL, skipped → SKIPPED, and blocked, error,
timeout or missing → BLOCKED. A timeout is BLOCKED, not FAIL: it does not prove a defect of the source (raise the
timeout or abort); it never passes. A FAIL recorded for the candidate stands under any later gate definition
(standing): it is never run again and never dropped (integration.recorded_failures).
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from .config import ProjectConfig
from .errors import CoordinatorError, GitOperationError
from .gates import GateContext, SandboxedGateRunner, gate_cwd
from .integration import GATE_STATUS_RESULT, clean_text, evidence_digest
from .lint_baseline import PYLINT_ARGS, changed_python_files
from .model import EXIT_BLOCKED, GateResult, utc_now
from .sandbox import Bind, SandboxUnavailableError
from .workflow import DEFAULT_INTEGRATION_GATES

UNOWNED = "unowned"            # required_by: a changed path no role owns
INTEGRATION = "integration"    # required_by: the feature integration gates
MISSING_ROLE_PREFIX = "role:"
NODE_PROGRAMS = frozenset({"npm", "npx", "node", "pnpm", "yarn"})
MAX_LOCKFILE = 64 << 20
REUSABLE = ("PASS", "SKIPPED")   # reused for the same definition; a FAIL stands under any (standing); BLOCKED re-runs
PYTHON_PROGRAMS = ("python", "python3")
PYTHON_TOOLS = frozenset({"pytest", "pylint"})    # `python -m <tool>` gates whose tool is checked before they run
TOOL_PROBE = "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec(sys.argv[1]) else 3)"
TOOL_MISSING_EXIT = 3
TOOL_CHECK_TIMEOUT = 120
DEPENDENCY_KEYS = ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies", "overrides",
                   "bundleDependencies", "bundledDependencies", "workspaces")


@dataclass(frozen=True)
class PlannedGate:
    """One gate the candidate must pass, why (*required_by*), what it is (command | lint | unsafe | missing) and its
    fingerprint: evidence is reused only for the same fingerprint."""

    name: str
    required_by: tuple[str, ...]
    kind: str
    command: tuple[str, ...]
    detail: str
    fingerprint: str
    timeout: int = 0


def gate_timeout(name: str, config: ProjectConfig) -> int:
    """The effective timeout of gate *name* in seconds: `github.integration.gate_timeouts[name]`, else
    `runtime.gate_timeout_seconds` (Codex review of M3, F6)."""
    return int(config.github.integration.gate_timeouts.get(name, config.runtime.gate_timeout_seconds))


def gate_plan(paths: Iterable[Mapping[str, Any]], config: ProjectConfig) -> list[PlannedGate]:
    """The gates for a candidate whose classified changed paths are *paths* (see the module docstring), in the order
    of the gate registry (missing names last)."""
    wanted: dict[str, set[str]] = {}
    paths = list(paths)
    for role in sorted({entry["role"] for entry in paths if entry["role"]}):
        gates = config.github.intake.role_gates.get(role, ())
        for gate in gates or (f"{MISSING_ROLE_PREFIX}{role}",):
            wanted.setdefault(gate, set()).add(role)
    if any(entry["role"] is None for entry in paths):
        for gate in config.github.integration.unowned_gates:
            wanted.setdefault(gate, set()).add(UNOWNED)
    for gate in DEFAULT_INTEGRATION_GATES:
        wanted.setdefault(gate, set()).add(INTEGRATION)
    order = {name: index for index, name in enumerate(config.gate_names)}
    names = sorted(wanted, key=lambda name: (name not in order, order.get(name, 0), name))
    return [_planned(name, tuple(sorted(wanted[name])), config) for name in names]


def _planned(name: str, required_by: tuple[str, ...], config: ProjectConfig) -> PlannedGate:
    lint = None
    if name.startswith(MISSING_ROLE_PREFIX):
        role = name[len(MISSING_ROLE_PREFIX):]
        kind, command, detail = "missing", (), (f"no validation gate is configured for role {role} "
                                                "(github.intake.role_gates)")
    elif name in config.unsafe_gates:
        kind, command, detail = "unsafe", tuple(config.gates.get(name, ())), config.unsafe_gates[name]
    elif name in config.lint_gates:
        spec = config.lint_gates[name]
        lint = {"root": spec.root, "targets": list(spec.targets), "pythonpath": list(spec.pythonpath),
                "policy": spec.policy}
        kind, command, detail = "lint", ("python", *PYLINT_ARGS, *(f"{spec.root}/{t}" for t in spec.targets)), ""
    elif name in config.gates:
        kind, command, detail = "command", tuple(config.gates[name]), ""
    else:
        kind, command, detail = "missing", (), f"gate {name} is not configured (gates / lint_gates)"
    cwd = os.path.relpath(gate_cwd(Path("/r"), name), "/r")
    timeout = gate_timeout(name, config)
    payload = {"gate": name, "kind": kind, "command": list(command), "lint": lint, "cwd": cwd, "detail": detail,
               "timeout": timeout}
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
    return PlannedGate(name=name, required_by=required_by, kind=kind, command=command, detail=detail,
                       fingerprint=fingerprint, timeout=timeout)


def plan_view(plan: Sequence[PlannedGate]) -> dict[str, Any]:
    """The plan as recorded in a validation summary: its digest and each gate with its reasons."""
    digest = hashlib.sha256(json.dumps([[p.name, list(p.required_by), p.fingerprint] for p in plan],
                                       sort_keys=True).encode("utf-8")).hexdigest()
    return {"sha256": digest, "gates": [{"gate": p.name, "required_by": list(p.required_by)} for p in plan]}


def reusable(entry: Mapping[str, Any] | None, planned: PlannedGate) -> bool:
    """True when *entry* (the newest gate evidence of this gate for the candidate) is a PASS or SKIPPED of exactly this
    gate definition: then the gate is not run again (the candidate's content is immutable). BLOCKED (nothing learned
    about the content) runs again; a FAIL is decided by standing()."""
    return entry is not None and entry["fingerprint"] == planned.fingerprint and entry["result"] in REUSABLE


def standing(entries: Mapping[str, Mapping[str, Any]], failures: Mapping[str, Mapping[str, Any]],
             planned: PlannedGate) -> Mapping[str, Any] | None:
    """The recorded evidence that stands for *planned* — the gate is not run again — or None (it runs). *entries*: the
    newest gate evidence per gate for the candidate (integration.current_gate_evidence); *failures*: its recorded FAILs
    (integration.recorded_failures). A FAIL of this gate stands under ANY gate definition — a changed timeout, command
    or plan never retries it into a pass (Codex review of M3, F1); otherwise a PASS or SKIPPED of exactly this
    definition stands (reusable)."""
    if planned.name in failures:
        return failures[planned.name]
    entry = entries.get(planned.name)
    return entry if reusable(entry, planned) else None


# ---------------------------------------------------------------------------
# The toolchain of the workspace
# ---------------------------------------------------------------------------

@dataclass
class Toolchain:
    """What the gates of one workspace get from the operator's environment: read-only binds, per gate a description
    (recorded in its evidence) or the reason it cannot run here (BLOCKED)."""

    binds: list[Bind] = field(default_factory=list)
    info: dict[str, dict[str, str]] = field(default_factory=dict)
    problems: dict[str, str] = field(default_factory=dict)


def real_dir_inside(path: Path, root: Path) -> bool:
    """True when *path* is a real directory (not a symlink) that resolves inside *root*: only such a directory of the
    checkout is ever bound into the gate sandbox (a symlink planted in the checkout could otherwise map any host
    directory — HOME, the coordinator state — into it; a bind follows symlinks on the host, a path inside the sandbox
    does not)."""
    if not os.path.lexists(path) or os.path.islink(path) or not os.path.isdir(path):
        return False
    real, top = os.path.realpath(path), os.path.realpath(root)
    return real == top or real.startswith(top.rstrip("/") + "/")


def trusted_python(checkout: Path) -> tuple[str, list[Bind]]:
    """(interpreter, read-only binds) for a gate's `python`: the checkout's `.venv` interpreter when `.venv` is a real
    directory inside the checkout, else the coordinator's own interpreter — NEVER one inside the candidate workspace,
    whose `.venv/bin/python` is candidate content (Codex review of M3: the gate runner's fallback looked there when the
    checkout had no `.venv`). SandboxUnavailableError (the gate is BLOCKED) when there is no trusted interpreter."""
    venv = Path(checkout) / ".venv"
    if real_dir_inside(venv, Path(checkout)) and (venv / "bin" / "python").exists():
        return str(venv / "bin" / "python"), [Bind(os.path.realpath(venv), str(venv), "ro")]
    if not sys.executable or not os.path.isabs(sys.executable) or not os.access(sys.executable, os.X_OK):
        raise SandboxUnavailableError("no trusted Python interpreter for the gate (no .venv in the checkout and the "
                                      "coordinator's interpreter is unknown)")
    prefix = Path(sys.prefix)
    return sys.executable, ([] if str(prefix).startswith("/usr") else [Bind(str(prefix), str(prefix), "ro")])


def python_tool(argv: Sequence[str]) -> str | None:
    """The known Python tool a gate command runs (`python -m pytest …` → "pytest"), or None."""
    if len(argv) >= 3 and argv[0] in PYTHON_PROGRAMS and argv[1] == "-m" and argv[2] in PYTHON_TOOLS:
        return argv[2]
    return None


def gate_object_directories(candidate_objects: Path, checkout_objects: Path) -> list[Path]:
    """Exactly the two object directories the workspace's `.git` reads: the candidate repository's (its alternate is
    the checkout's, integration_candidate.repository_problem) and the checkout's. A checkout whose object directory
    names further alternates is refused for gates (GitOperationError): its alternates are not followed into the
    sandbox, where an added line could expose any host directory."""
    alternates = Path(checkout_objects) / "info" / "alternates"
    if os.path.lexists(alternates) and (os.path.islink(alternates)
                                        or alternates.read_text(encoding="utf-8", errors="replace").strip()):
        raise GitOperationError(f"the checkout's object directory {checkout_objects} names alternates; they are never "
                                "bound into the gate sandbox, so the candidate workspace cannot be read there")
    return [Path(os.path.realpath(candidate_objects)), Path(os.path.realpath(checkout_objects))]


def prepare_toolchain(plan: Sequence[PlannedGate], *, workspace: Path, checkout: Path,
                      object_dirs: Sequence[Path]) -> Toolchain:
    """Bind the object directories the workspace's `.git` reads (gate_object_directories), record the trusted
    interpreter of every Python gate and wire `node_modules` for the Node gates (module docstring). Writes only into
    the workspace, never into the checkout."""
    toolchain = Toolchain(binds=[Bind(str(path), str(path), "ro") for path in object_dirs])
    wired: dict[str, tuple[str | None, dict[str, str]]] = {}
    for planned in plan:
        if planned.kind in ("command", "lint") and planned.command and planned.command[0] in PYTHON_PROGRAMS:
            try:   # recorded with the gate's evidence: which interpreter ran it
                toolchain.info.setdefault(planned.name, {})["python"] = trusted_python(checkout)[0]
            except SandboxUnavailableError as exc:
                toolchain.problems[planned.name] = str(exc)
        if planned.kind != "command" or not planned.command or Path(planned.command[0]).name not in NODE_PROGRAMS:
            continue
        rel = os.path.relpath(gate_cwd(workspace, planned.name), workspace)
        if rel not in wired:
            wired[rel] = _wire_node_modules(rel, workspace=workspace, checkout=checkout, toolchain=toolchain)
        problem, info = wired[rel]
        if problem:
            toolchain.problems[planned.name] = problem
        toolchain.info.setdefault(planned.name, {}).update(info)
    return toolchain


def _read_regular(path: Path) -> bytes | None:
    """A regular file's bytes, never through a symlink, bounded (None: absent, a symlink or too large)."""
    try:
        handle = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:
        return None
    with os.fdopen(handle, "rb") as source:
        data = source.read(MAX_LOCKFILE + 1)
    return data if len(data) <= MAX_LOCKFILE else None


def _wire_node_modules(rel: str, *, workspace: Path, checkout: Path,  # pylint: disable=too-many-return-statements
                       toolchain: Toolchain) -> tuple[str | None, dict[str, str]]:
    shown = "" if rel == "." else f"{rel}/"
    target_dir, source_dir = workspace / rel, checkout / rel
    if not (target_dir / "package.json").is_file():
        return None, {}                     # not a Node project directory: the gate runs as configured
    link = target_dir / "node_modules"
    if os.path.lexists(link):
        return f"the candidate itself contains {shown}node_modules; it is never mixed with installed dependencies", {}
    source = source_dir / "node_modules"
    if os.path.islink(source) or (os.path.lexists(source) and not real_dir_inside(source, checkout)):
        return (f"{shown}node_modules of the checkout is a symlink or resolves outside the checkout: it is never bound "
                "into the gate sandbox"), {}
    if not real_dir_inside(source, checkout):
        return (f"{shown}node_modules is not installed in the checkout {checkout}: the gate cannot run (the "
                "coordinator never installs dependencies)"), {}
    lock = _read_regular(target_dir / "package-lock.json")
    if lock is None:
        return f"the candidate has no {shown}package-lock.json: its dependencies cannot be matched", {}
    if _read_regular(source_dir / "package-lock.json") != lock:
        return (f"the candidate's {shown}package-lock.json differs from the checkout's, so the checkout's installed "
                f"{shown}node_modules does not match the candidate's dependencies (the coordinator never installs "
                "dependencies; validate them in a prepared environment)"), {}
    problem = _dependency_problem(shown, target_dir, source_dir, lock)
    if problem:
        return problem, {}
    real = Path(os.path.realpath(source))
    if real == workspace or workspace in real.parents:
        return f"{shown}node_modules of the checkout resolves into the workspace", {}
    os.symlink(real, link)
    # Git sees the link as an untracked FILE (`/node_modules/` in a .gitignore matches directories only): exclude it in
    # the workspace's own `.git`, so a gate's `git status` still sees exactly the candidate.
    exclude = workspace / ".git" / "info" / "exclude"
    exclude.parent.mkdir(exist_ok=True)
    with exclude.open("a", encoding="utf-8") as out:
        out.write(f"/{shown}node_modules\n")
    toolchain.binds.append(Bind(str(real), str(real), "ro"))
    return None, {"node_modules": str(real), "package_lock_sha256": hashlib.sha256(lock).hexdigest()}


def _declared_dependencies(path: Path) -> dict[str, Any] | None:
    """The dependency declarations of a package.json (DEPENDENCY_KEYS), or None when it cannot be read as JSON."""
    data = _read_regular(path)
    try:
        parsed = json.loads(data) if data is not None else None
    except ValueError:
        return None
    return {key: parsed.get(key) for key in DEPENDENCY_KEYS} if isinstance(parsed, dict) else None


def _dependency_problem(shown: str, target_dir: Path, source_dir: Path, lock: bytes) -> str | None:
    """Minimal checks that the checkout's installed node_modules can stand for the candidate's dependencies (Codex
    review of M3): the candidate's package.json declares exactly the checkout's dependencies (an identical lock file
    with other declarations is out of sync — e.g. a removed dependency still installed would pass), and every
    non-optional package the lock file lists is present in the checkout's node_modules (lockfileVersion 2/3
    `packages`). Versions and integrity of installed packages are not verified (documented limitation)."""
    declared = _declared_dependencies(target_dir / "package.json")
    if declared is None or declared != _declared_dependencies(source_dir / "package.json"):
        return (f"the candidate's {shown}package.json declares other dependencies than the checkout's (or cannot be "
                f"read), although {shown}package-lock.json is the same: the installed {shown}node_modules cannot stand "
                "for the candidate's dependencies")
    try:
        packages = json.loads(lock).get("packages")
    except (ValueError, AttributeError):
        return f"the candidate's {shown}package-lock.json cannot be read as JSON"
    if not isinstance(packages, dict):
        return None              # lockfileVersion 1: no package list to check (documented limitation)
    missing = sorted(key for key, meta in packages.items()
                     if isinstance(key, str) and key.startswith("node_modules/") and isinstance(meta, dict)
                     and not (meta.get("optional") or meta.get("devOptional") or meta.get("peer"))
                     and ".." not in key.split("/") and not os.path.lexists(source_dir / key))
    if missing:
        return (f"the checkout's {shown}node_modules lacks {len(missing)} package(s) its package-lock.json requires "
                f"({', '.join(missing[:5])}): install them in the checkout (the coordinator never installs "
                "dependencies)")
    return None


class CandidateGateRunner(SandboxedGateRunner):
    """The gate runner for a candidate workspace (module docstring): SandboxedGateRunner's sandbox, timeouts and logs,
    plus read-only *binds* on every command, `python` resolved by trusted_python (never the candidate's interpreter),
    and a check that a known Python gate tool is installed before its gate runs (missing: BLOCKED). *timeouts*: per-gate
    timeouts (github.integration.gate_timeouts); other gates use runtime.gate_timeout_seconds."""

    def __init__(self, config: ProjectConfig, log_root: Path, *, checkout: Path, binds: Sequence[Bind],  # pylint: disable=too-many-arguments
                 state_root: Path, bwrap: str | None = None, timeouts: Mapping[str, float] | None = None) -> None:
        super().__init__(config, log_root, bwrap=bwrap, timeouts=timeouts)
        self.checkout = Path(checkout)
        self.binds = list(binds)
        # never mounted, not even below a bind: the coordinator state (records, claims, locks) and the user's home
        self.forbidden = [Path(os.path.realpath(state_root)), Path(os.path.realpath(os.path.expanduser("~")))]
        self.tools: dict[tuple[str, str], str | None] = {}    # (program, tool) → why it cannot run, or None

    def bind_problem(self, bind: Bind) -> str | None:
        """Why *bind* must not be mounted (None: it may): its source would contain the state directory or HOME."""
        source = Path(os.path.realpath(bind.source))
        for path in self.forbidden:
            if path == source or source in path.parents:
                return f"refusing to bind {source} into the gate sandbox: it contains {path}"
        return None

    def resolve(self, argv: Sequence[str], repo_root: Path) -> tuple[list[str], list[Bind], list[str]]:
        if argv and argv[0] in PYTHON_PROGRAMS:   # never SandboxedGateRunner's fallback <repo_root>/.venv: candidate
            program, binds = trusted_python(self.checkout)
            resolved: tuple[list[str], list[Bind], list[str]] = ([program, *argv[1:]], binds, [])
        else:
            resolved = super().resolve(argv, repo_root)
        problem = next((p for b in resolved[1] if (p := self.bind_problem(b))), None)
        if problem:
            raise SandboxUnavailableError(problem)   # reported as a blocked gate by the runner
        return resolved

    def run_command(self, name: str, argv: Sequence[str], context: GateContext, *, cwd: Path,  # pylint: disable=too-many-arguments
                    extra: Sequence[Bind] = (), env: Mapping[str, str] | None = None,
                    timeout: float | None = None) -> GateResult:
        binds = [*self.binds, *extra]
        problem = next((p for b in binds if (p := self.bind_problem(b))), None) or self.tool_problem(name, argv,
                                                                                                    context, binds)
        if problem:
            return GateResult(name, EXIT_BLOCKED, tuple(argv), status="blocked", summary=problem)
        return super().run_command(name, argv, context, cwd=cwd, extra=binds, env=env, timeout=timeout)

    def tool_problem(self, name: str, argv: Sequence[str], context: GateContext, binds: Sequence[Bind]) -> str | None:
        """Why the known Python tool *argv* runs (python_tool) is not usable, or None (also for other commands): it is
        looked up — not imported — by the same interpreter in the same sandbox, isolated (`-I`: no current directory,
        PYTHONPATH or user site), so only the installed tool counts, never candidate code (Codex review of M3, F4).
        Checked once per runner and tool."""
        tool = python_tool(argv)
        if tool is None:
            return None
        key = (argv[0], tool)
        if key not in self.tools:
            probe = super().run_command(f"{name}.tool-check", [argv[0], "-I", "-c", TOOL_PROBE, tool], context,
                                        cwd=Path(context.repo_root), extra=binds, timeout=TOOL_CHECK_TIMEOUT)
            if probe.status == "passed":
                self.tools[key] = None
            elif probe.status == "failed" and probe.exit_code == TOOL_MISSING_EXIT:
                self.tools[key] = (f"the gate tool {tool} is not installed for the gate's Python interpreter (the "
                                   "checkout's .venv, else the coordinator's): the gate cannot run — missing tooling "
                                   "is BLOCKED, not a validation failure; install it and validate again")
            else:
                self.tools[key] = (f"the gate tool {tool} could not be checked ({probe.status}, exit code "
                                   f"{probe.exit_code}): {clean_text(probe.summary, 300)}")
        return self.tools[key]


# ---------------------------------------------------------------------------
# Running one gate and recording it
# ---------------------------------------------------------------------------

@dataclass
class GateOutcome:  # pylint: disable=too-many-instance-attributes
    """What one gate did: the runner's status, or `skipped` / `missing` decided before running."""

    status: str
    exit_code: int | None
    command: tuple[str, ...]
    duration: float
    started_at: str
    summary: str = ""
    log: str = ""
    toolchain: dict[str, str] = field(default_factory=dict)


def run_gate(planned: PlannedGate, *, runner: Callable[[str, GateContext], GateResult], context: GateContext,  # pylint: disable=too-many-return-statements,too-many-arguments
             config: ProjectConfig, toolchain: Toolchain | None, unavailable: str = "",
             paths: Iterable[Mapping[str, Any]] = ()) -> GateOutcome:
    """Run *planned* against the workspace of *context* (or decide without running: missing, unsafe, toolchain or
    workspace *unavailable* → blocked; a lint gate without changed Python files → skipped). *paths*: the candidate's
    classified changed paths — a lint gate whose diff (added, modified, renamed only) would not list a Python file the
    candidate adds or changes (a type change, a copy) is BLOCKED, never SKIPPED or passed."""
    started, clock = utc_now(), time.monotonic()
    info = dict((toolchain.info.get(planned.name) if toolchain else None) or {})

    def decided(status: str, summary: str, exit_code: int | None = None) -> GateOutcome:
        return GateOutcome(status=status, exit_code=exit_code, command=planned.command, duration=0.0,
                           started_at=started, summary=summary, toolchain=info)

    if planned.kind == "missing":
        return decided("missing", planned.detail)
    if planned.kind == "unsafe":
        return decided("blocked", f"listed in unsafe_gates (never run against a real tree): {planned.detail}")
    if unavailable:
        return decided("blocked", unavailable)
    if toolchain is not None and planned.name in toolchain.problems:
        return decided("blocked", toolchain.problems[planned.name])
    try:
        if planned.kind == "lint":
            lint = config.lint_gates[planned.name]
            files = changed_python_files(Path(context.repo_root), context.base_sha, context.head_sha, lint)
            unlisted = sorted(lint_expected(paths, lint.root, lint.targets) - set(files))
            if unlisted:
                return decided("blocked", f"the lint gate's diff does not list {unlisted[:5]} although the candidate "
                                          "changes them (a type change or a copy): not reported as passed")
            if not files:
                return decided("skipped", f"no Python file changed under {lint.root}/{{{','.join(lint.targets)}}}; "
                                          "nothing to lint")
        result = runner(planned.name, context)
    except (CoordinatorError, OSError, subprocess.SubprocessError, ValueError) as exc:
        return GateOutcome(status="error", exit_code=None, command=planned.command,
                           duration=time.monotonic() - clock, started_at=started,
                           summary=f"the gate could not run: {clean_text(exc, 400)}", toolchain=info)
    duration = result.duration_seconds or (time.monotonic() - clock)
    return GateOutcome(status=result.status, exit_code=result.exit_code, command=tuple(result.command or
                                                                                         planned.command),
                       duration=duration, started_at=started, summary=result.summary, log=result.log, toolchain=info)


def lint_expected(paths: Iterable[Mapping[str, Any]], root: str, targets: Iterable[str]) -> set[str]:
    """The Python files below a lint gate's targets that the candidate adds or changes (still present in it)."""
    specs = [f"{root}/{target}" for target in targets]
    return {entry["path"] for entry in paths
            if entry["path"].endswith(".py") and "deleted" not in entry["changes"]
            and any(entry["path"] == spec or entry["path"].startswith(f"{spec}/") for spec in specs)}


def log_digest(log: str, name: str) -> dict[str, Any]:
    """{sha256, bytes} of one log file of a gate run ({"", 0} when there is none)."""
    if not log:
        return {"sha256": "", "bytes": 0}
    path = Path(log) / name
    try:
        data = path.read_bytes()
    except OSError:
        return {"sha256": "", "bytes": 0}
    return {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def gate_entry(candidate: Mapping[str, Any], planned: PlannedGate, outcome: GateOutcome) -> dict[str, Any]:
    """A gate evidence entry WITHOUT its id (given when it is appended, under the coordinator lock); its digest does
    not cover the id or the time."""
    log = outcome.log if len(outcome.log) <= 1000 else ""
    entry = {"kind": "gate", "id": "", "at": utc_now(), "candidate": candidate["evidence"],
             "head_sha": candidate["head_sha"], "tree_sha": candidate["tree_sha"], "gate": planned.name,
             "required_by": list(planned.required_by), "fingerprint": planned.fingerprint,
             "command": [clean_text(arg, 1000) for arg in outcome.command][:100],
             "result": GATE_STATUS_RESULT[outcome.status], "status": outcome.status, "exit_code": outcome.exit_code,
             "duration_seconds": round(max(outcome.duration, 0.0), 3), "started_at": outcome.started_at,
             "summary": clean_text(outcome.summary, 1000), "log": log, "stdout": log_digest(log, "stdout.log"),
             "stderr": log_digest(log, "stderr.log"),
             "toolchain": {clean_text(k, 100): clean_text(v, 1000) for k, v in outcome.toolchain.items()}}
    if entry["result"] == "PASS" and entry["exit_code"] != 0:   # a runner that says passed must have exited 0
        entry.update(result="BLOCKED", status="error", summary=clean_text(f"passed with exit code "
                                                                          f"{entry['exit_code']}: {entry['summary']}"))
    entry["sha256"] = evidence_digest(entry)
    return entry


def same_blocked(entry: Mapping[str, Any] | None, new: Mapping[str, Any]) -> bool:
    """True when *new* repeats the newest entry's BLOCKED outcome — the same gate definition with the same status — so
    it is not appended again: a blocked validation repeated many times (a timeout's log tail differs each time) must
    not fill the evidence list (independent review, finding 6)."""
    return (entry is not None and new["result"] == "BLOCKED" and entry["result"] == "BLOCKED"
            and entry["fingerprint"] == new["fingerprint"] and entry["status"] == new["status"])
