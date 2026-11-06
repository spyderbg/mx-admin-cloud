"""W4.6.1 (R2): the reviewer as a separate process — the two files between the Worker service and that process, its
process TAG, and the process itself: `python -m feature_coordinator github review exec RUN_DIR` (internal).

The Worker service (review_worker.py) never waits for the model. For one review attempt it writes `request.json`
into the attempt's run directory (`<state>/runs/review/<entry>/attempt-<n>/`: the prompt, the answer's JSON
schema, the timeout, and what it needs to judge the answer later — the changed files and its own notes), then
starts this process with a random tag in `FC_REVIEW_RUN`. The process:

  * refuses unless the run directory lies under the state directory's `runs/review/` and it carries the request's
    tag (an untagged reviewer could not be found again after a crash, so it never runs), and runs each attempt
    once (an existing `result.json` is never overwritten);
  * runs the R1 reviewer UNCHANGED (reviewer.ClaudeCodeReviewer: Claude Code on the subscription, no tools, no MCP
    server, an empty working directory, the existing supervisor and its deadline). The one addition is the tag in
    the CLI's environment, so every process of the attempt can be found — and stopped — after any crash;
  * is cancelled by SIGTERM/SIGINT (the service's graceful stop) and by `worker pause` / `worker stop` (the
    control file, read at most once a second — the W4.5 planner's rule): a cancelled run is recorded as such;
  * writes `result.json` (status, answer, error, the run's facts) atomically and exits.

It reads nothing from GitHub, takes no coordinator lock and writes no record: only the service judges the answer,
records it and publishes it (review.py). A missing or mismatched `result.json` is an interrupted attempt, which
stays counted (R1). Its environment is the native allowlist plus the tag: no GitHub token or API key reaches it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable, Mapping

from .errors import CoordinatorError
from .locks import ProcessIdentity
from .model import utc_now
from .review_context import ReviewContext
from .review_result import FileFacts
from .reviewer import RESULT_STATUSES, ReviewRequest, ReviewResult, Reviewer
from .store import atomic_write_json
from .supervisor import stop_requested, tagged_processes, terminate_pids

REVIEW_TAG_ENV = "FC_REVIEW_RUN"   # set on the reviewer process and its CLI; never on an implementation agent
EXCHANGE_SCHEMA = 1
_REQUEST_KEYS = frozenset({"schema", "key", "attempt", "tag", "feature", "prompt", "answer_schema",
                           "timeout_seconds", "files", "notes", "input_sha256", "created_at"})
_RESULT_KEYS = frozenset({"schema", "key", "attempt", "tag", "status", "output", "error", "details", "finished_at"})


def request_path(run_dir: Path | str) -> Path:
    """The service's request for one attempt."""
    return Path(run_dir) / "request.json"


def result_path(run_dir: Path | str) -> Path:
    """The reviewer process's result for one attempt."""
    return Path(run_dir) / "result.json"


def write_request(run_dir: Path, *, key: str, attempt: int, tag: str, feature: str, context: ReviewContext,
                  timeout_seconds: float) -> None:
    """The attempt's request (mode 0600, atomic). It holds the full prompt: never logged, never printed."""
    run_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(request_path(run_dir), {
        "schema": EXCHANGE_SCHEMA, "key": key, "attempt": attempt, "tag": tag, "feature": feature,
        "prompt": context.prompt, "answer_schema": context.schema, "timeout_seconds": timeout_seconds,
        "files": {path: {"status": f.status, "lines": f.lines} for path, f in context.files.items()},
        "notes": list(context.notes), "input_sha256": context.input_sha256, "created_at": utc_now()})


def _exchange(path: Path, keys: frozenset[str], key: str, running: Mapping[str, Any]) -> dict[str, Any] | None:
    """A request or result file of exactly this entry's running attempt (None: missing, unreadable or another's)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or set(data) != keys or data["schema"] != EXCHANGE_SCHEMA:
        return None
    if (data["key"], data["attempt"], data["tag"]) != (key, running.get("attempt"), running.get("tag")):
        return None
    return data


def load_context(key: str, running: Mapping[str, Any]) -> ReviewContext | None:
    """What the service needs to judge the attempt's answer: the context the reviewer was given."""
    data = _exchange(request_path(running["run_dir"]), _REQUEST_KEYS, key, running)
    if data is None:
        return None
    try:
        files = {str(p): FileFacts(str(f["status"]), f["lines"]) for p, f in dict(data["files"]).items()}
    except (TypeError, KeyError, ValueError):
        return None
    return ReviewContext(prompt="", schema=dict(data["answer_schema"]), files=files,
                         notes=[str(n) for n in data["notes"]], input_sha256=str(data["input_sha256"]))


def load_result(key: str, running: Mapping[str, Any]) -> ReviewResult | None:
    """The attempt's result as the reviewer process recorded it (None: none, or not this attempt's)."""
    data = _exchange(result_path(running["run_dir"]), _RESULT_KEYS, key, running)
    if data is None or data["status"] not in RESULT_STATUSES:
        return None
    details = data["details"] if isinstance(data["details"], dict) else {}
    return ReviewResult(str(data["status"]), output=data["output"], error=str(data["error"] or ""), details=details)


def _identity(data: Any) -> ProcessIdentity | None:
    try:
        return ProcessIdentity.from_dict(data)
    except (KeyError, TypeError, ValueError):
        return None


