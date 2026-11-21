"""W4.7 (M6): several REAL Worker processes and one integration job (integration_worker.py, locks.py).

Each "Worker" here is its own operating-system process running one scheduling step (fc_integration_step_process.py)
against the same state directory, released at the same instant. Checked with real processes, real `flock` locks and
real kills: however many step at once, ONE stage process is started and ONE launch is counted; a Worker killed between
counting a launch and starting it, or right after starting it, never leads to a second stage process; a job lock left
by a dead process is not stale; the stage commands' own job lock admits one process."""

# pylint: disable=missing-function-docstring,redefined-outer-name,protected-access,unused-argument,unused-import
# pylint: disable=consider-using-with

from __future__ import annotations

import json
import os
import pickle
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from feature_coordinator.integration import JobState
from feature_coordinator.integration_schedule import RECORD_KIND, STAGE_TAG_ENV
from feature_coordinator.integration_slot import execution_owner
from feature_coordinator.integration_worker import job_lock_busy
from feature_coordinator.supervisor import tagged_processes

from fc_integration_rig import OperatorKey, Rig, github_origin, origin
from fc_integration_worker_rig import REPO_ROOT, Clock, events_of, hold_job_lock, poll, schedule, scheduling
from test_fc_integration_review import launchers, rrig
from test_fc_integration_validate import FakeGates, key, rig, seeded, tracker
from test_fc_integration_worker import approved_job, stages

STEP = Path(__file__).resolve().parent / "fc_integration_step_process.py"
WORKERS = 8


class Workers:
    """Real Worker processes over one rig; every stage process they start is killed at the end."""

    def __init__(self, rig: Rig, tmp: Path) -> None:
        self.rig, self.tmp = rig, tmp
        self.config = tmp / "config.pickle"
        self.config.write_bytes(pickle.dumps(rig.config))
        self.round = 0
        self.tags: set[str] = set()

    def run(self, count: int, fault: str = "") -> list[dict[str, Any]]:
        """*count* Worker processes, released together; what each printed (a killed one: what it had done)."""
        self.round += 1
        go = self.tmp / f"go-{self.round}"
        env = {**{k: v for k, v in os.environ.items() if k != STAGE_TAG_ENV},
               "PYTHONPATH": f"{REPO_ROOT}{os.pathsep}{STEP.parent}"}
        procs = [subprocess.Popen([sys.executable, str(STEP), str(self.rig.state), str(self.config), str(go),
                                   *([fault] if fault else [])],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, cwd=REPO_ROOT)
                 for _ in range(count)]
        time.sleep(1.0)                                                    # every process is waiting for the signal
        go.write_text("go", encoding="utf-8")
        results = []
        for proc in procs:
            out, err = proc.communicate(timeout=120)
            assert proc.returncode in (0, 137), (proc.returncode, err[-2000:])
            results.append({**json.loads(out.strip().splitlines()[-1]), "exit": proc.returncode})
        for result in results:
            self.tags |= {item["tag"] for item in result["started"]}
        return results

    def stage_processes(self) -> list[int]:
        return sorted(pid for tag in self.tags for pid in tagged_processes(STAGE_TAG_ENV, tag))

    def cleanup(self) -> None:
        for pid in self.stage_processes():
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass


@pytest.fixture
def workers(rrig: Rig, tmp_path: Path):
    made: list[Workers] = []

    def make() -> Workers:
        made.append(Workers(rrig, tmp_path))
        return made[-1]
    yield make
    for item in made:
        item.cleanup()


def test_many_worker_processes_at_once_start_one_stage_process(rrig: Rig, workers: Any):
    scheduling(rrig)
    job = approved_job(rrig)
    pool = workers()
    results = pool.run(WORKERS)
    starters = [r for r in results if r["started"]]
    assert len(starters) == 1, results                                     # ONE process started the stage
    assert starters[0]["started"][0]["tail"][:4] == ["integration", "inspect", "--job", job]
    assert len(pool.stage_processes()) == 1                                # one stage process exists
    rec = schedule(rrig, job)
    assert rec.launches == {"inspect": 1} and rec.running["pid"] == starters[0]["started"][0]["pid"]
    assert len(events_of(rrig, "integration-stage-starting", job)) == 1
    assert len(events_of(rrig, "integration-stage-started", job)) == 1
    for other in (r for r in results if not r["started"]):                 # the others saw the lock or the stage
        assert other["busy"] or any("integration-worker.lock is held" in d for d in other["deferred"]), other
    again = pool.run(WORKERS)                                              # a second wave while it runs: nothing new
    assert not [r for r in again if r["started"]] and len(pool.stage_processes()) == 1
    assert schedule(rrig, job).launches == {"inspect": 1}


