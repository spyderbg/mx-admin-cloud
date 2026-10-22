# Autonomous Worker Operations (W4)

> Owner role: **coordination**. Status: **IMPLEMENTED AND VERIFIED OFFLINE (2026-10-22).**
> - Branch `feature/worker-autonomy-w4` from `dev` (`3019d37`, the W3 merge). Not committed, not pushed, no PR.
> - Verified with fake agents only. The tests use real temporary Git repositories, real `feature.py worker start` processes, real signals and SIGKILLs, and simulated time.
> - **No real Claude Code or Codex request was made in W4.** W4 has **not** been verified with real long-running AI workloads, and no real unattended run took place.
> - The systemd user service is a **template**: it is not installed, enabled or started. Linger is not enabled.
> Related: [worker-checkpoints.md](worker-checkpoints.md) (W3: checkpoints and recovery, which W4 runs automatically) · [worker-functional.md](worker-functional.md) (W2: the Worker and the native-cli backend) · [feature-workflow.md](feature-workflow.md) · [operations.md](operations.md) · [phase-status.md](phase-status.md)
>
> Status classes:
> - **IMPLEMENTED**
> - **VERIFIED OFFLINE**: fake agents, real processes and Git.
> - **SIMULATED**: an accelerated clock, or a short soak run.
> - **NOT VERIFIED WITH REAL AGENTS**
> - **PLANNED**: a later stage, not implemented.

## 1. What W4 adds

Before W4, `feature.py work FEATURE --apply` drove one feature's tasks while an operator watched. It ran several agents at once (one per role), retried only on request, and stopped when the feature stopped.

W4 adds a **long-running local service** that runs a feature's tasks without a terminal. Each cycle it:
1. starts, and runs W3 recovery for interrupted work;
2. finds eligible tasks;
3. runs **one agent at a time**;
4. continues with the next task;
5. waits when nothing can run, and wakes when work becomes due;
6. survives temporary failures;
7. stops gracefully.

Everything is **local**: no AWS, no new database, no new sandbox, no Git worktrees, no GitHub writes.

| Capability | Status |
| --- | --- |
| Service loop `feature.py worker start` (foreground; systemd runs exactly this) | IMPLEMENTED, VERIFIED OFFLINE |
| One agent at a time by default, *enforced* at claim time; manual launches refused while the service runs | IMPLEMENTED, VERIFIED OFFLINE (tests A, P) |
| W2 parallel scheduler kept as an explicit option (`work --max-parallel N`) | IMPLEMENTED, VERIFIED OFFLINE (test N + the W2 suite) |
| Automatic W3 recovery at startup (stale claims, interrupted launches, checkpoint consistency) | IMPLEMENTED, VERIFIED OFFLINE (tests C, H, Q) |
| Error classification (transient, subscription, validation, critical; unknown fails closed) | IMPLEMENTED, VERIFIED OFFLINE (19 classification cases; tests D, E, F, G, M) |
| Bounded, persistent retries with exponential backoff; budgets survive restarts | IMPLEMENTED, VERIFIED OFFLINE (tests D, L, Q) |
| Subscription-aware waiting: no API-key fallback, no model or provider change | IMPLEMENTED, VERIFIED OFFLINE with simulated usage-limit errors (test E). **NOT VERIFIED WITH REAL AGENTS**: the real CLIs' usage-limit messages were not observed in W4 (§7) |
| Health record, `worker status/health/waiting/attention` (read-only) | IMPLEMENTED, VERIFIED OFFLINE |
| Watchdog: stale launch heartbeat warning, hung-launch stop, orphaned agents stopped only with certain identity after their deadline | IMPLEMENTED, VERIFIED OFFLINE (test H; the hung-launch stop is covered by code review only, §9) |
| Pause / resume / stop / stop after current | IMPLEMENTED, VERIFIED OFFLINE (tests J, K) |
| One service per state directory (exit 73); restart-loop guard (exit 78) | IMPLEMENTED, VERIFIED OFFLINE (tests I, P) |
| systemd user unit template + instructions | IMPLEMENTED (rendered and checked with `systemd-analyze --user verify`); **NOT installed, enabled or started** |
| Accelerated long run (simulated days) | SIMULATED (in-process, simulated clock) |
| Soak run: real service process, 36 tasks, 396 s | SIMULATED (fake agents; §14) |
| Real Claude Code / Codex under the service, overnight operation | **NOT VERIFIED WITH REAL AGENTS** |
| Central coordinator, several Worker machines, cross-machine migration | **PLANNED** (§17) |

