# Task Lifecycle and Dependencies (Phase 4)

> Owner role: **coordination**. Status: **Phase 4 deliverable, approved** (2026-10-17).
> State machines and locks: [feature-workflow §4–§7](feature-workflow.md#4-data-model-and-schema-proposal) · GitHub projection: [github-integration.md](github-integration.md) · Issue/comment formats: [templates/](templates/README.md) · Commands: [operations.md](operations.md)
>
> Evidence tags: **[V]** verified by execution (the named test), **[S]** source-read, **[U]** unverified.

## 1. Records

| Required by the Phase 4 assignment | Where it lives (local state, authoritative) |
| --- | --- |
| **Feature:** unique ID, title, description/acceptance criteria, base branch, feature branch | `Feature.id`, `title`, `acceptance`, `base_branch`, `branch` (`feature/<slug>`, one per feature) |
| Required agent roles, component tasks, dependency graph | `Feature.task_ids` → `Task.role`, `Task.depends_on` (validated as a DAG) |
| Verification requirements | `Task.required_gates`; integration gates of `integrate()` |
| Current state, execution history | `Feature.state`, `Feature.history` |
| GitHub parent Issue number | `Feature.issue_number` (identity proof: `github/<feature-id>.json`) |
| Test marker | `Feature.test` → every Issue carries `feature:test` |
| **Task:** unique ID, parent feature ID, assigned role | `Task.id` (`<feature>.<key>`), `feature_id`, `role` |
| Writable paths | The role's owned/excluded paths in the configuration plus `Task.grants` (validated; never from GitHub) |
| Acceptance criteria, dependencies | `Task.acceptance`, `Task.depends_on` |
| Current execution state | `Task.state`, `blocked_reason`, the live claim in `claims/<task>.json` (PID, start time, boot ID, heartbeat) |
| GitHub Issue number | `Task.issue_number` (identity proof: `github/<task-id>.json`) |
| Verification evidence | `Task.evidence` (HEAD SHA, gate results with status, exit code, duration and log), `Task.commits` |

There is **one** state store (the D-12 directory). `github/` and `outbox/` hold only the GitHub side of these records: mappings, identity, cursors and pending comments. GitHub is never a second source of task state.

## 2. States

| Task state | GitHub label | Leaves by |
| --- | --- | --- |
| `proposed` | role (+ `feature:test`) | activation |
| `ready` | role | an atomic claim |
| `in-progress` | `feature:in-progress` | the run's outcome: verified, failed, needs-human; or back to ready (yield, cancel, provider unavailable, deferred commit) |
| `blocked` | `feature:blocked` | **only** when every dependency is verified (dependency block), or never automatically (other reasons) |
| `needs-human` | `feature:blocked` | **only** an explicit operator release |
| `failed` | `feature:blocked` | a retry (`ready`) or escalation (`needs-human`) |
| `verified` | `feature:verified` | re-opened (`ready`) or blocked again when a dependency changes |

Legal transitions: [feature-workflow §5.1](feature-workflow.md#51-task). Re-applying the current state is a no-op. Each state change is reported once on the task's Issue (an `h<start>-<end>` progress comment).

## 3. Dependency lifecycle

```text
 consumer (CLI / Frontend)                   coordinator                               Backend task
 ───────────────────────────                 ───────────                               ────────────
 needs an API that is missing ──request_contract(key)──►  create OR reuse ONE <feature>.contract-<key>
                                              add the edge, reject unknown IDs and cycles
 blocked ("waiting for …") ◄──────────────── block ONLY this consumer; others continue
                                              sync: feature:blocked + comment, Issue refs #N
                              probe_dependency ──► run the provider's gates at the branch HEAD
                                              (evidence only; "unavailable" comment)        claim (atomic) → run
                                                                                             serialized commit
                                                                                             gates at the combined HEAD
                                              verified ◄─────────────────────────────────── evidence bound to HEAD
 ready ◄──────────────────────────────────── refresh_readiness: unblock eligible consumers
                                              sync: labels, "Unblocked" progress comments
 … later the contract must change ──reopen_task──► provider → ready; EVERY dependent that relied on it is
                                              blocked again (verified ones must re-verify, ready ones wait)
```

| Rule | Implementation | Evidence |
| --- | --- | --- |
| A missing API creates **or reuses** exactly one Backend task and Issue per contract key | `request_contract` (task ID `<feature>.contract-<key>`; the sync's key search prevents a second Issue) | `test_contract_requests_are_deduplicated` **[V]**; live D/E (both consumers reuse the planned Backend task) **[V]** |
| Unknown dependencies and cycles are rejected | `validate_dag` on plan and on every new edge | `test_fc_model.py` (cycle, unknown, self) **[V]** |
| Only affected consumers are blocked; independent tasks continue | `refresh_readiness` blocks a task only for its own unmet dependencies | `test_blocked_consumer_does_not_freeze_unrelated_work` **[V]**; live G (Backend stays runnable) **[V]** |
| A dependency is available **only** when the provider task is `verified`: its commit landed on the shared branch and its required gates passed at the branch HEAD | `transition_task(…, VERIFIED, evidence, expected_head)` | `test_fc_model.py` evidence tests **[V]**; live I (real sandboxed `contract-check` and `backend-tests`) **[V]** |
| Never unblocked by a comment, a label, a closed Issue or an unsupported completion claim | No GitHub input reaches `refresh_readiness`; `/feature release` frees human holds only; `verify_task` demands gates at the current HEAD | live T1, I2 **[V]**; `test_a_release_cannot_free_a_dependency_block_or_a_newer_hold` **[V]** |
| A consumer is never verified while a dependency is unverified (e.g. re-opened during its run) | `_finish` and `verify_task` check `unmet_dependencies` | `test_a_consumer_is_never_verified_while_its_dependency_is_unverified` **[V]** |
| An invalidated dependency re-blocks its dependents | `reopen_task` → `invalidate_dependents` (verified) + `refresh_readiness` (ready) | `test_reopened_contract_reblocks_ready_dependents` **[V]**; live K2 **[V]** (a Phase 4 fix: Phase 2 re-blocked only *verified* dependents) |
| A probe is evidence, not verification | `probe_dependency` runs the provider's gates read-only; a pass leaves consumers blocked until the provider's own verified run | `test_probe_reports_an_unavailable_dependency_and_never_unblocks` **[V]**; live F **[V]** |

## 4. Claims (who may run a task)

Atomic under `coordinator.lock`: the task must be `ready`, unclaimed and of the caller's role; the claim takes `project.lock` shared and `role-<role>.lock` exclusive, records PID + start time + boot ID, and moves the task to `in-progress` ([feature-workflow §7](feature-workflow.md#7-locks-claims-and-recovery-d-3-d-12)). One writer per role; different roles run in parallel; full-project work is exclusive. Labels are never locks. Live I1 shows a duplicate claim and a second Backend writer rejected **[V]**.

## 4a. Multi-step tasks and checkpoints (W3)

A task may define ordered `steps` (`Task.steps`; task records are schema v2, and W2's v1 records are migrated on load). Each step is one bounded agent invocation, and its accepted changes become one **checkpoint**. The record lives in `checkpoints/<task>.json`, plus one commit with a `Feature-Checkpoint` trailer when files changed.

The task's states are unchanged: progress is checkpoint metadata.
- **interrupted:** `in-progress` with a dead claim;
- **resumable:** `ready` with completed and remaining steps;
- **blocked by a recovery conflict:** `needs-human`, reason `recovery conflict: …`.

A resumed run reconciles first and runs only the remaining steps. Design, protocol and recovery: [worker-checkpoints.md](worker-checkpoints.md).

## 5. Human escalation

| Action | Local | GitHub (authorized operator, ≥ write permission) |
| --- | --- | --- |
| Hold for a human decision | `feature.py hold TASK --reason … --apply` | `/feature hold <task-id> <reason>` |
| Release | `feature.py release TASK --apply` | `/feature release <task-id>` |

A held task (`needs-human`) stays held when its dependencies become verified, across restarts and syncs, and against labels or a closed Issue. Only a release resumes it, and it is blocked again at once if its dependencies are unverified (`test_a_human_hold_survives_dependency_verification` **[V]**; live T3, J, J2 **[V]**). A running task cannot be held; stop it first. Full command policy: [github-integration §7.2](github-integration.md#72-github--local-validated-commands-only).

## 6. Handoff contract (Backend → consumers)

A contract request carries the exact contract text as the Backend task's acceptance criterion, and the Backend Issue shows the consumers under "Required by" ([templates/handoff.md](templates/handoff.md)). Before a consumer resumes, the Backend task must be verified with its `contract-check` (`export_openapi.py --docs-agents --check` in the real project) and `backend-tests` gates at the shared HEAD (architecture §11.8). Consumers then re-run their own gates at the combined HEAD.

## 7. One feature, one PR candidate

After every task is verified, `integrate()` runs the integration gates as a quiescent barrier and renders **one** PR candidate (`feature/<slug>` → base). A second integration is refused; `open_pull_request` needs operator approval, and in Phase 4 the GitHub adapter refuses it before any request. Component tasks never get their own PR. Body format: [templates/pull-request.md](templates/pull-request.md). Live R/R2: one candidate, a second refused, no remote PR, four serialized single-role commits on one branch **[V]**.

## 8. Worked example: the Phase 4 live scenario

`live_test.py` (run against real GitHub; results in [phase-status](phase-status.md)):

| Step | What happens |
| --- | --- |
| A, B | Plan `fc-p4-<timestamp>` (test feature) with `contract-items-export` (backend, provides the contract), `cli` and `web` (frontend). The sync creates the parent Issue and the three component Issues. A crash is injected right after GitHub created the Frontend Issue; a restarted synchronizer **adopts** it (no duplicate) |
| C | Parent ↔ component references are present in both directions |
| D, E | CLI and Frontend request the contract: the planned Backend task is reused (still three tasks) |
| F, G, H | The dependency probe runs the real `contract-check` in the gate sandbox: unavailable. CLI and Frontend are blocked, Backend stays runnable; labels and comments show it on GitHub |
| T1–T3 | A human adds `feature:verified` to the Backend Issue, removes `feature:blocked` from the CLI Issue, closes the Backend Issue and comments `/feature release` on the CLI Issue: nothing is unblocked, the drift is repaired, the release is rejected. `/feature hold` on the Frontend Issue holds it |
| I, I1, I2 | An atomic claim rejects a duplicate claim and a second Backend writer; fabricated evidence (no gates, wrong HEAD) is rejected; the Backend run commits through the Git coordinator and passes both real gates → verified |
| J, J2, K | CLI becomes ready; the held Frontend task stays held until `/feature release`; GitHub shows the verified Backend (with evidence) and the unblocked consumers |
| K2, K3 | The contract is re-opened (v2): CLI and Frontend are blocked again; the re-verified contract unblocks them |
| L, M | A repeated sync sends no write; exactly four owned Issues, one per key |
| R, R1, R2 | CLI and Frontend verified; one PR candidate; a second refused; no remote PR; one branch with four single-role commits |
| N, O, P | A new process runs `feature.py github reconcile --apply`: no drift, orphan, duplicate or write; Issue numbers unchanged |
| Q–Q4 | Every pre-existing Issue and PR, every pre-existing label and the bug-workflow trees are unchanged |
| Cleanup | A final verification comment on each test Issue, then close |
