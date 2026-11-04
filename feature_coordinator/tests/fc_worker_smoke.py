#!/usr/bin/env python3
"""W4.2 — real-agent smoke test of the AUTONOMOUS Worker service: bounded, foreground, sequential.

TEST TOOLING ONLY (never imported by the coordinator). One disposable feature, three tasks, at most
FOUR agent sessions, run by the real service path (`python -m feature_coordinator worker start --exit-when-idle` →
`python -m feature_coordinator launch` → native-cli backend → the agent CLI → checkpoints, gates):

  backend   Claude Code `opus`         steps `module`, `validation-test`   gate w4s-backend
  frontend  Codex (its default model)  step  `ts-utility`                  gate w4s-frontend
  cli       Claude Code `sonnet`       step  `cli-utility`, depends on backend   gate w4s-cli

Backend and Frontend are eligible together from the start, so the service must enforce one agent
at a time; CLI becomes eligible only when Backend is verified (dependency-aware scheduling).

  service 1  A test-only hook (BOUNDARY_HOOK, a `sitecustomize` on this service's PYTHONPATH,
             inherited by its launch processes) holds Backend's launch at the PRODUCTION cancel
             check right after step `module` was checkpointed: no step-2 agent exists then. It
             writes a marker. The harness verifies the boundary and runs `python -m feature_coordinator worker stop`
             (graceful): the production check sees the cancel request, the task is deferred, the
             service exits. No live model request is interrupted.
  between    nothing runs; `worker status/health/waiting/attention` must change nothing.
  service 2  a NEW `worker start --exit-when-idle` (no hook): adopts checkpoint #1, runs Backend
             step 2 only, then CLI and Frontend, and exits when idle.
  verify     A–L below; the report holds sanitized facts only (never prompts or transcripts).

Independent evidence: every agent CLI call goes through AUDIT_WRAPPER (it refuses a session unless
every check passes and logs start, child identity, end and exit status), a monitor thread samples
/proc for processes carrying this test's run tags, and a sampler records `worker health --json`.
Nothing here kills an agent: on any unexpected condition the harness asks the service to stop
(`worker stop`), and a leftover is reported, never signalled. The Worker never outlives the
harness: each service runs in its own session with a parent-death signal (SIGTERM — the service's
graceful stop — the moment the harness dies, even by SIGKILL), and SIGINT/SIGTERM/SIGHUP to the
harness stop the service and still write the report. Independently, the wrapper refuses every NEW
session unless BOTH supervisors are valid: the harness process (identity) is alive, and the
session's launch was started by the one Worker instance (identity) the harness authorized. The
authorization is granted before a service may start, and revoked before any stop, when a service
exits and when supervision ends — so neither an orphaned launch (its Worker died) nor a Worker
the harness did not start (a manual recovery) can spend a session.

    python fc_worker_smoke.py --agent fake --apply      # offline rehearsal (fake agents)
    python fc_worker_smoke.py --agent real              # real-agent PLAN only (no model request)
    python fc_worker_smoke.py --agent real --apply      # REAL — separate operator approval
"""

# pylint: disable=too-many-lines

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from feature_coordinator import CLI  # pylint: disable=wrong-import-position
from feature_coordinator.autonomy import load_scheduling, scrub  # pylint: disable=wrong-import-position
from feature_coordinator.checkpoints import load_record  # pylint: disable=wrong-import-position
from feature_coordinator.cli import EXAMPLE_CONFIG  # pylint: disable=wrong-import-position
from feature_coordinator.demo import init_demo_repo  # pylint: disable=wrong-import-position
from feature_coordinator.locks import FileLock, LockSet  # pylint: disable=wrong-import-position
from feature_coordinator.store import StateStore  # pylint: disable=wrong-import-position

FEATURE = "autonomous-worker-smoke"
BRANCH = f"feature/{FEATURE}"
TASKS: dict[str, dict[str, Any]] = {
    "backend": {"role": "backend", "provider": "claude", "model": "opus", "steps": ("module", "validation-test"),
                "gate": "w4s-backend", "depends_on": (), "title": "Smoke: scoring module and its unit test",
                "acceptance": "the unit test backend/w4smoke/test_scoring.py passes (score([1, 2, 3]) == 14)"},
    "cli": {"role": "cli", "provider": "claude", "model": "sonnet", "steps": ("cli-utility",), "gate": "w4s-cli",
            "depends_on": ("backend",), "title": "Smoke: greeting command-line utility",
            "acceptance": "`python3 cli/w4smoke/greet.py --name Worker` prints exactly `Hello, Worker!`"},
    "frontend": {"role": "frontend", "provider": "codex", "model": "", "steps": ("ts-utility",), "gate": "w4s-frontend",
                 "depends_on": (), "title": "Smoke: slugify TypeScript utility",
                 "acceptance": "slugify('  Hello, Worker W4.2!  ') returns 'hello-worker-w4-2'"},
}
MAX_SESSIONS = sum(len(t["steps"]) for t in TASKS.values())  # 4: one per step, never more
BOUNDARY_AFTER = "module"
FILES = {
    "module": ("backend/w4smoke/scoring.py",
               '"""W4.2 smoke test: a tiny scoring module."""\n\n\ndef score(values):\n'
               '    """The sum of the squares of *values*."""\n    return sum(v * v for v in values)\n'),
    "validation-test": ("backend/w4smoke/test_scoring.py",
                        "import unittest\n\nfrom scoring import score\n\n\nclass ScoreTest(unittest.TestCase):\n"
                        "    def test_sum_of_squares(self):\n        self.assertEqual(score([1, 2, 3]), 14)\n\n"
                        "    def test_empty(self):\n        self.assertEqual(score([]), 0)\n\n\n"
                        'if __name__ == "__main__":\n    unittest.main()\n'),
    "cli-utility": ("cli/w4smoke/greet.py",
                    'import argparse\n\n\ndef main(argv=None):\n    parser = argparse.ArgumentParser(description="W4.2 '
                    'smoke test greeting")\n    parser.add_argument("--name", required=True)\n'
                    '    args = parser.parse_args(argv)\n    print(f"Hello, {args.name}!")\n    return 0\n\n\n'
                    'if __name__ == "__main__":\n    raise SystemExit(main())\n'),
    "ts-utility": ("frontend/w4smoke/slugify.ts",
                   "export function slugify(text: string): string {\n  return text\n    .trim()\n    .toLowerCase()\n"
                   '    .replace(/[^a-z0-9]+/g, "-")\n    .replace(/^-+|-+$/g, "");\n}\n'),
}
GATES = {
    "w4s-backend": ["python3", "-m", "unittest", "discover", "-s", "backend/w4smoke", "-p", "test_*.py"],
    "w4s-cli": ["python3", "-c", "import subprocess, sys; out = subprocess.run([sys.executable, "
                "'cli/w4smoke/greet.py', '--name', 'Worker'], capture_output=True, text=True, check=True).stdout; "
                "assert out == 'Hello, Worker!\\n', repr(out); print('cli ok')"],
    "w4s-frontend": ["node", "--input-type=module", "-e", "import { slugify } from './frontend/w4smoke/slugify.ts'; "
                     "const got = slugify('  Hello, Worker W4.2!  '); if (got !== 'hello-worker-w4-2') "
                     "{ console.error('unexpected: ' + got); process.exit(1); } console.log('frontend ok');"],
}
SEED = {"README.md": "# disposable W4.2 autonomous Worker smoke repository\n", "backend/README.md": "# backend\n",
        "cli/README.md": "# cli\n", "frontend/README.md": "# frontend\n",
        ".gitignore": "__pycache__/\n.pytest_cache/\nnode_modules/\n"}  # tool caches never count as changes
SESSION_TIMEOUT = 600      # runtime.task_timeout_seconds: the Supervisor's per-session deadline
TOTAL_SECONDS = 40 * 60    # the whole test; a session may start only if it can end before this
CLAUDE_BUDGET_USD = 0.5    # --max-budget-usd per Claude Code session (as in W2 and W3.2)
STEP_PROVIDER = {s: (k, t) for k, t in TASKS.items() for s in t["steps"]}


SAFETY_CHECKS = (  # independent of whether the planned work completed: a failure is a safety violation
    "the disposable repository", "routing:", "bounded configuration", "subscription logins", "the gate sandbox",
    "no claim, no session, no process of this test yet", "boundary: no agent process alive",
    "the stop started no session", "between the services: status/health/waiting/attention changed nothing",
    "A. every session ran under", "A. never more than", "B. no API key or model substitution",
    "B. every native-cli run directory", "C. ", "D. unique", "D. manifests", "D. Feature-Task",
    "D. each checkpoint commit holds only", "G. ", "E. no task started before", "E. CLI was not ready while",
    "F. checkpoint #1 adopted unchanged", "F. no step repeated",
    "after the run: status/health/waiting/attention are read-only", "J. ", "K", "L. ",
    "the service exited after `worker stop`", "the scenario ran without a harness error")
COMPLETION_CHECKS = (  # outcome checks that need ALL planned work: after a primary failure they are its consequences
    "service 2 ended by itself", "A. exactly four", "B. three Claude", "D. four committed", "E. CLI ran",
    "F. service 1 ran only", "F. every step", "F. launches", "F. the resumed prompt", "H. every task verified",
    "I. service 1")
GATE_PROBE = r'''import json, sys
from pathlib import Path
from feature_coordinator.config import load_config
from feature_coordinator.gates import GateContext, SandboxedGateRunner
config, repo, logs = load_config(Path(sys.argv[1])), Path(sys.argv[2]), Path(sys.argv[3])
runner, out = SandboxedGateRunner(config, logs), {}
probes = (("probe-python", ["python3", "-c", "print('ok')"]), ("probe-node", ["node", "-e", "console.log('ok')"]))
for name, argv in probes:
    result = runner.run_command(name, argv, GateContext(repo), cwd=repo)
    out[name] = {"status": result.status, "exit": result.exit_code, "summary": result.summary[:300]}
print(json.dumps(out))
'''


def step_title(step: str) -> str:
    """The step's instruction. The fake agent follows exactly this text too (no test-only prompt)."""
    path, content = FILES[step]
    lang = "typescript" if path.endswith(".ts") else "python"
    return (f"Create the file {path} with exactly the content below and nothing else in it.\n"
            f"```{lang}\n{content}```\n"
            "Do not create, change or delete any other file. Do not run any command.")