What is unchanged:
- the W2/W3 task state machine;
- the claim, launch, checkpoint and gate paths: the service launches `feature.py launch TASK --apply`, exactly as `work` does;
- provider routing (Backend/CLI → Claude Code `opus`/`sonnet`, Frontend → Codex);
- subscription-only authentication (W2 §6);
- one active feature per checkout;
- GitHub stays off.

## 2. Architecture

```
systemd --user (optional, operator-installed)        operator: feature.py worker status|pause|stop|…
        │ ExecStart                                            │ read-only, or control flag + signal
        ▼                                                      ▼
 feature.py worker start ──► WorkerService (service.py) ◄── state dir: flags/worker-control.json
        │   one cycle: heartbeat → control → active feature → workspace → claims (W3 recovery,
        │   orphan policy) → in-flight launches of earlier instances → eligible tasks (autonomy.py)
        │   → pre-launch checks → write-ahead launch count → spawn → watch → integrity → classify
        │   → retry policy → events + log line
        ▼
 feature.py launch TASK --apply  (one process per launch, W2/W3 path, unchanged)
        │ claim (refused if another claim exists: agent-exclusive flag) → native-cli backend
        ▼
 claude / codex CLI (subscription login, allowlisted environment, run tag)
```

**Modules.**
- `scripts/feature_coordinator/service.py`: the `WorkerService` loop.
- `autonomy.py`: the policy.
  - The scheduling and service records, with strict schema-versioned serialization.
  - Outcome classification, backoff and the retry policy.
  - Task eligibility and status.
  - The health report and the control flag.
- `service_commands.py`: `feature.py worker …`.
- `systemd/mx-admin-worker.service.in`: the unit template.
- Changes to existing modules:
  - `config.py`: the `worker:` block;
  - `claims.py`: the exclusive flag;
  - `supervisor.py`: the wake signal;
  - `worker.py`: the launch tag;
  - `worker_commands.py`: `work` defaults to 1;
  - `checkpoints.py`: one fix, §8;
  - `github_api.py` and `workflow.py`: secret redaction, §12.

**Reused, not duplicated:**
- the W2 `cli_spawner` and launch path;
- `ProcessIdentity` (pid + start time + boot id) and `terminate_pids`, which re-verifies identity before every signal;
- W3 `claims.recover` (orphan-aware), `checkpoints.inspect` and `recover_task`;
- `workspace_problems`;
- `probe_runner` and `check_subscription`;
- the state store's atomic, revision-checked records and `events.jsonl`;
- `flock` locks.

There is no second process supervisor. The launch process supervises its agent (W2). The service supervises only its own child launch process.

**Portable vs machine-specific state.** Records that could later move to a central coordinator are kept apart from records about this machine.
- Portable: `tasks/`, `checkpoints/` and `scheduling/<task>.json` (launches, attempts, waits, attention, history).
- Machine-specific: `service/worker.json` (worker id, pid identity, host, heartbeat), `worker-startup.json` and the locks.

## 3. Sequential execution

**Default: one AI agent at a time.** `worker.max_parallel` is `1`, and the service refuses any other value (exit 78). `feature.py work` now also defaults to `worker.max_parallel`.

**Enforced, not just scheduled.**
- While a sequential Worker runs (the service, or `work` with parallelism 1), it holds the **`agent-exclusive` flag** (`flags/agent-exclusive.json`: holder plus process identity).
- `ClaimManager.claim` refuses any claim while *another* claim exists and a live exclusive holder exists. It raises `CheckoutBusyError` with "sequential mode …".
- The check runs under `coordinator.lock`, atomically with the claim. A manual `feature.py launch OTHER --apply` beside the service is therefore refused before its agent starts (test P).
- A stale flag (its holder dead) is ignored. The flag is dropped at shutdown.

**Conflicting starts are refused:**
- a second service on the same state directory: `worker-service.lock`, exit **73** (test P);
- `feature.py work FEATURE --apply` while the service drives that feature: the feature's Worker lock (test P);
- a live claim found by the service (a manual launch, or a launch of an earlier service instance): the service waits (WAITING) and never starts a second writer.

**Explicit parallelism stays available.** `feature.py work FEATURE --apply --max-parallel 3` is the unchanged W2 scheduler: one writer per role, Git commits serialized. It does not take the exclusive flag. All W2 parallel tests still pass, including the real-overlap rendezvous test.

**Why sequential?** One subscription session at a time keeps usage predictable. It also makes every commit attributable, and stops agents' file changes from interleaving on the shared checkout.

## 4. Lifecycle

