"""W4.7 (M6): the checkout's EXECUTION SLOT as integration stages hold it — the durable facts, and nothing else.

A leaf module (it imports no workflow, job or service code), because everything that executes work on a checkout must
be able to ask the same question before it starts:

    execution_owner(store) -> (kind, why)
        ""          no integration stage holds the slot
        "running"   a stage process lives (found by its process TAG); a launch is recorded for which no process is
                    recorded yet (it holds the slot until it is SETTLED in a critical section — no timer and no
                    launcher's death releases it); a stage left processes behind; or a reviewer process of the job's
                    review stage
                    still runs (found by the tags of the job's own review attempts, so it is seen at once, not
                    only after the scheduling step noticed it)
        "uncertain" a schedule record cannot be read: whether a stage runs is unknown. Unknown is not free

Who asks: the Worker service before every cycle's work (service.py: no agent, no publication pass, no intake, no
reviewer beside a stage — also right after a restart, and also when scheduling was switched off while a stage ran);
every task claim (claims.py: a manual `launch` or `work` is refused); the feature integration barrier (workflow.py
`integrate`); and the scheduling step itself (integration_worker.py). The admission of a stage is the mirror image,
decided in the same `coordinator.lock` critical section as a claim: no claim exists and no barrier holds the project
lock, or the stage is not recorded.

THE HAND-OFF from the launcher to its stage process is decided in critical sections only (`owner_of`,
`confirm_launch`, the scheduling step's settlement). A launch that is recorded while no process is recorded for it
HOLDS THE SLOT until one of three things happens under `coordinator.lock`: the stage process confirms it and
records itself (`confirm_launch`, called first by the stage commands when they carry a stage tag: the record must
still name its tag, no claim may exist and no barrier may hold the project lock); the scheduling step closes it,
having read again there that nothing of it lives; or an operator's `schedule --resume` does. Whichever comes first
wins and the others see it: a closed launch is not confirmed (the stage process then does nothing), a confirmed one
is not closed. So neither a launcher that died at the worst moment nor a restarted service can find the slot free
while a stage process is about to work.

The answer comes from the schedule records (`integration-schedule/<job>.json`: the Worker's bookkeeping, one per job
it ever started a stage for) and from /proc — never from memory, so a restarted service, another process and an
operator's command all see the same owner. Without that directory the answer is "" at the cost of one `stat`: a
checkout that never scheduled an integration stage is not affected at all.

The record is strict on load (`Schedule.from_dict`): exact types, finite numbers, a job id that equals its file name,
a stage out of the four schedulable ones, a tag and a log name of this module's own form. A record that names anything
else is unreadable — and an unreadable record is `uncertain`: nothing is started beside it or because of it.
"""

# pylint: disable=unidiomatic-typecheck  # exact types on purpose: a bool is not an int, a subclass is not a dict

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .errors import StateDirError
from .locks import ProcessIdentity
from .supervisor import tagged_processes, terminate_pids

RECORD_KIND = "integration-schedule"
STAGES = ("inspect", "validate", "review", "recover")   # everything the Worker may start; never a merge
STAGE_TAG_ENV = "FC_INTEGRATION_STAGE"    # set on each stage process: found again after a service crash
REVIEW_TAG_ENV = "FC_REVIEW_RUN"          # review_process.REVIEW_TAG_ENV (the reviewer a review stage started)
SCHEDULE_SCHEMA = 1
HISTORY_LIMIT = 20
MAX_LOG_BYTES = 2 * 1024 * 1024
MAX_EPOCH = 1e11

_JOB_RE = re.compile(r"int-[0-9]{8}-[0-9]{4,6}")
_TAG_RE = re.compile(r"istage-[0-9a-f]{16}")
_REVIEW_TAG_RE = re.compile(r"review-[0-9a-f]{16}")
_FIELDS = frozenset({"schema_version", "job_id", "launches", "stalls", "next_attempt_at", "hold", "running", "last",
                     "history", "acknowledged", "revision"})