AUDIT_WRAPPER = r'''#!/usr/bin/python3
"""Audit wrapper around an agent CLI — W4.2 Worker smoke test (fc_worker_smoke.py); TEST TOOLING
ONLY. <this file>.json: {"provider", "real", "sha256", "control"}; the shared control file names the
repository, state directory, audit log, steps (task, role, provider, model), budgets and deadline.

Known non-session calls (--version, --help, the status commands) pass through and are logged;
anything else that is not a session is refused. Before a SESSION every check must pass, else it is
refused (exit 97, no model request): the real binary is the one recorded at preparation (sha256);
the working directory (and Codex `-C`) is the disposable repository; the run tag is one the
coordinator recorded for this state directory; the prompt's role, task and CURRENT STEP match a
step of this provider; the requested model is the step's; the subscription-only flags are present;
no API-key, token or provider-switch variable is set; the CLI's own status command reports the
subscription login; the session can end before the test's deadline; BOTH supervisors are valid
(re-read at decision time, under the budget lock): the harness process (pid + start time) is
alive and still accepts sessions, AND the control file holds an unrevoked authorization for
exactly one Worker instance (pid + start time) that is alive and is the parent of this session's
launch process — an orphaned launch or an unauthorized Worker gets no session; the total and
per-role budgets are not exhausted and the step had no session yet; no process of an earlier
session (identity) and no process carrying another run tag of this test is alive. It then runs
the real CLI as its child (same process group and environment, the prompt on its stdin), waits,
and logs the end and exit status. It never retries, never changes arguments and never reads credentials;
only the login METHOD is logged."""
import fcntl, glob, hashlib, json, os, re, signal, subprocess, sys, time

REFUSED = 97
KEYISH = re.compile(r"^(ANTHROPIC_|OPENAI_|CODEX_API|CLAUDE_CODE_USE_|CLAUDE_CODE_OAUTH|AZURE_OPENAI|AWS_|GOOGLE_"
                    r"|VERTEX|BEDROCK)")
PASSTHROUGH = {"claude": (["--version"], ["--help"], ["auth", "status", "--json"]),
               "codex": (["--version"], ["--help"], ["exec", "--help"], ["login", "status"])}
here = os.path.abspath(sys.argv[0])
with open(here + ".json", encoding="utf-8") as handle:
    own = json.load(handle)
with open(own["control"], encoding="utf-8") as handle:
    control = json.load(handle)
provider, real, args = own["provider"], own["real"], sys.argv[1:]


def fields(pid):
    with open(f"/proc/{pid}/stat", encoding="ascii") as handle:
        return handle.read().rsplit(")", 1)[1].split()


def ticks(pid):
    try:
        return int(fields(pid)[19])
    except (OSError, ValueError, IndexError):
        return None


def ppid(pid):
    try:
        return int(fields(pid)[1])
    except (OSError, ValueError, IndexError):
        return 0


def alive(pid, start):
    try:
        found = fields(pid)
    except (OSError, ValueError, IndexError):
        return False
    return found[0] != "Z" and int(found[19]) == start


def environ(pid):
    try:
        with open(f"/proc/{pid}/environ", "rb") as handle:
            raw = handle.read()
    except OSError:
        return {}
    return dict(item.decode(errors="replace").split("=", 1) for item in raw.split(b"\0") if b"=" in item)


def log(entry):
    with open(control["audit"], "a", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        handle.write(json.dumps(entry, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def refuse(entry, problems):
    entry.update(decision="refused", problems=problems)
    log(entry)
    message = "refused by the W4.2 audit wrapper (no model session): " + "; ".join(problems)
    sys.stderr.write(message + "\n")
    # also in the CLI's own output format: the coordinator records this as the run's error (not a bare exit 97)
    event = ({"type": "result", "subtype": "error_during_execution", "is_error": True, "result": message}
             if provider == "claude" else {"type": "turn.failed", "error": {"message": message}})
    sys.stdout.write(json.dumps(event) + "\n")
    sys.stdout.flush()
    sys.exit(REFUSED)


me = os.getpid()
launch = os.getppid()
service = ppid(launch)
entry = {"at": time.time(), "provider": provider, "pid": me, "start_ticks": ticks(me), "launch_pid": launch,
         "service_pid": service, "service_start_ticks": ticks(service),
         "launch_tag": environ(launch).get("FC_WORKER_LAUNCH", ""), "run_tag": os.environ.get("FC_RUN_TAG", "")}
if provider == "claude":
    session = "-p" in args or "--print" in args
else:
    session = args[:1] == ["exec"] and "--help" not in args
if not session:
    if args not in [list(a) for a in PASSTHROUGH[provider]]:
        refuse({**entry, "call": " ".join(args[:2])}, [f"not a known non-session call: {' '.join(args[:2])!r}"])
    log({**entry, "decision": "passthrough", "call": " ".join(args)})
    os.execv(real, [real, *args])

prompt = sys.stdin.read()
problems = []
try:
    with open(real, "rb") as handle:
        if hashlib.sha256(handle.read()).hexdigest() != own["sha256"]:
            problems.append("the CLI binary changed since preparation")
except OSError:
    problems.append("the CLI binary recorded at preparation is not readable (updated or removed?)")
cwd = os.path.realpath(os.getcwd())
if cwd != control["repo"]:
    problems.append(f"working directory {cwd} is not the disposable repository")
if provider == "codex" and ("-C" not in args or os.path.realpath(args[args.index("-C") + 1]) != control["repo"]):
    problems.append("Codex -C is not the disposable repository")
tags = {}
for path in glob.glob(os.path.join(control["state"], "runs", "*", "native.json")):
    try:
        with open(path, encoding="utf-8") as handle:
            tags[json.load(handle).get("run_tag")] = os.path.basename(os.path.dirname(path))
    except (OSError, ValueError):
        problems.append("an unreadable run record")
if not entry["run_tag"] or entry["run_tag"] not in tags:
    problems.append("no run tag recorded by the coordinator for this state directory")
head = re.match(r"You are the (\S+) agent working on feature task (\S+) in this repository\.", prompt)
current = re.search(r"CURRENT STEP (\d+) of (\d+) — `([^`]+)`", prompt)
step = current.group(3) if current else None
entry.update(role=head.group(1) if head else None, task=head.group(2) if head else None, step=step,
             prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest())
expected = control["steps"].get(step or "")
model = ""
for flag in ("--model", "-m"):
    if flag in args and args.index(flag) + 1 < len(args):
        model = args[args.index(flag) + 1]
entry["model"] = model or "CLI default"
if expected is None:
    problems.append(f"unknown step {step!r}")
else:
    if expected["provider"] != provider:
        problems.append(f"step {step!r} belongs to {expected['provider']}, not {provider}")
    if not head or (head.group(1), head.group(2)) != (expected["role"], expected["task"]):
        problems.append(f"role/task {entry['role']}/{entry['task']} is not {expected['role']}/{expected['task']}")
    if model != expected["model"]:
        problems.append(f"model {entry['model']!r} is not the routed {expected['model'] or 'CLI default'!r}")
required = (["--restricted", "--no-session-persistence"] if provider == "claude" else
            ["--ignore-user-config", "--strict-config", 'forced_login_method="chatgpt"'])
missing = [flag for flag in required if flag not in args]
if missing:
    problems.append(f"subscription-only flags missing: {missing}")
keyish = sorted(k for k in os.environ if KEYISH.match(k))
if keyish:
    problems.append(f"API-key/token/provider variables present: {keyish}")
status_argv = [real, "auth", "status", "--json"] if provider == "claude" else [real, "login", "status"]
try:
    status = subprocess.run(status_argv, capture_output=True, text=True, timeout=60, check=False)
    out, text = status.stdout, status.stdout + status.stderr
except (OSError, subprocess.TimeoutExpired):
    out = text = ""
if provider == "claude":
    try:
        data = json.loads(out)  # the whole JSON document (it spans several lines), as check_subscription()
    except ValueError:
        data = {}
    data = data if isinstance(data, dict) else {}
    ok = (data.get("loggedIn") is True and data.get("authMethod") == "claude.ai"
          and data.get("apiProvider") == "firstParty")
    plan = data.get("subscriptionType") or "plan not reported"
    entry["login"] = f"claude.ai subscription ({plan})" if ok else "NOT verified"
else:
    ok = "Logged in using ChatGPT" in text
    entry["login"] = "ChatGPT subscription" if ok else "NOT verified"
if not ok:
    problems.append("the CLI does not report the subscription login")
if time.time() + control["session_timeout"] > control["deadline"]:
    problems.append("the session could not end before the test's deadline")


def supervision():
    """(problems, authorization id): the harness AND the authorized Worker, from the control file
    as it is NOW — a revocation takes effect for every later decision."""
    with open(own["control"], encoding="utf-8") as handle:
        current = json.load(handle)
    found = []
    harness = current.get("harness") or {}
    if not alive(int(harness.get("pid") or 0), harness.get("start_ticks")):
        found.append("the harness that supervises this test is not running")
    if current.get("closed"):
        found.append(f"the harness no longer accepts sessions ({current['closed']})")
    grant = current.get("authorization") or {}
    worker = grant.get("worker") or {}
    if not grant:
        found.append("no Worker is authorized to start sessions")
    elif grant.get("revoked"):
        found.append(f"the Worker authorization {grant.get('id')} was revoked ({grant['revoked']})")
    elif not alive(int(worker.get("pid") or 0), worker.get("start_ticks")):
        found.append(f"the authorized Worker (pid {worker.get('pid')}) is not running")
    elif (service, entry["service_start_ticks"]) != (worker.get("pid"), worker.get("start_ticks")):
        found.append(f"this launch's Worker (pid {service}) is not the authorized Worker (pid {worker.get('pid')})")
    return found, grant.get("id")


with open(control["audit"] + ".lock", "a", encoding="utf-8") as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)  # supervision and budgets: read, decide and log as one step
    found, entry["authorization"] = supervision()
    problems += found
    try:
        with open(control["audit"], encoding="utf-8") as handle:
            earlier = [json.loads(line) for line in handle if line.strip()]
    except FileNotFoundError:
        earlier = []
    started = [e for e in earlier if e.get("decision") == "started"]
    if len(started) >= control["max_sessions"]:
        problems.append(f"session budget exhausted ({len(started)} of {control['max_sessions']})")
    used = sum(e.get("role") == (expected or {}).get("role") for e in started)
    if expected is not None and used >= control["budget"][expected["role"]]:
        problems.append(f"the {expected['role']} session budget is exhausted")
    if any(e.get("step") == step for e in started):
        problems.append(f"step {step!r} already had a session (never a second one)")
    spawned = [e for e in earlier if e.get("decision") == "spawned"]
    living = [e["pid"] for e in started if alive(e["pid"], e["start_ticks"])]
    living += [e["child_pid"] for e in spawned if alive(e["child_pid"], e["child_start_ticks"])]
    others = set(tags) | {e.get("run_tag") for e in started}
    others.discard(entry["run_tag"])
    others.discard(None)
    tagged = [int(p) for p in os.listdir("/proc") if p.isdigit() and int(p) != me
              and environ(int(p)).get("FC_RUN_TAG") in others]
    entry.update(earlier_sessions_alive=living, earlier_tag_processes=tagged, earlier_tags_checked=len(others))
    if living:
        problems.append(f"a process of an earlier session is still alive: {living}")
    if tagged:
        problems.append(f"processes with an earlier run tag of this test are still alive: {tagged}")
    if problems:
        refuse(entry, problems)
    entry["decision"] = "started"
    log(entry)
received = []
for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):  # the child gets the group's signal itself
    signal.signal(sig, lambda signum, _frame: received.append(signum))
child = subprocess.Popen([real, *args], stdin=subprocess.PIPE)
log({"at": time.time(), "decision": "spawned", "pid": me, "step": step, "child_pid": child.pid,
     "child_start_ticks": ticks(child.pid), "run_tag": entry["run_tag"]})
child.communicate(prompt.encode())
code = child.returncode
log({"at": time.time(), "decision": "ended", "pid": me, "step": step, "exit": code, "signals": received,
     "run_tag": entry["run_tag"], "duration": round(time.time() - entry["at"], 1)})
sys.exit(code if code >= 0 else 128 - code)
'''

