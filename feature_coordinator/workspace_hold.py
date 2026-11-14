"""Security S1 — workspace baseline, workspace hold and the launch admission protocol.

Three concepts that are deliberately NOT the same thing:

  SECURITY POLICY (paths.py, fsguard.Classifier, sandbox.py) — which paths are protected or secret, hence
      read-only, masked or denied. Fixed by the configuration. Nothing in this module can change it: accepting a
      baseline never makes a path writable, readable or unmasked (sandbox masks and native deny lists are computed
      from the policy and the file system only — never from this module's state, never from Git's index).
  WORKSPACE BASELINE (`baseline` below) — a record of file-system STATE used only to DETECT CHANGE: the sensitive
      entries Git does not explain, as an operator or an accepted run last left them.
  WORKSPACE HOLD — a persistent block on NEW agent executions in one checkout until an operator resolves it.

One record per checkout holds all coordination state and is always written atomically, as a whole, under the
Coordinator's existing process-shared lock (`coordinator.lock`, StateStore.transaction — a flock the kernel
releases when its holder dies):

    flags/workspace-guard.json: { <checkout path>: {
        generation   bumped on EVERY change of the hold (a new entry, a resolution); never reset
        launches     bumped on every admitted execution; never reset
        hold         None | {id, since, entries[]}
        baseline     None | {entries: path → stamp, revision, updated_at, how}
        executions   {id → {task, role, runner, run_dir, worker, agent, admitted_at}}   runs in flight } }

ADMISSION — the single authoritative decision (launch_gate). An agent process starts only INSIDE the gate: under
the Coordinator lock the record is reloaded; a hold refuses; the caller's FRESH inventory is compared with the
baseline (an unexplained new or changed sensitive entry puts the checkout on hold and refuses); the execution is
registered; the caller starts the agent process; its identity is recorded; then the lock is released. Publishing a
hold takes the same lock, so every hold is totally ordered against every launch: an agent either started before the
hold existed (it is then a registered execution, and the hold says so) or it does not start. Checks made earlier
(guard_before_run before the claim, the claim itself) only fail fast; a decision made before a hold existed is
never reused, because the gate decides again. Multi-step tasks pass the gate once per agent invocation.

EXECUTIONS. A registered execution ends in finish_execution (its residue, if any, becomes a hold entry in the same
write that removes it) or abandon_execution. If its Worker dies, the record survives the crash: it counts as in
flight while its Worker or agent process is alive, and is dropped (an event) once both are gone — whatever it left
in the checkout is then found by the next admission's inventory check, never trusted. Known limit: a Worker that
dies in the instant between starting the agent and recording its identity leaves an execution without an agent
identity, which stops counting as in flight although the agent may live (feature-workflow.md §25.7).

RESOLUTION is bound to a generation. `resolve` notes the hold's id and generation, the launch counter and the
baseline revision; inspects the checkout WITHOUT the lock; then, under the lock, reloads the record and applies
only if all four are unchanged and no execution is in flight — otherwise it changes nothing and reports `stale`.
A hold another process added or replaced meanwhile therefore survives. `--accept` updates the BASELINE only and
never touches the security policy. Two things are never acceptable, whatever the reason given: a sensitive entry
that a RUN CREATED (forbidden residue: the operator removes it — it does not stay in the checkout for the next
agent to read) and a sensitive path staged in Git's index (unstage it first). Nothing here ever deletes, resets,
cleans or stashes a file. Known limits: Git's object store is outside this module — a blob stays there after it is
unstaged, a staged path whose working file is absent is not seen by admission, and a run-created entry that Git
later explains (committed, clean) no longer blocks a resolution (feature-workflow.md §25.7).

FIRST INVENTORY. A checkout whose first inventory finds unexplained sensitive entries is put on hold: they need an
operator's review (`workspace status`, then `workspace resolve --accept --reason …`). Only an inventory with nothing
to trust is recorded silently.

QUIESCENCE BOUNDARY. Worker-controlled writers are the agent executions registered here (and the Git coordinator,
which never writes sensitive working-tree paths). While a hold exists none can start; resolution requires that
none is in flight. With one agent at a time (`worker.max_parallel: 1`, the service default) admission, inventory,
launch and final verification of a run see no other Worker-controlled writer at all. With parallel roles another
role's agent may write while a run is admitted; what it leaves is judged by its own final verification. A host
process that does not use the Coordinator's locks (an editor, a script, an operator's shell) can change the
checkout at any moment after any scan: this cannot be prevented from here. It is DETECTED afterwards — against the
baseline at the next admission, and by the run's before/after inventories — and never trusted silently. Detection
is after the fact: it does not undo a read that already happened.
"""

