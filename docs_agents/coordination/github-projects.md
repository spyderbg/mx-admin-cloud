# GitHub Projects Board — Kanban Visualization of Coordinator Tasks (Phase 4 extension)

> Owner role: **coordination**. Status: **Phase 4 extension deliverable, approved** (2026-10-17); finalized the same day with D-28 (Project #2 workflows) and D-29 (Active Tasks shows open Issues only).
> Issues and their synchronization: [github-integration.md](github-integration.md) · Lifecycle and dependencies: [task-lifecycle.md](task-lifecycle.md) · Commands: [operations §8](operations.md#8-github-projects-board-phase-4-extension) · Configuration: [project-config.example.yaml](project-config.example.yaml) (`github.project`) · Status and live evidence: [phase-status.md](phase-status.md)
>
> Evidence tags: **[V]** verified by execution (the named test or command), **[S]** source-read, **[U]** unverified.

The board is a **picture** of the coordinator's work. GitHub Issues remain the tasks' public record ([github-integration.md](github-integration.md)), and the local coordinator state (`~/.local/state/mx-admin-agents/`, D-12) remains the only authority for task states, claims, locks, workers and approvals. The board never claims, locks, starts, verifies or approves anything. A card moved by a person is reported as drift and restored on the next sync.

## 1. The configured project

| Item | Value |
| --- | --- |
| Owner | `spyderbg` (user) |
| Repository of the Issues | `spyderbg/mx-admin-cloud` |
| Project | **#2 "mx-admin-cloud automation"**, <https://github.com/users/spyderbg/projects/2> |
| GraphQL node ID | `PVT_kwHOACEIPc4BndH-` |
| Protected project | **#1 "mx-admin-cloud issues"** (`PVT_kwHOACEIPc4BkoUP`), owned by the independent `mxissues` bug workflow. Never written |
| Configuration | `github.project` in [project-config.example.yaml](project-config.example.yaml); the built-in `default_config()` holds the same values (`config.MX_ADMIN_CLOUD_PROJECT`). The generic default (`GitHubProjectConfig()`) is **disabled** and names no project |

A project is accepted only when its owner, its number **and** its node ID match the configuration, it is not one of `protected_numbers`, it is open, and the `gh` account can update it (`ProjectSync.resolve`). The title is informational: a renamed project is reported, not refused.

### 1.1 What `setup` configured on Project #2 (2026-10-17) [V]

Before: the default fields only. Status had the options `Todo` / `In Progress` / `Done`, there were no items, and the two views were "View 1" (a table) and "View 2" (a board grouped by Status). `feature.py github project setup --apply` changed:

| Change | Detail |
| --- | --- |
| Status options (reused) | `Todo` **renamed** to `Backlog`, keeping option ID `f75ad846`, so items and the "Item added to project" workflow that point at it keep working. `In Progress` (`47fc9ee4`) and `Done` (`98236657`) were reused unchanged, colors and descriptions included |
| Status options (added) | `Ready` (`b62cdbed`), `Blocked` (`dc3fb247`), `Review` (`ec596878`) |
| Board order | Backlog, Ready, In Progress, Blocked, Review, Done |
| Fields created | `Agent` (single select: Coordination, Backend, CLI, Frontend, Automation, Full Project), `Feature ID` (text), `Blocked Reason` (text) |
| Views | "View 2" (already a board grouped by Status, default name) **renamed** to `Kanban`; new board view `Active Tasks` with the filter `status:Ready,"In Progress",Blocked,Review` (since D-29: `is:open status:Ready,"In Progress",Blocked,Review`, §8). Both show Agent, Feature ID and Blocked Reason on the cards. "View 1" is unchanged |
| Not changed | No field, option or view was deleted. Project #1 was compared byte for byte with the snapshot taken at the start of the session: identical |

A second `setup` changes nothing (`validate` reports **READY**). `setup` never deletes a field, an option or a view. It renames an option only through `status_reuse` (keeping its ID). It keeps unknown options, and lists them with their item counts. A field of the wrong type is reported, never replaced. Rules and tests: `test_setup_discovers_and_reuses_fields_options_and_views` and `test_setup_adds_missing_options_and_never_changes_a_conflicting_field` **[V]**.

## 2. Authentication and scopes

| Need | Scope | Check |
| --- | --- | --- |
| Read and write Projects (v2) | `project` | `feature.py github status` (row "token scopes (header)") and `feature.py github project inspect` read GitHub's `X-OAuth-Scopes` **response header**. The token itself is never read, printed or stored |
| Issues, labels, comments | `repo` | as in Phase 4 |

Observed 2026-10-17 **[V]**: `gist, project, read:org, repo, workflow`. If `project` is missing, every board operation stops with `GitHubPermissionError` ("… the operator must run `gh auth refresh -s project`"); there is no fallback. Changing the login is an operator action only.

## 3. Status mapping (from the existing state machines)

| Local record and state | Status | Blocked Reason |
| --- | --- | --- |
| task `proposed` (planned, feature not activated) | **Backlog** | — |
| task `ready` | **Ready** | — |
| task `in-progress` (claimed, agent executing) | **In Progress** | — |
| task `in-progress`, the run finished and its commit landed, gates running (the live claim's checkpoint is `verifying`) | **Review** | — |
| task `blocked` | **Blocked** | `waiting for <task> (<state>), …`: only the dependencies that are still unverified |
| task `needs-human` | **Blocked** | `held for a human decision by <actor>: <reason>` (or `needs a human: <reason>`) |
| task `failed` | **Blocked** | `failed: <reason>` (e.g. `failed: gates failed: [...]`) |
| task `verified` **with valid evidence** | **Done** | — |
| task `verified` without valid evidence | **Review** (and a report warning) | — |
| feature `planned` | Backlog | — |
| feature `active` / `integration` | In Progress | — |
| feature `review` (the single PR candidate is rendered) | Review | — |
| feature `done` **with merge evidence** (`merged_sha`) | Done | — |
| feature `done` without merge evidence | Review (and a warning) | — |
| feature `blocked` / `failed` | Blocked | the last recorded reason |

- **Valid evidence for Done:** every required gate passed in the task's evidence, the evidence is bound to a commit SHA, and that commit is on the feature branch (`git merge-base --is-ancestor`). `ProjectSync.evidence_problem`; `test_task_status_mapping_follows_the_state_machine` **[V]**.
- **The parent is never Done because its tasks are verified.** It is In Progress until integration, Review with the single PR candidate (and with an opened PR), and Done only after `complete(merged_sha)`. `test_the_parent_is_never_done_because_its_tasks_are_verified` and `test_feature_status_is_done_only_with_merge_evidence` (8 cases) **[V]**.
- **Re-blocked and invalidated tasks follow the local state.** A re-opened provider goes Done → Ready; its dependents go to Blocked with "waiting for …" (`test_dependency_invalidation_reblocks_dependents_on_the_board` **[V]**; live B10 **[V]**).
- **Review for tasks** exists only while a worker verifies its commit, because the state machine verifies inside the same claimed run. The worker never talks to GitHub while it holds its role slot (§5), so a separate process publishes it (live B9 **[V]**).

## 4. Fields

| Field | Value | Notes |
| --- | --- | --- |
| Status | §3 | single select; option names from `status_names` |
| Agent | the task's role: backend → Backend, cli → CLI, frontend → Frontend, automation → Automation, coordination → Coordination; the parent feature → **Coordination** | `Full Project` exists for full-project work; no current task type uses it |
| Feature ID | the feature ID (parent and tasks) | text |
| Blocked Reason | set while Status is Blocked, **cleared** otherwise | text; sanitized (control characters removed, home directory as `~`, at most 200 characters) |

The coordinator writes only these four fields on its own items. It never touches Title, Assignees, Labels, Milestone or any other field, and it never writes items it does not own.

## 5. Synchronization

```text
FeatureCoordinator (workflow.py) — local state change in StateStore.transaction()   (authoritative)
   │ after the transaction, outside every coordinator / project / role / Git lock:
   ├─► IssueSync.sync      (Phase 4: Issues, labels, comments, validated commands)
   └─► ProjectSync.sync    (this page: items and their four fields)   — holds only github-sync.lock
          │  resolve + bind the project (identity proof) → layout (fields/options) →
          │  per record: Issue ownership proof → item (recorded | found | added) → values → read-back
          ▼
       BoardClient: ProjectsClient (GraphQL over the same GitHubClient: retries, pacing, rate limits)
                    LocalProjectBoard (tests, rehearsal, --tracker local)
```

| When | What updates the board |
| --- | --- |
| `plan` | The feature is **authorized** for the board, its Issues are created, and their items are added automatically (Backlog) |
| `activate`, `request_contract`, `probe_dependency`, `verify_task`, `reopen_task`, `integrate`, `hold`, `release`, `complete` | the sync after the operation |
| `run_task` claimed the task | `DetachedPublisher` starts `feature.py github sync FEATURE --apply` as a **separate process**: no inherited file descriptors (so none of the worker's flocks), its own session, never waited for. The board shows **In Progress** while the agent runs |
| the run's commit landed, gates start | the claim's checkpoint becomes `verifying`, and a second detached sync shows **Review** |
| the run finished and released its slot | the regular sync: Done, Blocked (failed) or Ready |
| `feature.py github sync FEATURE --apply` | Issues, then the board (`--no-board` skips it) |
| `feature.py github project sync/associate/reconcile … --apply` | the board only |

**Item management** (`ProjectSync._ensure_item`):

1. The recorded item is read and verified: it is in the configured project, and its content is exactly the record's Issue (repository, number, database ID, node ID).
2. Without a recorded item, the Issue's own project items are looked up (`Issue.projectItems`), and an existing item on this board is **adopted**.
3. Only then is `pending_add` persisted, `addProjectV2ItemById` sent, and the item ID recorded. GitHub returns the existing item when an Issue is added twice.
4. After an add, the sync waits `item_settle_seconds` (2 s) so GitHub's "Item added to project" workflow can run first. After writing values, the item is **read back**; a value a workflow changed meanwhile is re-applied (up to `max_field_attempts`, then the sync is deferred).

Records: `<state>/project/<record-id>.json` (`ProjectLink`): authorization (feature records), project and item ID, Issue number and database ID, `pending_add`, `fields_written` (for drift detection), last sync time. Repeated synchronization sends no write (`test_plan_adds_new_feature_issues_automatically_with_derived_values` **[V]**; live B11 **[V]**).

### 5.1 Authorization and ownership

- **Which features appear.** A feature planned while the board is enabled is authorized by `plan`. A feature planned earlier — for example the Phase 4 test feature of Issues #11–#14 — is **not** associated automatically. It needs `feature.py github project associate FEATURE --apply` (an operator decision, recorded as a `github-project-authorized` event). Tests: `test_existing_features_are_associated_only_when_authorized` **[V]**; live B2/B3 **[V]**.
- **Which records.** Only records whose Issue passes the Phase 4 ownership proof: the marker, the coordinator account as author, and the recorded number and database ID. A record that fails is reported as **protected** and its item is untouched.
- **Which items.** Only the item recorded for (or found on) the owned Issue. A recorded item that now shows another Issue is refused. Items a person added, drafts and other Issues' items are reported by `reconcile` as foreign, or as orphans (an owned Issue without a local record), and never modified. Test: `test_items_that_are_not_provably_ours_are_never_modified` **[V]**.
- **Write scope.** The Phase 4 `github.write_scope` applies: against the real board, only `test: true` features are written (`test_write_scope_limits_live_boards_to_test_features` **[V]**).

### 5.2 Nothing on the board is input

The coordinator reads the board only to compare it with what it last wrote. A moved card, an edited field, a removed item or an archived item never changes a task state, never creates a claim or starts a worker, and never verifies, releases or approves anything. Claims check the local state (`ClaimManager.claim`), and the scheduler and `launch` read only the local state.

Tests: `test_a_card_moved_on_the_board_never_releases_claims_or_completes` **[V]**. Live B6 and B7 **[V]**: a card moved to In Progress or Done started and completed nothing, and a held task moved to Ready could not be claimed. In each case the next sync reported the drift and restored the derived value. Because coordinator and operator share one account (D-26), field edits on the board are not an authorization channel either. Sensitive approvals stay with `feature.py` run by the operator.

## 6. Protection of Project #1

Three independent layers, all tested:

1. **Configuration.** `number` may not be in `protected_numbers`, and the project must match owner, number and node ID (`test_project_configuration_is_validated` **[V]**).
2. **Write guard** (`BoardGuard`, in both board clients). Before `bind()` nothing is written. After it, a mutation is refused **before any request** unless its project ID is the bound project, its field or view ID was read from that project, and its item ID was seen in that project. Protected node IDs are refused even if passed in. Tests: `test_the_guard_refuses_other_and_protected_projects_and_foreign_nodes` and `test_items_read_from_a_protected_project_are_never_writable` **[V]**.
3. **Tripwire.** The local board raises on any mutation that reaches a protected project. `test_protected_projects_are_never_bound_or_written` runs the whole lifecycle and compares Project #1 before and after **[V]**.

Live **[V]**: Project #1 (fields, options, views, workflows, items with their values, `updatedAt`) was identical before and after `setup` and before and after the live test (B14).

## 7. Recovery and reconciliation

| Failure point | Persisted first | Next sync | Test |
| --- | --- | --- | --- |
| Crash after GitHub added an item, before its ID was recorded | `ProjectLink.pending_add` | The Issue's items are looked up → adopted; never added twice | `test_a_crash_after_adding_an_item_never_duplicates_it` **[V]**; live B4/B5 **[V]** |
| Lost response of an add (timeout or 5xx after sending) | same | `GitHubUncertainWriteError` defers the sync; the next sync finds the item | `test_a_lost_add_response_is_recovered_without_a_duplicate`, `test_an_uncertain_add_is_never_blindly_resent` **[V]** |
| A field update failed (5xx after retries, network) | — | Deferred (`tracker-sync-deferred` event with `board: true`); local state untouched; the next sync completes | `test_network_failures_never_corrupt_local_state_and_recover` **[V]** |
| A GitHub workflow overwrote a value right after the write | — | Read-back → re-applied; if it keeps changing → deferred | `test_a_project_workflow_overwrite_is_detected_and_reapplied` **[V]** |
| Coordinator restarted | links on disk | `feature.py github project reconcile --apply` in a new process: same item IDs, no write when nothing changed; foreign items and orphans reported only | `test_a_restarted_coordinator_reconciles_without_writes` **[V]**; live B13 **[V]** |
| Two syncs at once | — | `github-sync.lock` serializes them | `test_concurrent_board_syncs_never_create_duplicate_items` (3 processes) **[V]** |
| An item was removed from the board | — | Reported as drift, re-added (idempotent) | `_ensure_item` **[S]** |
| GraphQL rate limit / secondary limit | — | `RATE_LIMITED` (HTTP 200) or 403/429: waits within `max_rate_limit_wait_seconds`, else deferred | `test_graphql_rate_limit_waits_then_retries_and_is_bounded` **[V]** |
| Missing `project` scope | — | `GitHubPermissionError` naming `gh auth refresh -s project` | `test_missing_project_scope_names_the_operator_action` **[V]** |

No coordinator, project, role or Git lock is held during a board request (`test_no_coordinator_lock_is_held_during_board_requests`: every request tries to take those locks **[V]**).

## 8. Views

| View | Layout | Content |
| --- | --- | --- |
| **Kanban** (#2) | Board, columns = Status | every item; cards show Agent, Feature ID, Blocked Reason |
| **Active Tasks** (#3) | Board, columns = Status | filter `is:open status:Ready,"In Progress",Blocked,Review`: open Issues in an active status (`project_sync.active_filter`) |
| View 1 (#1) | Table | unchanged (the operator's) |

The API can create views and set their name, layout, filter and visible fields. It cannot choose the board's column field. New boards use Status, which GitHub confirmed for both views (`verticalGroupByFields` = Status) **[V]**. Whether a filtered board also hides the empty Backlog and Done columns is a display detail the API does not report **[U]**. If they show, they can be hidden in the view's menu in the UI.

**Open Issues only (D-29, 2026-10-17).** The first filter used Status alone, so the closed test parent #15 (left in Review, because its feature was never merged) stayed on Active Tasks. A closed Issue is never active work: the Issue sync closes a feature's Issues only when the feature is done or a test cleanup finished it, and it reopens an Issue a person closed. `setup` therefore sets `is:open` in front of the Status filter, and `validate` reports NOT READY until it is there (`test_setup_limits_the_active_tasks_view_to_open_issues` **[V]**). Applied to Project #2 at 10:52:55 UTC with `setup --apply` (one change, nothing else touched). GitHub evaluates a view's filter server-side through `ProjectV2.items(query:)`; read back afterwards **[V]**: Active Tasks → **0** items; Kanban (no filter) → 2 items (#15 closed / Review, #16 closed / Done). The test Issues stay on the board and in GitHub for audit; they are not archived or deleted.

## 9. GitHub's built-in project workflows

The API can list these workflows but cannot switch them off (only delete them, which the coordinator never does). **D-28 (approved 2026-10-17): the board must not close Issues or overwrite coordinator-managed states.** The operator switched off **Auto-close issue** and **Item closed** in the UI; the other four remain enabled, and the coordinator corrects what they change:

| Workflow | Effect on coordinator items | Coordinator behavior |
| --- | --- | --- |
| Item added to project | sets Status (the reused `Backlog` option) on add | settle + read-back; re-applied if different |
| Item closed — **disabled (D-28)** | set Done when an Issue was closed | restored on the next sync. Test cleanup restored the parent to Review |
| Auto-close issue — **disabled (D-28)** | closed the Issue when Status became Done | conflicted with the Phase 4 rule "an Issue stays open until its feature is done": the Issue sync reopened it and commented (drift). Observed in the live test (§11) |
| Pull request linked to issue / Pull request merged | change Status for PR activity (Phase 5) | restored on the next sync |
| Auto-add sub-issues to project | adds sub-issues | none created by the coordinator |

**D-28 verification (2026-10-17, 10:54–10:56 UTC) [V]:**

- **Configuration.** `feature.py github project inspect` (read-only) reports `Auto-close issue: disabled` and `Item closed: disabled`.
- **Behavior.** A check through the coordinator's guarded clients (identity proven, write guard bound to Project #2, ownership of both test Issues verified):
  - **Auto-close issue:** test Issue #16 was reopened and its card moved Review → Done. It **stayed open** for 45 s, then was closed again as completed.
  - **Item closed:** test Issue #15 was reopened and closed again. Its card **stayed Review** for 45 s. In the live test, the same close had set it to Done.
- **End state:** both Issues ended exactly as they started (closed as completed; #15 Review, #16 Done). Six writes in total; Project #1 was unchanged.

Before Phase 5, consider also switching off the two pull-request workflows. They would move the parent's card when the single PR is linked or merged; the coordinator restores the derived value on the next sync, so correctness does not depend on it. `inspect` and `validate` list the workflows that are still enabled. If "Auto-close issue" or "Item closed" is ever switched on again, the coordinator still repairs the effect (the Issue sync reopens, the board sync restores the value), at the cost of comments and drift reports.

## 10. Operator procedures

```bash
python scripts/feature.py github status                       # gh, login, repository, scopes (incl. project)
python scripts/feature.py github project inspect              # identity proof, protected projects, fields, views, workflows
python scripts/feature.py github project validate             # READY? what would setup change? (exit 1 if not ready)
python scripts/feature.py github project setup [--apply]      # create/reuse options, fields, views (never deletes)
python scripts/feature.py github project associate F [--apply]   # show an existing feature on the board
python scripts/feature.py github project sync F [--apply]     # converge one feature's items
python scripts/feature.py github project reconcile [F…] [--apply]  # after a restart; foreign/orphan report
python scripts/feature.py github project active [--feature F] # active tasks: local state vs board
python scripts/feature.py github project task TASK_OR_FEATURE # one record: derived vs board values
python scripts/feature.py github project test [--apply]       # the live Projects test (2 test Issues)
python scripts/feature.py --tracker local github project …    # the same against a local JSON board
```

Which items a view shows, evaluated by GitHub (read-only; replace the filter with a view's `filter` from `inspect`):

```bash
gh api graphql -F id=PVT_kwHOACEIPc4BndH- -f q='is:open status:Ready,"In Progress",Blocked,Review' -f query='
  query($id:ID!,$q:String!){ node(id:$id){ ... on ProjectV2 { items(first:50, query:$q){ totalCount
    nodes { content { ... on Issue { number state } } } } } } }'
```

Exit codes: 0 OK; 1 a problem was found (identity not proven, setup incomplete, deferred or refused work, drift that could not be repaired); 2 invalid arguments. Every write is a dry run unless `--apply` is given.

| Situation | Action |
| --- | --- |
| `NOT VERIFIED` identity | Do not change the configuration to match an unexpected project. Compare `github.project.node_id` with the project's real node ID (`inspect`), and fix the configuration only if the project is the intended one |
| `not set up` | `github project setup` (dry run), review it, then `--apply` |
| `not authorized for the board` | Decide whether the feature belongs on the board; if yes, `github project associate F --apply` |
| `drift` | Someone moved a card or edited a field; the derived value was restored. Change task state with `feature.py` (hold, release, reopen, …), not on the board |
| `protected` | The Issue or item failed ownership verification; it is untouched. Inspect it (`github inspect N`, `github project task R`) |
| `foreign` / `orphan` in `reconcile` | Items the coordinator does not own, or owned Issues without a local record: never modified; review by hand |
| `deferred: … kept changing` | A project workflow keeps changing a value: disable it (§9) and sync again |

## 11. Live integration test

`live_projects.py` (`feature.py github project test --apply`, or `FC_GITHUB_PROJECTS_LIVE=1 … test_fc_projects_live.py`) creates **two** Issues: the parent of a new test feature `fc-p4x-<UTC>` and its single Backend task, both labeled `feature:test`. It drives the real coordinator, Git coordinator and sandboxed gates through steps B0–B14 (module docstring), reading every board value back through the Projects API. `--tracker local` rehearses the same scenario without GitHub; it is part of the regular suite (`test_projects_rehearsal_with_the_local_tracker` **[V]**).

Results of the live run: [phase-status](phase-status.md#phase-4-extension--github-projects-board-record-approved-2026-10-17).

## 12. Limitations

- **Last writer wins on fields.** GitHub has no conditional field update. A person's edit between the sync's read and its write is overwritten, which is the intended direction: the board shows the local state.
- **Review for tasks is brief.** It lasts only while gates run, and it is visible only through the detached publisher. Operations started from the Python API without a publisher show In Progress and Review only if a sync happens to run during the claim.
- **Workflows act asynchronously.** Values that GitHub's still-enabled workflows set seconds later ("Item added to project"; the pull-request workflows in Phase 5) can appear as drift on the next sync. They are restored, but reported (§9). The two workflows that closed Issues or set Done are disabled (D-28).
- **One account.** The board, Issues and comments act as `spyderbg`, like the operator (D-26). Board edits are never treated as authorization.
- **Column visibility** of filtered board views cannot be set through the API (§8).
- **The Phase 4 test Issues #11–#14 are not on the board (D-29, approved).** They stay closed audit records outside Project #2. They could be associated only with an explicit `associate` of their feature, from the test state directory; this is not to be done.
- **Active Tasks hides closed Issues.** If a person closes a coordinator Issue, its card leaves Active Tasks until the next Issue sync reopens it (§8).
