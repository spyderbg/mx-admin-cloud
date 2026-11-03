"""W4 optional soak run: the REAL Worker service process over many fake-agent tasks, measured.

    python fc_service_soak.py --workdir DIR [--tasks 24] [--failure-every 4] [--idle-seconds 60]

It creates a disposable repository, a fake HOME (fake credential files — the operator's logins
are never read) and a project configuration whose agents are the fake CLI (fc_fake_agent.py: no
model, no subscription use). One feature of --tasks tasks (Backend, CLI, Frontend; some chained)
is activated; every --failure-every-th task's agent fails once with a transient network error.
`python -m feature_coordinator worker start` runs as a subprocess; every second the harness samples the service's
memory (VmRSS), open file descriptors, threads and child processes, and checks that at most one
claim exists. After the feature completes the service idles for --idle-seconds, then is stopped
with `python -m feature_coordinator worker stop`. Report: DIR/soak-report.json; exit 0 only if every invariant held.

This is an OPTIONAL operator tool (not part of the test suite). It does not prove real-world
24/7 reliability: the agents are fake and the run is short.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
sys.path[:0] = [str(REPO_ROOT), str(HERE)]

from feature_coordinator import CLI  # noqa: E402  pylint: disable=wrong-import-position
from feature_coordinator.cli import EXAMPLE_CONFIG  # noqa: E402  pylint: disable=wrong-import-position
from feature_coordinator.demo import init_demo_repo  # noqa: E402  pylint: disable=wrong-import-position
from fc_helpers import FAKE_CREDENTIAL, install_fake_agent  # noqa: E402  pylint: disable=wrong-import-position

ROLES = (("backend", "backend/src"), ("cli", "cli/src"), ("frontend", "frontend/src"))


def actions(*steps: dict) -> str:
    """A fake-agent prompt carrying *steps*."""
    return "FAKE-ACTIONS:" + json.dumps(list(steps))


def proc_sample(pid: int) -> dict:
    """Memory, file descriptors, threads and children of *pid* now."""
    status = Path(f"/proc/{pid}/status").read_text(encoding="utf-8")
    fields = dict(line.split(":", 1) for line in status.splitlines() if ":" in line)
    children = {int(p): _cmd(int(p)) for p in os.listdir("/proc") if p.isdigit() and _ppid(int(p)) == pid}
    return {"t": round(time.time(), 1), "rss_kb": int(fields["VmRSS"].split()[0]),
            "fds": len(os.listdir(f"/proc/{pid}/fd")), "threads": int(fields["Threads"]), "children": children}


def _cmd(pid: int) -> str:
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")[:120]
    except OSError:
        return "?"


def _ppid(pid: int) -> int:
    try:
        return int(Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").rsplit(") ", 1)[1].split()[1])
    except (OSError, IndexError, ValueError):
        return -1


class Soak:  # pylint: disable=too-many-instance-attributes
    """One soak run in WORKDIR."""

    def __init__(self, workdir: Path, tasks: int, failure_every: int, idle_seconds: int) -> None:
        self.dir, self.tasks, self.failure_every, self.idle = workdir, tasks, failure_every, idle_seconds
        self.repo, self.state, self.home = workdir / "repo", workdir / "state", workdir / "home"
        self.config, self.audit = workdir / "config.yaml", workdir / "audit.log"
        self.env = {**os.environ, "HOME": str(self.home), "PYTHONPATH": str(REPO_ROOT)}
        for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "CODEX_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"):
            self.env.pop(name, None)
        self.report: dict = {"tasks": tasks, "failure_every": failure_every, "idle_seconds": idle_seconds}

    def feature(self, *args: str, timeout: float = 120) -> subprocess.CompletedProcess[str]:
        """Run `python -m feature_coordinator` for this soak's repository, state and configuration."""
        return subprocess.run([*CLI, "--repo", str(self.repo), "--state-dir",
                               str(self.state), "--project-config", str(self.config), *args], capture_output=True,
                              text=True, env=self.env, timeout=timeout, check=False, cwd=REPO_ROOT)

    def prepare(self) -> None:
        """Repository, fake HOME and agent, configuration, and the activated soak feature."""
        for path in (self.repo, self.state, self.home):
            if path.exists():
                raise SystemExit(f"{path} exists: use an empty --workdir")
        self.dir.mkdir(parents=True, exist_ok=True)
        for rel in (".claude/.credentials.json", ".codex/auth.json"):
            (self.home / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.home / rel).write_text(json.dumps({"token": FAKE_CREDENTIAL}), encoding="utf-8")
        agent = install_fake_agent(self.dir / "agents")
        init_demo_repo(self.repo)
        data = yaml.safe_load(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
        for name in ("claude", "codex"):
            data["providers"][name]["executable"] = str(agent)
        data["runtime"].update(task_timeout_seconds=120, terminate_grace_seconds=2)
        worker = data["worker"]
        worker["autonomous"]["enabled"] = True
        worker.update(idle_poll_seconds=2, heartbeat_seconds=2)
        worker["retry"]["transient"].update(initial_delay_seconds=1, max_delay_seconds=4)
        self.config.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        tasks = []
        for i in range(self.tasks):
            role, folder = ROLES[i % len(ROLES)]
            key = f"t{i:03d}"
            steps = [{"do": "append", "path": str(self.audit), "text": f"{key}\n"}]
            if self.failure_every and i % self.failure_every == self.failure_every - 1:
                steps.append({"do": "once", "path": str(self.dir / f"{key}.failed"), "actions": [
                    {"do": "exit", "code": 1, "message": "API Error: read ECONNRESET"}]})
            steps.append({"do": "write", "path": f"{folder}/soak_{key}.txt", "text": f"{key}\n"})
            task = {"key": key, "role": role, "title": actions(*steps)}
            if i and i % 4 == 0:
                task["depends_on"] = [f"t{i - 1:03d}"]
            tasks.append(task)
        spec = self.dir / "soak.yaml"
        spec.write_text(yaml.safe_dump({"slug": "soak", "title": "W4 soak", "tasks": tasks}), encoding="utf-8")
        for step in (("plan", str(spec), "--apply"), ("activate", "soak", "--apply")):
            result = self.feature(*step)
            if result.returncode != 0:
                raise SystemExit(f"{' '.join(step)} failed: {result.stdout}{result.stderr}")

    def run(self) -> bool:
        """Run the service until the feature is done and the idle period passed; then verify."""
        log = self.dir / "service.log"
        argv = [*CLI, "--repo", str(self.repo), "--state-dir", str(self.state),
                "--project-config", str(self.config), "worker", "start"]
        samples, max_claims, started = [], 0, time.time()
        with open(log, "wb") as out:
            proc = subprocess.Popen(argv, stdout=out, stderr=subprocess.STDOUT, env=self.env,  # pylint: disable=consider-using-with
                                    start_new_session=True, cwd=REPO_ROOT)
        done_at = None
        try:
            while proc.poll() is None:
                samples.append(proc_sample(proc.pid))
                max_claims = max(max_claims, len(list((self.state / "claims").glob("*.json"))))
                states = [json.loads(p.read_text(encoding="utf-8"))["state"]
                          for p in (self.state / "tasks").glob("soak.*.json")]
                if done_at is None and states and all(s == "verified" for s in states):
                    done_at = time.time()
                    print(f"all {len(states)} tasks verified after {done_at - started:.0f}s; idling {self.idle}s",
                          flush=True)
                if done_at is not None and time.time() - done_at >= self.idle:
                    self.feature("worker", "stop")
                    break
                if time.time() - started > 3600:
                    print("timed out after an hour", flush=True)
                    self.feature("worker", "stop")
                    break
                time.sleep(1)
            code = proc.wait(timeout=180)
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid, 9)
        return self.verify(code, samples, max_claims, done_at, started)

    def verify(self, code: int, samples: list[dict], max_claims: int, done_at: float | None,  # pylint: disable=too-many-arguments,too-many-locals
               started: float) -> bool:
        """Check the invariants; write and print the report."""
        audit = self.audit.read_text(encoding="utf-8").split() if self.audit.exists() else []
        failing = {f"t{i:03d}" for i in range(self.tasks)
                   if self.failure_every and i % self.failure_every == self.failure_every - 1}
        expected = {f"t{i:03d}": 2 if f"t{i:03d}" in failing else 1 for i in range(self.tasks)}
        runs = {k: audit.count(k) for k in expected}
        base = subprocess.run(["git", "-C", str(self.repo), "merge-base", "dev", "feature/soak"], capture_output=True,
                              text=True, check=True).stdout.strip()
        trailers = subprocess.run(["git", "-C", str(self.repo), "log", "--format=%(trailers:key=Feature-Task,"
                                   "valueonly)", f"{base}..feature/soak"], capture_output=True, text=True,
                                  check=True).stdout.split()
        service = json.loads((self.state / "service" / "worker.json").read_text(encoding="utf-8"))
        warm = samples[len(samples) // 10:] or samples
        idle = [s for s in samples if done_at and s["t"] >= done_at + 5]  # the last launch has exited by then
        lingering = sorted({pid for a, b, c in zip(idle, idle[1:], idle[2:]) for pid in a["children"]
                            if pid in b["children"] and pid in c["children"]})  # not a brief `git` call
        checks = {
            "service exited 0": code == 0,
            "every task verified": all(json.loads((self.state / "tasks" / f"soak.{k}.json").read_text(
                encoding="utf-8"))["state"] == "verified" for k in expected),
            "each agent ran as expected (1, or 2 after one transient failure)": runs == expected,
            "one commit per task, none duplicated": sorted(trailers) == sorted(f"soak.{k}" for k in expected),
            "never more than one claim": max_claims <= 1,
            "no claim left": not list((self.state / "claims").glob("*.json")),
            "service recorded OFFLINE": service["state"] == "OFFLINE",
            "file descriptors bounded": max(s["fds"] for s in warm) - min(s["fds"] for s in warm) <= 8,
            "threads bounded": max(s["threads"] for s in samples) <= 4,
            "no lingering child process while idle": not lingering,
        }
        self.report.update({
            "exit_code": code, "duration_seconds": round(time.time() - started), "checks": checks,
            "agent_runs": sum(runs.values()), "max_claims": max_claims,
            "rss_kb": {"first": samples[0]["rss_kb"] if samples else None, "max": max(s["rss_kb"] for s in samples),
                       "last": samples[-1]["rss_kb"] if samples else None},
            "fds": {"min": min(s["fds"] for s in samples), "max": max(s["fds"] for s in samples)},
            "threads_max": max(s["threads"] for s in samples), "samples": len(samples),
            "events": sum(1 for _ in (self.state / "events.jsonl").open(encoding="utf-8")),
            "counters": service.get("counters"),
            "idle_children_seen": sorted({cmd for s in idle for cmd in s["children"].values()}),
        })
        (self.dir / "soak-report.json").write_text(json.dumps(self.report, indent=2), encoding="utf-8")
        for name, ok in checks.items():
            print(f"  {'ok  ' if ok else 'FAIL'} {name}")
        print(json.dumps({k: self.report[k] for k in ("duration_seconds", "agent_runs", "rss_kb", "fds",
                                                       "threads_max", "events", "counters")}))
        return all(checks.values())


def main(argv: list[str] | None = None) -> int:
    """Run one soak; exit 0 only if every invariant held."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--workdir", required=True, type=Path)
    parser.add_argument("--tasks", type=int, default=24)
    parser.add_argument("--failure-every", type=int, default=4, help="every Nth task fails once (0: never)")
    parser.add_argument("--idle-seconds", type=int, default=60)
    args = parser.parse_args(argv)
    soak = Soak(args.workdir.resolve(), args.tasks, args.failure_every, args.idle_seconds)
    soak.prepare()
    return 0 if soak.run() else 1


if __name__ == "__main__":
    sys.exit(main())