BOUNDARY_HOOK = r'''"""TEST-ONLY (fc_worker_smoke.py); never part of the coordinator. With
FC_TEST_BOUNDARY_AFTER_STEP and FC_TEST_BOUNDARY_MARKER set, a `python -m feature_coordinator launch` process — once
that step returned checkpointed — holds at the PRODUCTION cancel check before the next step (no
agent of the next step exists): it
writes the marker and waits, bounded by FC_TEST_BOUNDARY_SECONDS, until a cancel request for its
claim exists. Then the production check decides as always."""
import importlib.machinery, json, os, sys, time
_AFTER = os.environ.get("FC_TEST_BOUNDARY_AFTER_STEP", "")
_MARKER = os.environ.get("FC_TEST_BOUNDARY_MARKER", "")
_SECONDS = float(os.environ.get("FC_TEST_BOUNDARY_SECONDS", "600") or 600)
_STATE = {"armed": None, "done": False}

def _patch(module):
    cls = module.FeatureCoordinator
    run_step, cancel = cls._run_step, cls._cancel_requested
    def run_step_then_arm(self, feature, task, claim, mark, coincident, step, record, progress):
        run = run_step(self, feature, task, claim, mark, coincident, step, record, progress)
        if step.key == _AFTER and not _STATE["done"] and run.state.name == "IN_PROGRESS":
            _STATE["armed"] = step.key
        return run
    def held_cancel_check(self, task_id, token):
        if _STATE["armed"]:
            after, _STATE["armed"], _STATE["done"] = _STATE["armed"], None, True
            with open(_MARKER + ".tmp", "w", encoding="utf-8") as handle:
                json.dump({"pid": os.getpid(), "task": task_id, "after_step": after, "at": time.time()}, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(_MARKER + ".tmp", _MARKER)
            deadline = time.monotonic() + _SECONDS
            while time.monotonic() < deadline and not cancel(self, task_id, token):
                time.sleep(0.1)
        return cancel(self, task_id, token)
    cls._run_step, cls._cancel_requested = run_step_then_arm, held_cancel_check

class _Finder:
    @staticmethod
    def find_spec(name, path=None, target=None):
        if name != "feature_coordinator.workflow":
            return None
        spec = importlib.machinery.PathFinder.find_spec(name, path)
        if spec is not None and spec.loader is not None:
            run = spec.loader.exec_module
            def exec_module(module):
                run(module)
                _patch(module)
            spec.loader.exec_module = exec_module
        return spec

if _AFTER and _MARKER:
    sys.meta_path.insert(0, _Finder())
'''

SERVICE_LAUNCHER = (  # becomes the service (exec): authorized first; SIGTERM (graceful stop) when the harness dies
    "import ctypes, os, signal, sys, time\n"
    "harness, go, die = int(sys.argv[1]), sys.argv[2], sys.argv[3] == '1'\n"
    "if die and ctypes.CDLL(None, use_errno=True).prctl(1, signal.SIGTERM) != 0:\n"
    "    sys.exit(75)  # PR_SET_PDEATHSIG failed: never start a service that could outlive the harness\n"
    "deadline = time.monotonic() + 30\n"
    "while not os.path.exists(go) and os.getppid() == harness and time.monotonic() < deadline:\n"
    "    time.sleep(0.02)  # the harness records this process's authorization, then creates *go*\n"
    "if not os.path.exists(go) or os.getppid() != harness:\n"
    "    sys.exit(75)  # not authorized, or the harness is gone: never start unwatched\n"
    "os.execv(sys.argv[4], sys.argv[4:])\n")

GH_SHIM = '''#!/bin/sh
# W4.2 smoke test: GitHub is out of scope. Every call is logged (subcommand only) and refused.
printf '%s %s %s\\n' "$(date +%s)" "$1" "$2" >> "@LOG@"
echo "gh is disabled in the W4.2 smoke test" >&2
exit 97
'''


def _stat(pid: int) -> list[str]:
    with open(f"/proc/{pid}/stat", encoding="ascii") as handle:
        return handle.read().rsplit(")", 1)[1].split()


def _environ(pid: int) -> dict[str, str]:
    try:
        with open(f"/proc/{pid}/environ", "rb") as handle:
            raw = handle.read()
    except OSError:
        return {}
    return dict(item.decode(errors="replace").split("=", 1) for item in raw.split(b"\0") if b"=" in item)


def _ancestors() -> set[int]:
    pids, pid = set(), os.getpid()
    while pid > 1:
        pids.add(pid)
        try:
            pid = int(_stat(pid)[1])
        except (OSError, ValueError, IndexError):
            break
    return pids


