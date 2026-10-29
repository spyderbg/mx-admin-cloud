# Coordinator Operations — Launch, Stop, Resume, Recover, GitHub Issues and Projects (Phases 3–4)

> Owner role: **coordination**. Status: Phase 3 sections **approved** (2026-10-17); §7 (GitHub Issues) is the **Phase 4 deliverable, approved** (2026-10-17); §8 (GitHub Projects board) is the **Phase 4 extension, approved** (2026-10-17).
> Entry point: `python scripts/feature.py` (use the repository's `.venv` Python). Every command that changes something is a **dry run unless `--apply`** is given.
> Global options: `--repo PATH` (default: this checkout), `--state-dir PATH` (default: `~/.local/state/mx-admin-agents/`), `--project-config FILE` (default: built-in configuration), `--tracker local|github` (default: `local` for feature operations, `github` for the `github` commands).

## 1. Before anything runs

| Step | Command | What it proves |
| --- | --- | --- |
| State directory | `feature.py doctor --apply` | Local filesystem (FUSE and network rejected), mode 0700, two-process `flock` contention test |
| Runners | `feature.py runners` | `claude` and `codex` exist, support every required flag, and get no bypass flag; prints the execution backend, each role's agent, model and exact command line, and — for `native-cli` (W2) — the subscription login each CLI's status command reports. **No model call** |
| Containment | `feature.py sandbox-check` (dry run) | Each role's writable roots, read-only islands and hidden secrets on `--repo` |
| | `feature.py sandbox-check --apply` | The full containment matrix in a **disposable** repository ([agent-ownership §3](agent-ownership.md#3-containment-matrix-exit-gate-evidence)) |

## 2. One feature, step by step

```bash
feature.py plan spec.yaml --apply            # record the feature and its tasks (local tracker: <state>/tracker.json)
feature.py activate <feature> --apply        # create feature/<slug> from dev (D-4); every writer must be quiescent
feature.py launch <feature>.<task> --apply   # one task: claim → runner (execution backend) → commit → gates → release
feature.py work <feature> --apply            # W2: one Worker runs every task — dependencies first, roles in parallel
feature.py status                            # tasks, claims (live / stale / heartbeat age), stop requests
feature.py integrate <feature> --apply       # quiescent barrier → single PR candidate (not opened)
```

**Execution backend (W2).** `runtime.execution_backend` is `native-cli` by default: the installed `claude` and `codex` run directly in the checkout with the operator's **subscription logins**. That is **not a security sandbox**: write scopes are verified after each run, and changes outside the role are rejected and left in place. `bwrap` selects the Phase 3 containment. There is never a fallback between the two. Design: [worker-functional.md](worker-functional.md).

**The Worker (W2).** `feature.py work <feature>` (dry run) lists the tasks in dependency order with their agent, model and state, and checks the workspace. With `--apply` it launches READY tasks as separate `launch` processes:
- one per role at a time, up to `--max-parallel`;
- dependents start once their dependencies are verified;
- each task is launched at most `--max-attempts` times (default 1);
- Ctrl-C/SIGTERM stops every running task gracefully.

`--integrate` runs the integration when every task is verified.

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
| W3: the worker died but its native agent still runs | `feature.py recover` reports it **orphaned** and does not reclaim the task | No second execution starts. `recover <task> --apply --terminate-orphans` stops exactly the run's processes (run tag / process identity), then reclaims (`test_h_a_surviving_agent_blocks_a_second_execution` **[V]**) |

**W3 — multi-step tasks** ([worker-checkpoints.md](worker-checkpoints.md)). A task with `steps` commits a checkpoint after every step and resumes from the last one:

```bash
feature.py checkpoints <task>                      # read-only: steps done/remaining, checkpoints, Git consistency
feature.py recover <task> [--show-prompt]          # PREVIEW: what recovery would do, the exact checkpoint and
                                                   # revision a resume continues from (and the next step's context)
feature.py recover <task> --apply                  # reclaim, settle interrupted checkpoints, or record a conflict
feature.py recover <task> --apply --abandon-pending    # explicit decision: re-run a step whose files changed
feature.py recover <task> --apply --redo-from STEP     # redo STEP and later steps (e.g. after failed gates)
feature.py launch <task> --apply                   # resume: only the remaining steps run
```

`feature.py work` does the same automatically. At start it reclaims dead claims, never orphaned ones, and every run reconciles its checkpoints before the first step. A conflict sends the task to `needs-human`. Recovery never resets Git, never discards files and never starts an agent.

**W4 — the autonomous Worker service** ([worker-autonomy.md](worker-autonomy.md)). It runs an activated feature's tasks one agent at a time, without a terminal, and retries within bounded, persistent budgets. It is OFF until `worker.autonomous.enabled: true`:

```bash
feature.py worker start [--exit-when-idle]          # foreground (systemd --user runs exactly this)
feature.py worker status | health [--json]          # read-only; health exits 1 unless alive, fresh and attention-free
feature.py worker waiting | attention               # read-only: retry / subscription waits; decisions needed (+ action)
feature.py worker pause | resume                    # no new work / again (an active agent finishes)
feature.py worker stop [--after-current]            # graceful: cancel the active agent (checkpoints stay) / finish it first
feature.py worker reset-retries <task> [--apply]    # operator decision: clear budgets, waits and attention
feature.py worker systemd-unit --config FILE        # print the user unit; nothing is installed or enabled
```

`feature.py work` now defaults to one agent at a time (`worker.max_parallel`); `--max-parallel N` is the W2 parallel scheduler. While a sequential Worker runs, a manual `launch` of another task is refused ("sequential mode").

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

| Variable / command | Test | Cost |
| --- | --- | --- |
| `feature.py subscription-smoke [--apply]` | W2: three small real tasks (Backend Claude Code `opus`, CLI Claude Code `sonnet`, Frontend Codex default) through the Worker in a new disposable repository; dry run without `--apply` | subscription usage only (no API key); **operator approval required** ([worker-functional §10](worker-functional.md#10-real-subscription-smoke-test-operator-approval-required)); approved and **passed** once on 2026-10-22 (Ubuntu laptop) |
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
| `feature.py github intake show N` / `poll [--apply]` / `status` | W4.3.1: review an Issue (digest, `/worker approve` line per role); turn approved Issues into tasks (plan, activate, verify the approved text); list them ([github-intake.md](github-intake.md)) | `poll --apply`: local records, one comment on each approved Issue, its board item |
| `feature.py github publish status` / `poll [FEATURE…] [--apply]` / `reset FEATURE [--apply]` | W4.3.3: an approved Issue's verified feature is integrated, its branch pushed, ONE Draft PR opened (or a matching PR adopted); after a person merged it, the merge is verified and the feature completed, the Issue closed and its card moved to Done. `status` is local; `poll` without `--apply` only reads; `reset` clears a stop ([github-intake §10](github-intake.md#10-automatic-publication-and-completion-w433)) | `poll --apply` (needs `github.publish.enabled`): the feature branch on `origin`, one Draft PR, comments on and the closing of that Issue, its board item. Never a merge |

Operating rules:

- Feature operations (`plan`, `activate`, `launch`, `integrate`, `hold`, `release`) use the local JSON tracker unless `--tracker github` is given; with it, each operation syncs the Issues **after** its state change and outside every coordinator lock. A failed sync is recorded (`tracker-sync-deferred` event) and finished by `github sync FEATURE --apply`.
- Phase 4 write scope: only features planned with `test: true` may be written to GitHub (`github.write_scope: test-features-only`); W4.3.1 adds features made from an operator-approved Issue, limited to that Issue's comments and board item. Widening it further is an operator decision.
- No command creates a pull request, except `github publish poll --apply` (and the Worker service) with `github.publish.enabled`: the ONE Draft PR of an approved Issue's feature. Nothing merges; a person reviews and merges it.
- If `github status` reports a missing capability, stop the live operation and fix it (for example `gh auth login`); there is no fallback to the local tracker.

| Situation | What to do |
| --- | --- |
| A sync was interrupted (crash, network) | Run `github sync FEATURE --apply` again: pending creations and comments are found by key/operation ID before anything is re-sent |
| `protected` in a report | The Issue failed ownership verification (marker removed, wrong author, deleted, mapped elsewhere); it is untouched. Inspect it with `github inspect NUMBER` and decide manually |
| `duplicates` in a report | Two owned Issues carry one key; the oldest is used. Close the other one by hand after checking it |
| `drift` in a report | A human changed labels, the managed block or the open/closed state; the local state was re-applied. Use `/feature hold`/`release` or `feature.py` commands to change task state |
| Rate limited | The sync waits up to `max_rate_limit_wait_seconds`, otherwise it is deferred; run it again later |
| `github publish poll` reports a stop (NEEDS ATTENTION) | Read the reason (`github publish status`). Resolve it: restore the remote or the branch; close a stray PR; inspect the merge. Then run `github publish reset FEATURE --apply`. A PR closed without merge is not a stop: reopen and merge it, and the next pass continues |
| Publication was interrupted (crash, network, lost GitHub response) | Run `github publish poll --apply` again (or let the service do it). The remote branch and the PRs are read before anything is pushed or created, so nothing is duplicated ([github-intake §10.5](github-intake.md#105-recovery-after-interruptions)) |

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

## 9. Supervised GitHub intake → Worker → Draft PR → human merge → Done (verified 2026-10-24)

The commands below reflect the successful live tests of Issues #25 and #29. They operate on the **same local Coordinator state directory** (the default, unless `--state-dir` was explicitly supplied earlier), and a **local** config outside the repository. Do not put access tokens or machine-specific settings in the repository.

Preconditions: current `dev` fetched with `git pull --ff-only origin dev`, clean checkout, no running Worker or unattended service, GitHub Project #2 correctly configured, and `github.intake.enabled: true`, `github.publish.enabled: true`, `worker.autonomous.enabled: true` in `~/.config/mx-admin-agents/project.yaml`. The default config intentionally disables these capabilities. The W4.3.4 test was run under the user's approval and the shared `spyderbg` identity risk acceptance (§9 of [github-intake.md](github-intake.md)).

```bash
# Run from the mx-admin-cloud checkout. Inspection only:
git status --short --branch
python scripts/feature.py --tracker github --project-config ~/.config/mx-admin-agents/project.yaml worker status
python scripts/feature.py --tracker github --project-config ~/.config/mx-admin-agents/project.yaml worker attention

# After creating and reviewing a new Issue N, print its digest (read-only):
python scripts/feature.py --tracker github --project-config ~/.config/mx-admin-agents/project.yaml github intake show N
# Publish exactly the printed /worker approve <role> <64-hex-digest> line as a GitHub Issue comment.

# Dry-run (read-only), then intake apply (writes, operator authorization):
python scripts/feature.py --tracker github --project-config ~/.config/mx-admin-agents/project.yaml github intake poll
python scripts/feature.py --tracker github --project-config ~/.config/mx-admin-agents/project.yaml github intake poll --apply

# Verify clean feature branch and run foreground Worker until Draft PR is opened:
git status --short --branch
python scripts/feature.py --tracker github --project-config ~/.config/mx-admin-agents/project.yaml worker start
# In another terminal, watch `worker status` / `github publish status`.
# When the service reports WAITING for the Draft PR and no agent is running:
python scripts/feature.py --tracker github --project-config ~/.config/mx-admin-agents/project.yaml worker stop

# A PERSON reviews the Draft PR and explicitly marks it ready and merges with a merge commit.
# Then, with the Worker stopped, dry-run and apply the verified completion for issue-N:
python scripts/feature.py --tracker github --project-config ~/.config/mx-admin-agents/project.yaml github publish poll issue-N
python scripts/feature.py --tracker github --project-config ~/.config/mx-admin-agents/project.yaml github publish poll issue-N --apply
# Update local dev only AFTER completion and once the feature branch is no longer needed:
git switch dev
git pull --ff-only origin dev
```

**Safety and interpretation.** `github intake poll` and `github publish poll` without `--apply` are read-only; the Worker service and `--apply` perform real writes. Run `worker stop` only after confirming no active agent (or use `--after-current` to let an active task finish). Do **not** manually push a feature branch or create a PR during the automatic Draft PR test; that would test adoption instead. One approved Issue creates one feature and one Draft PR, never auto-merges. PR merge commits are required by the existing merge-evidence verification (squash/rebase are not supported). With the Worker stopped, manual `publish poll issue-N --apply` completes the Issue and Project #2 after human merge. Always update local `dev` before activating the **next** feature: the Coordinator fetches remote `dev` for verification but does not move the local branch. Keep Project #1 untouched.

**2026-10-24 acceptance:** Issue #25 / manually merged PR #27 proved post-hoc adoption/closure; Issue #29 / automatically created Draft PR #30 proved the real auto-publication path and subsequent verified closure. Worker was stopped normally after Draft PR #30. These do not establish unattended reliability; W4.4 will use disposable repositories and fault injection before any wider live test.