_RUNNING_KEYS = frozenset({"stage", "launch", "tag", "pid", "process", "launcher", "started_at", "started_epoch",
                           "deadline", "deadline_epoch", "instance", "log", "basis", "watchdog", "reviewer"})
_ENTRY_KEYS = frozenset({"stage", "launch", "started_at", "ended_at", "cause", "exit_code", "outcome", "detail",
                         "progressed", "state_before", "state_after"})
_WATCHDOG_KEYS = frozenset({"kind", "reason", "at", "pids"})
CAUSES = ("exited", "deadline", "orphaned", "service-stop")
_HOLD_KEYS = frozenset({"reason", "since", "stage", "kind", "basis"})
_ACK_KEYS = frozenset({"by", "at", "reason", "state"})
_BASIS_KEYS = frozenset({"state", "revision"})
_PROCESS_KEYS = frozenset({"pid", "start_ticks", "boot_id"})


def new_tag(token: str) -> str:
    """The process tag of one stage launch (*token*: 16 hex characters)."""
    return f"istage-{token}"


def log_name(job_id: str, stage: str, launch: int, tag: str) -> str:
    """The log file (in `<state>/logs/`) of one stage launch."""
    return f"integration-{job_id}.{stage}.{launch}-{tag[-6:]}.log"


# -- the record ---------------------------------------------------------------------------------------------------

def _int(value: Any, minimum: int = 0, maximum: int = 10 ** 9) -> bool:
    return type(value) is int and minimum <= value <= maximum


