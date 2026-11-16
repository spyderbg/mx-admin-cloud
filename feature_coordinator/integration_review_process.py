"""W4.7 (M4): the reviewer process of one integration review attempt — started, waited for, stopped and collected —
and the process itself (`python -m feature_coordinator integration review-exec RUN_DIR`, internal).

It is the W4.6.1 R2 reviewer process (review_process.py) reused unchanged: the attempt's `request.json` (the prompt,
the answer schema, the changed files and the coordinator's notes), a random process TAG in `FC_REVIEW_RUN` on the
process and on its Claude Code CLI, review_process.run_exec running reviewer.ClaudeCodeReviewer (subscription only, no
tools, no MCP server, an empty working directory, the supervisor's timeout and cancellation) and writing
`result.json` once, atomically. The integration review only adds:

  * where: `<state>/runs/review/<job>/attempt-<tag>/` (under `runs/review/`, so run_exec's location check holds);
  * who may run: `github.integration.enabled`, the configured reviewer eligible, the run directory and tag must be the
    job's OPEN review attempt (an evidence entry recorded BEFORE the process started), and this process's own
    configuration must yield the attempt's recorded reviewer identity (provider, kind, model, `config_sha256`)
    (Codex review of M4, F3) and its CURRENT `github.review.independence` must accept the attempt's recorded
    independence (independence_problem; Codex re-review, finding 1) — anything else writes a `cancelled` result and
    no model request is made. A policy changed while the reviewer runs does not stop it: the waiting side reads the
    configuration again before any decision (integration_review_context.policy_problem);
  * what cancels: a stop signal, or the job leaving IN_REVIEW (an operator abort) — read from the job record at most
    once a second. Never the Worker's `pause`: the production Worker may stay paused while an operator reviews.

The waiting side (AttemptProcess.wait) decides "ours" by the TAG alone (a recorded PID could be reused): it waits until
no process carries the tag, and stops the tagged processes (SIGTERM, grace, SIGKILL, the tag re-checked before every
signal) at the attempt's DEADLINE (timeout + termination grace + slack, recorded in the attempt), on a stop signal of
the waiting command, or when the job left IN_REVIEW. AttemptProcess.collect turns the result file into a `review-run`
entry, judged with the context the request recorded (review_result.validate_output / decide) once its run facts are
found to be the attempt's reviewer (execution_problem; else `refused`, the answer unused): a late non-answer is a
timeout; no result file is `not-started` when the CLI never launched (its output file does not exist — no model
request was made) and `interrupted` otherwise. The reviewer process receives the native environment allowlist plus
its tag: no GitHub token, no API key.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from .config import ProjectConfig
from .errors import CoordinatorError
from .integration import IntegrationJobs, JobState, clean_text, open_review_attempt, parse_record_time
from .integration_review_context import independence_problem, reviewer_identity
from .integration_schema import UNCOUNTED_RUN_STATUSES
from .model import utc_now
from .native_backend import model_substitution
from .review_config import reviewer_problem
from .review_process import REVIEW_TAG_ENV, attempt_processes, load_context, load_result, process_environment, \
    result_path, run_exec, stop_attempt, write_request
from .review_context import ReviewContext
from .review_result import decide, validate_output
from .reviewer import ReviewResult
from .supervisor import stop_requested

REVIEW_SLACK_SECONDS = 120.0     # the reviewer process's start-up and bookkeeping beyond its timeout and grace (R2)
POLL_SECONDS = 0.2
LAUNCHED_MARKER = "stdout.jsonl"  # ClaudeCodeReviewer's CLI output: it exists once the CLI was started
_DETAIL_KEYS = ("auth", "cli_version", "model", "reported_model", "api_key_source", "exit_code", "duration_seconds")
# start(argv tail, log file name, environment) -> a started process with .pid, .poll() and .wait() (worker.cli_spawner)
Starter = Callable[[list[str], str, Mapping[str, str]], Any]


def attempt_key(job_id: str, attempt: Mapping[str, Any]) -> str:
    """The request/result key of an attempt (review_process: matched together with the attempt number and tag)."""
    return f"{job_id}/{attempt['id']}"


def running_of(attempt: Mapping[str, Any]) -> dict[str, Any]:
    """The R2 `running` shape (review_process) of an attempt: what finds, stops and collects its process. Its process
    identity is not recorded (evidence is immutable): the TAG is the authority for "ours"."""
    return {"attempt": int(str(attempt["id"]).split("-")[1]), "tag": attempt["tag"], "run_dir": attempt["run_dir"],
            "process": None}


def deadline_text(seconds: float) -> str:
    """ISO-8601 UTC, whole seconds, of an epoch timestamp (an attempt's deadline)."""
    return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_entry(attempt: Mapping[str, Any], status: str, error: str, *, result: ReviewResult | None = None,
              answer: tuple[str, Any, str] | None = None, result_sha: str = "") -> dict[str, Any]:
    """A `review-run` entry for *attempt* (its id and digest are assigned when it is recorded). *answer*: (outcome,
    review_result.Review, digest of the raw answer) of a valid answer (status ok)."""
    ended = utc_now()
    started = parse_record_time(attempt["at"], "at")
    details = {k: (clean_text(v, 200) if isinstance(v, str) else v)
               for k, v in ((result.details if result else {}) or {}).items()
               if k in _DETAIL_KEYS and (v is None or isinstance(v, (str, int, float, bool)))}
    outcome, review, output_sha = answer if answer is not None else (None, None, "")
    return {"kind": "review-run", "id": "", "at": ended, "attempt": attempt["id"], "candidate": attempt["candidate"],
            "head_sha": attempt["head_sha"], "tree_sha": attempt["tree_sha"], "status": status,
            "counted": status not in UNCOUNTED_RUN_STATUSES, "outcome": outcome,
            "verdict": review.verdict if review else None, "summary": review.summary if review else "",
            "findings": review.findings if review else [], "limitations": review.limitations if review else [],
            "output_sha256": output_sha, "result_sha256": result_sha, "error": clean_text(error, 1000),
            "details": details, "started_at": attempt["at"], "ended_at": ended,
            "duration_seconds": round(max(0.0, (parse_record_time(ended, "at") - started).total_seconds()), 1),
            "reasons": []}


class AttemptProcess:  # pylint: disable=too-many-instance-attributes
    """Start, wait for, stop and collect the reviewer process of integration review attempts (module docstring)."""

    def __init__(self, config: ProjectConfig, jobs: IntegrationJobs, *, start: Starter | None,  # pylint: disable=too-many-arguments
                 wall: Callable[[], float], stop: Callable[[], bool], fault: Callable[[str], None],
                 poll_seconds: float = POLL_SECONDS) -> None:
        self.config, self.jobs, self.start = config, jobs, start
        self.wall, self.stop, self.fault, self.poll_seconds = wall, stop, fault, poll_seconds
        self.handles: dict[str, Any] = {}

    def launch(self, job_id: str, attempt: Mapping[str, Any], context: ReviewContext) -> tuple[Any, str]:
        """Write the attempt's request and start its reviewer process: (handle, '') or (None, why it did not start —
        no model request was made)."""
        running = running_of(attempt)
        write_request(Path(attempt["run_dir"]), key=attempt_key(job_id, attempt), attempt=running["attempt"],
                      tag=attempt["tag"], feature=job_id, context=context,
                      timeout_seconds=self.config.github.review.timeout_seconds)
        self.fault("after-request")
        if self.start is None:
            return None, "no reviewer process can be started here"
        try:
            handle = self.start(["integration", "review-exec", str(attempt["run_dir"])],
                                f"integration-review-{job_id}.{attempt['id']}.log", process_environment(attempt["tag"]))
        except OSError as exc:
            return None, f"the reviewer process could not be started: {exc}"
        self.handles[attempt["tag"]] = handle
        return handle, ""

    def wait(self, job_id: str, attempt: Mapping[str, Any]) -> str | None:
        """Until no process carries the attempt's tag. Its processes are stopped at the attempt's deadline
        ("deadline"), on a stop signal ("cancelled"), or when the job left IN_REVIEW or the attempt is no longer the
        open one ("meanwhile"); an orphaned CLI (its reviewer process ended) is stopped too. None: it ended."""
        running = running_of(attempt)
        deadline = parse_record_time(attempt["deadline"], "deadline").timestamp()
        grace = float(self.config.runtime.terminate_grace_seconds)
        checked_at = float("-inf")
        while True:
            handle = self.handles.get(attempt["tag"])
            if handle is not None and handle.poll() is not None:     # reaped: never a zombie that looks alive
                self.handles.pop(attempt["tag"], None)
            state, _pids = attempt_processes(running)
            if state == "gone":
                return None
            why = None
            if state == "orphaned":
                why = "orphaned"
            elif self.wall() >= deadline:
                why = "deadline"
            elif self.stop():
                why = "cancelled"
            elif time.monotonic() - checked_at >= 1.0:
                checked_at = time.monotonic()
                current = self.jobs.load(job_id)
                opened = open_review_attempt(current)
                if current.state is not JobState.IN_REVIEW or opened is None or opened["id"] != attempt["id"]:
                    why = "meanwhile"
            if why is not None:
                stop_attempt(running, grace)
                handle = self.handles.pop(attempt["tag"], None)
                if handle is not None:
                    with contextlib.suppress(Exception):
                        handle.wait(timeout=grace + 5)
                return None if why == "orphaned" else why
            self.fault("while-waiting")
            time.sleep(self.poll_seconds)

    def collect(self, job_id: str, attempt: Mapping[str, Any], why: str | None) -> dict[str, Any]:  # pylint: disable=too-many-return-statements
        """The run of a finished attempt from its result file, judged with the context its request recorded — never a
        reviewer run here."""
        running, key, run_dir = running_of(attempt), attempt_key(job_id, attempt), Path(attempt["run_dir"])
        context, result = load_context(key, running), load_result(key, running)
        raw = result_path(run_dir)
        result_sha = hashlib.sha256(raw.read_bytes()).hexdigest() if result is not None and raw.is_file() else ""
        overdue = why == "deadline" or self.wall() >= parse_record_time(attempt["deadline"], "deadline").timestamp()
        if result is None:
            if not (run_dir / LAUNCHED_MARKER).exists():
                return run_entry(attempt, "not-started", "the reviewer process ended without starting the reviewer "
                                                         "(no model request was made)")
            return run_entry(attempt, "timeout" if overdue else "interrupted",
                             "the reviewer process was stopped at its deadline" if overdue else
                             "the reviewer process ended without a result (it crashed or was killed)")
        if overdue and result.status != "ok":
            result = ReviewResult("timeout", error=f"the reviewer did not answer before the attempt's deadline "
                                                   f"({attempt['deadline']})", details=result.details)
        if context is None or context.input_sha256 != attempt["context"]["input_sha256"]:
            return run_entry(attempt, "failed", "the attempt's request does not match the recorded attempt",
                             result=result, result_sha=result_sha)
        mismatch = execution_problem(attempt["reviewer"], result.details or {}) if result.status == "ok" else ""
        if mismatch:   # never another model's answer under the attempt's reviewer (F3): refused, the answer unused
            return run_entry(attempt, "refused", f"the reviewer run is not the attempt's recorded reviewer "
                                                 f"{shown_identity(attempt['reviewer'])}: {mismatch}; its answer is "
                                                 "not used", result=result, result_sha=result_sha)
        if result.status != "ok":
            return run_entry(attempt, result.status, result.error or result.status, result=result,
                             result_sha=result_sha)
        output_sha = hashlib.sha256(json.dumps(result.output, sort_keys=True, ensure_ascii=False,
                                               default=str).encode("utf-8")).hexdigest()
        review, problems = validate_output(result.output, head_sha=attempt["head_sha"], files=context.files,
                                           max_findings=self.config.github.review.max_findings)
        if review is not None:
            outcome, reason = decide(review, list(attempt["context"]["notes"]))
            if outcome != "ERROR":
                return run_entry(attempt, "ok", "", result=result, answer=(outcome, review, output_sha),
                                 result_sha=result_sha)
            problems = [reason]
        entry = run_entry(attempt, "invalid", "; ".join(problems[:8]), result=result, result_sha=result_sha)
        entry["reasons"] = [clean_text(p) for p in problems[:8]]
        return entry


def shown_identity(reviewer: Mapping[str, Any]) -> str:
    """`provider/kind/model (config <digest prefix>)` of a reviewer identity (integration_review_context)."""
    return (f"{reviewer.get('provider')}/{reviewer.get('kind')}/{reviewer.get('model')} "
            f"(config {str(reviewer.get('config_sha256') or '?')[:12]})")


def execution_problem(reviewer: Mapping[str, Any], details: Mapping[str, Any]) -> str:
    """Why the facts of a finished `ok` reviewer run are not the attempt's recorded *reviewer* ('' : they are) — Codex
    review of M4, F3. The model the reviewer process ran (ClaudeCodeReviewer's `model`: the configured model, or `CLI
    default`) must be the attempt's model, and the model Claude Code reported in its init event must be of that model
    (native_backend.model_substitution, the post-run check the reviewer itself makes). Missing facts never agree."""
    requested = "" if reviewer.get("model") == "default" else str(reviewer.get("model") or "")
    ran, reported = details.get("model"), details.get("reported_model")
    if not isinstance(ran, str) or not ran:
        return "the run does not report which model the reviewer process ran"
    if ran != (requested or "CLI default"):
        return f"the attempt was recorded for model {reviewer.get('model')!r}, but the reviewer process ran {ran!r}"
    if not isinstance(reported, str) or not reported:
        return "the run does not report the model Claude Code used"
    return model_substitution(requested, reported)


# -- the reviewer process (`integration review-exec RUN_DIR`, internal) ---------------------------------------------

def integration_exec(config: ProjectConfig, state_dir: Path, run_dir: Path, *, reviewer: Any = None,  # pylint: disable=too-many-arguments
                     environ: Mapping[str, str] | None = None, echo: Callable[[str], None] = print) -> int:
    """One integration review attempt as a separate reviewer process: review_process.run_exec unchanged, with the
    integration rules of who may run and what cancels (module docstring)."""
    from .store import StateStore  # pylint: disable=import-outside-toplevel
    environ = os.environ if environ is None else environ
    jobs = IntegrationJobs(StateStore(Path(state_dir)))
    try:
        request = json.loads((Path(run_dir) / "request.json").read_text(encoding="utf-8"))
        job_id = str(request.get("feature")) if isinstance(request, dict) else ""
    except (OSError, ValueError):
        job_id = ""
    refusal = "" if config.github.integration.enabled else ("github.integration.enabled is false: the reviewer did "
                                                            "not run")
    refusal = refusal or reviewer_problem(config) or attempt_problem(jobs, job_id, Path(run_dir), environ, config)
    state = {"at": float("-inf"), "stop": False}

    def cancelled() -> bool:
        now = time.monotonic()
        if now - float(state["at"]) >= 1.0:     # the job record is read at most once a second
            state["at"] = now
            state["stop"] = bool(attempt_problem(jobs, job_id, Path(run_dir), environ))
        return stop_requested() or bool(state["stop"])
    return run_exec(config, Path(state_dir), Path(run_dir), reviewer=reviewer, environ=environ, cancelled=cancelled,
                    echo=echo, refusal=refusal)


def attempt_problem(jobs: IntegrationJobs, job_id: str, run_dir: Path, environ: Mapping[str, str],
                    config: ProjectConfig | None = None) -> str:
    """Why *run_dir* is not the open review attempt of the IN_REVIEW job *job_id* with this process's tag ('' : it
    is). With *config* (the reviewer process's own configuration), also why that configuration is not the reviewer the
    attempt recorded (integration_review_context.reviewer_identity: provider, kind, model and their configuration
    digest) — the reviewer runs only as the attempt says (Codex review of M4, F3) — or why its independence policy
    does not accept the attempt's recorded independence (independence_problem: a `require` set after the attempt was
    recorded under `prefer`; Codex re-review, finding 1)."""
    try:
        job = jobs.load(job_id)
    except (CoordinatorError, OSError, ValueError) as exc:
        return f"the attempt's integration job cannot be read: {clean_text(exc, 200)}"
    opened = open_review_attempt(job)
    if job.state is not JobState.IN_REVIEW or opened is None:
        return f"{job_id} is {job.state.value} without an open review attempt: the reviewer did not run"
    if Path(opened["run_dir"]).resolve() != run_dir.resolve() or opened["tag"] != environ.get(REVIEW_TAG_ENV):
        return f"this is not {job_id}'s open review attempt {opened['id']}: the reviewer did not run"
    if config is not None and opened["reviewer"] != reviewer_identity(config):
        return (f"review attempt {opened['id']} was recorded for the reviewer {shown_identity(opened['reviewer'])}, "
                f"but this reviewer process's configuration is {shown_identity(reviewer_identity(config))}: the "
                "reviewer did not run")
    refused = independence_problem(config, opened["independence"]) if config is not None else ""
    return f"review attempt {opened['id']}: {refused}: the reviewer did not run" if refused else ""