def sha256_file(path: Path) -> str:
    """SHA-256 of a file, streamed."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


class Monitor(threading.Thread):
    """Samples /proc: processes carrying a run tag of this test (from runs/*/native.json) and the
    service's launch processes. Records first/last sight per tag and the largest concurrency."""

    def __init__(self, state: Path, workdir: Path, interval: float) -> None:
        super().__init__(daemon=True)
        self.state, self.workdir, self.interval = state, str(workdir), interval
        self.halt = threading.Event()
        self.tags: dict[str, str] = {}  # run tag → run directory name
        self.seen: dict[str, dict[str, Any]] = {}
        self.max_tags = self.max_launches = self.samples = 0
        self.overlaps: list[dict[str, Any]] = []

    def _known(self) -> None:
        for path in (self.state / "runs").glob("*/native.json"):
            if path.parent.name not in self.tags.values():
                try:
                    self.tags[json.loads(path.read_text(encoding="utf-8"))["run_tag"]] = path.parent.name
                except (OSError, ValueError, KeyError):
                    continue

    def sample(self) -> tuple[dict[str, list[int]], list[int]]:
        """(tag → live pids, live launch pids) now."""
        self._known()
        alive: dict[str, list[int]] = {}
        launches = []
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            env = _environ(int(entry))
            tag = env.get("FC_RUN_TAG")
            if tag in self.tags:
                alive.setdefault(tag, []).append(int(entry))
            if env.get("FC_WORKER_LAUNCH"):
                try:
                    cmd = Path(f"/proc/{entry}/cmdline").read_bytes()
                except OSError:
                    continue
                if self.workdir.encode() in cmd and b" launch " in cmd.replace(b"\0", b" "):
                    launches.append(int(entry))
        return alive, launches

    def run(self) -> None:
        while not self.halt.is_set():
            now = time.time()
            alive, launches = self.sample()
            for tag, pids in alive.items():
                seen = self.seen.setdefault(tag, {"run": self.tags.get(tag), "first": now, "pids": []})
                seen["last"] = now
                seen["pids"] = sorted(set(seen["pids"]) | set(pids))
            if len(alive) > 1:
                self.overlaps.append({"at": now, "tags": sorted(self.tags.get(t, t) for t in alive)})
            self.max_tags = max(self.max_tags, len(alive))
            self.max_launches = max(self.max_launches, len(launches))
            self.samples += 1
            self.halt.wait(self.interval)


class HealthSampler(threading.Thread):
    """`python -m feature_coordinator worker health --json` (read-only) every *interval* seconds, compacted."""

    def __init__(self, harness: "Harness", interval: float) -> None:
        super().__init__(daemon=True)
        self.harness, self.interval = harness, interval
        self.halt = threading.Event()
        self.samples: list[dict[str, Any]] = []

    def run(self) -> None:
        while not self.halt.is_set():
            result = self.harness.feature("worker", "health", "--json", timeout=60)
            try:
                data = json.loads(result.stdout)
            except ValueError:
                data = {"state": "UNREADABLE"}
            current = data.get("current") or {}
            self.samples.append({
                "at": time.time(), "exit": result.returncode, "state": data.get("state"),
                "diagnosis": data.get("diagnosis"), "healthy": data.get("healthy"), "alive": data.get("alive"),
                "heartbeat_age": data.get("heartbeat_age_seconds"),
                "current": {k: current.get(k) for k in ("task", "role", "agent", "model", "step", "launch")}
                if current else None,
                "agent_process": bool(current.get("agent_process")),
                "last_checkpoint": data.get("last_checkpoint"),
                "tasks": {t["task"].split(".", 1)[-1]: t.get("status") for t in data.get("tasks", ())},
                "claims": [t["task"].split(".", 1)[-1] for t in data.get("tasks", ()) if t.get("claim")],
                "waiting": [(t["task"].split(".", 1)[-1], (t.get("waiting") or {}).get("category"))
                            for t in data.get("waiting", ())],
                "attention": [(t["task"].split(".", 1)[-1], (t.get("attention") or {}).get("category"))
                              for t in data.get("attention", ())],
                "claim_heartbeat_age": [t["claim"].get("heartbeat_age_seconds") for t in data.get("tasks", ())
                                        if t.get("claim")]})
            self.halt.wait(self.interval)


class Harness:  # pylint: disable=too-many-instance-attributes,too-many-public-methods
    """One run of the W4.2 scenario in *workdir* (a new directory)."""

    def __init__(self, workdir: Path, agents: dict[str, Path], *, fake: bool,  # pylint: disable=too-many-arguments
                 echo: Callable[[str], None] = print, session_timeout: int = SESSION_TIMEOUT,
                 total_seconds: int = TOTAL_SECONDS, health_interval: float = 1.0, die_with_harness: bool = True,
                 ) -> None:
        self.dir = workdir
        self.repo, self.state = workdir / "repo", workdir / "state"
        self.audit_dir, self.reports = workdir / "audit", workdir / "reports"
        self.audit = self.audit_dir / "agent-invocations.jsonl"
        self.marker = self.audit_dir / "boundary.json"
        self.gh_log = self.audit_dir / "github-calls.log"
        self.bin, self.config = workdir / "bin", workdir / "config.yaml"
        self.agents, self.fake, self.echo = agents, fake, echo
        self.session_timeout, self.total_seconds, self.health_interval = session_timeout, total_seconds, health_interval
        self.deadline = 0.0
        self.checks: list[dict[str, Any]] = []
        self.report: dict[str, Any] = {"workdir": str(workdir), "agents": "fake" if fake else "real",
                                       "max_sessions": MAX_SESSIONS}
        self.services: list[dict[str, Any]] = []
        self.proc: subprocess.Popen[bytes] | None = None
        self.prepared = False
        self.grants: list[dict[str, Any]] = []  # every Worker authorization, with its revocation
        self.phase = "prepare"
        self.die_with_harness = die_with_harness  # False ONLY in the test of the wrapper's harness check
        self.monitor = Monitor(self.state, workdir, 0.1 if fake else 0.25)
        self.health = HealthSampler(self, health_interval)

    # -- plumbing ------------------------------------------------------------------------------

    def check(self, name: str, ok: bool, detail: Any = "") -> bool:
        """Record one verification; returns *ok*. Its kind: `preflight` (before any session), `safety`
        (must hold whatever happened) or `outcome` (the planned work)."""
        kind = "preflight" if self.phase == "preflight" else "safety" if name.startswith(SAFETY_CHECKS) else "outcome"
        self.checks.append({"check": name, "ok": bool(ok), "kind": kind, "detail": detail})
        self.echo(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))
        return bool(ok)

    def git(self, *args: str) -> str:
        """git in the disposable repository."""
        return subprocess.run(["git", "-C", str(self.repo), *args], capture_output=True, text=True,
                              check=True).stdout.strip()

    def env(self, **extra: str) -> dict[str, str]:
        """The service's environment: the gh shim first on PATH, this checkout's virtual environment
        off PATH (agents get the system python3), no test variables, no API-key-like variables."""
        venv = str(Path(sys.executable).parent)
        path = [str(self.bin / "shim")] + [p for p in os.environ.get("PATH", "").split(os.pathsep)
                                          if p and p != venv and not p.startswith(str(REPO_ROOT / ".venv"))]
        env = {k: v for k, v in os.environ.items() if not k.startswith(("FC_TEST_", "PYTHONPATH", "VIRTUAL_ENV"))
               and not k.startswith(("ANTHROPIC_", "OPENAI_", "CODEX_API", "CLAUDE_CODE_USE_"))}
        env["PATH"] = os.pathsep.join(path)
        env.update(extra)
        return env

    def feature(self, *args: str, timeout: float = 120, env: dict[str, str] | None = None,
                ) -> subprocess.CompletedProcess[str]:
        """`python -m feature_coordinator` on the disposable repository and state (a new process)."""
        return subprocess.run([*CLI, "--repo", str(self.repo), "--state-dir",
                               str(self.state), "--project-config", str(self.config), *args],
                              capture_output=True, text=True, timeout=timeout, env=env or self.env(), check=False,
                              cwd=REPO_ROOT)

    def invocations(self) -> list[dict[str, Any]]:
        """The wrapper's audit records."""
        if not self.audit.exists():
            return []
        return [json.loads(line) for line in self.audit.read_text(encoding="utf-8").splitlines() if line.strip()]

    def sessions(self) -> list[dict[str, Any]]:
        """Invocations handed to the real CLI, with their end record merged in."""
        every = self.invocations()
        ends = {e["run_tag"]: e for e in every if e["decision"] == "ended"}
        spawns = {e["run_tag"]: e for e in every if e["decision"] == "spawned"}
        out = []
        for entry in (e for e in every if e["decision"] == "started"):
            end, spawn = ends.get(entry["run_tag"], {}), spawns.get(entry["run_tag"], {})
            out.append({**entry, "ended_at": end.get("at"), "exit": end.get("exit"), "signals": end.get("signals"),
                        "child_pid": spawn.get("child_pid"), "child_start_ticks": spawn.get("child_start_ticks")})
        return out

    def store(self) -> StateStore:
        """The disposable state directory."""
        return StateStore(self.state)

    def task_id(self, key: str) -> str:
        """`<feature>.<key>`."""
        return f"{FEATURE}.{key}"

    def processes(self) -> list[str]:
        """Command lines of live processes naming this run's directory: not this harness, its
        ancestors or its own short-lived helpers (the read-only `python -m feature_coordinator worker health/status`
        calls it runs itself); a service the harness started always counts."""
        found, own, me = [], _ancestors(), os.getpid()
        services = {s["pid"] for s in self.services}
        for entry in os.listdir("/proc"):
            if not entry.isdigit() or int(entry) in own:
                continue
            try:
                cmdline = Path(f"/proc/{entry}/cmdline").read_bytes()
                fields = _stat(int(entry))
                if fields[0] == "Z" or (int(fields[1]) == me and int(entry) not in services):
                    continue
            except (OSError, IndexError, ValueError):
                continue
            if str(self.dir).encode() in cmdline:
                found.append(f"{entry}: " + cmdline.replace(b"\0", b" ").decode(errors="replace")[:200])
        return found

    @staticmethod
    def wait(predicate: Callable[[], bool], timeout: float, interval: float = 0.2) -> bool:
        """Poll *predicate* until true or *timeout* seconds (bounded; never a fixed delay)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(interval)
        return predicate()

    def classification(self) -> dict[str, Any]:
        """PRIMARY failure (the first thing that went wrong, from the evidence), SECONDARY consequences
        (planned work that therefore never happened), other findings (failed observations not explained
        by it) and INDEPENDENT safety violations; the verdict."""
        failed = [c for c in self.checks if not c["ok"]]
        preflight = [c["check"] for c in failed if c.get("kind") == "preflight"]
        safety = [c["check"] for c in failed if c.get("kind") == "safety"]
        outcome = [c["check"] for c in failed if c.get("kind") == "outcome"]
        primary = self.primary_failure(preflight)
        secondary = [c for c in outcome if primary and c.startswith(COMPLETION_CHECKS)]
        verdict = ("BLOCKED" if preflight or safety else "PARTIALLY VERIFIED" if outcome or primary else "VERIFIED")
        return {"verdict": verdict, "primary_failure": primary, "secondary_consequences": secondary,
                "other_findings": [c for c in outcome if c not in secondary], "safety_violations": safety,
                "preflight_failures": preflight}

    def primary_failure(self, preflight: list[str]) -> dict[str, Any] | None:
        """The earliest failure in the run's evidence (events, audit refusals, service exits)."""
        if preflight:
            return {"source": "preflight", "reason": preflight[0], "cause": "the environment is not ready: no session "
                    "was started"}
        found: list[tuple[float, str, str, str]] = []
        path = self.state / "events.jsonl"
        for line in path.read_text(encoding="utf-8").splitlines() if path.exists() else ():
            event = json.loads(line)
            if event["kind"] == "task-finished" and event.get("state") in ("failed", "needs-human"):
                found.append((_epoch(event["at"]), "task", str(event.get("task")), str(event.get("reason"))))
        for entry in self.invocations():
            if entry["decision"] == "refused":
                found.append((entry["at"], "audit wrapper", str(entry.get("task")), "; ".join(entry["problems"])))
        for service in self.services:
            if service.get("exit_kind") == "FAILED_EXECUTION":
                found.append((service.get("ended") or service["started"], "service", f"service {service['number']}",
                              f"exit {service.get('exit')}, {service.get('stop_reason')}"))
        if not found:
            return None
        at, source, subject, reason = min(found)
        return {"at": at, "source": source, "subject": subject, "reason": scrub(reason, 600),
                "cause": _cause(source, reason)}

    def update_control(self, **changes: Any) -> None:
        """Change the wrapper's control file atomically (a wrapper never reads half a file)."""
        path = self.audit_dir / "control.json"
        control = json.loads(path.read_text(encoding="utf-8"))
        control.update(changes)
        Path(f"{path}.tmp").write_text(json.dumps(control, indent=2), encoding="utf-8")
        os.replace(f"{path}.tmp", path)

    def exit_kind(self, service: dict[str, Any]) -> tuple[str, str | None]:
        """(NATURAL_IDLE_EXIT | OPERATOR_STOP | FAILED_EXECUTION, the last state before stopping) from the
        service's own log — never from its exit code alone."""
        path = self.audit_dir / f"service-{service['number']}.log"
        log = path.read_text(encoding="utf-8") if path.exists() else ""
        stopped = re.findall(r"stopped \((.+)\)\s*$", log, re.MULTILINE)
        states = [s for s in re.findall(r": ([A-Z_]+): ", log) if s not in ("OFFLINE", "STOPPING")]
        final = states[-1] if states else None
        if service.get("exit") != 0 or not stopped:
            kind = "FAILED_EXECUTION"
        elif stopped[-1] == "stop condition reached":
            kind = "NATURAL_IDLE_EXIT"
        elif stopped[-1].startswith("operator stop") or stopped[-1] == "stop signal":
            kind = "OPERATOR_STOP"
        else:
            kind = "FAILED_EXECUTION"
        service.update(exit_kind=kind, final_state=final, stop_reason=stopped[-1] if stopped else None)
        return kind, final

    def revoke(self, why: str) -> None:
        """Revoke the current Worker authorization (idempotent); later wrapper decisions see it."""
        grant = json.loads((self.audit_dir / "control.json").read_text(encoding="utf-8")).get("authorization")
        if grant and not grant.get("revoked"):
            revoked = {**grant, "revoked": why, "revoked_at": time.time()}
            self.update_control(authorization=revoked)
            for known in self.grants:
                if known["id"] == grant["id"]:
                    known.update(revoked=why, revoked_at=revoked["revoked_at"])

    def started_steps(self) -> list[str]:
        """The steps that got a session so far."""
        return [e["step"] for e in self.invocations() if e["decision"] == "started"]

    def branch_commits(self) -> list[str]:
        """The feature branch's commits after the seed, oldest first."""
        return self.git("rev-list", "--reverse", f"{self.report['base']}..{BRANCH}").split()

    def read_only_commands(self) -> dict[str, subprocess.CompletedProcess[str]]:
        """`worker status`, `worker health --json`, `worker waiting`, `worker attention`."""
        return {cmd: self.feature("worker", *cmd.split())
                for cmd in ("status", "health --json", "waiting", "attention")}

    def snapshot(self) -> dict[str, bytes]:
        """Every file of the state directory (read-only checks compare two snapshots)."""
        return {str(p.relative_to(self.state)): p.read_bytes() for p in sorted(self.state.rglob("*")) if p.is_file()}

    # -- preparation ----------------------------------------------------------------------------

    def prepare(self) -> None:
        """Disposable repository (no remote), configuration, spec, wrappers, gh shim, hook; activate."""
        import yaml  # pylint: disable=import-outside-toplevel
        if self.repo.exists() or self.state.exists() or self.audit_dir.exists():
            raise FileExistsError(f"{self.dir} was used before: every run uses a new directory")
        for sub in (self.dir, self.bin / "shim", self.dir / "hooks", self.audit_dir, self.reports):
            sub.mkdir(parents=True, exist_ok=True)
        self.prepared = True  # from here on, a report is always written
        self.dir.chmod(0o700)  # transcripts stay private (run directories keep full agent output)
        init_demo_repo(self.repo, SEED)
        hook = self.repo / ".git" / "hooks" / "pre-push"
        hook.write_text("#!/bin/sh\necho 'the W4.2 disposable repository is never pushed' >&2\nexit 1\n",
                        encoding="utf-8")
        hook.chmod(0o755)
        control = {"repo": os.path.realpath(self.repo), "state": str(self.state), "audit": str(self.audit),
                   "max_sessions": MAX_SESSIONS, "session_timeout": self.session_timeout, "deadline": 0,
                   "budget": {t["role"]: len(t["steps"]) for t in TASKS.values()},
                   "steps": {s: {"task": self.task_id(k), "role": t["role"], "provider": t["provider"],
                                 "model": t["model"]} for k, t in TASKS.items() for s in t["steps"]}}
        (self.audit_dir / "control.json").write_text(json.dumps(control, indent=2), encoding="utf-8")
        binaries = {}
        for provider, real in self.agents.items():
            wrapper = self.bin / f"{provider}-audit"
            wrapper.write_text(AUDIT_WRAPPER, encoding="utf-8")
            wrapper.chmod(0o755)
            binaries[provider] = {"real": str(real), "sha256": sha256_file(real)}
            Path(f"{wrapper}.json").write_text(json.dumps({"provider": provider, "control": str(
                self.audit_dir / "control.json"), **binaries[provider]}, indent=2), encoding="utf-8")
        self.report["binaries"] = binaries
        shim = self.bin / "shim" / "gh"
        shim.write_text(GH_SHIM.replace("@LOG@", str(self.gh_log)), encoding="utf-8")
        shim.chmod(0o755)
        (self.dir / "hooks" / "sitecustomize.py").write_text(BOUNDARY_HOOK, encoding="utf-8")
        data = yaml.safe_load(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
        for provider in self.agents:  # routing (role → provider, model) stays exactly as configured
            data["providers"][provider]["executable"] = str(self.bin / f"{provider}-audit")
        data["gates"].update(GATES)
        data["runtime"].update(execution_backend="native-cli", task_timeout_seconds=self.session_timeout,
                               claude_max_budget_usd=CLAUDE_BUDGET_USD)
        worker = data["worker"]
        worker["autonomous"]["enabled"] = True
        worker["max_launches_per_task"] = 2  # Backend: the launch stopped at the boundary + the resumed one
        worker["retry"]["transient"]["max_attempts"] = 0  # no automatic retry of a failed session
        worker["retry"]["subscription"].update(recheck_interval_seconds=3600, max_rechecks=1)  # WAITING, no recheck
        worker["retry"]["validation"]["auto_fix_enabled"] = False
        self.config.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        spec = self.dir / "feature.yaml"
        spec.write_text(yaml.safe_dump({"slug": FEATURE, "title": "W4.2 autonomous Worker real-agent smoke test",
                                        "test": True, "tasks": [
                                            {"key": key, "role": t["role"], "title": t["title"],
                                             "required_gates": [t["gate"]], "acceptance": [t["acceptance"]],
                                             "depends_on": list(t["depends_on"]),
                                             "steps": [{"key": s, "title": step_title(s)} for s in t["steps"]]}
                                            for key, t in TASKS.items()]}, sort_keys=False), encoding="utf-8")
        for step in (("plan", str(spec), "--apply"), ("activate", FEATURE, "--apply")):
            result = self.feature(*step)
            if result.returncode != 0:
                raise RuntimeError(f"{' '.join(step)} failed: {result.stdout}{result.stderr}")
        self.report["base"] = self.git("rev-parse", "dev")

    def preflight(self) -> bool:
        """Before any session: the fixture, the routing, the subscriptions, nothing running."""
        self.phase = "preflight"
        from feature_coordinator.config import load_config  # pylint: disable=import-outside-toplevel
        from feature_coordinator.native_backend import (  # pylint: disable=import-outside-toplevel
            check_subscription, native_environment)
        config = load_config(self.config)
        ok = self.check("the disposable repository has no remote", self.git("remote") == "")
        ok &= self.check("the disposable repository is outside the source checkout",
                         not str(self.repo.resolve()).startswith(str(REPO_ROOT.resolve()) + os.sep))
        routing = {k: (config.role(t["role"]).provider, config.model_for(t["role"])) for k, t in TASKS.items()}
        ok &= self.check("routing: backend → claude opus, cli → claude sonnet, frontend → codex (CLI default)",
                         routing == {k: (t["provider"], t["model"]) for k, t in TASKS.items()}, routing)
        runtime, worker = config.runtime, config.worker
        self.report["limits"] = {"task_timeout_seconds": runtime.task_timeout_seconds,
                                 "terminate_grace_seconds": runtime.terminate_grace_seconds,
                                 "stale_heartbeat_seconds": runtime.stale_heartbeat_seconds,
                                 "max_launches_per_task": worker.max_launches_per_task,
                                 "transient_max_attempts": worker.transient_max_attempts,
                                 "subscription_max_rechecks": worker.subscription_max_rechecks,
                                 "subscription_recheck_interval_seconds": worker.subscription_recheck_interval_seconds,
                                 "validation_auto_fix_enabled": worker.validation_auto_fix_enabled,
                                 "max_parallel": worker.max_parallel,
                                 "claude_max_budget_usd": runtime.claude_max_budget_usd}
        ok &= self.check("bounded configuration: one agent at a time, no automatic retry or auto-fix",
                         worker.max_parallel == 1 and worker.transient_max_attempts == 0
                         and not worker.validation_auto_fix_enabled
                         and runtime.task_timeout_seconds == self.session_timeout)
        logins = {}
        for provider in self.agents:
            profile = config.providers[provider]
            logins[provider] = check_subscription(profile.kind, str(self.bin / f"{provider}-audit"),
                                                  native_environment()).method
        self.report["logins"] = logins
        ok &= self.check("subscription logins (each CLI's own status command; no model request)",
                         logins["claude"].startswith("claude.ai subscription")
                         and logins["codex"] == "ChatGPT subscription",
                         logins)
        tasks = {k: self.store().load_task(self.task_id(k)).state.value for k in TASKS}
        ok &= self.check("Backend and Frontend ready together; CLI not ready before Backend is verified",
                         tasks["backend"] == "ready" and tasks["frontend"] == "ready" and tasks["cli"] != "ready",
                         tasks)
        ok &= self.check("no claim, no session, no process of this test yet",
                         not self.store().list_claims() and not self.sessions() and not self.processes())
        probe = subprocess.run([sys.executable, "-c", GATE_PROBE, str(self.config), str(self.repo),
                                str(self.audit_dir / "gate-probe")], capture_output=True, text=True, timeout=120,
                               env=self.env(PYTHONPATH=str(REPO_ROOT)), check=False)
        try:
            gates = json.loads(probe.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            gates = {"probe": {"status": "error", "summary": (probe.stderr or probe.stdout)[-300:]}}
        self.report["gate_probe"] = gates
        return self.check("the gate sandbox works in the services' environment (bubblewrap creates namespaces; "
                          "python3 and node run inside it) — else no session is started",
                          all(g.get("status") == "passed" for g in gates.values()), gates) and ok

    # -- the services --------------------------------------------------------------------------------

    def start_service(self, number: int, **extra: str) -> subprocess.Popen[bytes]:
        """`python -m feature_coordinator worker start --exit-when-idle` (the real CLI entry point) as a subprocess."""
        log = open(self.audit_dir / f"service-{number}.log", "wb")  # pylint: disable=consider-using-with
        go = self.audit_dir / f"service-{number}.authorized"
        die = "1" if self.die_with_harness else "0"
        argv = [sys.executable, "-c", SERVICE_LAUNCHER, str(os.getpid()), str(go), die,
                *CLI, "--repo", str(self.repo), "--state-dir", str(self.state),
                "--project-config", str(self.config), "worker", "start", "--exit-when-idle"]
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log,  # pylint: disable=consider-using-with
                                stderr=subprocess.STDOUT, env=self.env(**extra), start_new_session=True,
                                cwd=REPO_ROOT)
        log.close()
        self.services.append({"number": number, "pid": proc.pid, "started": time.time()})
        self.proc = proc
        start = _ticks(proc.pid)  # the launcher waits for *go*: this is the service's own identity
        if start is None:
            raise RuntimeError(f"service {number} ended before it was authorized")
        grant = {"id": secrets.token_hex(4), "service": number, "worker": {"pid": proc.pid, "start_ticks": start},
                 "granted_at": time.time()}
        self.grants.append(grant)
        self.update_control(authorization=grant)
        go.touch()
        self.echo(f"service {number} started (pid {proc.pid})")
        return proc

    def unexpected(self) -> tuple[str, bool]:
        """(why, graceful) for a condition on which the harness stops the service ('' if none). A
        safety violation or the time limit cancels the active agent (graceful stop); otherwise the
        service stops after the active task, so a live session is never interrupted."""
        if self.monitor.max_tags > 1:
            return "two agents' run tags were alive at the same time", True
        if time.time() > self.deadline:
            return "the test's time limit was reached", True
        refused = [e for e in self.invocations() if e["decision"] == "refused"]
        if refused:
            return f"the audit wrapper refused an invocation: {refused[-1].get('problems')}", False
        latest = self.health.samples[-1] if self.health.samples else {}
        if latest.get("state") in ("WAITING", "NEEDS_ATTENTION") or latest.get("waiting") or latest.get("attention"):
            return (f"the service reports {latest.get('state')} (waiting {latest.get('waiting')}, attention "
                    f"{latest.get('attention')}): no further session is started"), False
        return "", False

    def stop_service(self, proc: subprocess.Popen[bytes], why: str, graceful: bool = True) -> int | None:
        """`python -m feature_coordinator worker stop` (graceful: the active agent is cancelled) or `--after-current`;
        bounded wait. The harness never kills an agent."""
        self.revoke(why)  # first: no new session from now on, whatever the service still does
        self.echo(f"stopping the service ({'graceful' if graceful else 'after the active task'}): {why}")
        stopped = self.feature("worker", "stop", *(() if graceful else ("--after-current",)))
        self.report.setdefault("stops", []).append({"at": time.time(), "why": why, "graceful": graceful,
                                                    "output": stopped.stdout.strip()})
        try:
            return proc.wait(timeout=self.session_timeout + 120)
        except subprocess.TimeoutExpired:
            self.check("the service exited after `worker stop`", False, "still running; left for the operator")
            return None

    def run_service(self, proc: subprocess.Popen[bytes], *, boundary: bool) -> int | None:
        """Wait for the service to exit, the boundary marker (service 1) or an unexpected condition."""
        while True:
            code = proc.poll()
            if code is not None:
                self.revoke(f"service {self.services[-1]['number']} exited ({code})")
                return code
            if boundary and self.marker.exists():
                return None
            why, graceful = self.unexpected()
            if why:
                self.report.setdefault("unexpected", []).append(why)
                return self.stop_service(proc, why, graceful)
            time.sleep(0.2)

    def service1(self) -> bool:
        """Backend step 1, then the controlled stop at the boundary before step 2."""
        proc = self.start_service(1, PYTHONPATH=str(self.dir / "hooks"), FC_TEST_BOUNDARY_AFTER_STEP=BOUNDARY_AFTER,
                                  FC_TEST_BOUNDARY_MARKER=str(self.marker),
                                  FC_TEST_BOUNDARY_SECONDS=str(self.session_timeout))
        code = self.run_service(proc, boundary=True)
        self.services[-1].update(exit=code, ended=time.time() if code is not None else None)
        if not self.check("service 1 reached the boundary after Backend step 1", self.marker.exists(),
                          (self.audit_dir / "service-1.log").read_text(encoding="utf-8")[-2000:]):
            if proc.poll() is None:
                self.services[-1]["exit"] = self.stop_service(proc, "the boundary was not reached")
            return False
        boundary = json.loads(self.marker.read_text(encoding="utf-8"))
        record = load_record(self.store(), self.task_id("backend"))
        claim = self.store().load_claim(self.task_id("backend")) or {}
        sessions = self.sessions()
        alive, launches = self.monitor.sample()
        snap = {"boundary": boundary, "claim_pid": (claim.get("identity") or {}).get("pid"),
                "checkpoints": [(c.id, c.sequence, c.step, c.status, c.commit) for c in (record.checkpoints
                                                                                          if record else ())],
                "sessions": [(s["step"], s["exit"]) for s in sessions], "tagged_alive": alive,
                "launches": launches, "commits": self.branch_commits()}
        self.report["at_boundary"] = snap
        ok = self.check("boundary: checkpoint #1 (`module`) committed, nothing else",
                        [(c[1], c[2], c[3]) for c in snap["checkpoints"]] == [(1, "module", "committed")]
                        and snap["commits"] == [snap["checkpoints"][0][4]], snap["checkpoints"])
        ok &= self.check("boundary: exactly one session so far (`module`), ended with exit 0",
                         snap["sessions"] == [("module", 0)], snap["sessions"])
        ok &= self.check("boundary: no agent process alive (no run-tagged process), one launch holding the claim",
                         not alive and launches == [boundary["pid"]] and snap["claim_pid"] == boundary["pid"],
                         (alive, launches, snap["claim_pid"]))
        before = len(self.invocations())
        code = self.stop_service(proc, "controlled restart at the boundary between Backend steps 1 and 2")
        self.services[-1].update(exit=code, ended=time.time())
        kind, final = self.exit_kind(self.services[-1])
        ok &= self.check("service 1 ended by the operator stop at the boundary (OPERATOR_STOP)",
                         kind == "OPERATOR_STOP", (kind, final, code))
        ok &= self.check("the stop started no session (only the production cancel check ran)",
                         [e for e in self.invocations()[before:] if e["decision"] != "passthrough"] == [],
                         self.invocations()[before:])
        return ok

    def between(self) -> bool:
        """Nothing runs; status and health are read-only and report the service as stopped."""
        self.wait(lambda: not self.processes(), 60)  # detached board publishers end on their own
        backend = self.store().load_task(self.task_id("backend"))
        sched = load_scheduling(self.store(), backend)
        before, calls = self.snapshot(), len(self.invocations())
        outputs = self.read_only_commands()
        health = json.loads(outputs["health --json"].stdout)
        self.report["between"] = {"backend_state": backend.state.value, "status": outputs["status"].stdout,
                                  "health_state": health.get("state"), "health_alive": health.get("alive"),
                                  "health_exit": outputs["health --json"].returncode,
                                  "scheduling": {"launches": sched.launches, "attempts": sched.attempts,
                                                 "attention": sched.attention}}
        ok = self.check("between the services: status/health/waiting/attention changed nothing, ran no CLI",
                        self.snapshot() == before and len(self.invocations()) == calls)
        ok &= self.check("between the services: health reports the service stopped, not running or healthy",
                         health.get("state") == "OFFLINE" and health.get("alive") is False
                         and outputs["health --json"].returncode == 1, (health.get("state"), health.get("alive")))
        record = load_record(self.store(), backend.id)
        found = {"processes": self.processes(), "claims": sorted(self.store().list_claims()),
                 "backend": backend.state.value, "done": record.done_steps() if record else None}
        ok &= self.check("between the services: no process, no claim; Backend ready again with checkpoint #1",
                         found == {"processes": [], "claims": [], "backend": "ready", "done": ["module"]}, found)
        return ok

    def service2(self) -> bool:
        """A NEW service: resumes Backend at step 2, then CLI and Frontend; exits when idle."""
        proc = self.start_service(2)
        code = self.run_service(proc, boundary=False)
        self.services[-1].update(exit=code, ended=time.time())
        kind, final = self.exit_kind(self.services[-1])
        return self.check("service 2 ended by itself when idle with all work done (NATURAL_IDLE_EXIT after "
                          "AVAILABLE) — an exit code 0 alone proves nothing", (kind, final) == (
                              "NATURAL_IDLE_EXIT", "AVAILABLE"), (kind, final, code, self.report.get("unexpected")))

    # -- verification ----------------------------------------------------------------------------------

    def section(self, name: str, func: Callable[[], bool]) -> bool:
        """One group of checks; an error inside it is a failed check, never a lost report."""
        try:
            return func()
        except Exception as exc:  # pylint: disable=broad-except
            return self.check(f"{name}: could not be evaluated", False, f"{type(exc).__name__}: {exc}")

    def verify(self) -> bool:
        """A–L (see the module docstring and the W4.2 acceptance list). Every group runs, also after
        a failure, so a partial run still yields a complete report."""
        self.wait(lambda: not self.processes(), 60)
        ctx: dict[str, Any] = {"sessions": self.sessions(), "every": self.invocations(), "runs": {}}
        for path in sorted((self.state / "runs").glob("*/native.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            ctx["runs"][data["run_tag"]] = {"dir": path.parent, "model": data["model"],
                                            "auth": data["authentication"], "backend": data["backend"]}
        ok = True
        for name, func in (("A/B sessions and routing", self.verify_sessions),
                           ("C concurrency", self.verify_concurrency),
                           ("D/G/H checkpoints and Git", self.verify_checkpoints),
                           ("E dependencies", self.verify_dependencies), ("F recovery", self.verify_recovery),
                           ("H/I tasks and lifecycle", self.verify_tasks),
                           ("health", lambda c: self.health_checks(c["sessions"])),
                           ("J cleanup", lambda c: self.cleanup_checks(c["sessions"])),
                           ("K/L subscription and artifacts", self.verify_auth_and_artifacts)):
            ok &= self.section(name, lambda f=func: f(ctx))
        return ok

    def verify_sessions(self, ctx: dict[str, Any]) -> bool:
        """A. invocation count; B. routing and model evidence."""
        sessions, every, runs = ctx["sessions"], ctx["every"], ctx["runs"]
        self.report["invocations"] = [
            {"step": s["step"], "task": s["task"], "role": s["role"], "provider": s["provider"], "model": s["model"],
             "login": s.get("login"), "start": s["at"], "end": s["ended_at"], "exit": s["exit"], "pid": s["pid"],
             "start_ticks": s["start_ticks"], "child_pid": s["child_pid"], "run_tag": s["run_tag"],
             "service_pid": s["service_pid"], "launch_pid": s["launch_pid"]} for s in sessions]
        self.report["passthrough_calls"] = sum(e["decision"] == "passthrough" for e in every)
        ok = self.check("A. exactly four sessions, one per step, none refused, every one ended with exit 0",
                        [s["step"] for s in sessions] == ["module", "validation-test", "cli-utility", "ts-utility"]
                        and not [e for e in every if e["decision"] == "refused"]
                        and all(s["exit"] == 0 and s["ended_at"] for s in sessions),
                        [(s["step"], s["exit"]) for s in sessions])
        grant_of = {g["worker"]["pid"]: g for g in self.grants}
        ok &= self.check("A. every session ran under the unrevoked authorization of the Worker that launched it",
                         all(s.get("authorization") == grant_of.get(s["service_pid"], {}).get("id")
                             and grant_of[s["service_pid"]]["granted_at"] <= s["at"]
                             < (grant_of[s["service_pid"]].get("revoked_at") or float("inf"))
                             for s in sessions), [(s["step"], s.get("authorization")) for s in sessions])
        ok &= self.check(f"A. never more than {MAX_SESSIONS} sessions, never two for one step",
                         len(sessions) <= MAX_SESSIONS and len({s["step"] for s in sessions}) == len(sessions),
                         len(sessions))
        expected = {s: (t["provider"], t["model"] or "CLI default", t["role"])
                    for t in TASKS.values() for s in t["steps"]}
        routed = [(s["step"], s["provider"], s["model"]) for s in sessions]
        ok &= self.check("B. routing: module/validation-test → claude opus, cli-utility → claude sonnet, "
                         "ts-utility → codex CLI default",
                         all((s["provider"], s["model"], s["role"]) == expected[s["step"]] for s in sessions), routed)
        attempted = {e["run_tag"] for e in ctx["every"] if e["decision"] in ("started", "refused")}
        ok &= self.check("B. every native-cli run directory belongs to an audited invocation; every session has one",
                         set(runs) <= attempted and {s["run_tag"] for s in sessions} <= set(runs)
                         and all(r["backend"] == "native-cli" for r in runs.values()),
                         (sorted(runs), sorted(attempted)))
        evidence = {}
        for session in sessions:
            run = runs.get(session["run_tag"], {})
            facts: dict[str, Any] = {"requested": session["model"], "run_record": run.get("model"),
                                     "auth": run.get("auth")}
            stdout = run["dir"] / "stdout.jsonl" if run else None
            for line in (stdout.read_text(encoding="utf-8", errors="replace").splitlines()
                         if stdout and stdout.exists() else ()):
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if isinstance(event, dict) and event.get("type") == "system" and event.get("subtype") == "init":
                    facts.update(reported_model=event.get("model"), api_key_source=event.get("apiKeySource"))
                elif isinstance(event, dict) and session["provider"] == "codex" and "model" in json.dumps(event):
                    facts["codex_model_mentions"] = facts.get("codex_model_mentions", 0) + 1
            evidence[session["step"]] = facts
        self.report["model_evidence"] = evidence
        claude = [evidence[s["step"]] for s in sessions if s["provider"] == "claude"]
        ok &= self.check("B. no API key or model substitution: every Claude Code session that ran reported no API "
                         "key and the requested model family", all(f.get("api_key_source") == "none" and f["requested"]
                                                                  in str(f.get("reported_model")) for f in claude),
                         claude)
        return self.check("B. three Claude Code sessions ran (two Opus, one Sonnet)", len(claude) == 3, claude) and ok

    def verify_concurrency(self, ctx: dict[str, Any]) -> bool:
        """C. one agent at a time: audit intervals, the wrapper's own scan, the monitor, the claims."""
        sessions = ctx["sessions"]
        spans = sorted((s["at"], s["ended_at"] or float("inf"), s["step"]) for s in sessions)
        overlaps = [(a[2], b[2]) for a, b in zip(spans, spans[1:]) if b[0] < a[1]]
        ok = self.check("C. session intervals never overlap (start of the next ≥ end of the previous)",
                        not overlaps, overlaps)
        ok &= self.check("C. at every session start, no earlier session's process or run-tagged process was alive",
                         all(not s["earlier_sessions_alive"] and not s["earlier_tag_processes"] for s in sessions)
                         and [s["earlier_tags_checked"] for s in sessions] == list(range(len(sessions))),
                         [(s["step"], s["earlier_sessions_alive"], s["earlier_tag_processes"],
                           s["earlier_tags_checked"]) for s in sessions])
        seen = {s["run"]: (s["first"], s["last"]) for s in self.monitor.seen.values()}
        self.report["monitor"] = {"samples": self.monitor.samples, "max_concurrent_run_tags": self.monitor.max_tags,
                                  "max_concurrent_launches": self.monitor.max_launches,
                                  "overlaps": self.monitor.overlaps, "seen": seen}
        ok &= self.check("C. process monitor: at most one run tag and one launch process alive at any sample",
                         self.monitor.samples > 0 and self.monitor.max_tags <= 1 and self.monitor.max_launches <= 1,
                         self.report["monitor"])
        audited = {e["run_tag"]: e["decision"] for e in ctx["every"] if e["decision"] in ("started", "refused")}
        spawned = {e["run_tag"] for e in ctx["every"] if e["decision"] == "spawned"}
        ok &= self.check("C. every run-tagged process belonged to an audited invocation; a refused one never spawned "
                         "a CLI", set(self.monitor.seen) <= set(audited)
                         and not {t for t, d in audited.items() if d == "refused"} & spawned,
                         {"seen": sorted(self.monitor.tags.get(t, t) for t in self.monitor.seen), "audited": audited})
        return self.check("C. health samples: never more than one claim",
                          bool(self.health.samples) and all(len(h["claims"]) <= 1 for h in self.health.samples)) and ok

    def verify_checkpoints(self, _ctx: dict[str, Any]) -> bool:
        """D. checkpoint records and commits; H. file contents; G. Git history."""
        store, commits = self.store(), self.branch_commits()
        records = {key: load_record(store, self.task_id(key)) for key in TASKS}  # None: the task never ran
        rows = [{"task": key, "id": c.id, "sequence": c.sequence, "step": c.step, "status": c.status,
                 "commit": c.commit, "role": c.role, "agent": c.agent, "model": c.model, "files": c.files}
                for key, record in records.items() if record for c in record.checkpoints]
        self.report["checkpoints"] = rows
        by_commit = {r["commit"]: r for r in rows}
        ok = self.check("D. four committed checkpoints (backend #1 module, #2 validation-test; cli #1; frontend #1)",
                        sorted((r["task"], r["sequence"], r["step"], r["status"]) for r in rows) == [
                            ("backend", 1, "module", "committed"), ("backend", 2, "validation-test", "committed"),
                            ("cli", 1, "cli-utility", "committed"), ("frontend", 1, "ts-utility", "committed")],
                        [(r["task"], r["sequence"], r["step"], r["status"]) for r in rows])
        ok &= self.check("D. unique checkpoint IDs and commits; the branch holds exactly those commits",
                         len({r["id"] for r in rows}) == len(rows) == len(by_commit)
                         and sorted(commits) == sorted(by_commit), commits)
        ok &= self.check("D. each checkpoint names its task's role, agent and requested model",
                         all((r["role"], r["agent"]) == (TASKS[r["task"]]["role"], TASKS[r["task"]]["provider"])
                             and (r["model"] or "") in (TASKS[r["task"]]["model"], "CLI default") for r in rows),
                         [(r["task"], r["role"], r["agent"], r["model"]) for r in rows])
        manifest_ok = trailers_ok = owned_ok = content_ok = True
        for row in rows:
            path, content = FILES[row["step"]]
            data = (self.repo / path).read_bytes()
            manifest_ok &= row["files"] == {path: hashlib.sha256(data).hexdigest()}
            content_ok &= data.decode().rstrip("\n") == content.rstrip("\n")
            text = self.git("log", "-1", "--format=%(trailers:only,unfold)", row["commit"])
            trailers_ok &= all(t in text for t in (f"Feature-Task: {self.task_id(row['task'])}",
                                                   f"Feature-Role: {TASKS[row['task']]['role']}",
                                                   f"Feature-Checkpoint: {row['id']}"))
            owned_ok &= self.git("diff-tree", "--no-commit-id", "--name-only", "-r", row["commit"]).split() == [path]
        ok &= self.check("D. manifests equal the committed files' SHA-256", manifest_ok)
        ok &= self.check("D. Feature-Task / Feature-Role / Feature-Checkpoint trailers on every checkpoint commit",
                         trailers_ok)
        ok &= self.check("D. each checkpoint commit holds only its step's file, inside the role's own paths", owned_ok)
        ok &= self.check("H. the files have the expected content", content_ok)
        parents = [self.git("rev-list", "--parents", "-n", "1", c).split()[1:] for c in commits]
        ok &= self.check("G. linear history from the seed: every commit has exactly the previous one as parent",
                         parents == [[self.report["base"]], *[[c] for c in commits[:-1]]], parents)
        branches = self.git("branch", "--format=%(refname:short)").split()
        return self.check("G. one feature branch, no remote, clean checkout",
                          branches == ["dev", BRANCH] and self.git("remote") == ""
                          and self.git("status", "--porcelain") == "",
                          (branches, self.git("status", "--porcelain"))) and ok

    def verify_dependencies(self, ctx: dict[str, Any]) -> bool:
        """E. CLI only after Backend is verified; Frontend eligible alongside Backend."""
        sessions = ctx["sessions"]
        verified_at = self.verified_times()
        cli_start = next((s["at"] for s in sessions if s["step"] == "cli-utility"), None)
        ok = self.check("E. no task started before its dependencies were verified (CLI after Backend)",
                        cli_start is None or (verified_at.get("backend") is not None
                                              and cli_start > verified_at["backend"]),
                        (cli_start, verified_at.get("backend")))
        ok &= self.check("E. CLI ran, after Backend was verified", cli_start is not None, cli_start)
        backend_running = [h for h in self.health.samples for s in sessions if s["role"] == "backend"
                           and s["at"] < h["at"] < (s["ended_at"] or 0)]  # a Backend agent was running
        both = [h for h in backend_running if h["tasks"].get("frontend") == "ready"]
        ok &= self.check("E. Frontend was eligible (ready) while a Backend agent ran — two eligible tasks, one agent",
                         bool(both), len(both))
        blocked = [h["tasks"].get("cli") for h in backend_running]
        return self.check("E. CLI was not ready while Backend ran (waiting for its dependency)",
                          bool(blocked) and "ready" not in blocked, sorted(set(map(str, blocked)))) and ok

    def verify_recovery(self, ctx: dict[str, Any]) -> bool:
        """F. the restart: checkpoint #1 adopted, no step repeated, budgets kept."""
        sessions, runs = ctx["sessions"], ctx["runs"]
        s1, s2 = self.services[0], self.services[-1]
        record = load_record(self.store(), self.task_id("backend"))
        by_service = [(s["step"], s["service_pid"]) for s in sessions]
        ok = self.check("F. service 1 ran only `module`; service 2 ran `validation-test`, CLI and Frontend",
                        len(self.services) == 2 and by_service == [
                            ("module", s1["pid"]), ("validation-test", s2["pid"]), ("cli-utility", s2["pid"]),
                            ("ts-utility", s2["pid"])], by_service)
        first, second = record.checkpoints[0], record.checkpoints[1]
        at_boundary = self.report["at_boundary"]["checkpoints"][0]
        ok &= self.check("F. checkpoint #1 adopted unchanged after the restart (same ID and commit)",
                         (first.id, first.commit) == (at_boundary[0], at_boundary[4])
                         and first.attempt != second.attempt)
        resumed = next(r["dir"] for r in runs.values() if r["dir"].name.split(".")[2] == "validation-test")
        prompt = (resumed / "prompt.txt").read_text(encoding="utf-8")
        ok &= self.check("F. the resumed prompt lists `module` as completed (checkpoint #1) and runs step 2 of 2",
                         all(n in prompt for n in ("WHAT HAS BEEN COMPLETED", "step `module`", "checkpoint #1",
                                                   first.commit[:12], "CURRENT STEP 2 of 2 — `validation-test`"))
                         and "CURRENT STEP 1 " not in prompt)
        events = [json.loads(line) for line in (self.state / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        intents = [e["step"] for e in events if e["kind"] == "checkpoint-intent"]
        ok &= self.check("F. no step repeated (at most one checkpoint intent per step)",
                         len(intents) == len(set(intents)), intents)
        ok &= self.check("F. every step reached its checkpoint intent", sorted(intents) == sorted(STEP_PROVIDER),
                         intents)
        scheduling = self.report.get("scheduling") or self.scheduling()
        return self.check("F. launches: Backend 2 (stopped at the boundary + resumed), CLI 1, Frontend 1; no retry "
                          "budget used; the restart did not reset the launch count",
                          {k: (v["launches"], v["attempts"]) for k, v in scheduling.items()}
                          == {"backend": (2, {}), "cli": (1, {}), "frontend": (1, {})}
                          and scheduling["backend"]["history"] == ["cancelled", "success"], scheduling) and ok

    def scheduling(self) -> dict[str, Any]:
        """Each task's scheduling record (launches, retry attempts, history, waits, attention)."""
        store = self.store()
        out = {}
        for key in TASKS:
            rec = load_scheduling(store, store.load_task(self.task_id(key)))
            out[key] = {"launches": rec.launches, "attempts": rec.attempts,
                        "history": [h.get("category") for h in rec.history],
                        "reasons": [scrub(str(h.get("reason", "")), 300) for h in rec.history],
                        "waiting": rec.waiting and {"category": rec.waiting.get("category"),
                                                    "until": rec.waiting.get("until")},
                        "attention": rec.attention and {"category": rec.attention.get("category"),
                                                        "reason": scrub(str(rec.attention.get("reason")), 300)}}
        self.report["scheduling"] = out
        return out

    def verify_tasks(self, _ctx: dict[str, Any]) -> bool:
        """H. every task verified with its gate; I. both services exited 0."""
        self.scheduling()
        states, gates = {}, {}
        for key in TASKS:
            data = json.loads((self.state / "tasks" / f"{self.task_id(key)}.json").read_text(encoding="utf-8"))
            states[key] = data["state"]
            gates[key] = [(g["gate"], g["status"]) for g in (data.get("evidence") or {}).get("gates", ())]
        self.report["tasks"] = {"states": states, "gates": gates}
        ok = self.check("H. every task verified, each with its gate passed on the combined result",
                        states == {k: "verified" for k in TASKS}
                        and gates == {k: [(t["gate"], "passed")] for k, t in TASKS.items()}, (states, gates))
        kinds = [(s["number"],) + self.exit_kind(s) for s in self.services]
        return self.check("I. service 1 OPERATOR_STOP (the planned restart), service 2 NATURAL_IDLE_EXIT after "
                          "AVAILABLE", [k[:2] for k in kinds] == [(1, "OPERATOR_STOP"), (2, "NATURAL_IDLE_EXIT")]
                          and kinds[-1][2] == "AVAILABLE", kinds) and ok

    def verify_auth_and_artifacts(self, ctx: dict[str, Any]) -> bool:
        """K. subscription logins only, no GitHub; L. sanitized, private artifacts."""
        sessions, runs = ctx["sessions"], ctx["runs"]
        prefixes = ("claude.ai subscription", "ChatGPT subscription")
        ok = self.check("K. every session: the CLI reported the subscription login; no API-key variable",
                        all(str(s.get("login", "")).startswith(prefixes) for s in sessions)
                        and all(r["auth"].startswith(prefixes) for r in runs.values()),
                        sorted({str(s.get("login")) for s in sessions}))
        ok &= self.check("K/no GitHub: the gh shim was never called", not self.gh_log.exists(),
                         self.gh_log.read_text(encoding="utf-8") if self.gh_log.exists() else "")
        audit_text = self.audit.read_text(encoding="utf-8") if self.audit.exists() else ""
        ok &= self.check("L. the audit log holds no prompt text, e-mail or token-like value",
                         "@" not in audit_text and "CURRENT STEP" not in audit_text and "sk-" not in audit_text)
        return self.check("L. the run directory is private (0700)", (self.dir.stat().st_mode & 0o077) == 0) and ok

    def verified_times(self) -> dict[str, float]:
        """When each task became verified (the service's worker-task-verified event)."""
        out = {}
        for line in (self.state / "events.jsonl").read_text(encoding="utf-8").splitlines():
            event = json.loads(line)
            if event.get("kind") == "worker-task-verified":
                key = str(event.get("task", "")).split(".", 1)[-1]
                out.setdefault(key, _epoch(event.get("at") or event.get("ts") or ""))
        return out

    def health_checks(self, sessions: list[dict[str, Any]]) -> bool:
        """BUSY with the right task/agent/model/step and a fresh heartbeat while an agent runs."""
        samples = self.health.samples
        during = []
        for session in sessions:
            for h in samples:
                if session["at"] + 1 < h["at"] < (session["ended_at"] or 0) - 1:
                    during.append((session, h))
        self.report["health"] = {"samples": len(samples), "during_sessions": len(during),
                                 "states": sorted({str(h["state"]) for h in samples}),
                                 "diagnoses": sorted({str(h["diagnosis"]) for h in samples})}
        ok = self.check("health sampled while each agent ran", {s["step"] for s, _h in during} == {
            s["step"] for s in sessions}, self.report["health"])
        right = [h["state"] == "BUSY" and h["current"] is not None and h["current"]["task"] == s["task"]
                 and h["current"]["agent"] == s["provider"] and h["current"]["model"] == s["model"]
                 and h["current"]["step"] in (s["step"], None) for s, h in during]
        ok &= self.check("health: BUSY with the running task, agent and requested model during each session",
                         all(right), [(s["step"], h["state"], h["current"])
                                      for (s, h), r in zip(during, right) if not r][:5])
        stepped = {s["step"] for s, h in during if h["current"] and h["current"]["step"] == s["step"]}
        ok &= self.check("health: the current step is visible during sessions", len(stepped) >= 1, sorted(stepped))
        fresh = [h for _s, h in during if h["heartbeat_age"] is not None and h["heartbeat_age"] <= 60
                 and all(a is not None and a <= 60 for a in h["claim_heartbeat_age"])]
        ok &= self.check("health: fresh service and claim heartbeats while agents ran (≤ 60 s)",
                         len(fresh) == len(during), len(during) - len(fresh))
        ok &= self.check("health: healthy while agents ran; never NEEDS_ATTENTION",
                         all(h["healthy"] for _s, h in during)
                         and "NEEDS_ATTENTION" not in self.report["health"]["states"],
                         self.report["health"]["states"])
        progress = [h["last_checkpoint"] for h in samples if h.get("last_checkpoint")]
        ok &= self.check("health: checkpoint progress visible (last checkpoint advances)", len(
            {json.dumps(p, sort_keys=True) for p in progress}) >= 2)
        before, calls = self.snapshot(), len(self.invocations())
        outputs = self.read_only_commands()
        final = json.loads(outputs["health --json"].stdout)
        self.report["final_health"] = {"state": final.get("state"), "alive": final.get("alive"),
                                       "healthy": final.get("healthy"), "diagnosis": final.get("diagnosis"),
                                       "reason": final.get("reason"), "status": outputs["status"].stdout.strip()}
        ok &= self.check("after the run: status/health report the service stopped (OFFLINE, not alive, not healthy)",
                         final.get("state") == "OFFLINE" and final.get("alive") is False
                         and final.get("healthy") is False
                         and outputs["health --json"].returncode == 1, self.report["final_health"])
        ok &= self.check("after the run: status/health/waiting/attention are read-only and ran no CLI",
                         self.snapshot() == before and len(self.invocations()) == calls)
        return ok

    def cleanup_checks(self, sessions: list[dict[str, Any]]) -> bool:
        """No test-owned process, claim, exclusive flag, held lock or pending checkpoint transaction."""
        store = self.store()
        alive, launches = self.monitor.sample()
        residual = self.processes()
        children = [s["child_pid"] for s in sessions
                    if s["child_pid"] and _alive(s["child_pid"], s["child_start_ticks"])]
        self.report["cleanup"] = {"tagged": alive, "launches": launches, "processes": residual, "children": children}
        ok = self.check("J. no Claude Code / Codex / child process of this test is alive (run tags, identities)",
                        not alive and not launches and not children, self.report["cleanup"])
        ok &= self.check("J. no other process of this test is alive", not residual, residual)
        ok &= self.check("J. no claim and no live exclusive-ownership flag",
                         not store.list_claims() and not (self.state / "flags" / "agent-exclusive.json").exists()
                         and not any("exclusive" in p.name for p in (self.state / "flags").glob("*")),
                         sorted(p.name for p in (self.state / "flags").glob("*")))
        locks = sorted(self.state.rglob("*.lock"))
        held = []
        for path in locks:
            probe = FileLock(path)
            try:
                probe.acquire(timeout=0)
                probe.release()
            except Exception:  # pylint: disable=broad-except
                held.append(path.name)
        roles = LockSet(self.state / "locks")
        for role in ("backend", "cli", "frontend"):
            lock = roles.role(role)
            try:
                lock.acquire()
                lock.release()
            except Exception:  # pylint: disable=broad-except
                held.append(f"role {role}")
        ok &= self.check("J. no lock is held (service, feature, role and store locks)", not held, held)
        pending = {}
        for key in TASKS:  # `python -m feature_coordinator checkpoints TASK --json`: the read-only W3 inspection
            view = json.loads(self.feature("checkpoints", self.task_id(key), "--json").stdout)
            record = load_record(store, self.task_id(key))
            unsettled = any(view.get(k) for k in ("actions", "conflicts", "orphans"))
            claimed = (view.get("claim") or {}).get("state") not in (None, "none")
            if unsettled or claimed or (record is not None and record.pending()):
                pending[key] = {k: view.get(k) for k in ("status", "actions", "conflicts", "orphans", "claim")}
        ok &= self.check("J. no pending checkpoint transaction, conflict or orphan", not pending, pending)
        zombies = [p for p in os.listdir("/proc") if p.isdigit() and _ppid(int(p)) == os.getpid()
                   and _state(int(p)) == "Z"]
        ok &= self.check("J. no unreaped child (leaked process handle) of the harness", not zombies, zombies)
        return ok

    # -- the whole scenario --------------------------------------------------------------------------------

    def run(self) -> dict[str, Any]:
        """prepare → preflight → service 1 → between → service 2 → verify; stops at the first failed gate.
        SIGTERM and SIGHUP are handled like Ctrl-C: the service is stopped, the report written."""
        previous = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGHUP)}
        for sig in previous:
            signal.signal(sig, _interrupt)
        try:
            return self._run()
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)

    def _run(self) -> dict[str, Any]:
        passed = False
        try:
            self.prepare()
            passed = self.preflight()
            if passed:
                self.deadline = time.time() + self.total_seconds
                self.update_control(deadline=self.deadline,
                                    harness={"pid": os.getpid(), "start_ticks": _ticks(os.getpid())})
                self.monitor.start()
                self.health.start()
                self.phase = "run"
                try:
                    passed = self.service1() and self.between() and self.service2()
                except KeyboardInterrupt as exc:
                    self.report["interrupted"] = str(exc) or "interrupted (Ctrl-C)"
                    passed = self.check("the scenario ran to its end (not interrupted)", False,
                                        self.report["interrupted"])
                except Exception as exc:  # pylint: disable=broad-except
                    passed = self.check("the scenario ran without a harness error", False,
                                        f"{type(exc).__name__}: {exc}")
                finally:
                    self.revoke("the harness stopped supervising")
                    self.update_control(closed="the harness stopped supervising")  # and no new authorization
                    if self.proc is not None and self.proc.poll() is None:  # never leave a service running unwatched
                        self.services[-1]["exit"] = self.stop_service(self.proc, "the harness stopped early")
                    self.health.halt.set()
                    self.health.join(timeout=120)
                passed = self.verify() and passed
                self.monitor.halt.set()
                self.monitor.join(timeout=10)
        finally:
            if self.prepared:
                self.report.update(self.classification())
                self.report.update(passed=passed and all(c["ok"] for c in self.checks), checks=self.checks,
                                   services=self.services, grants=self.grants, audit=[{k: e.get(k) for k in (
                                       "decision", "provider", "step", "role", "model", "login", "exit", "call",
                                       "problems", "authorization", "service_pid", "at")} for e in self.invocations()],
                                   health_samples=self.health.samples)
                (self.reports / "report.json").write_text(json.dumps(self.report, indent=2, default=str),
                                                          encoding="utf-8")
        return self.report


def _cause(source: str, reason: str) -> str:
    """A plain-words class for a primary failure."""
    if reason.startswith("gates could not run"):
        return ("environment: a required gate could not run (its sandbox or tool is unavailable) — the code was "
                "never judged")
    if reason.startswith("gates failed"):
        return "validation: the agent's result failed its gate"
    if source == "audit wrapper" or "refused by the W4.2 audit wrapper" in reason:
        return "harness: the audit wrapper refused the session (no model request)"
    if re.search(r"usage[ _]limit|limit reached|quota", reason, re.IGNORECASE):
        return "subscription: a usage limit"
    if source == "service":
        return "the Worker service failed"
    return "agent run or coordinator: see the reason"


def _interrupt(signum: int, _frame: Any) -> None:
    raise KeyboardInterrupt(f"signal {signal.Signals(signum).name}")


def _ticks(pid: int) -> int | None:
    try:
        return int(_stat(pid)[19])
    except (OSError, ValueError, IndexError):
        return None

def _epoch(text: str) -> float:
    from feature_coordinator.autonomy import epoch  # pylint: disable=import-outside-toplevel
    return epoch(text) or 0.0


def _alive(pid: int, start: int | None) -> bool:
    try:
        fields = _stat(pid)
    except (OSError, IndexError):
        return False
    return fields[0] != "Z" and int(fields[19]) == start


def _ppid(pid: int) -> int:
    try:
        return int(_stat(pid)[1])
    except (OSError, ValueError, IndexError):
        return 0


def _state(pid: int) -> str:
    try:
        return _stat(pid)[0]
    except (OSError, IndexError):
        return ""


def plan(workdir: Path, agents: dict[str, Path]) -> dict[str, Any]:
    """The real-agent plan (no model request: only --version and the CLIs' status commands)."""
    from feature_coordinator.native_backend import (  # pylint: disable=import-outside-toplevel
        check_subscription, native_environment)
    from feature_coordinator.providers import ProviderKind  # pylint: disable=import-outside-toplevel
    kinds = {"claude": ProviderKind.CLAUDE_CODE, "codex": ProviderKind.CODEX}
    clis = {}
    for provider, real in agents.items():
        version = subprocess.run([str(real), "--version"], capture_output=True, text=True, timeout=30,
                                 check=False).stdout.strip()
        clis[provider] = {"binary": str(real), "version": version, "sha256": sha256_file(real),
                          "login": check_subscription(kinds[provider], str(real), native_environment()).method}
    return {"workdir": str(workdir), "repository": str(workdir / "repo"), "state": str(workdir / "state"),
            "clis": clis, "feature": FEATURE,
            "tasks": {k: {"role": t["role"], "agent": t["provider"], "model": t["model"] or "CLI default",
                          "steps": list(t["steps"]), "depends_on": list(t["depends_on"]), "gate": t["gate"]}
                      for k, t in TASKS.items()},
            "max_sessions": MAX_SESSIONS, "budget": {t["role"]: len(t["steps"]) for t in TASKS.values()},
            "session_timeout_seconds": SESSION_TIMEOUT, "total_limit_seconds": TOTAL_SECONDS,
            "claude_max_budget_usd_per_session": CLAUDE_BUDGET_USD,
            "restart": f"graceful stop after `{BOUNDARY_AFTER}`",
            "github": "none (local tracker; gh shim refuses)", "commands": [
                "worker start --exit-when-idle (service 1, boundary hook) → Backend `module`",
                "worker stop (graceful, at the boundary; no agent running)",
                "worker status / health / waiting / attention (read-only)",
                "worker start --exit-when-idle (service 2) → Backend `validation-test`, CLI, Frontend → idle exit"]}


def main(argv: list[str] | None = None) -> int:
    """CLI (see the module docstring)."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--workdir", type=Path, default=None,
                        help="a NEW directory (default: ~/.local/state/mx-admin-agents/w4-real-smoke/<run>-<UTC time>)")
    parser.add_argument("--agent", choices=("fake", "real"), required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    state_home = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    base = state_home / "mx-admin-agents" / "w4-real-smoke"
    workdir = args.workdir or base / (f"rehearsal-{stamp}" if args.agent == "fake" else f"run-{stamp}")
    if args.agent == "fake":
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from fc_helpers import install_fake_agent  # pylint: disable=import-outside-toplevel
        if not args.apply:
            print("dry run — pass --apply to run the offline rehearsal (fake agents)")
            return 0
        workdir.mkdir(parents=True, exist_ok=True)
        agents = {p: install_fake_agent(workdir / "fake-agents", p) for p in ("claude", "codex")}
        for agent in agents.values():
            Path(f"{agent}.json").write_text(json.dumps({"auth": "subscription", "follow_prompt": True, "around": {
                path: {"before": [{"do": "sleep", "seconds": 6}]} for path, _ in FILES.values()}}), encoding="utf-8")
        report = Harness(workdir, agents, fake=True, health_interval=0.5).run()
        print(f"report: {workdir / 'reports' / 'report.json'}")
        print("offline rehearsal: " + ("PASSED" if report["passed"] else "FAILED"))
        return 0 if report["passed"] else 1
    agents = {}
    for name in ("claude", "codex"):
        found = shutil.which(name)
        if not found:
            print(f"{name} is not installed")
            return 1
        agents[name] = Path(found).resolve()
    if not args.apply:
        print(json.dumps(plan(workdir, agents), indent=2))
        print(f"PLAN ONLY — no model request was made. --apply runs at most {MAX_SESSIONS} REAL sessions "
              "(operator approval required).")
        return 0
    report = Harness(workdir, agents, fake=False).run()
    print(f"report: {workdir / 'reports' / 'report.json'}")
    print("W4.2 real-agent smoke test: " + ("PASSED" if report["passed"] else "FAILED (see the report)"))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
