#!/usr/bin/env python3
"""W3.2 — checkpoint recovery smoke scenario: completed steps are never run again after a restart.

TEST TOOLING ONLY (never imported by the coordinator). One feature, ONE Backend task with three
ordered steps (model → service → validation), run through the real CLI path
(`python -m feature_coordinator work --apply` → `python -m feature_coordinator launch` → native-cli backend → agent):

  session 1  a Worker process runs steps 1 and 2 (checkpoints #1 and #2, commits A and B). A
             test-only fault injector (HOOK, a `sitecustomize` on this Worker's PYTHONPATH,
             inherited by its launch process) freezes the launch process at the exact boundary
             BEFORE step 3 starts and writes a marker. The harness snapshots the state, then
             SIGKILLs that launch process and the Worker (the loss of the running Worker).
  baseline   with every process gone: commits, checkpoint records, steps, claims, processes.
  preview    NEW processes: `recover TASK --show-prompt` and `checkpoints TASK --json` (no model
             request) must report checkpoint #2 / commit B, steps 1-2 done, ONLY step 3 left.
  session 2  a NEW Worker process (`work --apply`, no injector) reclaims the dead claim and runs
             only step 3 (checkpoint #3, commit C); the gate verifies the result.
  verify     agent-invocation audit, checkpoint history, Git history, file hashes, task state.

Every agent launch goes through WRAPPER, an audit wrapper around the agent CLI. It logs each
agent invocation (step, pid, its Worker's pid, model, prompt hash) and, BEFORE handing the
prompt to the real CLI, re-checks: the subscription login (`auth status --json`: claude.ai,
first party), no API-key variable, the requested model, the repository path, a known step, at
most `max_sessions` sessions and never a second session for one step. Anything else is refused
(no model session). `--version`, `--help` and `auth status` pass through unchanged.

    python fc_recovery_smoke.py --workdir DIR --agent fake --apply     # offline rehearsal
    python fc_recovery_smoke.py --workdir DIR --agent claude           # real-agent PLAN only
    python fc_recovery_smoke.py --workdir DIR --agent claude --apply   # REAL (operator approval)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from feature_coordinator import CLI  # pylint: disable=wrong-import-position
from feature_coordinator.checkpoints import load_record  # pylint: disable=wrong-import-position
from feature_coordinator.cli import EXAMPLE_CONFIG  # pylint: disable=wrong-import-position
from feature_coordinator.demo import init_demo_repo  # pylint: disable=wrong-import-position
from feature_coordinator.locks import LockSet, ProcessIdentity  # pylint: disable=wrong-import-position
from feature_coordinator.store import StateStore  # pylint: disable=wrong-import-position

FEATURE, TASK_KEY = "w3-recovery", "backend-recovery"
TASK, BRANCH = f"{FEATURE}.{TASK_KEY}", f"feature/{FEATURE}"
STEPS = ("model", "service", "validation")
HALT_STEP = "validation"
GATE = "recovery-validate"
MODEL = "opus"
FILES = {
    "model": ("backend/recovery/model.py", "class RecoveryModel:\n    version = 1\n"),
    "service": ("backend/recovery/service.py",
                "from .model import RecoveryModel\n\n\ndef get_version():\n    return RecoveryModel.version\n"),
    "validation": ("backend/recovery/validation.py",
                   "from .service import get_version\n\n\ndef validate():\n    return get_version() == 1\n"),
}
SEED = {"README.md": "# disposable W3.2 recovery smoke repository\n", "backend/README.md": "# backend\n",
        "cli/README.md": "# cli\n", "frontend/README.md": "# frontend\n"}

WRAPPER = r'''#!/usr/bin/python3
"""Audit wrapper around an agent CLI (W3.2 recovery smoke test; test tooling only). See
fc_recovery_smoke.py. Control file: <this file>.json."""
import fcntl, hashlib, json, os, re, subprocess, sys, time
here = os.path.abspath(sys.argv[0])
with open(here + ".json", encoding="utf-8") as handle:
    control = json.load(handle)
real, args = control["real"], sys.argv[1:]
if not ("-p" in args or "--print" in args):  # --version, --help, auth status: unchanged
    os.execv(real, [real, *args])
prompt = sys.stdin.read()
found = re.search(r"CURRENT STEP (\d+) of (\d+) — `([^`]+)`", prompt)
model = args[args.index("--model") + 1] if "--model" in args else ""
def grandparent():
    try:
        with open(f"/proc/{os.getppid()}/stat", encoding="ascii") as stat:
            return int(stat.read().rsplit(")", 1)[1].split()[1])
    except (OSError, ValueError, IndexError):
        return 0
entry = {"at": time.time(), "pid": os.getpid(), "launch_pid": os.getppid(), "worker_pid": grandparent(),
         "cwd": os.path.realpath(os.getcwd()), "step": found.group(3) if found else None, "model": model,
         "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(), "run_tag": os.environ.get("FC_RUN_TAG")}
problems = []
if entry["cwd"] != control["repo"]:
    problems.append(f"working directory {entry['cwd']} is not the disposable repository")
if model != control["model"]:
    problems.append(f"model {model!r} is not the requested {control['model']!r}")
if entry["step"] not in control["steps"]:
    problems.append(f"unknown step {entry['step']!r}")
keyish = sorted(k for k in os.environ if k.startswith(("ANTHROPIC_", "OPENAI_", "CLAUDE_CODE_USE_", "CODEX_API")))
if keyish:
    problems.append(f"API-key/provider variables present: {keyish}")
try:
    status = json.loads(subprocess.run([real, "auth", "status", "--json"], capture_output=True, text=True,
                                       timeout=60, check=False).stdout)
except (OSError, ValueError, subprocess.TimeoutExpired):
    status = {}
if not (status.get("loggedIn") is True and status.get("authMethod") == "claude.ai"
        and status.get("apiProvider") == "firstParty"):
    problems.append("the CLI does not report the claude.ai subscription login")
with open(control["audit"], "a+", encoding="utf-8") as log:
    fcntl.flock(log, fcntl.LOCK_EX)
    log.seek(0)
    earlier = [json.loads(line) for line in log.read().splitlines() if line.strip()]
    started = [e for e in earlier if e.get("decision") == "started"]
    if len(started) >= control["max_sessions"]:
        problems.append(f"session budget exhausted ({len(started)} of {control['max_sessions']})")
    if any(e.get("step") == entry["step"] for e in started):
        problems.append(f"a session for step {entry['step']!r} already ran (duplicate step execution)")
    entry.update(decision="refused" if problems else "started", problems=problems,
                 subscription=status.get("subscriptionType") or "", auth=status.get("authMethod") or "")
    log.write(json.dumps(entry, sort_keys=True) + "\n")
    log.flush()
    os.fsync(log.fileno())
if problems:
    sys.stderr.write("refused by the W3.2 audit wrapper: " + "; ".join(problems) + "\n")
    sys.exit(97)
read_end, write_end = os.pipe()
os.write(write_end, prompt.encode())  # < 64 KiB: fits the pipe buffer
os.close(write_end)
os.dup2(read_end, 0)
os.close(read_end)
os.execv(real, [real, *args])
'''

HOOK = r'''"""TEST-ONLY fault injection for the W3.2 recovery smoke test (fc_recovery_smoke.py).
With FC_TEST_HALT_BEFORE_STEP and FC_TEST_HALT_MARKER set, a `python -m feature_coordinator launch` process freezes
at the exact boundary BEFORE that step starts (every earlier checkpoint is settled by then):
it writes the marker and waits to be killed. Never part of the coordinator."""
import importlib.machinery, json, os, sys, time
_STEP = os.environ.get("FC_TEST_HALT_BEFORE_STEP", "")
_MARKER = os.environ.get("FC_TEST_HALT_MARKER", "")

def _patch(module):
    original = module.FeatureCoordinator._run_step
    def halting(self, feature, task, claim, mark, step, record, progress):
        if step.key == _STEP:
            data = {"pid": os.getpid(), "task": task.id, "step": step.key, "done_steps": record.done_steps(),
                    "checkpoints": [[c.id, c.status, c.commit] for c in record.checkpoints], "at": time.time()}
            with open(_MARKER + ".tmp", "w", encoding="utf-8") as handle:
                json.dump(data, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(_MARKER + ".tmp", _MARKER)
            deadline = time.monotonic() + 3600
            while time.monotonic() < deadline:  # frozen at the boundary until the harness kills it
                time.sleep(0.5)
            os._exit(99)
        return original(self, feature, task, claim, mark, step, record, progress)
    module.FeatureCoordinator._run_step = halting

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

if _STEP and _MARKER:
    sys.meta_path.insert(0, _Finder())
'''


def _ancestors() -> set[int]:
    """This process and its ancestors."""
    pids, pid = set(), os.getpid()
    while pid > 1 and pid not in pids:
        pids.add(pid)
        try:
            with open(f"/proc/{pid}/stat", encoding="ascii") as stat:
                pid = int(stat.read().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            break
    return pids


def step_title(step: str, fake: bool) -> str:
    """The step's instruction (first line short: it is how completed steps are listed later)."""
    path, content = FILES[step]
    title = (f"Create the file {path} with exactly the content below and nothing else in it.\n"
             f"```python\n{content}```\n"
             "Do not create, change or delete any other file. Do not run any command.")
    if fake:  # the fake agent performs the last FAKE-ACTIONS line of its prompt
        title += "\nFAKE-ACTIONS:" + json.dumps([{"do": "write", "path": path, "text": content}])
    return title