from __future__ import annotations

import secrets
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence

from .config import ProjectConfig
from .fsguard import Classifier, Inventory, InventoryError, InventoryUnstableError, scan
from .gitsafe import SAFE_CONFIG_ARGS, hardened_env
from .locks import ProcessIdentity
from .model import utc_now
from .paths import PathPolicy
from .store import StateStore
from .workspace import WorkspaceError

GUARD_FLAG = "workspace-guard"
MAX_LISTED = 50          # paths recorded per hold entry (the run directory has the complete evidence)
MAX_CREATED = 2000       # sensitive entries a run created, recorded per hold entry (never acceptable)
VERIFY_ATTEMPTS = 8      # walks of a post-run inventory before the run counts as unverifiable
_UNVERIFIABLE = ("Git state:", "unverified workspace:", "Git control")


class WorkspaceGuardError(WorkspaceError):
    """No agent may start in this checkout now."""


class WorkspaceHoldError(WorkspaceGuardError):
    """The checkout is on hold: no agent task may start until an operator resolves it."""


class WorkspaceUnstableError(WorkspaceGuardError):
    """The checkout kept changing while it was inventoried: nothing was decided, nothing was recorded (retry)."""


def checkout_key(root: Path) -> str:
    """The key a checkout's record is stored under (one state directory may serve several checkouts)."""
    return str(Path(root).resolve())


# ---------------------------------------------------------------------------
# The record (always read and written under the Coordinator lock)
# ---------------------------------------------------------------------------

def _load(store: StateStore, root: Path) -> dict[str, Any]:
    stored = (store.get_flag(GUARD_FLAG) or {}).get(checkout_key(root)) or {}
    return {"generation": int(stored.get("generation", 0)), "launches": int(stored.get("launches", 0)),
            "hold": stored.get("hold"), "baseline": stored.get("baseline"),
            "executions": dict(stored.get("executions") or {})}


def _save(store: StateStore, root: Path, record: Mapping[str, Any]) -> None:
    flag = dict(store.get_flag(GUARD_FLAG) or {})
    flag[checkout_key(root)] = dict(record)
    store.set_flag(GUARD_FLAG, flag)   # one atomic write: hold, baseline and executions never disagree on disk