| State | Meaning |
| --- | --- |
| `AVAILABLE` | Nothing to do: no active feature, or the feature is complete ("ready for integration") |
| `BUSY` | An agent runs (`current`: feature, task, step, role → agent, model, launch, since) |
| `WAITING` | An external condition is expected: a retry time, a subscription reset, a live claim of another process, an orphaned agent before its deadline |
| `PAUSED` | The operator paused: no new work (an active agent finishes) |
| `RECOVERING` | Startup: W3 recovery and settling launches of earlier instances |
| `NEEDS_ATTENTION` | An operator decision is required (a conflict, an exhausted budget, an unknown error, an unidentifiable process, broken state) |
| `STOPPING` | A graceful stop is cancelling the active agent |
| `OFFLINE` | Stopped cleanly, or the recorded process is gone (derived) |
| `UNRESPONSIVE` | Derived by `worker health`: the process lives but has not heartbeated for 3 × `heartbeat_seconds` |

`WAITING` and `NEEDS_ATTENTION` are deliberately distinct:
- **WAITING** resolves by itself.
- **NEEDS_ATTENTION** never does.

A critical problem in one task holds **that task only** (its scheduling `attention` record). Unrelated tasks continue (tested).

## 5. One cycle, one launch

**Eligible tasks** meet all of:
- the task is `READY`, so its dependencies are verified (the W2 DAG is never bypassed);
- its role is enabled;
- it has no attention record;
- its next attempt is due.

Eligible tasks are taken in dependency order. Only an **activated** feature is considered: planning plus activation is the operator's approval. The checkout is never switched to another branch to keep busy.

**Pre-launch checks**, made without a model request:
- the launch budget (`max_launches_per_task`, default 10, all categories together);
- the CLI and its required flags (`probe_runner`);
- the subscription login (`check_subscription`: never an API key);
- the task's W3 checkpoint consistency.

A failure here is classified like a launch outcome, and no agent starts.

**Write-ahead.** `launches += 1` and an `in_flight` marker are saved **before** the spawn. The marker records the launch number, service instance, time and launch tag (`FC_WORKER_LAUNCH`). A crash at any point therefore leaves the launch counted. A crash cannot refund the budget or produce an uncounted second launch (test Q).

**Watching the launch.** The service:
- refreshes its own heartbeat;
- copies the claim's progress (step, run tag) into `current`;
- on `stop`, asks the launch to cancel (W3 keeps completed checkpoints);
- runs the watchdog (§9).

**After the launch**, integrity checks come first. Each of these is CRITICAL:
- a process carrying the run's tag survived;
- the feature branch was rewritten (old head not an ancestor);
- a new commit lacks a `Feature-Task` trailer;
- files changed outside the role's paths that were not dirty before;
- a W3 checkpoint conflict.

If the launch process died **holding its claim**, the outcome is `interrupted`. The next cycle's W3 recovery releases the claim and applies the orphan policy, then the task runs again. Only after all this is the outcome classified (§6) and the retry policy applied (§7). Then the next task is considered.

## 6. Error classification

`autonomy.classify` reads the task's state and reason, the launch's events, the launch log, and the agents' final messages (`runs/<task>.*/stdout.jsonl`: `result`, `error` and `turn.failed` events only). Anything unrecognized fails closed.

| Category | Examples | Policy |
| --- | --- | --- |
| A **transient** | ECONNRESET/ETIMEDOUT, socket hang up, overloaded, HTTP 502/503/504/529, agent timeout, CLI temporarily missing, commit/verification deferred | Retry with backoff, `transient.max_attempts` (3), then NEEDS_ATTENTION |
| B **subscription** | "usage limit reached", "hit your usage limit", quota exhausted, out of credits | WAIT for the reported reset or a conservative recheck, `subscription.max_rechecks` (16), then NEEDS_ATTENTION. Never another provider, model or billing method |
| C **validation** | "gates failed: […]" | NEEDS_ATTENTION by default. Optional auto-fix (off): redo the last step of a multi-step task, `validation.max_attempts` (2) |
| D **critical** | API-key authentication, unknown auth method, model substitution, rejected changes (paths, symlinks), workspace/identity mismatch, Git conflicts, branch rewrite, foreign commits, unreadable state, launch budget exhausted, surviving agent processes | NEEDS_ATTENTION at once, never retried |
| **unknown** | Anything else | NEEDS_ATTENTION at once (fails closed) |
| not a failure | verified; blocked (dependency); cancelled (operator stop); interrupted (crash or restart; the launch stays counted); external (a sequential-mode refusal or a live foreign claim) | No budget consumed; the task stays eligible (external: waits) |

The patterns are deliberately narrow, and an unmatched message is `unknown`. The exact wording of real usage-limit and outage messages from Claude Code and Codex was **not observed** in W4. Real messages may therefore end up `unknown`, which means NEEDS_ATTENTION and is safe, rather than `subscription`. W5 should record them.

