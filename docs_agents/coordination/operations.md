# Coordinator Operations — Launch, Stop, Resume, Recover, GitHub Issues and Projects (Phases 3–4)

> Owner role: **coordination**. Status: Phase 3 sections **approved** (2026-10-17); §7 (GitHub Issues) is the **Phase 4 deliverable, approved** (2026-10-17); §8 (GitHub Projects board) is the **Phase 4 extension, approved** (2026-10-17).
> Entry point: `python -m feature_coordinator` (use the repository's `.venv` Python). Every command that changes something is a **dry run unless `--apply`** is given.
> Global options: `--repo PATH` (default: this checkout), `--state-dir PATH` (default: `~/.local/state/mx-admin-agents/`), `--project-config FILE` (default: built-in configuration), `--tracker local|github` (default: `local` for feature operations, `github` for the `github` commands).

## 1. Before anything runs

| Step | Command | What it proves |
| --- | --- | --- |
| State directory | `python -m feature_coordinator doctor --apply` | Local filesystem (FUSE and network rejected), mode 0700, two-process `flock` contention test |
| Runners | `python -m feature_coordinator runners` | `claude` and `codex` exist, support every required flag, and get no bypass flag; prints the execution backend, each role's agent, model and exact command line, and — for `native-cli` (W2) — the subscription login each CLI's status command reports; for `bwrap` (W1) also the verified minimum versions, sandbox dependencies and broker credential. **No model call** |
| Containment | `python -m feature_coordinator sandbox-check` (dry run) | Each role's writable roots, read-only islands and hidden secrets on `--repo` |
| | `python -m feature_coordinator sandbox-check --apply` | The full containment matrix **and the network/credential isolation self-test** (W1) in a **disposable** repository ([agent-ownership §3](agent-ownership.md#3-containment-matrix-exit-gate-evidence), [worker-security §8](worker-security.md#8-security-self-tests)) |
| Worker eligibility (W1) | `python -m feature_coordinator worker-check --apply` | For the `bwrap` backend: `Worker: ELIGIBLE` only when bwrap, the CLIs, the sandbox dependencies, a broker credential per CLI provider and both self-tests pass; otherwise `INELIGIBLE` with the blockers (exit 1). Never reads or prints a credential value. **No model call** |

## 2. One feature, step by step

```bash
python -m feature_coordinator plan spec.yaml --apply            # record the feature and its tasks (local tracker: <state>/tracker.json)
python -m feature_coordinator activate <feature> --apply        # create feature/<slug> from dev (D-4); every writer must be quiescent
python -m feature_coordinator launch <feature>.<task> --apply   # one task: claim → runner (execution backend) → commit → gates → release
python -m feature_coordinator work <feature> --apply            # W2: one Worker runs every task — dependencies first, roles in parallel
python -m feature_coordinator status                            # tasks, claims (live / stale / heartbeat age), stop requests
python -m feature_coordinator integrate <feature> --apply       # quiescent barrier → single PR candidate (not opened)
```

**Integration jobs of existing branches (W4.7, M1/M2).** The `integration intake | status | abort | approve | inspect` commands are a separate job type and are unrelated to `integrate <feature>`. M1 records and checks jobs; M2 adds the operator's SSH-signed authorization (`approve`) and the read-only inspection (`inspect`: pinned SHAs, provenance, the complete PR lookup, the changed paths classified through PathPolicy). No Git write, no PR, never a merge. See [integration-jobs.md](integration-jobs.md).

**Execution backend (W2).** `runtime.execution_backend` is `native-cli` by default: the installed `claude` and `codex` run directly in the checkout with the operator's **subscription logins**. That is **not a security sandbox**: write scopes are verified after each run, and changes outside the role are rejected and left in place. `bwrap` selects the Phase 3 containment. There is never a fallback between the two. Design: [worker-functional.md](worker-functional.md).

**The Worker (W2).** `python -m feature_coordinator work <feature>` (dry run) lists the tasks in dependency order with their agent, model and state, and checks the workspace. With `--apply` it launches READY tasks as separate `launch` processes:
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
| `sandbox.json` | The exact `bwrap` command line, writable roots, overlays, masked and hidden paths, the network mode and the broker endpoint and upstream (never the token) |
| `broker.log` | One JSON line per model request: method, path, status, reason, sizes, duration (W1; never headers or bodies) |
| `stdout.jsonl`, `stderr.log` | The runner's output |
| `home/` | The private HOME |

## 3. Stop, timeout, resume, recover

| Situation | What to do | What happens |
| --- | --- | --- |
| Stop a running task | `python -m feature_coordinator stop <task>` | A stop request bound to the running claim's token is recorded. The worker sends SIGTERM to the sandbox, waits `terminate_grace_seconds`, then SIGKILL. Every process in the sandbox's PID namespace dies. The task returns to **ready**; its uncommitted work stays in the role's roots (`test_operator_stop_terminates_the_sandbox_and_yields_the_task` **[V]**) |
| Ctrl-C / SIGTERM to a `launch` process | — | Same graceful stop (`test_sigterm_to_the_worker_is_a_graceful_stop` **[V]**) |
| A run exceeds `task_timeout_seconds` | — | The sandbox is killed and the task becomes **failed** (`test_task_timeout_fails_the_task` **[V]**) |
| Resume | `python -m feature_coordinator launch <task> --apply` again | The next run starts from the role's uncommitted files and commits them with its own changes (`test_sigkilled_worker_is_recovered_and_its_sandbox_is_gone` **[V]**) |
| A worker crashed (`kill -9`, OOM, reboot) | `python -m feature_coordinator recover` (dry run lists), then `recover --apply` | The kernel already released the worker's flocks, and `bwrap --die-with-parent` ended its sandbox. Recovery reclaims the task only when the worker is provably gone (PID absent, start time changed, zombie, other boot) |
| A worker is alive but stops heartbeating | `python -m feature_coordinator status` shows `UNRESPONSIVE`; then `python -m feature_coordinator stop <task>` | Never reclaimed automatically while alive (`test_alive_worker_without_heartbeats_is_reported_unresponsive` **[V]**) |
| A full-project session was requested by mistake | Python API `ClaimManager.withdraw_full_project_request()` | Scoped work can be scheduled again |
| W3: the worker died but its native agent still runs | `python -m feature_coordinator recover` reports it **orphaned** and does not reclaim the task | No second execution starts. `recover <task> --apply --terminate-orphans` stops exactly the run's processes (run tag / process identity), then reclaims (`test_h_a_surviving_agent_blocks_a_second_execution` **[V]**) |

**W3 — multi-step tasks** ([worker-checkpoints.md](worker-checkpoints.md)). A task with `steps` commits a checkpoint after every step and resumes from the last one:

```bash
python -m feature_coordinator checkpoints <task>                      # read-only: steps done/remaining, checkpoints, Git consistency
python -m feature_coordinator recover <task> [--show-prompt]          # PREVIEW: what recovery would do, the exact checkpoint and
                                                   # revision a resume continues from (and the next step's context)
python -m feature_coordinator recover <task> --apply                  # reclaim, settle interrupted checkpoints, or record a conflict
python -m feature_coordinator recover <task> --apply --abandon-pending    # explicit decision: re-run a step whose files changed
python -m feature_coordinator recover <task> --apply --redo-from STEP     # redo STEP and later steps (e.g. after failed gates)
python -m feature_coordinator launch <task> --apply                   # resume: only the remaining steps run
```

`python -m feature_coordinator work` does the same automatically. At start it reclaims dead claims, never orphaned ones, and every run reconciles its checkpoints before the first step. A conflict sends the task to `needs-human`. Recovery never resets Git, never discards files and never starts an agent.

**W4 — the autonomous Worker service** ([worker-autonomy.md](worker-autonomy.md)). It runs an activated feature's tasks one agent at a time, without a terminal, and retries within bounded, persistent budgets. It is OFF until `worker.autonomous.enabled: true`:

```bash
python -m feature_coordinator worker start [--exit-when-idle]          # foreground (systemd --user runs exactly this)
python -m feature_coordinator worker status | health [--json]          # read-only; health exits 1 unless alive, fresh and attention-free
python -m feature_coordinator worker waiting | attention               # read-only: retry / subscription waits; decisions needed (+ action)
python -m feature_coordinator worker pause | resume                    # no new work / again (an active agent finishes)
python -m feature_coordinator worker stop [--after-current]            # graceful: cancel the active agent (checkpoints stay) / finish it first
python -m feature_coordinator worker reset-retries <task> [--apply]    # operator decision: clear budgets, waits and attention
python -m feature_coordinator worker systemd-unit --config FILE        # print the user unit; nothing is installed or enabled
                                                    # (--config defaults to the global --project-config; the
                                                    #  global --state-dir / --tracker are kept in ExecStart)
```

`python -m feature_coordinator work` now defaults to one agent at a time (`worker.max_parallel`); `--max-parallel N` is the W2 parallel scheduler. While a sequential Worker runs, a manual `launch` of another task is refused ("sequential mode").

## 4. Gates

| Command | Notes |
| --- | --- |
| `python -m feature_coordinator gate <name> --apply` | One gate on `--repo`: checkout **read-only**, **no network**, private HOME and `/tmp`, timeout `gate_timeout_seconds`, logs under `<state>/logs/`. Lint gates compare with `--base` (default: the merge-base with `dev`) |
| `python -m feature_coordinator lint-report backend-lint --apply` | Full baseline findings by symbol (D-19 report) |
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
| `python -m feature_coordinator subscription-smoke [--apply]` | W2: three small real tasks (Backend Claude Code `opus`, CLI Claude Code `sonnet`, Frontend Codex default) through the Worker in a new disposable repository; dry run without `--apply` | subscription usage only (no API key); **operator approval required** ([worker-functional §10](worker-functional.md#10-real-subscription-smoke-test-operator-approval-required)); approved and **passed** once on 2026-10-22 (Ubuntu laptop) |
| `FC_HOST_RUNNER_CHECKS=1` | W1: real `claude auth status` / `codex login status` inside the sandbox with the real HOME path; asserts that **no** login is visible (nothing is mounted or read) | none (no model call) |
| `FC_LIVE_RUNNER_SMOKE=1` + `FC_LIVE_PROJECT_CONFIG=<config with a broker credential>` (bwrap backend) | One tiny real run per runner through the broker in a disposable repository (create `hello.txt`); Claude capped at $0.50 | per-token API billing; **operator approval required** (the Phase 3 run on 2026-10-17 used the pre-W1 login mount and is not evidence for the broker) |

The installed-CLI compatibility tests (`test_fc_runner_compat.py`) are **not** opt-in: they run the real CLIs offline against a fake provider, at no cost ([worker-security §8](worker-security.md#8-security-self-tests)).

```bash
FC_HOST_RUNNER_CHECKS=1 .venv/bin/python -m pytest -p no:cacheprovider feature_coordinator/tests/test_fc_launchers.py -k real_login
```

## 7. GitHub Issues (Phase 4)

Design and policies: [github-integration.md](github-integration.md). Dependency rules: [task-lifecycle.md](task-lifecycle.md).

| Command | What it does | Writes |
| --- | --- | --- |
| `python -m feature_coordinator github status` | Verifies `gh`, authentication, repository access, Issues enabled, permissions, endpoints and rate limit; names exactly what is missing (exit 1) | none |
| `python -m feature_coordinator github labels [--apply]` | Creates missing `feature:*` labels; never edits an existing label | labels |
| `python -m feature_coordinator github sync FEATURE [--apply] [--json] [--no-board]` | Local state → the feature's Issues (create or adopt, managed block, labels, open/closed, progress comments, outbox) and validated `/feature` commands → local state; then the feature's project board items (§8) unless `--no-board`. Without `--apply`: reads GitHub and lists what it would do | Issues and board items of that feature |
| `python -m feature_coordinator github reconcile [FEATURE…] [--apply] [--json]` | After a restart: sync each feature, then report drift, duplicates and orphans (owned Issues with no local record; never modified) | as `sync` |
| `python -m feature_coordinator github inspect NUMBER` | Ownership verdict for one Issue (marker, author, identity, foreign labels); untrusted text is sanitized for display | none |
| `python -m feature_coordinator github owned [--feature F]` | Issues carrying this coordinator's marker, created by the coordinator account | none |
| `python -m feature_coordinator github deps FEATURE [--remote]` | The dependency graph: states, Issue numbers, unmet dependencies (`--remote`: current labels on GitHub) | none |
| `python -m feature_coordinator github test-lifecycle [--apply] [--workdir DIR] [--no-cleanup]` | The Phase 4 acceptance scenario: 4 test Issues, a disposable repository, no PR ([github-integration §12](github-integration.md#12-live-acceptance-test)) | 4 new test Issues |
| `python -m feature_coordinator github cleanup-test FEATURE [--apply] --state-dir <work>/<feature>/state` | Final verification comment and close for a **test** feature's Issues; refuses anything not owned or not labeled `feature:test`; repeatable | those Issues |
| `python -m feature_coordinator hold TASK --reason TEXT [--apply]` / `release TASK [--apply]` | Human escalation (`needs-human`); a release re-checks dependencies | local state, then a sync |
| `python -m feature_coordinator github intake show N` / `poll [--apply]` / `status` | W4.3.1: review an Issue (digest, `/worker approve` line per role); turn approved Issues into tasks (plan, activate, verify the approved text); list them ([github-intake.md](github-intake.md)) | `poll --apply`: local records, one comment on each approved Issue, its board item |
| `python -m feature_coordinator github publish status` / `poll [FEATURE…] [--apply]` / `reset FEATURE [--apply]` | W4.3.3: an approved Issue's verified feature is integrated, its branch pushed, ONE Draft PR opened (or a matching PR adopted); after a person merged it, the merge is verified and the feature completed, the Issue closed and its card moved to Done. `status` is local; `poll` without `--apply` only reads; `reset` clears a stop ([github-intake §10](github-intake.md#10-automatic-publication-and-completion-w433)) | `poll --apply` (needs `github.publish.enabled`): the feature branch on `origin`, one Draft PR, comments on and the closing of that Issue, its board item. Never a merge |

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
| `drift` in a report | A human changed labels, the managed block or the open/closed state; the local state was re-applied. Use `/feature hold`/`release` or `python -m feature_coordinator` commands to change task state |
| Rate limited | The sync waits up to `max_rate_limit_wait_seconds`, otherwise it is deferred; run it again later |
| `github publish poll` reports a stop (NEEDS ATTENTION) | Read the reason (`github publish status`). Resolve it: restore the remote or the branch; close a stray PR; inspect the merge. Then run `github publish reset FEATURE --apply`. A PR closed without merge is not a stop: reopen and merge it, and the next pass continues. A PR whose head a person moved: `github publish revalidate FEATURE`, then with `--head <full 40-character SHA> --apply` ([github-intake.md §10.12](github-intake.md#1012-revalidating-a-pr-whose-head-a-person-changed)) |
| Publication was interrupted (crash, network, lost GitHub response) | Run `github publish poll --apply` again (or let the service do it). The remote branch and the PRs are read before anything is pushed or created, so nothing is duplicated ([github-intake §10.5](github-intake.md#105-recovery-after-interruptions)) |

## 8. GitHub Projects board (Phase 4 extension)

Design, status mapping and evidence: [github-projects.md](github-projects.md). The board is configured in `github.project` (Project #2 for mx-admin-cloud; Project #1 is protected and never written). It is a picture of the local state, never an input.

| Command | What it does | Writes |
| --- | --- | --- |
| `python -m feature_coordinator github project inspect [--json]` | The identity proof (owner, number, node ID; not protected; updatable), token scopes (from the response header), fields and options, views and their column field, built-in workflows (exit 1 if the identity is not proven or the `project` scope is missing) | none |
| `python -m feature_coordinator github project validate [--json]` | Required Status options, Agent / Feature ID / Blocked Reason fields and the Kanban / Active Tasks views present? Lists what `setup` would change (exit 1 if not READY) | none |
| `python -m feature_coordinator github project setup [--apply]` | Creates or reuses the options, fields and views; renames GitHub's default `Todo` to `Backlog` keeping its option ID; never deletes or retypes anything | project configuration |
| `python -m feature_coordinator github project associate FEATURE [--apply] [--actor NAME]` | Authorizes an **existing** feature for the board (recorded locally) and adds its Issues' items. Features planned while the board is enabled are authorized by `plan` | items of that feature |
| `python -m feature_coordinator github project sync FEATURE [--apply] [--json]` | Local state → the feature's items (add or adopt; Status, Agent, Feature ID, Blocked Reason; read-back) | items of that feature |
| `python -m feature_coordinator github project reconcile [FEATURE…] [--apply] [--json]` | After a restart: sync each authorized feature, then classify every item of the board as mapped, foreign or orphan, and report duplicates; foreign and orphan items are never modified | as `sync` |
| `python -m feature_coordinator github project active [--feature F] [--all] [--json]` | Active tasks (ready, in progress, blocked, held, failed): the local state, the derived board values and what the board shows | none |
| `python -m feature_coordinator github project task RECORD [--json]` | One task or feature: local state, derived values, board values, in sync? (exit 1 if not) | none |
| `python -m feature_coordinator github project test [--apply] [--workdir DIR] [--no-cleanup]` | The live Projects test: **two** test Issues added to the board, a disposable repository, no PR ([github-projects §11](github-projects.md#11-live-integration-test)) | 2 new test Issues and their items |

Operating rules:

- Every feature operation that syncs Issues (`plan`, `activate`, `launch`, `integrate`, `hold`, `release`, …) also syncs the board, outside every coordinator lock. A failure is recorded as a `tracker-sync-deferred` event with `board: true`; `github sync FEATURE --apply` or `github project sync FEATURE --apply` completes it.
- `launch` shows **In Progress** while the agent runs and **Review** while its commit is verified, through a separate `python -m feature_coordinator github sync` process that holds none of the worker's locks (output in `<state>/logs/publish-<feature>.log`).
- Moving a card or editing a field on the board never starts, releases, verifies or approves anything; the next sync restores the derived value and reports drift. Change task state with `python -m feature_coordinator`.
- The write scope applies: in Phase 4 only `test: true` features are written to the real board.
- The project workflows **Auto-close issue** and **Item closed** are switched off in Project #2 (D-28, verified 2026-10-17; [github-projects §9](github-projects.md#9-githubs-built-in-project-workflows)). `github project inspect` lists the workflows; if either is switched on again, the coordinator repairs its effect but reports drift.
- The **Active Tasks** view shows open Issues in Ready, In Progress, Blocked or Review (`is:open`, D-29). Finished test features stay on the Kanban view as closed audit records.

## 9. Supervised GitHub intake → Worker → Draft PR → human merge → Done (verified 2026-10-24)

The commands below reflect the successful live tests of Issues #25 and #29. They operate on the **same local Coordinator state directory** (the default, unless `--state-dir` was explicitly supplied earlier), and a **local** config outside the repository. Do not put access tokens or machine-specific settings in the repository.

Preconditions: current `dev` fetched with `git pull --ff-only origin dev`, clean checkout, no running Worker or unattended service, GitHub Project #2 correctly configured, and `github.intake.enabled: true`, `github.publish.enabled: true`, `worker.autonomous.enabled: true` in `~/.config/mx-admin-agents/project.yaml`. The default config intentionally disables these capabilities. The W4.3.4 test was run under the user's approval and the shared `spyderbg` identity risk acceptance (§9 of [github-intake.md](github-intake.md)).

```bash
# Run from the mx-admin-cloud checkout. Inspection only:
git status --short --branch
python -m feature_coordinator --tracker github --project-config ~/.config/mx-admin-agents/project.yaml worker status
python -m feature_coordinator --tracker github --project-config ~/.config/mx-admin-agents/project.yaml worker attention

# After creating and reviewing a new Issue N, print its digest (read-only):
python -m feature_coordinator --tracker github --project-config ~/.config/mx-admin-agents/project.yaml github intake show N
# Publish exactly the printed /worker approve <role> <64-hex-digest> line as a GitHub Issue comment.

# Dry-run (read-only), then intake apply (writes, operator authorization):
python -m feature_coordinator --tracker github --project-config ~/.config/mx-admin-agents/project.yaml github intake poll
python -m feature_coordinator --tracker github --project-config ~/.config/mx-admin-agents/project.yaml github intake poll --apply

# Verify clean feature branch and run foreground Worker until Draft PR is opened:
git status --short --branch
python -m feature_coordinator --tracker github --project-config ~/.config/mx-admin-agents/project.yaml worker start
# In another terminal, watch `worker status` / `github publish status`.
# When the service reports WAITING for the Draft PR and no agent is running:
python -m feature_coordinator --tracker github --project-config ~/.config/mx-admin-agents/project.yaml worker stop

# A PERSON reviews the Draft PR and explicitly marks it ready and merges with a merge commit.
# Then, with the Worker stopped, dry-run and apply the verified completion for issue-N:
python -m feature_coordinator --tracker github --project-config ~/.config/mx-admin-agents/project.yaml github publish poll issue-N
python -m feature_coordinator --tracker github --project-config ~/.config/mx-admin-agents/project.yaml github publish poll issue-N --apply
# Update local dev only AFTER completion and once the feature branch is no longer needed:
git switch dev
git pull --ff-only origin dev
```

**Safety and interpretation.** `github intake poll` and `github publish poll` without `--apply` are read-only; the Worker service and `--apply` perform real writes. Run `worker stop` only after confirming no active agent (or use `--after-current` to let an active task finish). Do **not** manually push a feature branch or create a PR during the automatic Draft PR test; that would test adoption instead. One approved Issue creates one feature and one Draft PR, never auto-merges. PR merge commits are required by the existing merge-evidence verification (squash/rebase are not supported). With the Worker stopped, manual `publish poll issue-N --apply` completes the Issue and Project #2 after human merge. Always update local `dev` before activating the **next** feature: the Coordinator fetches remote `dev` for verification but does not move the local branch. Keep Project #1 untouched.

**2026-10-24 acceptance:** Issue #25 / manually merged PR #27 proved post-hoc adoption/closure; Issue #29 / automatically created Draft PR #30 proved the real auto-publication path and subsequent verified closure. Worker was stopped normally after Draft PR #30. These do not establish unattended reliability. W4.4 (2026-10-29) subsequently passed isolated fault regressions, a fake-agent soak and a bounded real-agent smoke **without real GitHub calls**; see [W4.4 acceptance](w44-reliability-acceptance.md).


## 10. W4.4 closeout and safe reproduction (2026-10-29)

**Acceptance:** 26/26 selected existing pytest regressions passed (worker crash 4, simulated Git/GitHub outage 4, validation/retry 7, publisher interruption 11). A 48-task, 60-run, 513-second fake-agent soak passed 10/10 harness invariants; a bounded four-session real Claude Code/Codex recovery smoke passed 69/69 checks. These results are recorded in [W4.4 acceptance](w44-reliability-acceptance.md); they are **not** a 24/7 reliability guarantee.

### Read-only revalidation and scoped tests

From an up-to-date documentation checkout, with `.venv` active:

```bash
# Inspect ONLY; never start the main Worker to validate the closeout:
git status --short --branch
python -m feature_coordinator --tracker github --project-config ~/.config/mx-admin-agents/project.yaml worker status
python -m feature_coordinator --tracker github --project-config ~/.config/mx-admin-agents/project.yaml worker attention
python scripts/check_docs_agents.py

# Existing tests use disposable Git repos, fake agents / mock GitHub:
python -m pytest -p no:cacheprovider -v \\
  feature_coordinator/tests/test_fc_service_process.py::test_c_restart_continues_from_the_last_checkpoint \\
  feature_coordinator/tests/test_fc_service_process.py::test_q_crash_during_bookkeeping
python -m pytest -p no:cacheprovider -v \\
  feature_coordinator/tests/test_fc_publish.py::test_every_interruption_is_recovered_without_duplicates
```

For the complete W4.4.1–W4.4.4 list of 26 selected cases, see [the acceptance record](w44-reliability-acceptance.md) and the operator's execution logs. The optional soak tool `feature_coordinator/tests/fc_service_soak.py` creates its own repository, state and fake HOME in a **new, empty** `--workdir`; it intentionally runs a *real service process* but **not** a real AI CLI. Its 2026-10-29 acceptance used `--tasks 48 --failure-every 4 --idle-seconds 180`. Do not reuse a previous workdir.

The real-agent harness `feature_coordinator/tests/fc_worker_smoke.py --agent real` is **plan only**. `--agent real --apply` starts live subscription-backed AI sessions: **separate explicit operator approval is required for each such run**. It uses a disposable repository with **no Git remote** and does not exercise GitHub writes. Never use production Coordinator state for fault injection; never kill the main Worker as a substitute for the disposable test harness.

### Hand-off for proposed W4.5 (not authorized)

W4.4 is closed as **supervised bounded acceptance**. Do **not** install/enable systemd, switch on unattended scheduling, create test Issues, inject real network faults, or alter protected branches/Project #1 as part of this closeout. Proposed future acceptance adds: host-reboot/systemd recovery, a monitored multi-day run, measured resource and retry budgets, explicitly approved disposable live GitHub write-failure scenarios, and review of the shared-identity/W1 security exposure. Every new live or destructive step requires a separately reviewed rollback and authorization.

## 11. Test environment relocation: updating the Desktop Worker (2026-10-30)

The test environment moved from `scripts/` into `test_environment/`. The Worker's protected and approval-required paths moved with it. A machine-local project file written before the move still lists `scripts/mxfix/**`, `scripts/mxissues/**` and `testenv.toml`. Those entries protect nothing any more, and the automation role could write the moved files. From this change on, the Worker **refuses such a file**: every `python -m feature_coordinator` command exits with `error: pre-relocation test environment path(s) …`, and `worker start` exits **78**, which the systemd unit does not restart. `scripts/migrate_project_config.py` rewrites only the affected entries, keeps every other line, comment and setting, and backs the file up first.

Each machine migrates its own project files **when it is upgraded** to a `dev` that contains this change. Nothing has to be migrated before the pull request is merged, and a machine still on older code keeps working with its current file. Upgrade the Desktop with no feature in progress and the service idle (§9 preconditions). The unit file does not change, so it is not rendered or installed again. The installed Desktop unit passes `--state-dir`, `--project-config` and `--tracker github`: every Worker command below uses exactly the global options shown in its `ExecStart`.

```bash
# 0. Read-only: copy the runtime arguments from the installed unit. Its ExecStart reads
#    <PY> -m feature_coordinator --repo <REPO> --state-dir <STATE> --project-config <CFG> --tracker github worker start
#    (a unit rendered before the feature_coordinator relocation has <PY> <REPO>/scripts/feature.py instead: see §12)
systemctl --user cat mx-admin-worker | grep -E '^(WorkingDirectory|ExecStart)='
PY=/interpreter/from/ExecStart        # its first word, e.g. <REPO>/.venv/bin/python
REPO=/path/after/--repo               # the Worker's checkout (also WorkingDirectory)
STATE=/path/after/--state-dir
CFG=/path/after/--project-config      # e.g. ~/.config/mx-admin-agents/project.yaml
WORKER=("$PY" -m feature_coordinator --repo "$REPO" --state-dir "$STATE" --project-config "$CFG" --tracker github)
cd "$REPO"

# 1. Check that the service is idle, then stop it BEFORE the checkout changes: a running Worker keeps the
#    old policy in memory.
"${WORKER[@]}" worker status                  # no active agent or running task
"${WORKER[@]}" worker attention
systemctl --user stop mx-admin-worker
systemctl --user is-active mx-admin-worker    # expect "inactive"

# 2. Update the checkout.
git status --short --branch                   # must be clean
git switch dev
git pull --ff-only origin dev

# 3. Expected: the old file is now refused (error: pre-relocation test environment path(s) …, exit 1).
"${WORKER[@]}" worker status

# 4. Find every machine-local file that needs it (read-only; 1 = needs migration, 0 = fine, 2 = look at it).
for f in ~/.config/mx-admin-agents/*.yaml; do "$PY" scripts/migrate_project_config.py "$f" >/dev/null; echo "$? $f"; done

# 5. Per file: dry run (shows the diff, exit 1), apply (prints the backup path), run again (nothing to change, exit 0).
"$PY" scripts/migrate_project_config.py "$CFG"
"$PY" scripts/migrate_project_config.py --apply "$CFG"
"$PY" scripts/migrate_project_config.py "$CFG"

# 6. Validate and inspect (read-only). "DIFFERENT from the built-in configuration" is normal for a local file.
"$PY" -m feature_coordinator config --config "$CFG"
"${WORKER[@]}" worker status
"${WORKER[@]}" worker attention

# 7. Start the service again (operator decision).
systemctl --user reset-failed mx-admin-worker
systemctl --user start mx-admin-worker
systemctl --user is-active mx-admin-worker
journalctl --user -u mx-admin-worker --since "10 minutes ago"
```

- **What changes in the file:** in `protected_paths`, `scripts/mxfix/**` becomes `test_environment/mxfix/**` and `scripts/mxissues/**` becomes `test_environment/mxissues/**`, and `test_environment/testenv.local.toml` is added; in `approval_required_paths`, `testenv.toml` becomes `test_environment/testenv.toml`; in the automation role's `excluded_paths`, the same moves apply and both toml files are added. The backup is `$CFG.bak-<UTC timestamp>`, with the file's mode.
- **If the script exits 2:** it wrote nothing. Make the same edits by hand, compare with [`project-config.example.yaml`](project-config.example.yaml), then check with `python -m feature_coordinator config --config "$CFG"`.
- **If the service was started too early:** it exits 78 and stays failed; the journal names the stale entries. Migrate the file, then run step 7.
- **Rollback:** the old file only works with the old code. Restore both together: `cp -p "$CFG.bak-<stamp>" "$CFG"`, and check out the commit before the relocation (`git switch --detach 2cf629a`).

## 12. Feature coordinator relocation: updating a Worker host (2026-10-30)

The coordinator moved from `scripts/feature_coordinator/` to the root-level `feature_coordinator/`, its tests to `feature_coordinator/tests/`, and `scripts/feature.py` is gone. The CLI is now `python -m feature_coordinator`, run **from the checkout root** (or with the root on `PYTHONPATH`). Every option and subcommand is unchanged, and so are state formats, configuration, GitHub behavior and recovery. Two things on a Worker host change with it:

- **The installed systemd unit** runs `<PY> <REPO>/scripts/feature.py …`, which no longer exists. After the update it fails at once: exit 2, up to 5 restarts in 15 minutes, then `failed`. No agent runs. Render and install the unit again (step 5).
- **The `coordinator-tests` gate** in a machine-local project file still names `scripts/tests` only. It keeps passing but no longer runs the coordinator's own tests. Add `feature_coordinator/tests` (step 4).

Shell aliases or scripts that call `scripts/feature.py` need the same change. Each machine does this when it is upgraded to a `dev` that contains the change. Nothing has to happen before the pull request is merged.

```bash
# 0. Read-only: the installed unit's runtime arguments. Its ExecStart (before this change) reads
#    <PY> <REPO>/scripts/feature.py --repo <REPO> --state-dir <STATE> --project-config <CFG> --tracker github worker start
systemctl --user cat mx-admin-worker | grep -E '^(WorkingDirectory|ExecStart)='
PY=/interpreter/from/ExecStart; REPO=/path/after/--repo; STATE=/path/after/--state-dir; CFG=/path/after/--project-config
cd "$REPO"

# 1. With the OLD code still checked out: check that the service is idle, then stop it.
"$PY" scripts/feature.py --repo "$REPO" --state-dir "$STATE" --project-config "$CFG" --tracker github worker status
"$PY" scripts/feature.py --repo "$REPO" --state-dir "$STATE" --project-config "$CFG" --tracker github worker attention
systemctl --user stop mx-admin-worker
systemctl --user is-active mx-admin-worker    # expect "inactive"

# 2. Update the checkout.
git status --short --branch                   # must be clean
git switch dev
git pull --ff-only origin dev

# 3. From now on, every Worker command uses the module form with the unit's exact global options.
WORKER=("$PY" -m feature_coordinator --repo "$REPO" --state-dir "$STATE" --project-config "$CFG" --tracker github)
"${WORKER[@]}" worker status

# 4. The coordinator-tests gate (back the file up first; check the result).
cp -p "$CFG" "$CFG.bak-$(date -u +%Y%m%dT%H%M%SZ)"
sed -i 's#"no:cacheprovider", scripts/tests\]#"no:cacheprovider", scripts/tests, feature_coordinator/tests]#' "$CFG"
grep -n 'coordinator-tests' "$CFG"            # [python, -m, pytest, -q, -p, "no:cacheprovider", scripts/tests, feature_coordinator/tests]
"$PY" -m feature_coordinator config --config "$CFG"

# 5. Render the unit again in a shell whose PATH finds claude, codex and gh (worker-autonomy.md §10), verify, install.
"${WORKER[@]}" worker systemd-unit > /tmp/mx-admin-worker.service
grep -E '^(WorkingDirectory|ExecStart)=' /tmp/mx-admin-worker.service   # ExecStart=<PY> -m feature_coordinator --repo <REPO> …
systemd-analyze --user verify /tmp/mx-admin-worker.service
cp /tmp/mx-admin-worker.service ~/.config/systemd/user/mx-admin-worker.service
systemctl --user daemon-reload

# 6. Start the service again (operator decision).
systemctl --user reset-failed mx-admin-worker
systemctl --user start mx-admin-worker
systemctl --user is-active mx-admin-worker
journalctl --user -u mx-admin-worker --since "10 minutes ago"
```

- **A host that also crosses the test_environment relocation (§11)** stops the service with step 1 here, runs §11 from step 2 with the `WORKER` command from step 3 here, then does steps 4–6 here.
- **`python -m feature_coordinator` needs the checkout root as its working directory.** The unit sets `WorkingDirectory=<REPO>`. The coordinator's own child processes (`launch`, the Draft-PR publisher) start there too, whatever the caller's directory.
- **Rollback:** check out the commit before the relocation, put back the previous unit file and the backed-up project file, then run `systemctl --user daemon-reload`.