def _add_entry(store: StateStore, root: Path, record: dict[str, Any], *, reason: str, paths: Sequence[str] = (),
               evidence: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Add one finding to *record*'s hold (creating it) and bump the generation. The caller saves the record."""
    now = utc_now()
    record["generation"] += 1
    hold = record["hold"] or {"id": secrets.token_hex(4), "since": now, "entries": []}
    entry = {"at": now, "generation": record["generation"], "reason": reason, "paths": list(paths)[:MAX_LISTED],
             "path_count": len(paths), "in_flight": sorted(record["executions"]), **dict(evidence or {})}
    hold["entries"] = [*hold["entries"], entry]
    record["hold"] = hold
    store.append_event("workspace-hold", checkout=checkout_key(root), hold=hold_ref(_view(record)), reason=reason,
                       paths=entry["paths"],
                       **{k: v for k, v in entry.items() if k in ("task", "role", "runner", "run_dir")})
    return hold


def _view(record: Mapping[str, Any]) -> dict[str, Any] | None:
    hold = record.get("hold")
    return None if hold is None else {**hold, "generation": record["generation"]}


def hold_ref(hold: Mapping[str, Any] | None, generation: int = 0) -> str:
    """`<hold id>.<generation>`: what a resolution must name (`none.<generation>` when nothing is held)."""
    return f"{hold['id']}.{hold['generation']}" if hold else f"none.{generation}"


# ---------------------------------------------------------------------------
# What Git explains (baseline only — never used for masking or permissions)
# ---------------------------------------------------------------------------

def _git_lines(root: Path, *args: str) -> list[str] | None:
    proc = subprocess.run(["git", "-C", str(root), *SAFE_CONFIG_ARGS, *args], capture_output=True, text=True,
                          env=hardened_env(), check=False)
    return [t for t in proc.stdout.split("\0") if t] if proc.returncode == 0 else None


def unexplained(root: Path, inventory: Inventory) -> dict[str, str]:
    """path → stamp of the sensitive entries Git does not explain: everything except tracked entries equal to BOTH
    the index and HEAD (a staged change is not history) and directories holding tracked files. If Git cannot
    answer, nothing is explained (fail closed). For change detection only."""
    tracked = _git_lines(root, "ls-files", "-z")
    status = _git_lines(root, "status", "--porcelain=v1", "-z", "--untracked-files=no", "--ignored=no")
    if tracked is None or status is None:
        return {p: e.stamp for p, e in inventory.entries.items()}
    modified: set[str] = set()
    tokens, i = status, 0
    while i < len(tokens):
        modified.add(tokens[i][3:])
        i += 2 if tokens[i][:1] in ("R", "C") else 1
    clean = set(tracked) - modified
    holding = {"/".join(p.split("/")[:n]) for p in tracked for n in range(1, p.count("/") + 1)}
    return {p: e.stamp for p, e in inventory.entries.items()
            if not (p in clean or (e.kind == "dir" and p in holding))}


def contamination(baseline: Mapping[str, str], current: Mapping[str, str]) -> list[str]:
    """Unexplained sensitive entries that are new or changed relative to the baseline."""
    return sorted(p for p, stamp in current.items() if baseline.get(p) != stamp)


def staged_sensitive(root: Path, config: ProjectConfig) -> list[str]:
    """Sensitive paths whose INDEX entry differs from HEAD (staged, not committed). `git add` wrote their content
    into the object store, where no working-tree mask hides it: an operator must unstage them."""
    staged = _git_lines(root, "diff", "--cached", "--name-only", "-z", "--no-renames")
    classifier = Classifier(config)
    return sorted(p for p in (staged or []) if any(classifier.of(p)[:2]))


# ---------------------------------------------------------------------------
# Reading the state
# ---------------------------------------------------------------------------

def hold_of(store: StateStore, root: Path) -> dict[str, Any] | None:
    """The checkout's hold ({id, since, entries, generation}), or None when it is not held."""
    return _view(_load(store, root))


def trusted_of(store: StateStore, root: Path) -> dict[str, Any] | None:
    """The checkout's baseline record ({entries: path → stamp, revision, …}), or None before its first inventory."""
    return _load(store, root)["baseline"]


def describe(hold: Mapping[str, Any]) -> str:
    """One line for operators: why the checkout is held and how to resolve it."""
    entries = hold.get("entries", [])
    first = entries[0] if entries else {}
    where = f" (task {first.get('task')}, run {first.get('run_dir') or '-'})" if first.get("task") else ""
    more = f" (+{len(entries) - 1} more)" if len(entries) > 1 else ""
    return (f"the checkout is on hold {hold_ref(hold)} since {hold.get('since')}: "
            f"{first.get('reason', 'unauthorized changes')}{where}{more} — inspect `python -m feature_coordinator "
            f"workspace status`, remediate, then `python -m feature_coordinator workspace resolve --apply --hold "
            f"{hold_ref(hold)}` (nothing is removed automatically)")


def _alive(execution: Mapping[str, Any]) -> bool:
    for key in ("worker", "agent"):
        identity = execution.get(key)
        if identity and ProcessIdentity.from_dict(identity).is_alive():
            return True
    return False


def _reap(store: StateStore, root: Path, record: dict[str, Any]) -> bool:
    """Drop executions whose Worker AND agent processes are gone (they ended without a final verification: an
    event; their residue is the next inventory check's to find). True when the record changed."""
    gone = [key for key, execution in record["executions"].items() if not _alive(execution)]
    for key in gone:
        execution = record["executions"].pop(key)
        store.append_event("workspace-execution-abandoned", checkout=checkout_key(root), execution=key,
                           task=execution.get("task"), role=execution.get("role"))
    return bool(gone)


def executions_of(store: StateStore, root: Path) -> dict[str, dict[str, Any]]:
    """The executions in flight in this checkout (their Worker or agent process is alive)."""
    return {key: e for key, e in _load(store, root)["executions"].items() if _alive(e)}


# ---------------------------------------------------------------------------
# Publishing a hold
# ---------------------------------------------------------------------------

def put_on_hold(store: StateStore, root: Path, *, reason: str, paths: Sequence[str] = (),  # pylint: disable=too-many-arguments
                evidence: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Hold the checkout (a finding is added when it is already held); the generation is bumped either way.
    Ordered against every launch by the Coordinator lock. Returns the hold."""
    with store.transaction():
        record = _load(store, root)
        _add_entry(store, root, record, reason=reason, paths=paths, evidence=evidence)
        _save(store, root, record)
        return _view(record) or {}


def require_no_hold(store: StateStore, root: Path) -> None:
    """Raise WorkspaceHoldError when the checkout is held (a fresh read of the persistent record)."""
    hold = hold_of(store, root)
    if hold is not None:
        raise WorkspaceHoldError(describe(hold))


def hold_problems(store: StateStore, root: Path, config: ProjectConfig | None = None) -> list[str]:
    """[why no agent may start now] or [] — with *config*, after the inventory check (guard_before_run, which may
    put the checkout on hold); without, the hold record only (no scan). For the Worker service's cycle."""
    try:
        if config is None:
            require_no_hold(store, root)
        else:
            guard_before_run(store, root, config)
    except WorkspaceGuardError as exc:
        return [str(exc)]
    return []


# ---------------------------------------------------------------------------
# Admission
# ---------------------------------------------------------------------------

def _current(store: StateStore, root: Path, config: ProjectConfig, inventory: Inventory | None) -> dict[str, str]:
    """The checkout's unexplained sensitive entries NOW (*inventory*: the caller's fresh verified scan; none: one is
    taken). Never returns a result for a scan that could not be completed."""
    try:
        return unexplained(root, inventory if inventory is not None else scan(root, config))
    except InventoryUnstableError as exc:   # transient: nothing decided, nothing recorded
        raise WorkspaceUnstableError(f"{exc}; no agent is started (temporarily unavailable — it is tried again)") \
            from exc
    except InventoryError as exc:           # limits, permissions: an operator has to look
        hold = put_on_hold(store, root, reason=f"the workspace inventory could not be completed: {exc}")
        raise WorkspaceHoldError(describe(hold)) from exc


def _decide(store: StateStore, root: Path, record: dict[str, Any], current: Mapping[str, str]) -> str:
    """The admission decision on a freshly loaded *record* (under the lock): '' = admissible, else why not. May add
    a hold entry or record the first baseline in *record* (the caller saves it)."""
    if record["hold"] is not None:
        return describe(_view(record) or {})
    baseline = record["baseline"]
    if baseline is None:
        if current:
            listed = sorted(current)
            _add_entry(store, root, record, paths=listed, evidence={"source": "first inventory"}, reason=(
                f"first inventory of this checkout: {len(listed)} sensitive entries are not explained by Git and "
                f"need an operator's review before they are trusted: {', '.join(listed[:10])}"
                + (f" and {len(listed) - 10} more" if len(listed) > 10 else "")))
            return describe(_view(record) or {})
        record["baseline"] = {"entries": {}, "revision": 1, "updated_at": utc_now(), "how": "first inventory (empty)"}
        store.append_event("workspace-baseline", checkout=checkout_key(root), entries=0, how="first inventory (empty)")
        return ""
    new = contamination(baseline["entries"], current)
    if new:
        _add_entry(store, root, record, paths=new, reason=(
            f"sensitive entries appeared or changed outside an accepted run: {', '.join(new[:10])}"
            + (f" and {len(new) - 10} more" if len(new) > 10 else "")),
            evidence={"source": "inventory", "stamps": {p: current[p] for p in new[:MAX_LISTED]}})
        return describe(_view(record) or {})
    return ""


def guard_before_run(store: StateStore, root: Path, config: ProjectConfig,
                     inventory: Inventory | None = None) -> None:
    """The admission checks WITHOUT a launch: no hold, and a fresh inventory that matches the baseline (otherwise
    the checkout is put on hold). Fails fast before a claim; the authoritative decision is launch_gate's."""
    current = _current(store, root, config, inventory)
    with store.transaction():
        record = _load(store, root)
        before = (record["generation"], record["baseline"])
        problem = _decide(store, root, record, current)
        if (record["generation"], record["baseline"]) != before:
            _save(store, root, record)
    if problem:
        raise WorkspaceHoldError(problem)


@contextmanager
def launch_gate(store: StateStore, root: Path, config: ProjectConfig, *, execution: Mapping[str, Any],
                inventory: Inventory | None = None) -> Iterator[Callable[[int], None]]:
    """THE admission decision for one agent execution (see the module docstring). Enter it, start the agent
    process INSIDE it, and call the yielded function with the process ID. Raises WorkspaceGuardError — and nothing
    may be started — when the checkout is held, contaminated or could not be inventoried.

    *execution*: {id, task, role, runner, run_dir}. *inventory*: the caller's fresh verified scan — the one its
    sandbox plan or baseline snapshot was built from."""
    current = _current(store, root, config, inventory)
    key = str(execution["id"])
    with store.transaction():   # coordinator.lock: every hold publication is ordered against this block
        record = _load(store, root)
        _reap(store, root, record)
        problem = _decide(store, root, record, current)
        if problem:
            _save(store, root, record)
            raise WorkspaceHoldError(problem)
        record["launches"] += 1
        record["executions"][key] = {**{k: execution.get(k) for k in ("task", "role", "runner", "run_dir")},
                                     "worker": ProcessIdentity.current().to_dict(), "agent": None,
                                     "admitted_at": utc_now(), "generation": record["generation"]}
        _save(store, root, record)

        def started(pid: int) -> None:
            identity = ProcessIdentity.of_pid(pid)
            record["executions"][key]["agent"] = identity.to_dict() if identity else None
            _save(store, root, record)
        try:
            yield started
        except BaseException:   # the launch itself failed: nothing runs under this registration
            record["executions"].pop(key, None)
            _save(store, root, record)
            raise


def abandon_execution(store: StateStore, root: Path, execution_id: str) -> None:
    """Remove an execution that ended without a result to judge (its runner raised)."""
    with store.transaction():
        record = _load(store, root)
        if record["executions"].pop(str(execution_id), None) is not None:
            _save(store, root, record)


def finish_execution(store: StateStore, root: Path, config: ProjectConfig, *, execution_id: str | None,  # pylint: disable=too-many-arguments
                     role: str, grants: Sequence[str], result: Any, task_id: str) -> dict[str, Any] | None:
    """The end of one execution: it leaves the registry, and its residue — in the SAME write — becomes a hold
    entry (returned). Without residue the baseline takes over the sensitive changes the role was ALLOWED to make
    (PathPolicy; a protected path never qualifies). A workspace that cannot be inventoried is held too."""
    residue = list(result.details.get("residue") or [])
    created = sorted(str(c.get("path")) for c in result.details.get("sensitive_changes") or []
                     if c.get("before") is None and c.get("path") in residue)
    evidence = {"task": task_id, "role": role, "runner": result.runner, "status": result.status,
                "run_dir": str(result.details.get("run_dir") or ""),
                "created": created[:MAX_CREATED], "created_count": len(created)}
    with store.transaction():
        record = _load(store, root)
        changed = record["executions"].pop(str(execution_id), None) is not None
        if residue:
            _add_entry(store, root, record, paths=residue, evidence=evidence, reason=(
                f"run of {task_id} ({role}) left unauthorized changes in the checkout: {', '.join(residue[:10])}"
                + (f" and {len(residue) - 10} more" if len(residue) > 10 else "")))
        if changed or residue:
            _save(store, root, record)
        if residue:
            return _view(record)
    try:
        current = unexplained(root, scan(root, config, attempts=VERIFY_ATTEMPTS))
    except InventoryError as exc:
        return put_on_hold(store, root, evidence=evidence,
                           reason=f"the workspace inventory after the run of {task_id} could not be completed: {exc}")
    policy = PathPolicy(root, config)
    with store.transaction():
        record = _load(store, root)
        baseline = record["baseline"]
        if baseline is None or record["hold"] is not None:
            return None   # never inventoried through the gate (a direct runner call), or held meanwhile: no change
        entries = {p: s for p, s in baseline["entries"].items() if p in current}   # removed entries drop out
        for path in contamination(baseline["entries"], current):
            if policy.check(role, path, grants=grants).allowed:
                entries[path] = current[path]
        if entries != baseline["entries"]:
            record["baseline"] = {"entries": dict(sorted(entries.items())), "updated_at": utc_now(),
                                  "revision": int(baseline.get("revision", 0)) + 1,
                                  "how": f"after the run of {task_id}"}
            _save(store, root, record)
    return None


def guard_after_run(store: StateStore, root: Path, config: ProjectConfig, *,  # pylint: disable=too-many-arguments
                    role: str, grants: Sequence[str], result: Any, task_id: str,
                    execution_id: str | None = None) -> dict[str, Any] | None:
    """finish_execution() for callers that judge a run they did not admit through launch_gate."""
    return finish_execution(store, root, config, execution_id=execution_id, role=role, grants=grants,
                            result=result, task_id=task_id)


# ---------------------------------------------------------------------------
# Operator resolution (generation-bound)
# ---------------------------------------------------------------------------

@dataclass
class Resolution:  # pylint: disable=too-many-instance-attributes
    """What `workspace resolve` found (and, with apply, did)."""

    held: bool
    ref: str = ""                                            # the hold (and generation) this resolution is about
    remaining: list[str] = field(default_factory=list)      # unexplained sensitive entries not in the baseline
    unverifiable: list[str] = field(default_factory=list)   # held items that cannot be checked automatically
    unresolved_paths: list[str] = field(default_factory=list)  # held non-sensitive paths Git still reports
    staged: list[str] = field(default_factory=list)         # sensitive paths staged in the index (never acceptable)
    forbidden: list[str] = field(default_factory=list)      # sensitive entries a run created (never acceptable)
    in_flight: list[str] = field(default_factory=list)      # executions still running in the checkout
    stale: str = ""                                          # why nothing was applied although it was asked
    resolved: bool = False
    applied: bool = False

    @property
    def clean(self) -> bool:
        """True when nothing held or untrusted remains."""
        return not (self.remaining or self.unverifiable or self.unresolved_paths or self.staged)


def _git_reports(root: Path, paths: Sequence[str]) -> list[str]:
    """Which of *paths* Git still reports as modified or untracked (not ignored)."""
    if not paths:
        return []
    found = _git_lines(root, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--",
                       *(f":(literal){p}" for p in paths))
    if found is None:
        return list(paths)
    reported, i = set(), 0
    while i < len(found):
        reported.add(found[i][3:])
        i += 2 if found[i][:1] in ("R", "C") else 1
    return sorted(p for p in paths if p in reported or any(r.startswith(f"{p}/") for r in reported))


def _identity(record: Mapping[str, Any]) -> tuple[Any, int, int, int]:
    """What a resolution is bound to: the hold, its generation, the launch counter and the baseline revision."""
    return ((record["hold"] or {}).get("id"), record["generation"], record["launches"],
            int((record["baseline"] or {}).get("revision", 0)))


def resolve(store: StateStore, root: Path, config: ProjectConfig, *, apply: bool, accept: bool = False,  # pylint: disable=too-many-arguments,too-many-locals
            reason: str = "", actor: str = "operator", expect: str | None = None) -> Resolution:
    """Check whether the held residue has been remediated; with *apply*, lift the hold when it has — or, with
    *accept* (and a *reason*), record the current state as the baseline although it has not. Bound to the hold's
    generation: *expect* (a `hold_ref`) names the hold the operator means; whatever this call inspected must still
    be the stored state when it writes, else it changes nothing and reports `stale`. Never changes a file, never
    changes the security policy."""
    with store.transaction():
        record = _load(store, root)
        if _reap(store, root, record):
            _save(store, root, record)
    seen = _identity(record)
    out = Resolution(held=record["hold"] is not None, ref=hold_ref(_view(record), record["generation"]),
                     in_flight=sorted(k for k, e in record["executions"].items() if _alive(e)))
    if expect is not None and expect != out.ref:
        out.stale = f"the checkout's hold is {out.ref}, not {expect}: look at it again before resolving"
        return out
    inventory = scan(root, config)   # InventoryError: nothing is resolved on an unverified scan
    current = unexplained(root, inventory)
    baseline = (record["baseline"] or {"entries": {}})["entries"]
    out.remaining = contamination(baseline, current)
    out.staged = staged_sensitive(root, config)
    sensitive = set(current) | set(baseline)
    for entry in (record["hold"] or {}).get("entries", []):
        for item in entry.get("paths", []):
            if not isinstance(item, str) or item.startswith(_UNVERIFIABLE):
                out.unverifiable.append(str(item))
            elif item not in sensitive:
                out.unresolved_paths.append(item)
        if not entry.get("paths") and entry.get("reason"):
            out.unverifiable.append(str(entry["reason"]))
        out.forbidden += [p for p in entry.get("created", []) if p in inventory.entries]
        if int(entry.get("created_count", 0)) > len(entry.get("created", [])):   # more than were recorded
            out.forbidden += out.remaining
    out.forbidden = sorted(set(out.forbidden))
    out.unverifiable = sorted(set(out.unverifiable))
    out.unresolved_paths = _git_reports(root, sorted(set(out.unresolved_paths)))
    acceptable = not out.staged and not out.in_flight and not out.forbidden
    out.resolved = not out.in_flight and (out.clean or (accept and acceptable))
    if not apply or not out.resolved:
        return out
    if accept and not reason.strip():
        raise WorkspaceHoldError("--accept needs --reason: accepting unremediated changes is recorded")
    with store.transaction():
        record = _load(store, root)
        _reap(store, root, record)
        if _identity(record) != seen or any(_alive(e) for e in record["executions"].values()):
            out.resolved = False
            out.stale = ("the checkout's hold, baseline or executions changed while it was inspected "
                         f"(now {hold_ref(_view(record), record['generation'])}): nothing was changed — look again")
            return out
        was_held = record["hold"] is not None
        accepted = sorted(set(out.remaining) | set(out.unverifiable) | set(out.unresolved_paths)) if accept else []
        record["hold"] = None
        record["generation"] += 1
        kept = current if accept else {p: s for p, s in baseline.items() if p in current}
        record["baseline"] = {
            "entries": dict(sorted(kept.items())), "revision": seen[3] + 1, "updated_at": utc_now(),
            "how": f"resolved by {actor}" + (" (accepted)" if accept else "")}
        _save(store, root, record)   # one atomic write: the hold is lifted exactly when the baseline is recorded
        store.append_event("workspace-hold-resolved", checkout=checkout_key(root), actor=actor, accepted=accepted,
                           reason=reason, was_held=was_held, hold=out.ref)
    out.applied = True
    return out