def attempt_processes(running: Mapping[str, Any]) -> tuple[str, list[int]]:
    """Whether a Worker attempt's processes live — decided by its TAG, the authority for "ours":

    gone     — no process carries the tag (a recorded identity that is alive WITHOUT the tag is not ours: a reused
               or tampered record; it is never signalled and never blocks);
    running  — tagged processes live, and the recorded reviewer process is one of them (or not recorded yet: a
               service crash between starting it and recording it);
    orphaned — the recorded reviewer process has ended but tagged processes it started are left (its CLI): they
               can no longer deliver a result."""
    tag = str(running.get("tag") or "")
    pids = tagged_processes(REVIEW_TAG_ENV, tag) if tag else []
    if not pids:
        return "gone", []
    identity = _identity(running.get("process"))
    if identity is not None and not (identity.is_alive() and identity.pid in pids):
        return "orphaned", pids
    return "running", pids


def stop_attempt(running: Mapping[str, Any], grace_seconds: float) -> list[int]:
    """Stop one Worker attempt; returns the processes still alive. SIGTERM goes to the recorded reviewer process
    FIRST (it cancels its CLI through the supervisor and records the attempt), SIGKILL after *grace_seconds*; then
    every process still carrying the tag (an orphaned CLI) gets the same, briefly. The tag is re-checked before
    every signal, so a PID reused meanwhile — or anything unrelated — is never signalled."""
    tag = str(running.get("tag") or "")
    if not tag:
        return []

    def ours(pid: int) -> bool:
        return pid in tagged_processes(REVIEW_TAG_ENV, tag)
    identity = _identity(running.get("process"))
    survivors: list[int] = []
    if identity is not None and identity.is_alive() and ours(identity.pid):
        survivors = terminate_pids([identity.pid], grace_seconds=grace_seconds,
                                   still_ours=lambda pid: identity.is_alive() and ours(pid))
    left = [pid for pid in tagged_processes(REVIEW_TAG_ENV, tag) if pid not in survivors]
    if left:
        survivors += terminate_pids(left, grace_seconds=min(grace_seconds, 5.0), still_ours=ours)
    return sorted(set(survivors))


def tagged_run(run: Callable[..., Any], tag: str) -> Callable[..., Any]:
    """The supervisor with the attempt's tag added to the CLI's environment (and nothing else changed)."""
    def wrapped(argv: Any, *, env: Mapping[str, str] | None = None, **kwargs: Any) -> Any:
        return run(argv, env={**dict(env or {}), REVIEW_TAG_ENV: tag}, **kwargs)
    return wrapped


def process_environment(tag: str, source: Mapping[str, str] | None = None) -> dict[str, str]:
    """The reviewer process's environment: the native allowlist (no GitHub token, no API key), the tag."""
    from .native_backend import native_environment  # pylint: disable=import-outside-toplevel
    return {**native_environment(source=source), REVIEW_TAG_ENV: tag, "PYTHONUNBUFFERED": "1"}


def run_exec(config: Any, state_dir: Path, run_dir: Path, *, reviewer: Reviewer | None = None,  # pylint: disable=too-many-arguments
             environ: Mapping[str, str] | None = None, cancelled: Callable[[], bool] | None = None,
             echo: Callable[[str], None] = print) -> int:
    """The reviewer process (see the module docstring). Refusals raise CoordinatorError and write nothing."""
    environ = os.environ if environ is None else environ
    run_dir = Path(run_dir).resolve()
    root = (Path(state_dir) / "runs" / "review").resolve()
    if root not in run_dir.parents:
        raise CoordinatorError(f"refused: {run_dir} is not a review attempt directory under {root}")
    try:
        request = json.loads(request_path(run_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CoordinatorError(f"refused: no readable request in {run_dir}: {type(exc).__name__}") from exc
    if not isinstance(request, dict) or set(request) != _REQUEST_KEYS or request["schema"] != EXCHANGE_SCHEMA:
        raise CoordinatorError(f"refused: the request in {run_dir} is not a review request")
    tag = environ.get(REVIEW_TAG_ENV, "")
    if not tag or tag != request["tag"]:
        raise CoordinatorError("refused: this process does not carry the attempt's tag — only the Worker service "
                               "starts a reviewer process (`github review run` reviews in the foreground)")
    if result_path(run_dir).exists():
        raise CoordinatorError("refused: this attempt already has a result (an attempt runs once)")
    if cancelled is None:
        from .intake_poll import planning_hooks  # pylint: disable=import-outside-toplevel
        from .store import StateStore  # pylint: disable=import-outside-toplevel
        cancelled = planning_hooks(StateStore(Path(state_dir)), lambda: None)["cancelled"]
    if not config.github.review.enabled:
        result = ReviewResult("cancelled", error="github.review.enabled is false: the reviewer did not run")
    else:
        if reviewer is None:
            from .reviewer import ClaudeCodeReviewer  # pylint: disable=import-outside-toplevel
            from .supervisor import supervise  # pylint: disable=import-outside-toplevel
            reviewer = ClaudeCodeReviewer(config, run=tagged_run(supervise, tag))
        result = reviewer.review(ReviewRequest(key=request["key"], prompt=request["prompt"],
                                               schema=request["answer_schema"], run_dir=run_dir,
                                               timeout_seconds=float(request["timeout_seconds"]),
                                               cancelled=cancelled))
        if result.status not in ("ok", "refused") and (stop_requested() or cancelled()):
            result = ReviewResult("cancelled", error=f"the reviewer run was cancelled (it ended {result.status})",
                                  details=result.details)
    atomic_write_json(result_path(run_dir), {
        "schema": EXCHANGE_SCHEMA, "key": request["key"], "attempt": request["attempt"], "tag": tag,
        "status": result.status, "output": result.output, "error": result.error,
        "details": json.loads(json.dumps(result.details, default=str)), "finished_at": utc_now()})
    echo(f"review {request['key']} attempt {request['attempt']}: {result.status}")
    return 0
