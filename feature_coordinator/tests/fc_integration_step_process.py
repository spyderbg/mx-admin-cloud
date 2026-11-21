"""A REAL Worker process for the W4.7 M6 concurrency tests: one scheduling step (integration_worker.IntegrationStep)
over a state directory, as its own operating-system process.

    python fc_integration_step_process.py STATE_DIR CONFIG_PICKLE GO_FILE [FAULT]

It waits until GO_FILE exists (so several of these start their step at the same moment), runs ONE `poll()` whose
`start` launches a real, tagged, long-running stand-in for the stage process, prints one JSON line with what it did,
and exits. With FAULT it dies (os._exit, no cleanup — a kill) at that point of the step: `counted` (the launch is on
disk, no process started) or `started` (the process runs, its PID was never recorded)."""

from __future__ import annotations

import json
import os
import pickle
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from feature_coordinator.integration_schedule import STAGE_TAG_ENV
from feature_coordinator.integration_worker import IntegrationStep
from feature_coordinator.issue_sync import SimulatedCrash
from feature_coordinator.store import StateStore


def main() -> int:
    """One scheduling step (module docstring); the process exit code."""
    state, config_path, go = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    fault = sys.argv[4] if len(sys.argv) > 4 else ""
    config = pickle.loads(config_path.read_bytes())
    store = StateStore(state)
    started: list[dict[str, object]] = []

    def start(tail: list[str], log_name: str, env: dict[str, str]) -> subprocess.Popen:
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"], env=dict(env),  # pylint: disable=consider-using-with
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                start_new_session=True)
        started.append({"tail": list(tail), "log": log_name, "tag": env[STAGE_TAG_ENV], "pid": proc.pid})
        return proc

    jobs = [path.stem for path in sorted((state / "integration").glob("*.json"))]
    faults = [f"crash-after-stage-{fault}:{job}" for job in jobs] if fault else []
    deadline = time.monotonic() + 60
    while not go.exists():
        if time.monotonic() > deadline:
            return 3
        time.sleep(0.001)
    step = IntegrationStep(SimpleNamespace(store=store, config=config), start=start, faults=faults,
                           instance=f"proc-{os.getpid()}")
    try:
        report = step.poll()
    except SimulatedCrash:
        sys.stdout.write(json.dumps({"pid": os.getpid(), "crashed": fault, "started": started}) + "\n")
        sys.stdout.flush()
        os._exit(137)   # pylint: disable=protected-access  # a kill: no handler, no cleanup
    sys.stdout.write(json.dumps({"pid": os.getpid(), "started": started, "report_started": report.started,
                                 "busy": report.busy, "deferred": report.deferred,
                                 "attention": report.attention}) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
