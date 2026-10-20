# Coordinator Operations — Launch, Stop, Resume, Recover, GitHub Issues and Projects (Phases 3–4)

> Owner role: **coordination**. Status: Phase 3 sections **approved** (2026-10-17); §7 (GitHub Issues) is the **Phase 4 deliverable, approved** (2026-10-17); §8 (GitHub Projects board) is the **Phase 4 extension, approved** (2026-10-17).
> Entry point: `python scripts/feature.py` (use the repository's `.venv` Python). Every command that changes something is a **dry run unless `--apply`** is given.
> Global options: `--repo PATH` (default: this checkout), `--state-dir PATH` (default: `~/.local/state/mx-admin-agents/`), `--project-config FILE` (default: built-in configuration), `--tracker local|github` (default: `local` for feature operations, `github` for the `github` commands).

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

## 7. GitHub Issues (Phase 4)

Design and policies: [github-integration.md](github-integration.md). Dependency rules: [task-lifecycle.md](task-lifecycle.md).

| Command | What it does | Writes |
| --- | --- | --- |
| `feature.py github status` | Verifies `gh`, authentication, repository access, Issues enabled, permissions, endpoints and rate limit; names exactly what is missing (exit 1) | none |
| `feature.py github labels [--apply]` | Creates missing `feature:*` labels; never edits an existing label | labels |
| `feature.py github sync FEATURE [--apply] [--json] [--no-board]` | Local state → the feature's Issues (create or adopt, managed block, labels, open/closed, progress comments, outbox) and validated `/feature` commands → local state; then the feature's project board items (§8) unless `--no-board`. Without `--apply`: reads GitHub and lists what it would do | Issues and board items of that feature |
| `feature.py github reconcile [FEATURE…] [--apply] [--json]` | After a restart: sync each feature, then report drift, duplicates and orphans (owned Issues with no local record; never modified) | as `sync` |
| `feature.py github inspect NUMBER` | Ownership verdict for one Issue (marker, author, identity, foreign labels); untrusted text is sanitized for display | none |
| `feature.py github owned [--feature F]` | Issues carrying this coordinator's marker, created by the coordinator account | none |
| `feature.py github deps FEATURE [--remote]` | The dependency graph: states, Issue numbers, unmet dependencies (`--remote`: current labels on GitHub) | none |
| `feature.py github test-lifecycle [--apply] [--workdir DIR] [--no-cleanup]` | The Phase 4 acceptance scenario: 4 test Issues, a disposable repository, no PR ([github-integration §12](github-integration.md#12-live-acceptance-test)) | 4 new test Issues |
| `feature.py github cleanup-test FEATURE [--apply] --state-dir <work>/<feature>/state` | Final verification comment and close for a **test** feature's Issues; refuses anything not owned or not labeled `feature:test`; repeatable | those Issues |
| `feature.py hold TASK --reason TEXT [--apply]` / `release TASK [--apply]` | Human escalation (`needs-human`); a release re-checks dependencies | local state, then a sync |

Operating rules:

- Feature operations (`plan`, `activate`, `launch`, `integrate`, `hold`, `release`) use the local JSON tracker unless `--tracker github` is given; with it, each operation syncs the Issues **after** its state change and outside every coordinator lock. A failed sync is recorded (`tracker-sync-deferred` event) and finished by `github sync FEATURE --apply`.
- Phase 4 write scope: only features planned with `test: true` may be written to GitHub (`github.write_scope: test-features-only`). Widening it is an operator decision.
- No command creates a pull request.
- If `github status` reports a missing capability, stop the live operation and fix it (for example `gh auth login`); there is no fallback to the local tracker.

| Situation | What to do |
| --- | --- |
| A sync was interrupted (crash, network) | Run `github sync FEATURE --apply` again: pending creations and comments are found by key/operation ID before anything is re-sent |
| `protected` in a report | The Issue failed ownership verification (marker removed, wrong author, deleted, mapped elsewhere); it is untouched. Inspect it with `github inspect NUMBER` and decide manually |
| `duplicates` in a report | Two owned Issues carry one key; the oldest is used. Close the other one by hand after checking it |
| `drift` in a report | A human changed labels, the managed block or the open/closed state; the local state was re-applied. Use `/feature hold`/`release` or `feature.py` commands to change task state |
| Rate limited | The sync waits up to `max_rate_limit_wait_seconds`, otherwise it is deferred; run it again later |

## 8. GitHub Projects board (Phase 4 extension)

Design, status mapping and evidence: [github-projects.md](github-projects.md). The board is configured in `github.project` (Project #2 for mx-admin-cloud; Project #1 is protected and never written). It is a picture of the local state, never an input.

| Command | What it does | Writes |
| --- | --- | --- |
| `feature.py github project inspect [--json]` | The identity proof (owner, number, node ID; not protected; updatable), token scopes (from the response header), fields and options, views and their column field, built-in workflows (exit 1 if the identity is not proven or the `project` scope is missing) | none |
| `feature.py github project validate [--json]` | Required Status options, Agent / Feature ID / Blocked Reason fields and the Kanban / Active Tasks views present? Lists what `setup` would change (exit 1 if not READY) | none |
| `feature.py github project setup [--apply]` | Creates or reuses the options, fields and views; renames GitHub's default `Todo` to `Backlog` keeping its option ID; never deletes or retypes anything | project configuration |
| `feature.py github project associate FEATURE [--apply] [--actor NAME]` | Authorizes an **existing** feature for the board (recorded locally) and adds its Issues' items. Features planned while the board is enabled are authorized by `plan` | items of that feature |
| `feature.py github project sync FEATURE [--apply] [--json]` | Local state → the feature's items (add or adopt; Status, Agent, Feature ID, Blocked Reason; read-back) | items of that feature |
| `feature.py github project reconcile [FEATURE…] [--apply] [--json]` | After a restart: sync each authorized feature, then classify every item of the board as mapped, foreign or orphan, and report duplicates; foreign and orphan items are never modified | as `sync` |
| `feature.py github project active [--feature F] [--all] [--json]` | Active tasks (ready, in progress, blocked, held, failed): the local state, the derived board values and what the board shows | none |
| `feature.py github project task RECORD [--json]` | One task or feature: local state, derived values, board values, in sync? (exit 1 if not) | none |
| `feature.py github project test [--apply] [--workdir DIR] [--no-cleanup]` | The live Projects test: **two** test Issues added to the board, a disposable repository, no PR ([github-projects §11](github-projects.md#11-live-integration-test)) | 2 new test Issues and their items |

Operating rules:

- Every feature operation that syncs Issues (`plan`, `activate`, `launch`, `integrate`, `hold`, `release`, …) also syncs the board, outside every coordinator lock. A failure is recorded as a `tracker-sync-deferred` event with `board: true`; `github sync FEATURE --apply` or `github project sync FEATURE --apply` completes it.
- `launch` shows **In Progress** while the agent runs and **Review** while its commit is verified, through a separate `feature.py github sync` process that holds none of the worker's locks (output in `<state>/logs/publish-<feature>.log`).
- Moving a card or editing a field on the board never starts, releases, verifies or approves anything; the next sync restores the derived value and reports drift. Change task state with `feature.py`.
- The write scope applies: in Phase 4 only `test: true` features are written to the real board.
- The project workflows **Auto-close issue** and **Item closed** are switched off in Project #2 (D-28, verified 2026-10-17; [github-projects §9](github-projects.md#9-githubs-built-in-project-workflows)). `github project inspect` lists the workflows; if either is switched on again, the coordinator repairs its effect but reports drift.
- The **Active Tasks** view shows open Issues in Ready, In Progress, Blocked or Review (`is:open`, D-29). Finished test features stay on the Kanban view as closed audit records.
