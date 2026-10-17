# Coordinator Operations — Launch, Stop, Resume, Recover (Phase 3)

> Owner role: **coordination**. Status: **Phase 3 deliverable, awaiting operator approval** (2026-10-17).
> Entry point: `python scripts/feature.py` (use the repository's `.venv` Python). Every command that changes something is a **dry run unless `--apply`** is given.
> Global options: `--repo PATH` (default: this checkout), `--state-dir PATH` (default: `~/.local/state/mx-admin-agents/`), `--project-config FILE` (default: built-in configuration).

## 1. Before anything runs

| Step | Command | What it proves |
| --- | --- | --- |
| State directory | `feature.py doctor --apply` | Local filesystem (FUSE and network rejected), mode 0700, two-process `flock` contention test |
| Runners | `feature.py runners` | `claude` and `codex` exist, support every required flag, and get no bypass flag; prints each role's exact command line. **No model call** |
| Containment | `feature.py sandbox-check` (dry run) | Each role's writable roots, read-only islands and hidden secrets on `--repo` |
| | `feature.py sandbox-check --apply` | The full containment matrix in a **disposable** repository ([agent-ownership §3](agent-ownership.md#3-containment-matrix-exit-gate-evidence)) |

## 2. One feature, step by step

```bash
feature.py plan spec.yaml --apply            # record the feature and its tasks (local tracker: <state>/tracker.json)
feature.py activate <feature> --apply        # create feature/<slug> from dev (D-4); every writer must be quiescent
feature.py launch <feature>.<task> --apply   # one task: claim → sandboxed runner → commit → gates → release
feature.py status                            # tasks, claims (live / stale / heartbeat age), stop requests
feature.py integrate <feature> --apply       # quiescent barrier → single PR candidate (not opened)
```

**Parallel work** means one `launch` process per task, for **different** roles: Backend and Frontend together, never two Backend tasks. A second same-role launch exits with `RoleBusyError`, and a branch switch or full-project session is refused while any writer runs (`test_backend_and_frontend_write_concurrently_and_land_separately` **[V]**).

Each run gets `<state>/runs/<task>.<stamp>.<id>/` containing:

| File | Content |
| --- | --- |
| `prompt.txt` | The role prompt |
| `sandbox.json` | The exact `bwrap` command line, writable roots, overlays, masked and hidden paths |
| `stdout.jsonl`, `stderr.log` | The runner's output |
| `home/` | The private HOME |

## 3. Stop, timeout, resume, recover

| Situation | What to do | What happens |
| --- | --- | --- |
| Stop a running task | `feature.py stop <task>` | A stop request bound to the running claim's token is recorded. The worker sends SIGTERM to the sandbox, waits `terminate_grace_seconds`, then SIGKILL. Every process in the sandbox's PID namespace dies. The task returns to **ready**; its uncommitted work stays in the role's roots (`test_operator_stop_terminates_the_sandbox_and_yields_the_task` **[V]**) |
| Ctrl-C / SIGTERM to a `launch` process | — | Same graceful stop (`test_sigterm_to_the_worker_is_a_graceful_stop` **[V]**) |
| A run exceeds `task_timeout_seconds` | — | The sandbox is killed and the task becomes **failed** (`test_task_timeout_fails_the_task` **[V]**) |
| Resume | `feature.py launch <task> --apply` again | The next run starts from the role's uncommitted files and commits them with its own changes (`test_sigkilled_worker_is_recovered_and_its_sandbox_is_gone` **[V]**) |
| A worker crashed (`kill -9`, OOM, reboot) | `feature.py recover` (dry run lists), then `recover --apply` | The kernel already released the worker's flocks, and `bwrap --die-with-parent` ended its sandbox. Recovery reclaims the task only when the worker is provably gone (PID absent, start time changed, zombie, other boot) |
| A worker is alive but stops heartbeating | `feature.py status` shows `UNRESPONSIVE`; then `feature.py stop <task>` | Never reclaimed automatically while alive (`test_alive_worker_without_heartbeats_is_reported_unresponsive` **[V]**) |
| A full-project session was requested by mistake | Python API `ClaimManager.withdraw_full_project_request()` | Scoped work can be scheduled again |

## 4. Gates

| Command | Notes |
| --- | --- |
| `feature.py gate <name> --apply` | One gate on `--repo`: checkout **read-only**, **no network**, private HOME and `/tmp`, timeout `gate_timeout_seconds`, logs under `<state>/logs/`. Lint gates compare with `--base` (default: the merge-base with `dev`) |
| `feature.py lint-report backend-lint --apply` | Full baseline findings by symbol (D-19 report) |
| `backend-tests` | **Blocked** until [Backend task B-7](backend-task-b7.md) lands |

Phase 3 runs on the live checkout (2026-10-17, all read-only and offline) **[V]**:

| Gate | Result |
| --- | --- |
| `contract-check` | passed: 91/91 operations up to date |
| `docs-check` | passed: 6/6 |
| `frontend-type-check` | passed, 4.3 s |
| `frontend-build` (output in the sandbox's `/tmp`) | passed, 6.3 s |
| `frontend-tests`, `frontend-tests-content`, `frontend-tests-auth` | passed |
| `backend-lint` / `cli-lint`, baseline mode | passed: no Python changes against `dev` |
| `backend-lint` / `cli-lint`, baseline report | 1 / 34 existing findings |
| `backend-tests` | blocked (B-7) |

`git status` of the checkout was unchanged afterwards.

## 5. Remote Qwen (Automation)

- The HTTP transport reaches **loopback servers only**: `LIVE_REMOTE_ENABLED = False`. A remote endpoint is refused before any DNS lookup or socket.
- With `AGENT_QWEN_BASE_URL` unset, the Automation role is unavailable; its tasks go back to ready and the other roles continue.
- Capability validation (`capabilities.validate_capabilities`) has been run **only against the local fake server**. It reports results and never changes `validated_capabilities`.

Enabling the remote server needs, in order:

1. D-15 (transport), D-16 (disclosure) and D-18 (authentication);
2. operator approval of a live validation run;
3. a code change that sets `LIVE_REMOTE_ENABLED`;
4. recording the validated capabilities in the configuration.

## 6. Opt-in host tests

| Variable | Test | Cost |
| --- | --- | --- |
| `FC_HOST_RUNNER_CHECKS=1` | Real `claude auth status` / `codex login status` inside the sandbox with read-only real credentials; asserts only "logged in" | none (no model call) |
| `FC_LIVE_RUNNER_SMOKE=1` | One tiny real run per runner in a disposable repository (create `hello.txt`); Claude capped at $0.50 | small; **operator approval required** (approved and run once on 2026-10-17) |

```bash
FC_HOST_RUNNER_CHECKS=1 .venv/bin/python -m pytest -p no:cacheprovider scripts/tests/feature_coordinator/test_fc_launchers.py -k real_login
```