## 7. Retries, persistence and subscriptions

**Backoff.** The delay is `initial · 2^(n−1)`, capped at `max_delay` (defaults 60 s → 1800 s).

**Persistence.** Each task has a `scheduling/<task>.json` record (schema v1, strict, revision-checked). It holds:
- `launches`;
- `attempts` per category;
- `next_attempt_at`;
- `waiting` (category, reason, since, until);
- `attention` (category, reason, since, **action**);
- `in_flight`;
- `history`, the last 20 outcomes.

All of it survives restarts. A new service instance does not launch before `next_attempt_at` (test L), and an `in_flight` marker of a dead instance is settled with its evidence:
- if the launch still runs (a live claim, or a process with its launch tag), the service **waits**;
- otherwise, if it verified, the outcome is `success`;
- if there is no evidence, the outcome is `interrupted`.

**Task-state mapping** (no new task states):

| Status | Mapping |
| --- | --- |
| running | a claim |
| waiting-retry, waiting-subscription | `READY` + `next_attempt_at` + `waiting.category` |
| needs-attention | `attention`, or `NEEDS_HUMAN` |
| blocked | `BLOCKED` |
| failed | `FAILED` without attention: retryable |

**Retry requeue.** A retryable `FAILED` task is moved back to `READY` by the service (transition `failed → ready`, reason "worker service: retry scheduled after a … failure").

**Reset.** `feature.py worker reset-retries TASK --apply` is the operator's explicit decision. It clears budgets, waits and attention, and moves a `FAILED` task back to `READY`. It is recorded in history and as the event `worker-retries-reset`.

**Subscriptions.** The CLI may report a reset time in machine-readable form: `limit reached|<epoch>`. When that time lies within 7 days, the service waits until then. Otherwise it rechecks every `recheck_interval_seconds` (900). While waiting, the task stays `READY`, the service is `WAITING`, and nothing launches.

On the next attempt, the W3 checkpoints mean the task **continues with its next step**. Completed steps are not redone (test E: steps 1 and 2 are kept and only the interrupted step 2 runs again). Provider, model and authentication never change: the test asserts every launch used `claude` with the configured model.

Before every launch with the native-cli backend (the default), `check_subscription` confirms a subscription login. An API-key or unknown method is CRITICAL, and the agent never starts (process-tested). The native backend's post-run check (`apiKeySource`, model) is unchanged.

## 8. Recovery

**At startup** (`recovery.auto_recover_safe`, default true):
1. **Stale claims** go to W3 `claims.recover` (orphan-aware). A live claim means wait.
2. **`in_flight` launches of earlier instances** are settled as in §7.
3. **Every unverified multi-step task** gets W3 `inspect`. A conflict, or an unreadable checkpoint record, is CRITICAL for that task alone.

Only the actions W3 already calls safe are taken: adopt a commit, record a commit, release a dead claim.

**Never done:**
- resetting Git, rewriting history or deleting uncommitted files;
- abandoning a pending checkpoint or redoing a completed step (that needs an explicit `recover --abandon-pending` or `--redo-from`);
- killing a process whose identity is uncertain.

`recovery.stop_on_conflict` is fixed to `true`, and the configuration refuses `false`.