def test_many_worker_processes_and_two_jobs_still_run_one_stage_at_a_time(rrig: Rig, workers: Any):
    from test_fc_integration_review import X_PY  # pylint: disable=import-outside-toplevel
    scheduling(rrig)
    first, second = approved_job(rrig), approved_job(rrig, "integration/other", {"cli/src/kiddo_cli/y.py": X_PY})
    pool = workers()
    results = pool.run(WORKERS)
    assert sum(len(r["started"]) for r in results) == 1 and len(pool.stage_processes()) == 1
    running = [job for job in (first, second) if schedule(rrig, job).running]
    assert running == [first]                                              # the oldest job, and only it


def test_a_worker_killed_between_counting_and_starting_never_causes_a_second_stage_process(rrig: Rig, workers: Any):
    scheduling(rrig)
    job = approved_job(rrig)
    pool = workers()
    (killed,) = pool.run(1, "counted")                                     # dies after the write-ahead record
    assert killed["exit"] == 137 and killed["crashed"] == "counted" and not killed["started"]
    rec = schedule(rrig, job)
    assert rec.launches == {"inspect": 1} and rec.running["pid"] is None and not pool.stage_processes()
    assert execution_owner(rrig.store)[0] == "running"                     # reserved until a step settles it
    results = pool.run(WORKERS)                                            # restarted Workers settle the dead launch …
    assert not [r for r in results if r["started"]] and not pool.stage_processes()
    rec = schedule(rrig, job)
    assert rec.running is None and rec.stalls == 1 and rec.last["launch"] == 1
    assert execution_owner(rrig.store) == ("", "")                         # settled: released
    assert len(events_of(rrig, "integration-stage-ended", job)) == 1       # … exactly once
    assert rrig.state_of(job) is JobState.AWAITING_APPROVAL


def test_a_worker_killed_right_after_starting_leaves_one_stage_that_others_watch(rrig: Rig, workers: Any):
    scheduling(rrig)
    job = approved_job(rrig)
    pool = workers()
    (killed,) = pool.run(1, "started")                                     # the stage runs; its PID was never recorded
    assert killed["exit"] == 137 and len(killed["started"]) == 1
    assert schedule(rrig, job).running["pid"] is None and len(pool.stage_processes()) == 1
    results = pool.run(WORKERS)
    assert not [r for r in results if r["started"]] and len(pool.stage_processes()) == 1   # found by its tag
    assert all(r["busy"] or r["deferred"] for r in results)
    os.kill(pool.stage_processes()[0], signal.SIGKILL)                     # the stage process dies too
    deadline = time.monotonic() + 20
    while pool.stage_processes() and time.monotonic() < deadline:
        time.sleep(0.05)
    results = pool.run(WORKERS)
    assert not [r for r in results if r["started"]]                        # settled as a stall: delayed, not restarted
    rec = schedule(rrig, job)
    assert rec.running is None and rec.launches == {"inspect": 1} and rec.stalls == 1


def test_a_lock_left_by_a_killed_process_is_not_stale(rrig: Rig, stages: Any):
    scheduling(rrig)
    job = approved_job(rrig)
    holder = hold_job_lock(rrig, job)
    assert job_lock_busy(rrig.store, job)
    runner, clock = stages(), Clock()
    assert not poll(rrig, runner, clock).started                           # owned by a living process
    holder.send_signal(signal.SIGKILL)                                     # killed: no handler ran, the file stays
    holder.wait(timeout=30)
    assert (rrig.state / "locks" / f"integration-{job}.lock").exists() and not job_lock_busy(rrig.store, job)
    assert poll(rrig, runner, clock).started == [job]                      # nothing to break, nothing to clean up


def test_the_job_lock_admits_one_of_many_processes(rrig: Rig, tmp_path: Path):
    job = approved_job(rrig)
    lock = rrig.state / "locks" / f"integration-{job}.lock"
    go = tmp_path / "go"
    code = ("import sys, time\n"
            "from pathlib import Path\n"
            "from feature_coordinator.errors import LockBusyError\n"
            "from feature_coordinator.locks import FileLock\n"
            "while not Path(sys.argv[2]).exists():\n"
            "    time.sleep(0.001)\n"
            "try:\n"
            "    held = FileLock(Path(sys.argv[1])).acquire(timeout=0)\n"   # exactly as the stage commands take it
            "except LockBusyError:\n"
            "    print('refused', flush=True)\n"
            "    sys.exit(0)\n"
            "print('owner', flush=True)\n"
            "time.sleep(3)\n")
    procs = [subprocess.Popen([sys.executable, "-c", code, str(lock), str(go)], stdout=subprocess.PIPE, text=True,
                              cwd=REPO_ROOT, env={**os.environ, "PYTHONPATH": str(REPO_ROOT)}) for _ in range(WORKERS)]
    time.sleep(1.0)
    go.write_text("go", encoding="utf-8")
    answers = [proc.communicate(timeout=60)[0].strip() for proc in procs]
    assert sorted(answers) == ["owner"] + ["refused"] * (WORKERS - 1)
