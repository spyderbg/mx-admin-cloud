# Autonomous Worker Operations (W4)

> Owner role: **coordination**. W4/W4.1 offline; W4.2 and W4.3 bounded real-agent/GitHub acceptance; **W4.4 supervised recovery regressions, fake-agent soak and four-session real-agent revalidation VERIFIED (2026-10-29)**. This does **not** demonstrate unattended/overnight or live GitHub-outage resilience. [W4.4 record](w44-reliability-acceptance.md).
> - Historical implementation branch: `feature/worker-autonomy-w4`, integrated by merged PR [#23](https://github.com/spyderbg/mx-admin-cloud/pull/23) (`7ce3dbc`). W4.3 intake/publishing were merged separately via PRs #26 and #28; the latest supervised smoke merged PR #30 into `dev` (`9861975`).
> - W4 and W4.1 were verified with fake agents: real temporary Git repositories, real `python -m feature_coordinator worker start` processes, real signals and SIGKILLs, and simulated time.
> - **W4.2** ran 4 real Claude/Codex sessions in the original bounded foreground smoke test; **W4.3.4** ran a real Claude Sonnet task and opened Draft PR #30; **W4.4.5-B** repeated the bounded 4-session Claude/Codex smoke (69/69) with a controlled stop/restart. W4.4.5-A also ran 48 fake-agent tasks (10/10 soak checks). None proves 24/7 reliability.
> - The systemd user service is a **template**: it is not installed, enabled or started. Linger is not enabled.
> Related: [worker-checkpoints.md](worker-checkpoints.md) (W3: checkpoints and recovery, which W4 runs automatically) · [worker-functional.md](worker-functional.md) (W2: the Worker and the native-cli backend) · [feature-workflow.md](feature-workflow.md) · [operations.md](operations.md) · [phase-status.md](phase-status.md)
>
> Status classes:
> - **IMPLEMENTED**
> - **VERIFIED OFFLINE**: fake agents, real processes and Git.
> - **VERIFIED WITH REAL AGENTS**: real Claude Code / Codex sessions under the service, in the bounded W4.2 smoke test (§14.1).
> - **SIMULATED**: an accelerated clock, or a short soak run.
> - **NOT VERIFIED WITH REAL AGENTS**
> - **PLANNED**: a later stage, not implemented.

## 1. What W4 adds

Before W4, `python -m feature_coordinator work FEATURE --apply` drove one feature's tasks while an operator watched. It ran several agents at once (one per role), retried only on request, and stopped when the feature stopped.

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
| Service loop `python -m feature_coordinator worker start` (foreground; systemd runs exactly this) | IMPLEMENTED, VERIFIED WITH REAL AGENTS in bounded W4.2/W4.3.4 smoke runs; not tested unattended |
| One agent at a time by default, *enforced* at claim time; manual launches refused while the service runs | IMPLEMENTED, VERIFIED OFFLINE (tests A, P) |
| W2 parallel scheduler kept as an explicit option (`work --max-parallel N`) | IMPLEMENTED, VERIFIED OFFLINE (test N + the W2 suite) |
| Automatic W3 recovery at startup (stale claims, interrupted launches, checkpoint consistency) | IMPLEMENTED, VERIFIED OFFLINE (tests C, H, Q) |
| Error classification (transient, subscription, validation, critical; unknown fails closed) | IMPLEMENTED, VERIFIED OFFLINE (classification cases; tests D, E, F, G, M). W4.2.1: a required gate that could not run is critical, not validation (§6) |
| Bounded, persistent retries with exponential backoff; budgets survive restarts | IMPLEMENTED, VERIFIED OFFLINE (tests D, L, Q) |
| Subscription-aware waiting: no API-key fallback, no model or provider change | IMPLEMENTED, VERIFIED OFFLINE with simulated usage-limit errors (test E). **NOT VERIFIED WITH REAL AGENTS**: the real CLIs' usage-limit messages were not observed in W4 (§7) |
| Health record, `worker status/health/waiting/attention` (read-only) | IMPLEMENTED, VERIFIED OFFLINE |
| Watchdog: agent run deadline, hung-launch stop, orphaned agents and survivors stopped only with certain identity, ambiguous state failing closed (§9.1) | IMPLEMENTED, VERIFIED OFFLINE with real hung, silent, killed and orphaned processes (W4.1, §14). **NOT VERIFIED WITH REAL AGENTS** |
| Pause / resume / stop / stop after current | IMPLEMENTED, VERIFIED OFFLINE (tests J, K) |
| One service per state directory (exit 73); restart-loop guard (exit 78) | IMPLEMENTED, VERIFIED OFFLINE (tests I, P) |
| systemd user unit template + instructions | IMPLEMENTED (rendered and checked with `systemd-analyze --user verify`); **NOT installed, enabled or started** |
| Accelerated long run (simulated days) | SIMULATED (in-process, simulated clock) |
| Soak run: real service process, 36 tasks, 396 s | SIMULATED (fake agents; §14) |
| Real Claude Code / Codex under the service: sequential sessions, checkpoints, dependencies, a controlled restart, idle exit | **VERIFIED WITH REAL AGENTS** in one bounded foreground smoke test (W4.2, 4 sessions; §14.1) |
| Overnight / unattended operation, real usage limits, real outages | **NOT VERIFIED WITH REAL AGENTS** |
| Central coordinator, several Worker machines, cross-machine migration | **PLANNED** (§17) |

What is unchanged:
- the W2/W3 task state machine;
- the claim, launch, checkpoint and gate paths: the service launches `python -m feature_coordinator launch TASK --apply`, exactly as `work` does;
- provider routing (Backend/CLI → Claude Code `opus`/`sonnet`, Frontend → Codex);
- subscription-only authentication (W2 §6);
- one active feature per checkout;
- GitHub was off in the original W4 baseline; W4.3 adds opt-in intake and publication (`github.intake.enabled`, `github.publish.enabled`), verified in bounded supervised tests (§14.2).

## 2. Architecture

```
systemd --user (optional, operator-installed)        operator: python -m feature_coordinator worker status|pause|stop|…
        │ ExecStart                                            │ read-only, or control flag + signal
        ▼                                                      ▼
 python -m feature_coordinator worker start ──► WorkerService (service.py) ◄── state dir: flags/worker-control.json
        │   one cycle: heartbeat → control → active feature → workspace → claims (W3 recovery,
        │   orphan policy) → in-flight launches of earlier instances → eligible tasks (autonomy.py)
        │   → pre-launch checks → write-ahead launch count → spawn → watch → integrity → classify
        │   → retry policy → events + log line
        ▼
 python -m feature_coordinator launch TASK --apply  (one process per launch, W2/W3 path, unchanged)
        │ claim (refused if another claim exists: agent-exclusive flag) → native-cli backend
        ▼
 claude / codex CLI (subscription login, allowlisted environment, run tag)
```

**Modules.**
- `feature_coordinator/service.py`: the `WorkerService` loop.
- `autonomy.py`: the policy.
  - The scheduling and service records, with strict schema-versioned serialization.
  - Outcome classification, backoff and the retry policy.
  - Task eligibility and status.
  - The health report and the control flag.
- `service_commands.py`: `python -m feature_coordinator worker …`.
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

**Default: one AI agent at a time.** `worker.max_parallel` is `1`, and the service refuses any other value (exit 78). `python -m feature_coordinator work` now also defaults to `worker.max_parallel`.

**Enforced, not just scheduled.**
- While a sequential Worker runs (the service, or `work` with parallelism 1), it holds the **`agent-exclusive` flag** (`flags/agent-exclusive.json`: holder plus process identity).
- `ClaimManager.claim` refuses any claim while *another* claim exists and a live exclusive holder exists. It raises `CheckoutBusyError` with "sequential mode …".
- The check runs under `coordinator.lock`, atomically with the claim. A manual `python -m feature_coordinator launch OTHER --apply` beside the service is therefore refused before its agent starts (test P).
- A stale flag (its holder dead) is ignored. The flag is dropped at shutdown.

**Conflicting starts are refused:**
- a second service on the same state directory: `worker-service.lock`, exit **73** (test P);
- `python -m feature_coordinator work FEATURE --apply` while the service drives that feature: the feature's Worker lock (test P);
- a live claim found by the service (a manual launch, or a launch of an earlier service instance): the service waits (WAITING) and never starts a second writer.

**Explicit parallelism stays available.** `python -m feature_coordinator work FEATURE --apply --max-parallel 3` is the unchanged W2 scheduler: one writer per role, Git commits serialized. It does not take the exclusive flag. All W2 parallel tests still pass, including the real-overlap rendezvous test.

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

**W4.3.1 GitHub intake.** With `github.intake.enabled`, each cycle starts with a bounded poll, at most every `poll_interval_seconds`. An Issue an operator approved with `/worker approve` is planned as one task and activated when the checkout is free; that approval is the operator's plan and activation. Before launching such a task, and every poll interval while its agent runs, the service re-reads the Issue. If the Issue changed or was closed, the task is held and no agent starts. A running agent is stopped through the existing graceful cancellation (completed checkpoints stay), and its result is not accepted ([github-intake.md](github-intake.md)).

**W4.3.3 publication.** With `github.publish.enabled`, each cycle starts with a publication pass (at most every `github.publish.poll_interval_seconds`, before the intake poll, never while paused or while an agent runs). An approved Issue's feature whose tasks are all verified is integrated, its branch pushed, and ONE Draft PR opened. While it waits for a person to merge the PR, no feature is active, and the service shows **WAITING** ("Draft PR #N waits for a person to review and merge it"), or **NEEDS_ATTENTION** when publication stopped. The next intake feature is not activated meanwhile (one feature per checkout). After the merge the pass verifies it, completes the feature, closes the Issue and moves its card to Done. The service never merges ([github-intake §10](github-intake.md#10-automatic-publication-and-completion-w433)).

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
| C **validation** | "gates failed: […]" (a gate ran and failed) | NEEDS_ATTENTION by default. Optional auto-fix (off): redo the last step of a multi-step task, `validation.max_attempts` (2) |
| D **critical** | API-key authentication, unknown auth method, model substitution, rejected changes (paths, symlinks), workspace/identity mismatch, Git conflicts, branch rewrite, foreign commits, unreadable state, launch budget exhausted, surviving agent processes; "gates could not run: […] (cause)": a required gate was blocked or errored (its sandbox or tool unavailable), so the code was never judged (W4.2.1: never treated as validation, never an AI redo) | NEEDS_ATTENTION at once, never retried |
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

**Reset.** `python -m feature_coordinator worker reset-retries TASK --apply` is the operator's explicit decision. It clears budgets, waits and attention, and moves a `FAILED` task back to `READY`. It is recorded in history and as the event `worker-retries-reset`.

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

`python -m feature_coordinator worker health [--json]` and `worker status` are **read-only**: no state is created, no work started, no model asked. The report includes:
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

### 9.1 Watchdog (W4.1)

**Three layers, one supervisor each.** No second supervisor or watchdog daemon exists.

| What hangs | Detected by | Evidence | Action |
| --- | --- | --- | --- |
| The **agent** (no progress, no exit) | The launch process's existing Supervisor (W2) | Its run deadline: `task_timeout_seconds` from the agent's start (per step for multi-step tasks). Output is irrelevant | SIGTERM to the agent's process group, `terminate_grace_seconds`, SIGKILL to the group, then SIGKILL to every process still carrying the run's tag (a setsid'd child). The task fails with "runner timed out", which is **transient**: backoff and the retry budget |
| The **launch process** (no claim heartbeat) | The service (`_watch`) | The claim heartbeat, which the launch refreshes every `heartbeat_interval_seconds` while it supervises its agent | After `stale_heartbeat_seconds`: one warning (`worker-watchdog-stale`), health says **slow**, nothing is stopped. After `hung_after` = stale + task timeout + gate timeout + grace (4630 s by default): the decision is **recorded first** (the task's in-flight launch, `watchdog`), then SIGTERM, grace, SIGKILL to that launch only, and its end is **confirmed**. The outcome is **transient** ("watchdog: …"), whatever the launch reported on its way out |
| A launch of an **earlier service instance** (the service restarted) | The new service (`_live_claim`) | The claim holder's identity (pid + start time + boot id), **and** the launch tag `FC_WORKER_LAUNCH` that was recorded before the launch started | The same policy. A decision recorded by the crashed instance is **continued**: identity is re-checked, there is no new waiting period, and signalling stays idempotent. A live launch that this service did not start (a manual one) is **never** stopped. Beyond `hung_after` it is reported as NEEDS_ATTENTION and stays blocking |
| An **orphan** (holder dead, agent or tagged processes alive) | W3 claim recovery, then the service (`_orphan`) | The agent's recorded identity and the run tag | WAIT until the agent's run deadline (agent start + task timeout + grace) — **never because its parent died**. Then stop exactly those processes (`terminate_pids` re-checks identity before every signal), recover the claim, and resume from the W3 checkpoint |
| **Survivors** of a finished launch (claim released, run-tag processes alive) | The service (`_judge`) | Run tags seen in the claim, or recorded in the run's `native.json` (so a run too short to be polled is covered too) | Stopped once (the launch's own leftover policy). The task is CRITICAL in any case, because they may have written late. If they cannot be stopped, the run tags are kept in the attention record and **no agent starts on the checkout** while any of them lives, across restarts |

**Slow is not hung.** A silent agent is not slow: its launch keeps heartbeating, and health says "running" (tested with an agent silent for three stale periods). A launch that stops heartbeating, like a long gate, is "slow" and is only reported. Only the run deadline, or the hung threshold, stops anything.

**Identity.**
- Every automatic signal targets one of three kinds of process:
  - the service's own unreaped child (its identity is certain);
  - a process whose recorded identity matches exactly (pid + start ticks + boot id);
  - a process carrying a random run or launch tag that this system set before the process started.
- A PID whose start time differs is a different process. Its claim is recovered under the existing policy, and the process now using that PID is never signalled (tested with live processes and injected identities).
- An unreadable identity (missing fields) fails closed. Nothing is recovered, stopped or started, the service reports NEEDS_ATTENTION and does not crash (W4 crashed with `KeyError`), and health still works.
- **The window before the agent's identity is recorded.** The launch records the agent's identity in its claim just after starting it. If the launch dies within that short window, the orphan is known only by its run tag, and its run deadline cannot be timed. It is **held for the operator (NEEDS_ATTENTION) and never stopped automatically** (`python -m feature_coordinator recover TASK --apply --terminate-orphans` stops it by its run tag). The W4.1 full regression hit exactly this window once under load: a test had killed the launch too early. The service behaved as designed; the test now waits for the identity to be recorded.

**Cleanup that cannot be confirmed.**
- A process that survives SIGKILL keeps blocking: its claim stays, or its run tags stay in the attention record. Health says **cleanup-uncertain**.
- The service does not signal it again in a loop: one bounded attempt per service instance.
- A later instance re-checks it and finishes the cleanup once that is possible.

**Retries after a termination.**
- A hung agent (agent deadline) or a hung launch (watchdog) is **transient**: exponential backoff and `retry.transient.max_attempts`, then NEEDS_ATTENTION.
- A crash or restart is `interrupted`, a graceful stop is `cancelled`; neither uses the budget.
- Launches stay counted in every case (`max_launches_per_task`).
- Budgets are never reset by a restart.
- Completed W3 checkpoints are kept, and the task resumes at the interrupted step.

**What the watchdog never does:** reset Git, rewrite history, discard uncommitted files, kill by process name, or stop a process whose identity is uncertain.

**Health diagnosis.** `worker health --json` adds one `diagnosis`, the most urgent first:
1. `service-heartbeat-stale`
2. `cleanup-uncertain`
3. `termination-in-progress`
4. `orphan-detected`
5. `agent-deadline-exceeded`
6. `attention-required`
7. `agent-slow`
8. `agent-running`
9. `offline`
10. `waiting`
11. `idle`

**Health detail.** Each claimed task gets a `watchdog` view. Its status is one of running | slow | hung | deadline-exceeded | orphaned | stale | ambiguous, with:
- holder pid and liveness;
- heartbeat age;
- step;
- agent pid, start time and run deadline.

**Persisted detail.** `current.agent_process`, `current.watchdog` (phase, decision) and `last_error` (`cleanup-uncertain`, pids) are stored in the service record. A task in attention whose cleanup was uncertain also lists `blocking_processes`. Every attention record names the next safe action.

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
| `python -m feature_coordinator worker start [--max-cycles N] [--exit-when-idle]` | Run the service in the foreground (systemd runs this; the options are for tests and soak runs) |
| `python -m feature_coordinator worker status` | One screen: state, current agent, last checkpoint/error, every task (read-only) |
| `python -m feature_coordinator worker health [--json]` | Structured health; exit 1 unless healthy (read-only) |
| `python -m feature_coordinator worker waiting` | Tasks waiting for a retry or a subscription, with times and attempts (read-only) |
| `python -m feature_coordinator worker attention` | What needs an operator, with the **action** to take; exit 1 if anything (read-only) |
| `python -m feature_coordinator worker pause [--reason R]` / `resume` | Stop / restart scheduling new work. An active agent finishes its bounded run. SIGUSR1 wakes the service at once |
| `python -m feature_coordinator worker stop` | Graceful stop: SIGTERM to the verified service process. It cancels the active agent (completed checkpoints stay; the interrupted step runs again later) and exits 0 |
| `python -m feature_coordinator worker stop --after-current` | Let the active task finish, then exit (nothing new starts) |
| `python -m feature_coordinator worker reset-retries TASK [--apply]` | Clear a task's budgets, waits and attention (dry run without `--apply`) |
| `python -m feature_coordinator [--state-dir DIR] [--project-config FILE] [--tracker github] worker systemd-unit [--config FILE]` | Print the unit for this checkout. `--config` defaults to the global `--project-config` (two different files are refused); an explicit `--state-dir` and `--tracker` are kept in `ExecStart` (without them: the default state directory and the local tracker). Nothing is installed |

Control commands write `flags/worker-control.json` and signal only the **recorded** service process, after re-verifying its identity. The request stays recorded either way:
- a pause also applies at the next start;
- a stop is consumed by the instance that stops.

The existing commands are unchanged: `checkpoints TASK` and `recover TASK` for a task's W3 details, and `status` for the board.

## 11. systemd (user service) — instructions; nothing is installed

**Template.** `feature_coordinator/systemd/mx-admin-worker.service.in`:
- `Type=simple`, with `ExecStart` running `python -m feature_coordinator --repo R [--state-dir DIR] [--project-config FILE] [--tracker T] worker start` (global options before the subcommand, as absolute paths; each only when given to `systemd-unit`);
- `Restart=on-failure`, `RestartSec=60`, `RestartPreventExitStatus=73 78`;
- `StartLimitIntervalSec=900`, `StartLimitBurst=5`;
- `KillMode=mixed`, `KillSignal=SIGTERM`, `TimeoutStopSec=180`;
- output to the journal.

It contains no credentials and no `HOME` override: Claude Code and Codex keep using their own logins under the user's HOME, which the user manager provides. `PATH` is set to the directories where `claude`, `codex` and `gh` are found when the unit is rendered (as found, without resolving symlinks: a Snap `gh` adds `/snap/bin`), then the system defaults `/usr/local/bin:/usr/bin:/bin` in that order, without duplicates (no secrets). Render the unit in the shell whose `PATH` finds the CLIs the service should use.

**Preconditions** (operator decisions):
- a project configuration with `worker.autonomous.enabled: true`;
- an activated feature;
- a successful `python -m feature_coordinator worker start --exit-when-idle` in a terminal first.

```bash
# 1. Render the unit for this checkout and configuration (prints only; global options such as
#    --tracker github or --state-dir DIR go before `worker` and are kept in ExecStart):
python -m feature_coordinator worker systemd-unit --config /path/to/project.yaml > /tmp/mx-admin-worker.service
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
python -m feature_coordinator worker status

# Stop gracefully / disable / remove:
python -m feature_coordinator worker stop          # or: systemctl --user stop mx-admin-worker
systemctl --user disable mx-admin-worker
rm ~/.config/systemd/user/mx-admin-worker.service && systemctl --user daemon-reload
```

**Running without a login session** needs `loginctl enable-linger $USER`. That is a separate operator decision, and W4 did **not** enable it. Without linger, the service stops at logout.

**Sleep and power.** A user service **cannot keep a laptop working while it is suspended or powered off**. Work stops at suspend. At resume, the service continues: a wait that fell due is acted on, and a run that the suspend cut short is recovered by W3. W4 does not change sleep settings.

**Recovery after a failure:**
1. `systemctl --user status mx-admin-worker` shows the exit code.
2. Exit **78**: run `python -m feature_coordinator worker health` and read the last reason, or `worker-startup.json`. Fix the cause, then `systemctl --user reset-failed mx-admin-worker && systemctl --user start mx-admin-worker`.
3. Exit **73**: another service owns the state directory (`python -m feature_coordinator worker status` shows which pid).
   Exit **78** with `pre-relocation test environment path(s)` in the journal: the project file predates the `test_environment/` relocation; migrate it ([operations §11](operations.md#11-test-environment-relocation-updating-the-desktop-worker-2026-10-30)).
4. Then `python -m feature_coordinator worker attention`, which names the action for each held task.

## 12. Logging, sanitization, retention

**Two outputs:**
- **Service lines** go to stdout, one flushed line each (`<UTC time> <worker-id>: <message>`), which the journal stores under systemd. They record state changes, launches, outcomes, waits, attention, recovery and stops.
- **Structured events** go to `events.jsonl`:
  - service lifecycle: `worker-started`, `worker-stopped`, `worker-state`, `worker-control`;
  - recovery: `worker-recovered`, `worker-recovery-done`, `worker-orphans-stopped`;
  - launches: `worker-task-selected`, `worker-agent-started`, `worker-agent-stopped`;
  - outcomes: `worker-task-verified`, `worker-retry-scheduled`, `worker-subscription-unavailable`, `worker-attention-required`, `worker-outcome`;
  - the watchdog: `worker-watchdog-stale`, `worker-watchdog-hung` (with `resumed` when a restarted service continues a decision), `worker-watchdog-terminated` (confirmed, survivors), `worker-orphans-stopped`, `worker-leftovers-stopped`;
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

**`feature_coordinator/tests/test_fc_autonomy.py`** (43 tests, in-process). It uses mock agents in launch threads, real Git and a **simulated clock**. Covered:
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

**`test_fc_service_process.py`** (11 tests). It uses real `python -m feature_coordinator worker start` processes, the native-cli backend with the fake agent CLI, real Git, signals and SIGKILLs:
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

**W4.1 watchdog evidence: `test_fc_watchdog.py`** (17 tests). Real `python -m feature_coordinator worker start` processes, real launch processes (native-cli backend, Supervisor) and the fake agent CLI. Timeouts are short and set in each test's own configuration only. Processes are synchronized by pid and marker files and bounded polling.

Real-process tests:
- **Hung agent:** SIGTERM ignored, no output, plus a setsid'd child that ignores SIGTERM. Stopped at its run deadline: one SIGTERM, then SIGKILL; the child is stopped by its run tag. The outcome is transient, and the retry starts only after both are gone (the retry's agent checks).
- **Silent but healthy agent:** three stale periods without output are reported as "running"; nothing is signalled.
- **Slow launch phase:** 5 s without a heartbeat is reported as "slow", with one warning; nothing is stopped.
- **Hung launch:** the decision is recorded, then SIGTERM and SIGKILL, and the end is confirmed. The outcome is transient, and the task resumes at step 2.
- **Crash during cleanup, hung launch:** the service dies after SIGTERM. The next service continues the recorded decision. Health and status stay read-only, with state files byte-identical.
- **Crash during cleanup, orphan:** the service dies after SIGTERM to the orphan. The next service re-checks it and escalates.
- **Orphan after service and launch are SIGKILLed:** nothing starts until the orphan's deadline, the event timestamp is at or after that deadline, and the task resumes from its checkpoint.
- **Stop, after-current and pause during a hung agent:** each keeps its documented semantics; nothing is terminated twice and no claim is released early.

In-process tests (real processes, injected identity evidence or signal loss):
- an orphan that cannot be confirmed terminated blocks every task and is not re-signalled;
- survivors of a finished launch are stopped, or else block (both variants);
- PID reuse never signals the process now using the PID;
- an unreadable identity fails closed;
- the claim watchdog view and the diagnosis order.

Every process test ends with cleanup assertions: no process of the test left, no claim, no held lock, no live exclusive holder, checkpoint records consistent. A `reaper` fixture kills, after a failure, only processes whose command line names that test's temporary directory.

**Repetition (final code and tests):** the W4.1 watchdog suite **together with** the W4 process suite passed **20 consecutive runs, 28/28 each** (560 test executions, 282–292 s per run). The code was checksummed before and after and was unchanged, and no test process was left. Earlier loops: 20/20 runs of the watchdog suite alone (17/17 each) on the previous test version, and one loop stopped by hand to apply a final change.

**Optional soak.** `feature_coordinator/tests/fc_service_soak.py --workdir DIR [--tasks N] [--failure-every K] [--idle-seconds S]` runs the real service process over fake-agent tasks. It samples memory, file descriptors, threads and children every second, and checks invariants.

The 2026-10-22 run (36 tasks, every 4th failing once, 120 s idle) passed every check:
- 45 agent runs, one commit per task;
- at most one claim at a time;
- memory flat (about 28.9 MB RSS), 3–11 fds, 1 thread;
- no lingering child while idle.

**Not proven:**
- real-world 24/7 reliability, real model latency, real subscription exhaustion and real CLI error texts;
- suspend/resume;
- days of uptime.

### 14.1 W4.2 — real-agent smoke test (VERIFIED WITH REAL AGENTS, 2026-10-23)

**Tooling (test-only, never imported by the coordinator):**
- the harness `feature_coordinator/tests/fc_worker_smoke.py`;
- its offline tests `test_fc_worker_smoke.py` (38 tests).

**What the harness does:**
- **The real service path.** It drives `python -m feature_coordinator worker start --exit-when-idle` twice, in a disposable repository with its own state directory: launch, native-cli backend, the real CLI, then checkpoints and gates.
- **A controlled restart.** A test-only hook holds Backend's launch at the *production* cancel check after step 1. `worker stop` then defers the task before any step-2 agent exists, and a new service resumes it.
- **An independent audit wrapper.** Every agent CLI call passes through it, and it refuses a session unless all of these hold:
  - the CLI binary is unchanged;
  - the session is in the disposable repository and carries a coordinator run tag;
  - the role, task, step and model match the plan;
  - only subscription flags are used and no API-key variables are set;
  - the CLI's own status command reports the subscription login;
  - the budgets have room (4 sessions in total, per role, one per step) and the test deadline allows it;
  - no earlier agent or its children are alive;
  - **both supervisors are valid**: the harness process, and a revocable grant for the exact Worker instance (pid and start time) that launched the session.
- **Records and independent observation.** The wrapper logs start, child identity, end and exit status, never prompts. A `/proc` monitor and a `worker health --json` sampler observe on their own. A `gh` shim refuses any GitHub call.
- **The Worker never outlives the harness.** The service gets a parent-death SIGTERM, and its grant is revoked before any stop.
- **The report.** It gives a verdict (VERIFIED / PARTIALLY VERIFIED / BLOCKED) and separates the primary failure, its secondary consequences, other findings and independent safety violations.

**Bounded configuration:**
- 600 s per session (the Supervisor's deadline) and 40 minutes in total;
- at most 2 launches per task and no transient retry;
- validation auto-fix off;
- one delayed subscription recheck (3600 s), which only makes WAITING observable;
- Claude Code `--max-budget-usd 0.5`.

**Passing run `run-20261023T083640`** (operator, separate terminal):
- Claude Code 2.1.296 and Codex 0.162.1, each identified by the SHA-256 of its binary.
- Logins reported by the CLIs themselves: claude.ai subscription (Pro) and ChatGPT subscription.
- Verdict **VERIFIED**: 69/69 checks, independently re-verified from the raw evidence.

| Step | Task / agent | Requested model | Reported model | Session (UTC) | Exit | Checkpoint commit |
| --- | --- | --- | --- | --- | --- | --- |
| `module` | backend / Claude Code | `opus` | `claude-opus-5-5`, `apiKeySource: none` | 08:36:51.8–08:36:57.3 (service 1) | 0 | `b79557ea4b23` (cp1) |
| `validation-test` | backend / Claude Code | `opus` | `claude-opus-5-5`, `apiKeySource: none` | 08:37:00.3–08:37:07.8 (service 2) | 0 | `2598699bced7` (cp2) |
| `cli-utility` | cli / Claude Code | `sonnet` | `claude-sonnet-5-5`, `apiKeySource: none` | 08:37:10.0–08:37:19.5 (service 2) | 0 | `abc211a561ad` (cp1) |
| `ts-utility` | frontend / Codex | CLI default | **not reported** by Codex | 08:37:21.5–08:37:35.4 (service 2) | 0 | `1d7c59ef285e` (cp1) |

- **Sequential execution.** The session intervals never overlap (gaps of 2.0–3.0 s). At each start, the wrapper found no earlier session or run-tagged process alive. The monitor saw at most one run tag and one launch process in all 283 samples, and health showed at most one claim in 70 samples.
- **Dependencies.** Frontend was eligible during Backend's sessions but waited. CLI was not eligible until Backend was verified (verified 08:37:09.0, CLI session 08:37:10.0).
- **Checkpoints and Git:**
  - 4 committed checkpoints with unique IDs and commits, linear on one feature branch (each commit's parent is the previous one);
  - `Feature-Task` / `Feature-Role` / `Feature-Checkpoint` trailers on every commit, one file per commit inside the role's paths;
  - manifests equal to the files' SHA-256, and the agents' files byte-identical to the requested content;
  - no remote, and a clean checkout.
- **Restart and recovery:**
  - service 1 ran only `module`; at the boundary it was stopped through the production cancel check ("stopped before step 'validation-test'"), so no agent was interrupted;
  - service 2 adopted checkpoint #1 unchanged and ran only step 2, and the resumed prompt listed `module` as done;
  - Backend's launches were 2 with history `[cancelled, success]`, and no retry budget was used.
- **Gates and tasks.** `w4s-backend` (unittest), `w4s-cli` (exact output) and `w4s-frontend` (Node runs the TypeScript file) all passed in the gate sandbox, and all three tasks are verified.
- **Service lifecycle.** Service 1 ended OPERATOR_STOP (the planned restart). Service 2 ended NATURAL_IDLE_EXIT after AVAILABLE. Afterwards health reports OFFLINE, not alive and not healthy, and reading it changed nothing.
- **Cleanup.** No process carrying the test's run tags, no claim, exclusive flag, held lock or pending checkpoint transaction, and no `gh` call.

**First attempt `run-20261023T071146`: PARTIALLY VERIFIED** (kept as evidence; never reused):
- **What ran.** Two real Opus sessions ran and the restart worked.
- **What failed.** The Backend gate was then *blocked* (exit 125). The operator's terminal was unconfined, and Ubuntu 24.04 restricts unprivileged user namespaces for unconfined processes (`kernel.apparmor_restrict_unprivileged_userns=1`), so bubblewrap was denied `setpcap` (kernel audit log). The agents' code was correct: the same gate passes offline on a copy of that repository.
- **The consequence.** The harness stopped the Worker. Frontend's Codex invocation was refused by the audit wrapper (exit 97, no model request, the grant revoked), and CLI never started. There was no safety violation.
- **W4.2.1 fixes:**
  - **Production:** a gate that could not run is recorded with its cause ("gates could not run: … (cause)") and classified critical, never as validation, which with auto-fix would have spent an AI session on correct code.
  - **Harness:** the preflight runs a real sandboxed gate in the services' environment; a refusal is recorded with its reason; service exits are classified from their logs.
- **The host prerequisite.** The operator installed the distribution's `bwrap-userns-restrict` AppArmor profile (package `apparmor-profiles`), keeping the global restriction on (§16). Host setup procedure: [worker-host-setup.md](worker-host-setup.md).

**Offline evidence for the harness:**
- **Tests:** 38 tests, including the full rehearsal, a usage-limit path, the two supervisor-failure scenarios, the gate-sandbox preflight, and an exact offline reproduction of the first attempt's failure path.
- **Repetition:** 10/10 consecutive rehearsals (69/69 checks each).
- **Full suite:** `scripts/tests` 656 passed / 19 skipped / 0 failed before the host AppArmor profile was installed. On the same code after it, 1 environment failure, the nested Codex sandbox test (§16). **Final, with the nested-sandbox test guard and a fix for a `/proc` race in a W4 test helper: 672 passed / 20 skipped / 0 failed.** The extra skip is that test, skipped because its probe demonstrated the restriction; the other 19 are opt-in live checks and one filesystem condition. The real run logged no AppArmor denial.

**Classification:**
- **Verified with real AI:**
  - four real sessions under the autonomous service;
  - subscription logins and no API key (Claude Code's `apiKeySource: none`);
  - the requested Claude model families;
  - strict sequential execution, dependency order and checkpoint commits;
  - a controlled restart with checkpoint adoption, gates, idle exit and cleanup.
- **Verified offline:** the failure paths (harness or Worker death, refusals, usage limit, a broken gate sandbox) and the watchdog (W4.1).
- **Simulated:** usage-limit messages; long runs.
- **Not verified:** Codex's actual model (it is not reported); real usage limits and outages; long-running, overnight or unattended operation; the systemd service.

### 14.2 W4.3 — real GitHub workflow (SUPERVISED, 2026-10-24)

- **W4.3.2:** The operator approved Issue [#25](https://github.com/spyderbg/mx-admin-cloud/issues/25). A real Claude Worker verified its CLI task; PR #27 was opened and merged manually.
- **W4.3.3:** The coordinator adopted that manually merged PR, verified its merge SHA `f631c57` and closed Issue #25. The implementation is merged in PR #28 (`0b62949`).
- **W4.3.4:** A real Sonnet task from approved Issue [#29](https://github.com/spyderbg/mx-admin-cloud/issues/29) verified successfully. The running Worker integrated and pushed the branch and opened Draft PR [#30](https://github.com/spyderbg/mx-admin-cloud/pull/30), then waited for a human. PR #30 was manually marked ready and merged (`9861975`); after the Worker was stopped, the operator ran one manual publisher pass, which closed Issue #29 and moved Project #2 to `Done`.
- **Boundary:** W4.3 validates the supervised happy path, **not** fault injection with real GitHub, overnight service operation, unattended auto-retry or systemd startup. See [phase-status.md](phase-status.md) and [github-intake.md §10.10](github-intake.md#1010-live-verification-record-2026-10-24).

### 14.3 W4.4 — supervised reliability acceptance (2026-10-29)

**This is fresh verification of existing implementation, not a new source-code feature.** The operator reran 26 selected pytest cases across crash recovery (4), transient Git/GitHub failures (4), validation/retries (7), and publication interruptions (11). All passed. Offline publisher fault injection uses mock GitHub; no real network failure was induced.

An additional **513-second** soak exercised the real Worker service process in a disposable Git repository with **48 fake-agent tasks**, **60 runs**, **12 simulated transient failures** and **180 seconds idle**; all **10 invariant checks passed**, with no duplicated task commits or lingering claims/processes. A separate real-agent run invoked Claude Code Opus ×2, Sonnet ×1 and Codex (CLI default) ×1, gracefully stopped after the first Backend checkpoint, restarted without replay, and passed **69/69** independent checks (8 preflight, 28 outcome, 33 safety); all three gates passed and the service exited `OFFLINE` with no claims or agent processes. The harness prints its original `W4.2 real-agent smoke test` label.

Details, command transcript summary and explicit unverified scenarios: [W4.4 acceptance](w44-reliability-acceptance.md). No systemd host reboot, 24/7 runtime, real GitHub outage or live interruption was attempted.

## 15. Operational example (what really happens; offline-verified behaviour)

The steps match tests E and A and the soak run. The example is **not** a real-agent run.

1. **Worker starts.** systemd (or the operator) runs `worker start`. It takes the service lock, records its identity, holds `agent-exclusive` and runs RECOVERING: no stale claim, no in-flight launch. The feature is activated.
2. **Backend begins.** `api` (Backend → Claude Code, `opus`) is eligible. The pre-launch checks pass (CLI present, subscription login), launch 1 is recorded write-ahead, `python -m feature_coordinator launch` starts, and the state is BUSY.
3. **Two checkpoints complete.** Steps `models` and `service` each get a checkpoint commit (W3). `current.step` follows the claim's progress.
4. **Subscription becomes unavailable.** In step 3 the CLI fails with "usage limit reached|<epoch>". The launch ends FAILED and the outcome is classified `subscription`.
5. **Worker waits.** `api` is set back to READY with `next_attempt_at` = the reset time. The event `worker-subscription-unavailable` is recorded and the state is WAITING ("next attempt … (api: subscription)"). Nothing launches, and no API key or other model is tried. CLI and Frontend depend on `api`, so they wait too.
6. **Subscription becomes available.** At the reset time the idle sleep ends, which takes at most `idle_poll_seconds`.
7. **Worker resumes from the checkpoint.** In launch 2, W3 reconciles, keeps checkpoints #1 and #2, and runs **only** step 3.
8. **Backend finishes.** Its gates pass and it is VERIFIED (`worker-task-verified`).
9. **CLI begins.** `cmd` is now eligible and runs alone. Frontend is eligible too, but waits: one agent at a time.
10. **Frontend finishes.** After CLI is verified, `view` (Codex) runs and is verified.
11. **Feature becomes ready for integration.** All tasks are verified: "feature … is complete: ready for integration (`python -m feature_coordinator integrate … --apply`)". Integration and the PR remain operator steps.
12. **Worker becomes AVAILABLE** and idles: one bounded sleep per poll, no child processes, until an operator activates another feature.

**Implemented and offline-verified:** every transition above, with the fake agent and the mock runners.

**Simulated:**
- the usage-limit message (the real CLIs' wording was not observed);
- the passage of time (simulated clock);
- the agents (fake).

**Planned:** taking the next feature from a central queue.

## 16. Limitations

- **Only bounded real-agent runs.** W4.2 originally ran 4 real sessions; W4.3.4 ran one additional real Claude Sonnet task with live Draft PR publication; W4.4.5-B reran a separate 4-session Claude/Codex recovery smoke (69/69). W4.4.5-A was a 48-task **fake-agent** soak, not an overnight or 24/7 real-model trial. Actual GitHub outages, real CLI usage-limit text, and restart after machine reboot remain unverified.
- **Codex does not report its model.** The Frontend session ran the Codex CLI default, which is not independently verified.
- **Host prerequisite for gates.** On Ubuntu 24.04 the gate sandbox needs bubblewrap to be allowed unprivileged user namespaces, through the distribution's `bwrap-userns-restrict` AppArmor profile (package `apparmor-profiles`), installed and loaded by the operator ([worker-host-setup.md](worker-host-setup.md)); the coordinator never changes AppArmor. Without it every gate is blocked, and since W4.2.1 that is reported as critical "gates could not run".
- **That profile breaks nested sandboxes.** It runs everything bubblewrap starts under `unpriv_bwrap`, with all capabilities denied, so a bubblewrap started *inside* the coordinator's sandbox cannot create namespaces.
  - **Affected:** the optional `execution_backend: bwrap` with Codex, because Codex's own command sandbox would be nested inside ours. `test_fc_launchers.py::test_codex_own_sandbox_works_nested_inside_ours` is skipped there with an explicit reason, but only when a probe in the same Frontend sandbox demonstrates the restriction; any other failure still fails it ([agent-ownership §5](agent-ownership.md#5-residual-risks-operator-decisions-in-phase-status), item 6).
  - **Not affected:** the default `native-cli` backend, which runs the agents outside the coordinator's sandbox, and the gates, which are top-level sandboxes.
  - **Not exercised by real agents:** Codex's command sandbox under `native-cli`. In W4.2, Codex ran no shell command; it made one file change.
- **Subscription reset times** are trusted only in the machine-readable `|<epoch>` form. Otherwise the service rechecks conservatively.
- **Watchdog limits** (W4.1):
  - The hung-launch threshold is long by design (4630 s by default).
  - A process that clears its environment *and* leaves its session cannot be found by run tag (no cgroup tracking without systemd).
  - An uncertain cleanup is not retried within one service instance.
  - An orphan whose launch died before recording the agent's identity is held for the operator, not timed out.
  - While the service waits for an orphan's deadline, claim recovery logs a `recovery-refused` event every idle poll (bounded by the deadline).
- **Validation auto-fix** redoes only the last step of a multi-step task. A single-step task always goes to NEEDS_ATTENTION.
- **One feature per checkout.** When a feature completes, the service idles until an operator activates another. For an operator-planned feature it neither integrates nor opens a PR. W4.3.3 does both only for an approved GitHub Issue's feature with `github.publish.enabled`, and only up to a Draft PR; a person merges it.
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
- **Verified with real agents (bounded):** W4.2: 4 sessions, controlled restart and gates; W4.3.4: one real Sonnet task, automatic Draft PR and human merge; W4.4.5-B (2026-10-29): four real sessions, 69/69 checks, checkpoints reused and no leaks. See [W4.4 acceptance](w44-reliability-acceptance.md).
- **Simulated:** long runs (accelerated clock), real-service soaks with **fake agents** (W4.4.5-A: 48 tasks, 60 runs, 10/10 invariants, 513 seconds), GitHub outages and publication crash points (mock GitHub/disposable Git), usage-limit messages.
- **Not verified:** multi-day/overnight unattended work; installed systemd restart after host reboot; real GitHub outages and interrupted live push/PR creation; actual subscription-limit errors; Codex's effective model; suspend/resume. Watchdog fault scenarios were tested offline with real processes and fake agents, not during real-agent failures.
- **Planned:** central coordinator, multiple machines, a feature queue, notifications.