def _number(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= MAX_EPOCH


def _text(value: Any, limit: int = 1000) -> bool:
    return isinstance(value, str) and len(value) <= limit


def _basis_ok(value: Any) -> bool:
    return type(value) is dict and set(value) == _BASIS_KEYS and _text(value["state"], 40) \
        and _int(value["revision"])


def _process_ok(process: Any) -> bool:
    return type(process) is dict and set(process) == _PROCESS_KEYS and _int(process["pid"], 1) \
        and _int(process["start_ticks"], 0, 10 ** 18) and _text(process["boot_id"], 100)


def _entry_ok(entry: Any) -> bool:
    """One closed launch (`last`, `history`): exactly the fields `integration_schedule.finish` writes, typed."""
    if type(entry) is not dict or set(entry) != _ENTRY_KEYS:
        return False
    return entry["stage"] in STAGES and _int(entry["launch"], 1) and entry["cause"] in CAUSES \
        and (entry["exit_code"] is None or _int(entry["exit_code"], -1000, 1000)) \
        and _text(entry["outcome"], 80) and _text(entry["detail"], 400) and type(entry["progressed"]) is bool \
        and _text(entry["started_at"], 40) and _text(entry["ended_at"], 40) \
        and _text(entry["state_before"], 40) and _text(entry["state_after"], 40)


def _watchdog_ok(value: Any) -> bool:
    return type(value) is dict and set(value) == _WATCHDOG_KEYS and value["kind"] in ("deadline", "orphaned") \
        and _text(value["reason"], 600) and _text(value["at"], 40) and type(value["pids"]) is list \
        and all(_int(pid, 1) for pid in value["pids"])


def _running_ok(job_id: str, running: Any) -> bool:
    """A running entry names a stage, a launch, a tag of this module's form and exactly the log FILE NAME that launch
    gets (never a path: it is read back from `<state>/logs/`), finite times and a basis."""
    if type(running) is not dict or set(running) != _RUNNING_KEYS:
        return False
    stage, tag, process = running["stage"], running["tag"], running["process"]
    if stage not in STAGES or not _int(running["launch"], 1) or not isinstance(tag, str) or not _TAG_RE.fullmatch(tag):
        return False
    if running["log"] != log_name(job_id, stage, running["launch"], tag):
        return False
    if not (running["pid"] is None or _int(running["pid"], 1)) or not _number(running["started_epoch"]) \
            or not _number(running["deadline_epoch"]) or not _basis_ok(running["basis"]):
        return False
    if not (process is None or _process_ok(process)) or not _process_ok(running["launcher"]):
        return False
    reviewer = running["reviewer"]
    return _text(running["started_at"], 40) and _text(running["deadline"], 40) and _text(running["instance"], 100) \
        and (running["watchdog"] is None or _watchdog_ok(running["watchdog"])) \
        and (reviewer is None or (isinstance(reviewer, str) and bool(_REVIEW_TAG_RE.fullmatch(reviewer))))


@dataclass
class Schedule:  # pylint: disable=too-many-instance-attributes
    """The Worker's bookkeeping for one job (module docstring). Not evidence; it decides nothing about the job."""

    id: str
    launches: dict[str, int] = field(default_factory=dict)   # stage → stage processes started (write-ahead)
    stalls: int = 0                                           # runs (or failed starts) in a row that changed nothing
    next_attempt_at: float | None = None                      # epoch seconds; None: no earliest time
    hold: dict[str, Any] | None = None                        # {reason, since, stage, kind: stalls|launches, basis}
    running: dict[str, Any] | None = None                     # the stage process (see _RUNNING_KEYS)
    last: dict[str, Any] | None = None                        # how the newest stage run ended
    history: list[dict[str, Any]] = field(default_factory=list)
    acknowledged: dict[str, Any] | None = None                # {by, at, reason, state}: a terminal outcome was seen
    revision: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Deterministic JSON form."""
        data = asdict(self)
        data["job_id"] = data.pop("id")
        data["schema_version"] = SCHEDULE_SCHEMA
        return data

    @classmethod
    def from_dict(cls, data: Any) -> "Schedule":  # pylint: disable=too-many-branches
        """Strict (module docstring): anything else than a well-formed record is a StateDirError."""
        if type(data) is not dict:
            raise StateDirError(f"integration schedule record: not a JSON object ({type(data).__name__})")
        if not _int(data.get("schema_version"), SCHEDULE_SCHEMA, SCHEDULE_SCHEMA):
            raise StateDirError(f"integration schedule record: unsupported schema {data.get('schema_version')!r}")
        job_id = data.get("job_id")
        if not isinstance(job_id, str) or not _JOB_RE.fullmatch(job_id):
            raise StateDirError(f"integration schedule record: invalid job id {job_id!r}")
        where = f"integration schedule record {job_id}"
        if set(data) != _FIELDS:
            raise StateDirError(f"{where}: unknown or missing fields {sorted(set(data) ^ _FIELDS)}")
        launches, hold, last, ack = data["launches"], data["hold"], data["last"], data["acknowledged"]
        if type(launches) is not dict or not all(s in STAGES and _int(n) for s, n in launches.items()) \
                or not _int(data["stalls"]) or not _int(data["revision"]) \
                or not (data["next_attempt_at"] is None or _number(data["next_attempt_at"])):
            raise StateDirError(f"{where}: malformed counters")
        history = data["history"]
        if type(history) is not list or len(history) > HISTORY_LIMIT or not all(_entry_ok(e) for e in history) \
                or not (last is None or _entry_ok(last)):
            raise StateDirError(f"{where}: malformed history")
        if data["running"] is not None and not _running_ok(job_id, data["running"]):
            raise StateDirError(f"{where}: malformed running entry")
        if hold is not None and not (type(hold) is dict and set(hold) == _HOLD_KEYS
                                     and hold["kind"] in ("stalls", "launches") and hold["stage"] in STAGES
                                     and _text(hold["reason"]) and _text(hold["since"], 40)
                                     and _basis_ok(hold["basis"])):
            raise StateDirError(f"{where}: malformed hold")
        if ack is not None and not (type(ack) is dict and set(ack) == _ACK_KEYS
                                    and all(_text(ack[key]) for key in _ACK_KEYS)):
            raise StateDirError(f"{where}: malformed acknowledgement")
        values = {k: json.loads(json.dumps(v)) for k, v in data.items() if k not in ("schema_version", "job_id")}
        return cls(id=job_id, **values)


def schedule_path(store: Any, job_id: str) -> Path:
    """Where the job's schedule record lives."""
    return store.root / RECORD_KIND / f"{job_id}.json"


def read_schedule(store: Any, job_id: str) -> Schedule | None:
    """The job's schedule record; None ONLY when it has no file. A file that is empty, `null`, not JSON, not a
    well-formed record, or a record of another job is a StateDirError — never "absent"."""
    path = schedule_path(store, job_id)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise StateDirError(f"integration schedule record {path.name}: {exc}") from exc
    try:
        rec = Schedule.from_dict(json.loads(text))
    except ValueError as exc:
        raise StateDirError(f"integration schedule record {path.name}: not JSON ({exc})") from exc
    if rec.id != job_id:
        raise StateDirError(f"integration schedule record {path.name} names another job ({rec.id})")
    return rec


def load_schedule(store: Any, job_id: str) -> Schedule:
    """The job's schedule record (a new, empty one if the Worker never started a stage for it)."""
    return read_schedule(store, job_id) or Schedule(id=job_id)


def save_schedule(store: Any, rec: Schedule) -> None:
    """Persist *rec* (inside a transaction) — after proving that what is written will load again: a record this code
    could not read back would make the slot's ownership unknown and stop the checkout."""
    Schedule.from_dict(json.loads(json.dumps(rec.to_dict())))
    store.save_record(RECORD_KIND, rec)


def list_schedules(store: Any) -> list[tuple[str, Schedule | None, str]]:
    """(file stem, record, error) for every file of the schedule directory, by name. A file that is not a readable
    record of its own name has record None and the reason — it is never skipped."""
    folder = store.root / RECORD_KIND
    found: list[tuple[str, Schedule | None, str]] = []
    try:
        paths = sorted(folder.glob("*.json"))
    except OSError as exc:
        return [("?", None, str(exc))]
    for path in paths:
        try:
            found.append((path.stem, read_schedule(store, path.stem), ""))
        except (StateDirError, ValueError) as exc:
            found.append((path.stem, None, str(exc)))
    return found


# -- processes ----------------------------------------------------------------------------------------------------

def _identity(data: Any) -> ProcessIdentity | None:
    try:
        return ProcessIdentity.from_dict(data) if isinstance(data, Mapping) else None
    except (KeyError, TypeError, ValueError):
        return None


def stage_processes(running: Mapping[str, Any]) -> tuple[str, list[int]]:
    """Whether a stage's processes live — decided by its TAG (as review_process.attempt_processes):
    gone (no process carries the tag; a recorded identity alive WITHOUT the tag is not ours and is never signalled) |
    running (tagged processes live and the recorded stage process is one of them, or is not recorded yet) |
    orphaned (the stage process ended and left tagged processes behind)."""
    tag = str(running.get("tag") or "")
    pids = tagged_processes(STAGE_TAG_ENV, tag) if tag else []
    if not pids:
        return "gone", []
    identity = _identity(running.get("process"))
    if identity is not None and not (identity.is_alive() and identity.pid in pids):
        return "orphaned", pids
    return "running", pids


def any_stage_processes() -> list[int]:
    """Every process of this user that carries a stage tag, whatever its value — what is asked before an UNREADABLE
    schedule record is set aside: its tag cannot be trusted, so no tagged process may live at all."""
    import os  # pylint: disable=import-outside-toplevel
    needle = f"{STAGE_TAG_ENV}=".encode()
    found = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit() or int(entry) == os.getpid():
            continue
        try:
            with open(f"/proc/{entry}/environ", "rb") as handle:
                if any(item.startswith(needle) for item in handle.read().split(b"\0")):
                    found.append(int(entry))
        except OSError:
            continue
    return found


def review_tags(store: Any, job_id: str) -> list[str] | None:
    """The process tags of every review attempt the job record holds (M4 evidence `review-attempt`), newest last;
    None when the job record cannot be read. Read as plain JSON (this module imports no job code): it is used to
    FIND processes for the ownership question, never to decide anything about the job and never to SIGNAL a process
    (integration_worker signals only tags of the strictly loaded job, under the job's lock). A file that is not this
    job's record gives None."""
    try:
        data = json.loads((store.root / "integration" / f"{job_id}.json").read_text(encoding="utf-8"))
        if type(data) is not dict or data.get("job_id") != job_id or type(data.get("evidence")) is not list:
            return None   # not this job's record: its tags are nobody's to trust
        return [entry["tag"] for entry in data["evidence"]
                if isinstance(entry, dict) and entry.get("kind") == "review-attempt"
                and isinstance(entry.get("tag"), str) and _REVIEW_TAG_RE.fullmatch(entry["tag"])]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def reviewer_processes(store: Any, rec: "Schedule") -> tuple[list[int], bool]:
    """(processes, unknown) of the reviewers a `review` stage of this job started. M4 runs a reviewer in its own
    session with its own tag, so it outlives a killed stage process; its tag is on the job's review attempts, which is
    where it is looked for — from the first moment, by every reader. *unknown*: the job record cannot be read, so
    whether a reviewer runs cannot be told."""
    running = rec.running
    if running is None or running["stage"] != "review":
        return [], False
    tags = review_tags(store, rec.id)
    if tags is None:
        return [], True
    if running.get("reviewer"):
        tags = [*tags, running["reviewer"]]
    return sorted({pid for tag in set(tags) for pid in tagged_processes(REVIEW_TAG_ENV, tag)}), False


def stop_stage(running: Mapping[str, Any], grace_seconds: float) -> list[int]:
    """Stop one stage; returns the processes still alive. SIGTERM goes to the recorded stage process FIRST (a review
    then stops its reviewer and gives the attempt back), SIGKILL after *grace_seconds*; then every process still
    carrying the tag. The tag is re-checked before every signal, so a reused PID is never signalled."""
    tag = str(running.get("tag") or "")
    if not tag:
        return []

    def ours(pid: int) -> bool:
        return pid in tagged_processes(STAGE_TAG_ENV, tag)
    identity = _identity(running.get("process"))
    survivors: list[int] = []
    if identity is not None and identity.is_alive() and ours(identity.pid):
        survivors = terminate_pids([identity.pid], grace_seconds=grace_seconds,
                                   still_ours=lambda pid: identity.is_alive() and ours(pid))
    left = [pid for pid in tagged_processes(STAGE_TAG_ENV, tag) if pid not in survivors]
    if left:
        survivors += terminate_pids(left, grace_seconds=min(grace_seconds, 5.0), still_ours=ours)
    return sorted(set(survivors))


# -- the slot -----------------------------------------------------------------------------------------------------

def live_processes(store: Any, rec: Schedule) -> str:
    """Why *rec*'s stage still EXECUTES ('' : nothing of it runs): its stage process, processes it left behind, or a
    reviewer of its review stage. What `schedule --resume` asks before it discards anything."""
    running = rec.running
    if running is None:
        return ""
    head = f"integration job {rec.id}: `{running['stage']}`"
    state, pids = stage_processes(running)
    if state == "running":
        return (f"{head} runs (launch {running['launch']}, pid {running['pid'] or pids[0]}, until "
                f"{running['deadline']}); no agent is started meanwhile")
    if state == "orphaned":
        return f"{head} ended and left processes {pids} behind; no agent is started meanwhile"
    reviewers, unknown = reviewer_processes(store, rec)
    if reviewers:
        return (f"{head} ended, but the reviewer process it started still runs ({reviewers}); no agent is started "
                "meanwhile — the next `review` run collects its answer")
    if unknown:
        return (f"{head} ended, and its job record cannot be read, so whether its reviewer still runs is unknown; no "
                "agent is started meanwhile")
    return ""


def owner_of(store: Any, rec: Schedule) -> str:
    """Why *rec*'s stage holds the execution slot now ('' : it does not): something of it executes, or its launch
    is recorded and NO PROCESS IS RECORDED FOR IT YET. Such a launch holds the slot until it is settled in a
    critical section — by the scheduling step, by the stage process confirming it, or by `schedule --resume` — and
    by nothing else: not by elapsed time, and not by its launcher having died (the stage process a dead launcher
    started may still be about to confirm; two looks from outside cannot tell)."""
    running = rec.running
    if running is None:
        return ""
    why = live_processes(store, rec)
    if why:
        return why
    if running["pid"] is None:
        launcher = _identity(running["launcher"])
        alive = launcher is not None and launcher.is_alive()
        return (f"integration job {rec.id}: `{running['stage']}` is being started (launch {running['launch']}, by "
                f"pid {running['launcher']['pid']}"
                + ("" if alive else ", which is gone: the Worker's next scheduling step settles it — without a "
                                    f"running Worker, `integration schedule --job {rec.id} --resume --reason TEXT "
                                    "--apply`")
                + "); no agent is started meanwhile")
    return ""   # its recorded process ended: the scheduling step closes the launch; nothing executes


def admission_problem(store: Any) -> str:
    """Why no integration stage may take the execution slot NOW ('' : it may) — asked inside the coordinator's
    critical section, the one a task claim is decided in (claims.py asks the mirror question there): a claim
    exists, or a quiescent barrier (`integrate`, a full-project session, a tree-changing Git operation) holds the
    project lock."""
    from .errors import LockBusyError  # pylint: disable=import-outside-toplevel
    if store.list_claims():
        return "an agent holds a claim on the checkout"
    barrier = store.locks.project(shared=True)
    try:
        barrier.acquire(timeout=0)
    except LockBusyError:
        return "a quiescent barrier holds the project lock (a feature integration or a full-project session)"
    barrier.release()
    return ""


def confirm_launch(store: Any, job_id: str, tag: str, process: ProcessIdentity | None) -> str:
    """The stage process's side of the hand-off (module docstring): '' when this process is the recorded launch of
    *job_id* and may run, else why it must not do anything. *process*: this process's identity, recorded for the
    launch if none is yet (None: the caller is not the stage process itself — a test double — and records nothing).
    One critical section with every claim: a launch that is not registered yet is admitted again here."""
    with store.transaction():
        try:
            rec = read_schedule(store, job_id)
        except StateDirError as exc:
            return f"its schedule record cannot be read ({exc})"
        running = rec.running if rec is not None else None
        if rec is None or running is None or running["tag"] != tag:
            return "its launch is no longer recorded (it was settled, resumed or replaced meanwhile)"
        if running["pid"] is None:
            problem = admission_problem(store)
            if problem:
                return f"the checkout is no longer free for it: {problem}"
            if process is not None:
                running.update(pid=process.pid, process=process.to_dict())
                save_schedule(store, rec)
    return ""


def execution_owner(store: Any, now: float | None = None) -> tuple[str, str]:
    """(kind, why): who holds the checkout's execution slot on behalf of an integration stage (module docstring).
    Read-only: the schedule records and /proc. ('', '') when nobody does."""
    folder = store.root / RECORD_KIND
    if not folder.is_dir():
        return "", ""
    del now   # (kept for callers: ownership depends on no clock)
    uncertain = ""
    for stem, rec, error in list_schedules(store):
        if rec is None:
            uncertain = uncertain or (
                f"integration schedule record {stem!r} cannot be read ({error}): whether an integration stage runs "
                f"is unknown, so nothing is started beside it — inspect {RECORD_KIND}/{stem}.json, then `integration "
                f"schedule --job {stem} --resume --reason TEXT --apply`")
            continue
        why = owner_of(store, rec)
        if why:
            return "running", why
    return ("uncertain", uncertain) if uncertain else ("", "")