**Orphaned agents.** Sometimes a launch process has died but its agent runs on (the agent's identity and run tag are recorded in the claim, W3). The service then **waits** (WAITING) until the agent's run deadline:

  agent start time (from `/proc`) + `task_timeout_seconds` + `terminate_grace_seconds`.

After the deadline (`recovery.stop_orphans_after_deadline`, default true), it stops exactly those processes with `terminate_pids`, which re-verifies identity before each signal. Then it recovers the claim and the task runs again (test H). If the agent's identity is **unknown** (only the run tag matches), it reports NEEDS_ATTENTION and **never kills** (tested).

**Fix found by W4 (W3 code).** `recover --redo-from` superseded a `no-changes` checkpoint, which has no commit. `inspect` then reported "its commit None is not on the branch", a false conflict. This happens when a redone step changes nothing and is redone again. The fix: a superseded checkpoint without a commit is valid, and is a conflict only if a commit nevertheless carries its id. A regression test was added to `test_fc_checkpoints.py`; it failed before the fix.

## 9. Health and watchdog

`feature.py worker health [--json]` and `worker status` are **read-only**: no state is created, no work started, no model asked. The report includes:
- the worker id, recorded and derived state, reason, pid, host, start and heartbeat times (with age);
- `current`: feature, task, step, agent, model, launch, start;
- the last checkpoint and the last error, plus counters per category;
- every task's status, launches, attempts and next attempt, plus a claim's progress and heartbeat age;
- the tasks waiting and needing attention, the pending count and the control flags.

`healthy` is true only when:
- the recorded process is alive (same pid, start time and boot);
- its heartbeat is fresh (≤ 3 × `heartbeat_seconds`);
- it is not NEEDS_ATTENTION and no task needs attention.

**Process existence alone is not health.** Exit 0 means healthy; 1 means not.

**Watchdog.** It works on the evidence a running launch provides: the claim heartbeat that the W2 launch process refreshes while its agent runs, independent of the agent's output.
- **Slow model:** a claim heartbeat older than `stale_heartbeat_seconds` gives one warning event, `worker-watchdog-stale`, and nothing else. A slow model is not a dead process.
- **Hung launch:** the claim heartbeat is older than stale + task timeout + gate timeout + grace. The service stops its **own child launch process**, whose identity is certain (SIGTERM, grace, SIGKILL; event `worker-watchdog-hung`). The launch's own supervisor normally stops an agent at its timeout first, so this is a backstop. **Verified by code review only:** no test hangs a launch process for that long.
- **Dead launch:** the claim's holder is gone. W3 recovery handles it, plus the orphan policy (§8).
- **Stopped service:** health derives OFFLINE.
- **Unresponsive service:** health derives UNRESPONSIVE.

**Restart-loop guard.**
- A refused configuration (`autonomous.enabled: false`, or `max_parallel ≠ 1`) exits **78** at once.
- Other startup failures are counted in `worker-startup.json`, keyed by cause (numbers normalized). Exit 1 allows a restart. The same cause `startup.failure_limit` (3) times in a row exits **78**.
- The unit has `RestartPreventExitStatus=73 78` and `StartLimitBurst=5` within 900 s. Test I emulates that loop: exits 1, 1, 78, then a normal start after the repair resets the counter.
- An error *inside* a running cycle is recorded once (`worker-cycle-failed`). The service goes to NEEDS_ATTENTION and looks again after an idle interval. It neither crashes nor spins (tested).

## 10. Operator commands

| Command | Effect |
| --- | --- |
| `feature.py worker start [--max-cycles N] [--exit-when-idle]` | Run the service in the foreground (systemd runs this; the options are for tests and soak runs) |
| `feature.py worker status` | One screen: state, current agent, last checkpoint/error, every task (read-only) |
| `feature.py worker health [--json]` | Structured health; exit 1 unless healthy (read-only) |
| `feature.py worker waiting` | Tasks waiting for a retry or a subscription, with times and attempts (read-only) |
| `feature.py worker attention` | What needs an operator, with the **action** to take; exit 1 if anything (read-only) |
| `feature.py worker pause [--reason R]` / `resume` | Stop / restart scheduling new work. An active agent finishes its bounded run. SIGUSR1 wakes the service at once |
| `feature.py worker stop` | Graceful stop: SIGTERM to the verified service process. It cancels the active agent (completed checkpoints stay; the interrupted step runs again later) and exits 0 |
| `feature.py worker stop --after-current` | Let the active task finish, then exit (nothing new starts) |
| `feature.py worker reset-retries TASK [--apply]` | Clear a task's budgets, waits and attention (dry run without `--apply`) |
| `feature.py worker systemd-unit --config FILE` | Print the unit for this checkout. Nothing is installed |

Control commands write `flags/worker-control.json` and signal only the **recorded** service process, after re-verifying its identity. The request stays recorded either way:
- a pause also applies at the next start;
- a stop is consumed by the instance that stops.

The existing commands are unchanged: `checkpoints TASK` and `recover TASK` for a task's W3 details, and `status` for the board.

## 11. systemd (user service) — instructions; nothing is installed

**Template.** `scripts/feature_coordinator/systemd/mx-admin-worker.service.in`:
- `Type=simple`, with `ExecStart` running `feature.py … worker start`;
- `Restart=on-failure`, `RestartSec=60`, `RestartPreventExitStatus=73 78`;
- `StartLimitIntervalSec=900`, `StartLimitBurst=5`;
- `KillMode=mixed`, `KillSignal=SIGTERM`, `TimeoutStopSec=180`;
- output to the journal.

It contains no credentials and no `HOME` override: Claude Code and Codex keep using their own logins under the user's HOME, which the user manager provides. `PATH` is set to the directories of the found `claude`/`codex` executables plus the system defaults (no secrets).

**Preconditions** (operator decisions):
- a project configuration with `worker.autonomous.enabled: true`;
- an activated feature;
- a successful `feature.py worker start --exit-when-idle` in a terminal first.

```bash
# 1. Render the unit for this checkout and configuration (prints only):
python scripts/feature.py worker systemd-unit --config /path/to/project.yaml > /tmp/mx-admin-worker.service
systemd-analyze --user verify /tmp/mx-admin-worker.service

# 2. Install (operator approval required):
mkdir -p ~/.config/systemd/user
cp /tmp/mx-admin-worker.service ~/.config/systemd/user/mx-admin-worker.service
systemctl --user daemon-reload

# 3. Start now / enable at login (operator approval required):
systemctl --user start mx-admin-worker
systemctl --user enable mx-admin-worker        # starts at the next login

# Status and logs:
systemctl --user status mx-admin-worker
journalctl --user -u mx-admin-worker --since "8 hours ago"
python scripts/feature.py worker status

# Stop gracefully / disable / remove:
python scripts/feature.py worker stop          # or: systemctl --user stop mx-admin-worker
systemctl --user disable mx-admin-worker
rm ~/.config/systemd/user/mx-admin-worker.service && systemctl --user daemon-reload
```

**Running without a login session** needs `loginctl enable-linger $USER`. That is a separate operator decision, and W4 did **not** enable it. Without linger, the service stops at logout.

**Sleep and power.** A user service **cannot keep a laptop working while it is suspended or powered off**. Work stops at suspend. At resume, the service continues: a wait that fell due is acted on, and a run that the suspend cut short is recovered by W3. W4 does not change sleep settings.

**Recovery after a failure:**
1. `systemctl --user status mx-admin-worker` shows the exit code.
2. Exit **78**: run `feature.py worker health` and read the last reason, or `worker-startup.json`. Fix the cause, then `systemctl --user reset-failed mx-admin-worker && systemctl --user start mx-admin-worker`.
3. Exit **73**: another service owns the state directory (`feature.py worker status` shows which pid).
4. Then `feature.py worker attention`, which names the action for each held task.

## 12. Logging, sanitization, retention

**Two outputs:**
- **Service lines** go to stdout, one flushed line each (`<UTC time> <worker-id>: <message>`), which the journal stores under systemd. They record state changes, launches, outcomes, waits, attention, recovery and stops.
- **Structured events** go to `events.jsonl`:
  - service lifecycle: `worker-started`, `worker-stopped`, `worker-state`, `worker-control`;
  - recovery: `worker-recovered`, `worker-recovery-done`, `worker-orphans-stopped`;
  - launches: `worker-task-selected`, `worker-agent-started`, `worker-agent-stopped`;
  - outcomes: `worker-task-verified`, `worker-retry-scheduled`, `worker-subscription-unavailable`, `worker-attention-required`, `worker-outcome`;
  - the watchdog: `worker-watchdog-stale`, `worker-watchdog-hung`;
  - problems and operator actions: `worker-cycle-failed`, `worker-retries-reset`;
  - the exclusive flag: `agent-exclusive-held`, `agent-exclusive-released`;
  - the existing W2/W3 events, for checkpoints, claims and task results.

**What happened overnight:**
```bash
journalctl --user -u mx-admin-worker --since yesterday
grep '"kind": "worker-' state/events.jsonl
```

**Sanitization.** Everything W4 records, emits or prints passes `scrub`. That is the existing `sanitize` (GitHub tokens, terminal control sequences, length) plus `redact_secrets`. `redact_secrets` covers:
- provider keys (`sk-…`, `sk-ant-…`, `AIza…`);
- `NAME=value` / `"name": value` assignments of keys, tokens, secrets, passwords and authorization, including `Bearer …`.

The W2 coordinator now also redacts a task's result reason (`_finish`), because a runner error can quote CLI output. A test plants a fake key in an agent error and asserts it appears nowhere in the state directory or the service output.

What is never logged:
- credentials (never read; W2);
- environment contents;
- transcripts: the service reads only the final `result`, `error` and `turn.failed` messages, for classification, and does not store them.

**Retention (guidance).** Growth comes from four places:
- **the journal**, bounded by journald (`SystemMaxUse`);
- **`events.jsonl`** (append-only): about 2.4 KB per launch in the soak, 107 KB and 658 events for 45 launches;
- **`logs/work-<task>.<launch>.log`**, at most `max_launches_per_task` per task;
- **`runs/<task>.<n>/`**, the W2 run directories. **These keep the agent CLI's full output stream (a transcript) as evidence.** That is pre-existing W2 behaviour, not changed in W4. The fake agents used about 19 KB per run; real agents write far more.

Prune `runs/` and `logs/` only for **integrated** features with no attention record and no unresolved recovery. Never prune for a task in NEEDS_ATTENTION or with a pending checkpoint. Archive `events.jsonl` at a quiet time (service stopped). No automatic deletion is implemented.

## 13. Configuration (`worker:`)

The defaults are in `project-config.example.yaml`, which equals the built-in configuration. Configurations without the block load unchanged. The parser is strict: an unknown key, a wrong type or a negative budget is an error.

```yaml
worker:
  autonomous: {enabled: false}       # the service refuses to start until an operator sets true
  max_parallel: 1                    # service: must be 1; `work` default
  idle_poll_seconds: 30
  heartbeat_seconds: 30
  max_launches_per_task: 10          # hard cap, all categories
  retry:
    enabled: true
    transient: {max_attempts: 3, initial_delay_seconds: 60, max_delay_seconds: 1800}
    subscription: {enabled: true, recheck_interval_seconds: 900, max_rechecks: 16}
    validation: {auto_fix_enabled: false, max_attempts: 2}
  recovery: {auto_recover_safe: true, stop_on_conflict: true, stop_orphans_after_deadline: true}
  startup: {failure_limit: 3}
```

## 14. Tests and evidence

**`scripts/tests/feature_coordinator/test_fc_autonomy.py`** (43 tests, in-process). It uses mock agents in launch threads, real Git and a **simulated clock**. Covered:
- **classification:** 19 cases;
- **policy:** backoff and budgets, strict configuration, the exclusive claim;
- **A** one agent at a time, plus a manual claim refused;
- **B** dependencies first;
- **D** transient retries with backoff, bounded across a restart;
- **E** subscription waits until the reported reset and resumes from the checkpoint with the same provider and model, plus bounded conservative rechecks;
- **F** validation: attention by default, bounded auto-fix;
- **G** a rewound branch is CRITICAL and nothing runs after it;
- **J** pause and resume;
- **L** retry schedule across a new instance;
- **M** unknown error not retried;
- **N** explicit parallel mode (a rendezvous proves real overlap), while the service still refuses parallelism;
- **O** idle: no unapproved work, one bounded sleep per cycle;
- **operations and safety:** disabled mode refused; read-only status and health (state files byte-identical); reset; secrets redacted; per-task isolation of broken state; a failing cycle; the **accelerated long run** (four tasks with transient and subscription failures, a pause, a restart and an attention case; invariants checked after every cycle).

**`test_fc_service_process.py`** (11 tests). It uses real `feature.py worker start` processes, the native-cli backend with the fake agent CLI, real Git, signals and SIGKILLs:
- **C** service and launch SIGKILLed at a step boundary: the restart resumes with only step 3, and the launch is counted twice;
- **E** an API-key or unidentifiable login: the task is CRITICAL before any launch (no claim, no run, nothing counted);
- **H** orphaned agent: waited for, stopped after its deadline, and the task reruns; an unidentified process is never killed;
- **I** crash loop: exits 1, 1, 78; refused configuration 78; a repair resets the counter;
- **K** graceful stop (agent cancelled, partial work kept, exit 0, OFFLINE) and stop after current;
- **P** second service exits 73; a manual launch and `work` are refused while the service runs;
- **Q** service crash before spawn, after spawn and before the outcome is recorded: one agent run, one commit, the correct launch count and history.

**Stability.** The process suite (then 9 tests) passed **8 consecutive runs on frozen code**, 9/9 each, about 85 s per run.
- An earlier repetition had one failure in 8 runs. That run overlapped an edit: it collected the old test file and ran it against new service code.
- The same test passed alone on the final code.
- Details: [phase-status.md](phase-status.md#w4--autonomous-worker-operations-record-2026-10-22).

**Optional soak.** `scripts/tests/feature_coordinator/fc_service_soak.py --workdir DIR [--tasks N] [--failure-every K] [--idle-seconds S]` runs the real service process over fake-agent tasks. It samples memory, file descriptors, threads and children every second, and checks invariants.

The 2026-10-22 run (36 tasks, every 4th failing once, 120 s idle) passed every check:
- 45 agent runs, one commit per task;
- at most one claim at a time;
- memory flat (about 28.9 MB RSS), 3–11 fds, 1 thread;
- no lingering child while idle.

**Not proven:**
- real-world 24/7 reliability, real model latency, real subscription exhaustion and real CLI error texts;
- suspend/resume;
- days of uptime.

## 15. Operational example (what really happens; offline-verified behaviour)

The steps match tests E and A and the soak run. The example is **not** a real-agent run.

1. **Worker starts.** systemd (or the operator) runs `worker start`. It takes the service lock, records its identity, holds `agent-exclusive` and runs RECOVERING: no stale claim, no in-flight launch. The feature is activated.
2. **Backend begins.** `api` (Backend → Claude Code, `opus`) is eligible. The pre-launch checks pass (CLI present, subscription login), launch 1 is recorded write-ahead, `feature.py launch` starts, and the state is BUSY.
3. **Two checkpoints complete.** Steps `models` and `service` each get a checkpoint commit (W3). `current.step` follows the claim's progress.
4. **Subscription becomes unavailable.** In step 3 the CLI fails with "usage limit reached|<epoch>". The launch ends FAILED and the outcome is classified `subscription`.
5. **Worker waits.** `api` is set back to READY with `next_attempt_at` = the reset time. The event `worker-subscription-unavailable` is recorded and the state is WAITING ("next attempt … (api: subscription)"). Nothing launches, and no API key or other model is tried. CLI and Frontend depend on `api`, so they wait too.
6. **Subscription becomes available.** At the reset time the idle sleep ends, which takes at most `idle_poll_seconds`.
7. **Worker resumes from the checkpoint.** In launch 2, W3 reconciles, keeps checkpoints #1 and #2, and runs **only** step 3.
8. **Backend finishes.** Its gates pass and it is VERIFIED (`worker-task-verified`).
9. **CLI begins.** `cmd` is now eligible and runs alone. Frontend is eligible too, but waits: one agent at a time.
10. **Frontend finishes.** After CLI is verified, `view` (Codex) runs and is verified.
11. **Feature becomes ready for integration.** All tasks are verified: "feature … is complete: ready for integration (`feature.py integrate … --apply`)". Integration and the PR remain operator steps.
12. **Worker becomes AVAILABLE** and idles: one bounded sleep per poll, no child processes, until an operator activates another feature.

**Implemented and offline-verified:** every transition above, with the fake agent and the mock runners.

**Simulated:**
- the usage-limit message (the real CLIs' wording was not observed);
- the passage of time (simulated clock);
- the agents (fake).

**Planned:** taking the next feature from a central queue.

## 16. Limitations

- **No real agents.** No real-agent W4 run and no unattended overnight run took place. Real CLI usage-limit and outage messages were not observed, so the classification patterns may miss them. They would then fall into `unknown`, which means NEEDS_ATTENTION and is safe.
- **Subscription reset times** are trusted only in the machine-readable `|<epoch>` form. Otherwise the service rechecks conservatively.
- **Hung-launch watchdog** stop is verified by code review only. Its threshold is long by design.
- **Validation auto-fix** redoes only the last step of a multi-step task. A single-step task always goes to NEEDS_ATTENTION.
- **One feature per checkout.** When a feature completes, the service idles until an operator activates another. It neither integrates nor opens a PR.
- **Retention is guidance only.** W2 `runs/` directories keep the agents' full output streams.
- **The service's own clock** governs waits. A large system clock jump shifts waits accordingly.
- **Suspend and power-off stop all work.** systemd cannot change that.

## 17. Future: central coordination (planned, not implemented)

The target is several Worker machines, one active Worker per feature, and sequential agents within each Worker. Planned components:
- a Lambda coordinator;
- DynamoDB coordination state;
- S3 checkpoint storage;
- central merge coordination.

W4 prepares this without implementing it:
- **The worker id is stable** (`service/worker.json`). The health report is a JSON document that could be posted as a heartbeat.
- **Scheduling records are portable and keyed by task.** They could become DynamoDB items with conditional writes replacing the revision check. Machine state (identity, pid, locks, startup counter) stays local.
- **Events are append-only JSON** with stable kinds, suitable for shipping.
- **Planned, not built:**
  - lease-based feature assignment with fencing tokens;
  - checkpoint bundles in S3;
  - a pull-based queue replacing "one activated feature";
  - notifications (email, chat) driven by `worker-attention-required` events.

W4 has no AWS client, no distributed lock and no migration.

## 18. Implemented vs simulated vs planned (summary)

- **Implemented and offline-verified:**
  - sequential enforcement; the lifecycle; automatic W3 recovery; classification and bounded persistent retries;
  - subscription waiting (simulated errors); health and status; pause, resume and stop;
  - the restart-loop guard; orphan handling; per-task isolation; redaction;
  - the unit template (rendered and verified, not installed).
- **Simulated:** long runs (accelerated clock), the soak (fake agents, minutes), usage-limit messages.
- **Not verified:** real Claude Code / Codex under the service; overnight operation; suspend/resume; the hung-launch watchdog under a real hang.
- **Planned:** central coordinator, multiple machines, a feature queue, notifications.