class Harness:  # pylint: disable=too-many-instance-attributes
    """One run of the scenario in *workdir* (a new directory)."""

    def __init__(self, workdir: Path, real_agent: Path, *, fake: bool, echo: Callable[[str], None] = print,
                 step_timeout: int = 300) -> None:
        self.dir = workdir
        self.repo, self.state = workdir / "repo", workdir / "state"
        self.audit_dir = workdir / "audit"
        self.audit, self.marker = self.audit_dir / "agent-invocations.jsonl", self.audit_dir / "boundary.json"
        self.wrapper, self.config = workdir / "bin" / "agent-audit", workdir / "config.yaml"
        self.real_agent, self.fake, self.echo, self.step_timeout = real_agent, fake, echo, step_timeout
        self.checks: list[dict[str, Any]] = []
        self.report: dict[str, Any] = {"workdir": str(workdir), "agent": "fake" if fake else str(real_agent)}

    # -- plumbing ------------------------------------------------------------------------

    def check(self, name: str, ok: bool, detail: Any = "") -> bool:
        """Record one verification; returns *ok*."""
        self.checks.append({"check": name, "ok": bool(ok), "detail": detail})
        self.echo(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))
        return bool(ok)

    def git(self, *args: str) -> str:
        """git in the disposable repository."""
        return subprocess.run(["git", "-C", str(self.repo), *args], capture_output=True, text=True,
                              check=True).stdout.strip()

    def feature(self, *args: str, timeout: float = 120, env: dict[str, str] | None = None,
                ) -> subprocess.CompletedProcess[str]:
        """`python -m feature_coordinator` on the disposable repository and state (a new process)."""
        return subprocess.run([*CLI, "--repo", str(self.repo), "--state-dir",
                               str(self.state), "--project-config", str(self.config), *args],
                              capture_output=True, text=True, timeout=timeout, env=env, check=False, cwd=REPO_ROOT)

    def invocations(self) -> list[dict[str, Any]]:
        """The wrapper's audit records (every agent invocation, started or refused)."""
        if not self.audit.exists():
            return []
        return [json.loads(line) for line in self.audit.read_text(encoding="utf-8").splitlines() if line.strip()]

    def sessions(self) -> list[dict[str, Any]]:
        """Agent invocations that were handed to the real CLI."""
        return [e for e in self.invocations() if e["decision"] == "started"]

    def run_dirs(self) -> list[str]:
        """The coordinator's run directories of this task (one per agent invocation)."""
        return sorted(p.name for p in (self.state / "runs").glob(f"{TASK}.*") if p.is_dir())

    def record(self) -> Any:
        """The task's checkpoint record (strictly loaded)."""
        return load_record(StateStore(self.state), TASK)

    def processes(self) -> list[str]:
        """Command lines of live processes that mention this run's directory (not this harness
        process or its ancestors, whose command lines name the directory)."""
        found, own = [], _ancestors()
        for entry in os.listdir("/proc"):
            if not entry.isdigit() or int(entry) in own:
                continue
            try:
                with open(f"/proc/{entry}/cmdline", "rb") as handle:
                    cmdline = handle.read()
            except OSError:
                continue
            if str(self.dir).encode() in cmdline:
                found.append(cmdline.replace(b"\0", b" ").decode(errors="replace"))
        return found

    @staticmethod
    def wait(predicate: Callable[[], bool], timeout: float) -> bool:
        """Poll *predicate* until true or *timeout* seconds (bounded; never a fixed delay)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.1)
        return predicate()

    # -- preparation ----------------------------------------------------------------------

    def prepare(self) -> None:
        """Disposable repository, configuration, spec, audit wrapper and fault injector."""
        import yaml  # pylint: disable=import-outside-toplevel
        if self.repo.exists() or self.state.exists() or self.audit.exists():
            raise FileExistsError(f"{self.dir} was used before: every run uses a new directory")
        for sub in ("bin", "hooks", "audit"):
            (self.dir / sub).mkdir(parents=True, exist_ok=True)
        init_demo_repo(self.repo, SEED)
        self.wrapper.write_text(WRAPPER, encoding="utf-8")
        self.wrapper.chmod(0o755)
        Path(f"{self.wrapper}.json").write_text(json.dumps({
            "real": str(self.real_agent), "audit": str(self.audit), "repo": os.path.realpath(self.repo),
            "model": MODEL, "steps": list(STEPS), "max_sessions": 3}, indent=2), encoding="utf-8")
        (self.dir / "hooks" / "sitecustomize.py").write_text(HOOK, encoding="utf-8")
        data = yaml.safe_load(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
        data["providers"]["claude"]["executable"] = str(self.wrapper)  # Backend routing unchanged: claude, opus
        data["gates"][GATE] = ["python3", "-c", "from backend.recovery.validation import validate; "
                                               "assert validate() is True; print('recovery ok')"]
        data["runtime"].update(execution_backend="native-cli", task_timeout_seconds=self.step_timeout,
                               claude_max_budget_usd=0.5)
        self.config.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        spec = self.dir / "feature.yaml"
        spec.write_text(yaml.safe_dump({"slug": FEATURE, "title": "W3.2 checkpoint recovery smoke test", "test": True,
                                        "tasks": [{"key": TASK_KEY, "role": "backend", "required_gates": [GATE],
                                                   "title": "Recovery smoke: model, service and validation modules",
                                                   "acceptance": ["validate() in backend/recovery/validation.py "
                                                                  "returns True"],
                                                   "steps": [{"key": s, "title": step_title(s, self.fake)}
                                                             for s in STEPS]}]}, sort_keys=False), encoding="utf-8")
        for step in (("plan", str(spec), "--apply"), ("activate", FEATURE, "--apply")):
            result = self.feature(*step)
            if result.returncode != 0:
                raise RuntimeError(f"{' '.join(step)} failed: {result.stdout}{result.stderr}")
        self.report["base"] = self.git("rev-parse", "dev")

    # -- session 1: steps 1 and 2, then the loss of the Worker at the boundary ----------------

    def session1(self) -> bool:
        """Run until the injector freezes the launch process before step 3; snapshot; kill."""
        env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(self.dir / "hooks"), str(REPO_ROOT)]),
               "FC_TEST_HALT_BEFORE_STEP": HALT_STEP, "FC_TEST_HALT_MARKER": str(self.marker)}
        with open(self.audit_dir / "worker-1.log", "wb") as log:
            worker = subprocess.Popen(  # pylint: disable=consider-using-with
                [*CLI, "--repo", str(self.repo), "--state-dir", str(self.state),
                 "--project-config", str(self.config), "work", FEATURE, "--apply"],
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, env=env, cwd=REPO_ROOT)
        self.report["worker_1_pid"] = worker.pid
        reached = self.wait(lambda: self.marker.exists() or worker.poll() is not None, 2 * self.step_timeout + 120)
        why = (self.audit_dir / "worker-1.log").read_text(encoding="utf-8")[-2000:] if reached else "timed out"
        if not self.check("session 1 reached the boundary before step 3", self.marker.exists(), why):
            worker.kill()
            return False
        boundary = json.loads(self.marker.read_text(encoding="utf-8"))
        record, claim = self.record(), StateStore(self.state).load_claim(TASK) or {}
        snap = {"boundary": boundary, "claim_pid": claim.get("identity", {}).get("pid"),
                "claim_progress": claim.get("checkpoint", {}),
                "checkpoints": [{"id": c.id, "sequence": c.sequence, "step": c.step, "status": c.status,
                                 "commit": c.commit, "files": c.files, "attempt": c.attempt}
                                for c in record.checkpoints],
                "done": record.done_steps(), "remaining": record.remaining_steps(),
                "agent_sessions": [{"step": e["step"], "pid": e["pid"], "worker_pid": e["worker_pid"]}
                                   for e in self.sessions()],
                "run_dirs": self.run_dirs(), "head": self.git("rev-parse", BRANCH),
                "commits": self.git("rev-list", "--reverse", f"{self.report['base']}..{BRANCH}").split()}
        self.report["at_boundary"] = snap
        cps = snap["checkpoints"]
        self.check("checkpoints #1 and #2 committed at the boundary",
                   [(c["sequence"], c["step"], c["status"]) for c in cps] == [(1, "model", "committed"),
                                                                              (2, "service", "committed")], cps)
        self.check("commits A and B are exactly the two checkpoint commits",
                   snap["commits"] == [c["commit"] for c in cps] and snap["head"] == cps[-1]["commit"], snap["commits"])
        self.check("steps 1-2 done, step 3 next", snap["done"] == ["model", "service"]
                   and snap["remaining"] == ["validation"], (snap["done"], snap["remaining"]))
        self.check("two agent sessions so far (model, service), both from Worker 1",
                   [s["step"] for s in snap["agent_sessions"]] == ["model", "service"]
                   and {s["worker_pid"] for s in snap["agent_sessions"]} == {worker.pid}, snap["agent_sessions"])
        self.check("step 3 has not started (no agent invocation, no run directory, claim still at step 2)",
                   not any(e["step"] == "validation" for e in self.invocations())
                   and not any(".validation." in d for d in snap["run_dirs"])
                   and snap["claim_progress"].get("step") == "service", (snap["run_dirs"], snap["claim_progress"]))
        launch = boundary["pid"]
        cmdline = Path(f"/proc/{launch}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
        ours = snap["claim_pid"] == launch and " launch " in cmdline and TASK in cmdline and str(self.dir) in cmdline
        if not self.check("the frozen process is this task's launch process and holds its claim", ours, cmdline):
            worker.kill()
            return False
        os.kill(worker.pid, signal.SIGKILL)  # the loss of the running Worker: its process ...
        os.kill(launch, signal.SIGKILL)      # ... and the launch process it started
        worker.wait(timeout=30)
        holder = ProcessIdentity.from_dict(StateStore(self.state).load_claim(TASK)["identity"])
        gone = self.wait(lambda: ProcessIdentity.of_pid(launch) is None or not holder.is_alive(), 30)
        self.report["killed"] = {"worker_pid": worker.pid, "launch_pid": launch}
        return self.check("Worker 1 and its launch process were killed (bounded)",
                          gone and worker.returncode is not None, self.report["killed"])

    # -- the recovery baseline (nothing running) -------------------------------------------------

    def baseline(self) -> bool:
        """Persistence after the loss: the same commits and checkpoints, step 3 pending, nothing alive."""
        snap = self.report["at_boundary"]
        self.wait(lambda: not self.processes(), 60)  # detached board publishers finish on their own
        record = self.record()
        claim = StateStore(self.state).load_claim(TASK) or {}
        commits = self.git("rev-list", "--reverse", f"{self.report['base']}..{BRANCH}").split()
        base = {"commits": commits, "checkpoints": [(c.id, c.sequence, c.step, c.status, c.commit)
                                                    for c in record.checkpoints],
                "done": record.done_steps(), "remaining": record.remaining_steps(),
                "claim": {"pid": claim.get("identity", {}).get("pid"),
                          "alive": ProcessIdentity.from_dict(claim["identity"]).is_alive() if claim else None},
                "processes": self.processes()}
        self.report["baseline"] = base
        ok = self.check("commits A and B survived; no other commit", commits == snap["commits"], commits)
        ok &= self.check("checkpoint #1 and #2 records survived unchanged",
                         base["checkpoints"] == [(c["id"], c["sequence"], c["step"], c["status"], c["commit"])
                                                 for c in snap["checkpoints"]], base["checkpoints"])
        ok &= self.check("step 1 and 2 completed, step 3 pending", base["done"] == ["model", "service"]
                         and base["remaining"] == ["validation"])
        ok &= self.check("no agent or coordinator process of this run is alive", not base["processes"],
                         base["processes"])
        ok &= self.check("the only claim is the stale one of the killed launch process",
                         base["claim"] == {"pid": snap["boundary"]["pid"], "alive": False}
                         and list(StateStore(self.state).list_claims()) == [TASK], base["claim"])
        probe = LockSet(self.state / "locks").role("backend")
        try:
            probe.acquire()
            probe.release()
            free = True
        except Exception:  # pylint: disable=broad-except
            free = False
        return self.check("no unfinished writer holds the Backend role lock", free) and ok

    # -- recovery preview (new processes, no model request) -----------------------------------------

    def preview(self) -> bool:
        """`recover TASK --show-prompt` and `checkpoints TASK --json`: only step 3 may be proposed."""
        before = {p: p.read_bytes() for p in self.state.rglob("*.json")}
        shown = self.feature("recover", TASK, "--show-prompt")
        data = json.loads(self.feature("checkpoints", TASK, "--json").stdout)
        sessions = len(self.sessions())
        cps = self.report["at_boundary"]["checkpoints"]
        self.report["preview"] = {"output": shown.stdout, "status": data["status"], "resume_from": data["resume_from"],
                                  "done": data["done"], "remaining": data["remaining"], "actions": data["actions"],
                                  "conflicts": data["conflicts"], "orphans": data["orphans"], "claim": data["claim"],
                                  "expected_ai_executions": len(data["remaining"])}
        ok = self.check("the preview proposes ONLY step 3 (never step 1 or 2)",
                        data["remaining"] == ["validation"] and data["done"] == ["model", "service"],
                        (data["done"], data["remaining"]))
        ok &= self.check("last valid checkpoint #2 at commit B", data["resume_from"] == {
            "checkpoint": cps[1]["id"], "sequence": 2, "step": "service", "commit": cps[1]["commit"]},
            data["resume_from"])
        ok &= self.check("expected remaining AI executions: ONE", len(data["remaining"]) == 1)
        ok &= self.check("no checkpoint to settle (no duplicate could be created), no conflict, no orphan",
                         not data["actions"] and not data["conflicts"] and not data["orphans"],
                         (data["actions"], data["conflicts"], data["orphans"]))
        ok &= self.check("interrupted task, stale claim to be reclaimed", data["status"] == "interrupted"
                         and data["claim"]["state"] == "stale" and "would reclaim the stale claim" in shown.stdout)
        prompt = shown.stdout
        needed = ["WHAT HAS BEEN COMPLETED", "do NOT repeat", "step `model`", "step `service`", "checkpoint #1",
                  "checkpoint #2", cps[0]["commit"][:12], cps[1]["commit"][:12], FILES["model"][0], FILES["service"][0],
                  "CURRENT STEP 3 of 3 — `validation`", "Recovery smoke: model, service and validation modules"]
        ok &= self.check("the reconstructed step-3 prompt names what is done, what remains and not to repeat it",
                         all(n in prompt for n in needed) and "CURRENT STEP 1 " not in prompt
                         and "CURRENT STEP 2 " not in prompt, [n for n in needed if n not in prompt])
        ok &= self.check("the preview changed nothing and asked no model",
                         {p: p.read_bytes() for p in self.state.rglob("*.json")} == before
                         and len(self.sessions()) == sessions and shown.returncode == 0)
        return ok

    # -- session 2: a NEW Worker resumes ---------------------------------------------------------------

    def session2(self) -> bool:
        """A new Worker process (no injector): reclaims the dead claim and runs step 3 only."""
        env = {k: v for k, v in os.environ.items() if not k.startswith("FC_TEST_")}
        with open(self.audit_dir / "worker-2.log", "wb") as log:
            worker = subprocess.Popen(  # pylint: disable=consider-using-with
                [*CLI, "--repo", str(self.repo), "--state-dir", str(self.state),
                 "--project-config", str(self.config), "work", FEATURE, "--apply"],
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, env=env, cwd=REPO_ROOT)
        self.report["worker_2_pid"] = worker.pid
        try:
            code = worker.wait(timeout=self.step_timeout + 300)
        except subprocess.TimeoutExpired:
            worker.kill()
            code = None
        out = (self.audit_dir / "worker-2.log").read_text(encoding="utf-8")
        self.report["worker_2_output"] = out
        ok = self.check("Worker 2 (a new process) finished with every task verified", code == 0
                        and "1/1 task(s) verified" in out, out[-2000:])
        return self.check("Worker 2 reclaimed the dead claim and started at step 3",
                          f"recovered {TASK}" in out and "steps 2/3 done, next: validation" in out, out[-2000:]) and ok

    # -- the acceptance checks -----------------------------------------------------------------------------

    def verify(self) -> bool:  # pylint: disable=too-many-locals
        """A. agent audit, B. checkpoint history, C. Git history, D. file contents, E. task state."""
        snap = self.report["at_boundary"]
        sessions, every = self.sessions(), self.invocations()
        w1, w2 = self.report["worker_1_pid"], self.report["worker_2_pid"]
        self.report["agent_audit"] = every
        ok = self.check("A. agent sessions: model (W1), service (W1), validation (W2) — nothing else",
                        [(s["step"], s["worker_pid"]) for s in sessions] == [("model", w1), ("service", w1),
                                                                            ("validation", w2)]
                        and len(every) == 3, [(e["step"], e["decision"], e["worker_pid"]) for e in every])
        runs = self.run_dirs()
        ok &= self.check("A. coordinator run directories: one per step, step 3 only after the restart",
                         [d.split(".")[2] for d in runs] == ["model", "service", "validation"]
                         and runs[:2] == snap["run_dirs"], runs)
        events = [json.loads(line) for line in (self.state / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        intents = [e["step"] for e in events if e["kind"] == "checkpoint-intent"]
        ok &= self.check("A. coordinator events: one checkpoint intent per step, a recovery between 2 and 3",
                         intents == ["model", "service", "validation"]
                         and any(e["kind"] == "recovered" and e.get("task") == TASK for e in events), intents)
        record = self.record()
        cps = [(c.id, c.sequence, c.step, c.status, c.commit) for c in record.checkpoints]
        before = [(c["id"], c["sequence"], c["step"], c["status"], c["commit"]) for c in snap["checkpoints"]]
        self.report["checkpoints_after"] = cps
        ok &= self.check("B. checkpoints #1/#2 unchanged (ids, commits), #3 new, sequences 1-3, one task",
                         cps[:2] == before and len(cps) == 3 and cps[2][1:4] == (3, "validation", "committed")
                         and len({c[0] for c in cps}) == 3 and record.id == TASK
                         and {c.role for c in record.checkpoints} == {"backend"}, cps)
        first, second, third = (c.attempt for c in record.checkpoints)
        ok &= self.check("B. steps 1-2 from the first attempt, step 3 from the resumed one", first == second != third)
        commits = self.git("rev-list", "--reverse", f"{self.report['base']}..{BRANCH}").split()
        parents = [self.git("rev-list", "--parents", "-n", "1", c).split()[1:] for c in commits]
        ok &= self.check("C. linear history Seed ← A ← B ← C; A and B unchanged",
                         commits == [c[4] for c in cps] and commits[:2] == snap["commits"]
                         and parents == [[self.report["base"]], [commits[0]], [commits[1]]], commits)
        trailers_ok, owned_ok = True, True
        for checkpoint in record.checkpoints:
            text = self.git("log", "-1", "--format=%(trailers:only,unfold)", checkpoint.commit)
            trailers_ok &= all(t in text for t in (f"Feature-Task: {TASK}", "Feature-Role: backend",
                                                    f"Feature-Checkpoint: {checkpoint.id}"))
            owned_ok &= self.git("diff-tree", "--no-commit-id", "--name-only", "-r",
                                 checkpoint.commit).split() == [FILES[checkpoint.step][0]]
        ok &= self.check("C. Feature-Task / Feature-Role / Feature-Checkpoint trailers", trailers_ok)
        ok &= self.check("C. each checkpoint commit holds only its step's Backend file", owned_ok)
        branches = self.git("branch", "--format=%(refname:short)").split()
        ok &= self.check("C. one feature branch", branches == ["dev", BRANCH], branches)
        contents_ok, hashes_ok = True, True
        for checkpoint in record.checkpoints:
            path, expected = FILES[checkpoint.step]
            data = (self.repo / path).read_bytes()
            contents_ok &= data.decode().rstrip("\n") == expected.rstrip("\n")
            hashes_ok &= checkpoint.files.get(path) == hashlib.sha256(data).hexdigest()
        ok &= self.check("D. the three files have the expected content", contents_ok)
        ok &= self.check("D. their SHA-256 equals the checkpoint manifests", hashes_ok)
        task = json.loads((self.state / "tasks" / f"{TASK}.json").read_text(encoding="utf-8"))
        ok &= self.check("E. task verified; all steps completed; no pending checkpoint",
                         task["state"] == "verified" and record.remaining_steps() == []
                         and not record.pending() and task["commits"] == commits, task["state"])
        ok &= self.check("E. the gate verified the combined result",
                         [g["gate"] for g in task["evidence"]["gates"] if g["status"] == "passed"] == [GATE])
        ok &= self.check("E. the checkout is clean", self.git("status", "--porcelain").strip() == "")
        ok &= self.check("no process of this run is left", self.wait(lambda: not self.processes(), 60),
                         self.processes())
        auth = [json.loads((self.state / "runs" / d / "native.json").read_text(encoding="utf-8")) for d in runs]
        ok &= self.check("every run: native-cli, model opus, subscription authentication",
                         all(a["backend"] == "native-cli" and a["model"] == MODEL
                             and a["authentication"].startswith("claude.ai subscription") for a in auth),
                         [(a["model"], a["authentication"]) for a in auth])
        inits = []
        for run in runs:
            for line in (self.state / "runs" / run / "stdout.jsonl").read_text(encoding="utf-8").splitlines():
                event = json.loads(line) if line.startswith("{") else {}
                if event.get("type") == "system" and event.get("subtype") == "init":
                    inits.append((event.get("model"), event.get("apiKeySource")))
        self.report["init_events"] = inits
        resumed = (self.state / "runs" / runs[2] / "prompt.txt").read_text(encoding="utf-8")
        ok &= self.check("the step-3 prompt actually sent lists steps 1-2 as done with their checkpoints",
                         all(n in resumed for n in ("step `model`", "step `service`", "checkpoint #1", "checkpoint #2",
                                                    "do NOT repeat", "CURRENT STEP 3 of 3")))
        return self.check("every agent session reported apiKeySource none and an Opus model",
                          len(inits) == 3 and all(k == "none" and "opus" in str(m) for m, k in inits), inits) and ok

    # -- the whole scenario ---------------------------------------------------------------------------------

    def run(self) -> dict[str, Any]:
        """prepare → session 1 → baseline → preview → session 2 → verify; stops at the first gate."""
        self.prepare()
        passed = self.session1() and self.baseline() and self.preview()
        if passed:  # never resume when the preview proposed anything but step 3
            passed = self.session2() and self.verify()
        self.report.update(passed=passed and all(c["ok"] for c in self.checks), checks=self.checks)
        (self.dir / "report.json").write_text(json.dumps(self.report, indent=2, default=str), encoding="utf-8")
        return self.report


def plan(workdir: Path, real_agent: Path) -> dict[str, Any]:
    """The real-agent plan (no model request: only --version and the subscription status)."""
    from feature_coordinator.native_backend import check_subscription, native_environment  # pylint: disable=import-outside-toplevel
    from feature_coordinator.providers import ProviderKind  # pylint: disable=import-outside-toplevel
    version = subprocess.run([str(real_agent), "--version"], capture_output=True, text=True, timeout=30,
                             check=False).stdout.strip()
    auth = check_subscription(ProviderKind.CLAUDE_CODE, str(real_agent), native_environment())
    return {"workdir": str(workdir), "repository": str(workdir / "repo"), "state": str(workdir / "state"),
            "agent": str(real_agent), "version": version, "authentication": auth.method, "authentication_ok": auth.ok,
            "model": MODEL, "task": TASK, "steps": list(STEPS), "interrupt_before": HALT_STEP,
            "model_sessions": 3, "max_sessions_enforced_by_wrapper": 3, "seconds_per_session": 300,
            "claude_max_budget_usd_per_session": 0.5, "attempts_per_step": 1, "github": "none (local tracker)",
            "commands": ["work --apply (Worker 1, injector on: steps model, service; frozen before validation)",
                         "SIGKILL Worker 1 + its launch process", "recover TASK --show-prompt (preview)",
                         "checkpoints TASK --json", "work --apply (Worker 2: validation only)"]}


def main(argv: list[str] | None = None) -> int:
    """CLI (see the module docstring)."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--workdir", required=True, type=Path)
    parser.add_argument("--agent", choices=("fake", "claude"), required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    if args.agent == "fake":
        from fc_helpers import install_fake_agent  # pylint: disable=import-outside-toplevel
        real = install_fake_agent(args.workdir / "fake-agent")
        if not args.apply:
            print("dry run — pass --apply to run the offline rehearsal")
            return 0
        return 0 if Harness(args.workdir, real, fake=True).run()["passed"] else 1
    found = shutil.which("claude")
    if not found:
        print("claude is not installed")
        return 1
    real = Path(found).resolve()
    if not args.apply:
        print(json.dumps(plan(args.workdir, real), indent=2))
        print("PLAN ONLY — no model request was made. --apply runs 3 REAL Claude Code sessions (operator approval).")
        return 0
    return 0 if Harness(args.workdir, real, fake=False).run()["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
