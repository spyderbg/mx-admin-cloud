# Phase Status — Multi-Agent Development (v5)

> Durable progress record. **Governing assignment: [`docs/multiple_agents/agents_prompt_v5.md`](../../docs/multiple_agents/agents_prompt_v5.md)** (authoritative since 2026-10-16). It supersedes [`agents_prompt_v4.md`](../../docs/multiple_agents/agents_prompt_v4.md) (adopted and reported earlier the same day; its feature-workflow decisions all carry over) and [`agents_prompt_v3.md`](../../docs/multiple_agents/agents_prompt_v3.md), under which Phases 0–1 were executed and are preserved as-is. Re-read this file before resuming; chat history is not the record.
> Index: [`../README.md`](../README.md) · Evidence: [`../discovery/README.md`](../discovery/README.md) · Architecture and v4 design: [`../architecture/README.md`](../architecture/README.md) · Contracts: [`../architecture/api-contracts.md`](../architecture/api-contracts.md)

| Field | Value |
| --- | --- |
| Current phase | **W1 — Worker security (D-20 credential isolation, D-21 network isolation)**, on branch `security/worker-d20-d21` from `dev` (`bfd55a7`): implemented and tested offline on 2026-10-21, **uncommitted, awaiting operator review** ([W1 record](#w1--worker-security-d-20-d-21-record-2026-10-21)). Earlier: **Phase 4 finalization**: Phase 4 (Real GitHub Issue Coordination and Task Dependencies) and its GitHub Projects (v2) extension are **approved** (2026-10-17). Validation is complete, D-22/D-28/D-29 are done, and the D-10 commit plan was **executed** on 2026-10-17 (C4 `ce2d1fc`, C5 `80cb1d6`); the file modes it got wrong were fixed by `545b9d3` on 2026-10-19 ([execution record](#execution-record-d-10)) |
| State | **awaiting-approval** of the W1 changes (commit and push need approval). Real agent execution stays **disabled**: this Worker is INELIGIBLE until the operator chooses the broker's credential source (D-20). The AWS coordinator is postponed; Content Statistics is not started. Previously: **awaiting-approval** of the push of the two local commits (`545b9d3` and this status update) and of the D-11 bootstrap PR `agents` → `dev`. Phase 5 not started; it needs a separate authorization |
| Execution policy | Supervised, phase by phase. No autonomous phased execution. Phases 0–3 approved (Phase 3 on 2026-10-17). **Phase 4 and the Phase 4 extension approved 2026-10-17** by the finalization instruction ("Phase 4 and the GitHub Projects V2 extension are approved. This is a Phase 4 finalization task. Do not start Phase 5."), which also approved D-10 (a reviewed commit plan; no commit or push yet), D-22, D-28 and D-29. The plan's execution was approved the same day ("The commit plan is approved, run it"); the mode correction was approved on 2026-10-19 as new commits instead of a history rewrite ("fix it with new commits instead of rewriting"), because `agents` had been pushed by then. Earlier: Phase 4 authorized 2026-10-17 ("Proceed with Phase 4 ONLY"), including **real** GitHub Issue operations on `spyderbg/mx-admin-cloud` for coordinator-owned test Issues and their `feature:*` labels — no PRs, no commit or push. The **Phase 4 extension** (GitHub Projects V2 Kanban integration, before Phase 5) was authorized the same day: configure the existing Project #2, never modify Project #1 or Issues #1–#14, up to two test Issues — no PRs, no commit or push. The operator's instruction for the extension did not state an approval of Phase 4 itself (approved later with the finalization) |
| Code base | Branch `agents`. Phases 0–3: `12183e0`, `8454e63`, `b3f3691` (operator). Phase 4: `ce2d1fc` (C4). Extension and finalization: `80cb1d6` (C5). Operator's execution reports: `0d4d8f5`. All of these were pushed by the operator to `origin/agents` on 2026-10-17. **Local, not pushed:** `545b9d3` (file-mode fix) and the documentation commit with this status update ([execution record](#execution-record-d-10)) |
| Last updated | 2026-10-21 |
| Active writers / locks | None running. The real state directory holds only `github-tests/fc-p4-20261017033506/` and `github-tests/fc-p4x-20261017090916/` (the live tests' disposable repositories, states and `report.json`), earlier gate logs and `commit-plan-2026-10-17/` (the D-10 plan, executed); no feature in the main state, no claims |

## Phase overview

| Phase (v5) | Title | State | Gate summary |
| --- | --- | --- | --- |
| 0 | Inspect and Baseline (historical, v3) | **verified** | Approved by the operator (2026-10-16, v5 instruction); not to be rerun |
| 1 | Documentation Foundation (v3 scope, preserved) | **verified** | Approved by the operator 2026-10-16 (twice); v4 and v5 handoff gates re-run and passing (below) |
| 2 | New Feature Coordinator Design and Minimal Skeleton (incl. remote Qwen provider configuration, mocked) | **verified** | Approved by the operator 2026-10-17; committed as `8454e63` |
| 3 | Role Launchers, Scoped Write Enforcement, Concurrent Shared Checkout (+ remote Qwen adapter tests against a local fake server) | **verified** | Approved by the operator (2026-10-17, Phase 4 instruction); committed as `b3f3691` |
| 4 | **Feature Task Issue Coordination and Dependencies — real GitHub** | **verified** | Approved by the operator 2026-10-17 (finalization). All 22 exit gates met (record below; live Issues #11–#14, closed). Committed as `ce2d1fc` (C4); file modes fixed in `545b9d3` |
| 4x | **Phase 4 extension — GitHub Projects (v2) Kanban board** | **verified** | Approved by the operator 2026-10-17 (finalization). All 16 extension gates met (live Issues #15–#16, 35/35 checks); finalized with D-28 (verified) and D-29 (applied). Committed as `80cb1d6` (C5) |
| 5 | First End-to-End Feature (vertical slice) | not-started | Not authorized. Prerequisites: [Phase 5 prerequisites](#phase-5-prerequisites-and-remaining-security-restrictions) |
| 6 | CI, API Drift and Final PR Quality Gates | not-started | Constrained by B-2 |
| 7 | Budget-Aware Optional Continuous Feature Delivery | not-started | Needs separate authorization |
| W1 | **Worker security: D-20 credential isolation, D-21 network isolation** (local Worker; AWS coordinator postponed) | **implemented, awaiting review** | D-20 PARTIALLY VERIFIED (isolation proven; real provider auth needs a credential decision), D-21 VERIFIED offline ([record](#w1--worker-security-d-20-d-21-record-2026-10-21)) |

> **Superseded by v4 (historical v3 phase titles):** v3 Phase 2 was "Existing Automation Audit and Minimal Extensions" of `mxfix`/`mxissues`; v3 Phase 3 was "Agent Profiles and Scope Enforcement" with per-task worktrees; v3 Phase 4 was "GitHub Issue Coordination" extending `mxissues`; v3 Phases 5–7 assumed per-task PRs. All of these are replaced by the v4 titles above.

## W1 — Worker security (D-20, D-21): record (2026-10-21)

**Instruction:** "W1 — D-20 Credential Isolation + D-21 Network Isolation" for the local Worker (the AWS coordinator is postponed). Started on `dev` at `bfd55a7` (clean, equal to `origin/dev`; no unfinished D1 work found), on the new branch `security/worker-d20-d21`. Nothing committed or pushed; no real model session, no provider API call, no credential file read.

**Design and evidence:** [worker-security.md](worker-security.md).

| Item | Result |
| --- | --- |
| Credential files mounted / API keys forwarded | **Removed**; `credential_files` rejected by the loader; a control test proves the probes would catch a reintroduced mount or key **[V]** |
| Host-side broker | `broker.py`: a per-run token (expiry, revocation at run end), approved routes (Claude Code → `api.anthropic.com` `/v1/messages`[`/count_tokens`]; Codex → `api.openai.com` `/v1/responses`), the upstream credential added on the host, limits, a redacted log **[V]** |
| Runner network | Own network namespace for every sandbox; the broker endpoint is handed over from inside it and verified to be in a different namespace (`SO_NETNS_COOKIE`); there is no `--share-net` path; `runtime.agent_network` fixed to `false` **[V]** |
| Nested command isolation | Claude Code strict Bash sandbox; Codex workspace-write sandbox with no network; both verified with the **installed** CLIs against a fake provider **[V]** |
| Desktop socket (the earlier finding) | `@/tmp/.X11-unix/X0` → ECONNREFUSED inside the sandbox (`sandbox-check --apply`) **[V]** |
| Real provider authentication | **NOT VERIFIED.** The documented brokered mode of both CLIs uses a per-token-billed API key; the operator's subscription logins cannot be brokered (Anthropic: gateway credentials replace the claude.ai login and are billed per token) |
| Worker eligibility | `feature.py worker-check --apply` → **INELIGIBLE** (blocker: no broker credential) — fail closed |
| Regression | `scripts/tests` **517 passed, 18 opt-in skipped** (baseline 455/18), exit 0; Pylint 10.00/10 (package and tests); docs check 6/6; `feature.py config` identical; `sandbox-check --apply` PASSED (containment and isolation); opt-in `FC_HOST_RUNNER_CHECKS=1`: no real login visible in the sandbox **[V]** |
| Files | New: `broker.py`, `netns_channel.py`, `sandbox_bootstrap.py`, `isolation_check.py`, `worker-security.md`, tests `test_fc_broker.py`, `test_fc_isolation.py`, `test_fc_runner_compat.py`, `fc_fake_provider.py`. Changed: `launchers.py`, `sandbox.py`, `supervisor.py`, `config.py`, `providers.py`, `operations.py`, `commands.py`, `cli.py`, `errors.py`, `gates.py`, `__init__.py`, the example configuration, the existing tests that assumed the login mount or the shared network, and the coordination and architecture docs |

W1 decisions for the operator (also under [Human decisions required](#human-decisions-required)):

1. **D-20 credential source:** provision provider API keys for the broker (`broker.credential_source: api-key-file`, per-token billing), or keep real runners disabled.
2. **Review and commit** the W1 changes (branch `security/worker-d20-d21`), then authorize the real authenticated smoke stage separately.
3. **W2 input:** `/usr/bin/python` does not exist on this host, so the Backend/CLI Bash allowlists (`python -m pytest`, `python -m pylint`) cannot run inside the sandbox (pre-existing; not changed in W1).

## Phase 4 finalization — record (2026-10-17)

**Instruction** (2026-10-17): "Phase 4 and the GitHub Projects V2 extension are approved. This is a Phase 4 finalization task. Do not start Phase 5." Approved decisions:

| ID | Decision | Done |
| --- | --- | --- |
| D-28 | Project #2 must not close Issues automatically or overwrite coordinator-managed states; verify that "Auto-close issue" and "Item closed" are disabled; do not modify Project #1 | **Verified**: configuration and a behavior check (below) |
| D-29 | Keep #11–#14 off Project #2; keep #15–#16 in GitHub for audit; exclude completed test tasks from Active Tasks without deleting their Issues | **Applied**: Active Tasks filter `is:open …`; #11–#14 untouched and off the board; #15–#16 kept (closed, on Kanban) |
| D-22 | Update `docs_agents/architecture/README.md` and `docs_agents/README.md` | **Done** |
| D-10 | Prepare a reviewed commit plan for all completed phases; do not commit or push yet | **Prepared and rehearsed**; then executed after approval: C4 `ce2d1fc`, C5 `80cb1d6`; file modes fixed in `545b9d3` ([execution record](#execution-record-d-10)) |

### Validation results (instruction items 1–10)

| # | Requirement | Result **[V]** |
| --- | --- | --- |
| 1 | Project #2 configured | `github project inspect` (read-only): identity proven (owner `spyderbg`, #2, `PVT_kwHOACEIPc4BndH-`, not protected, updatable); scopes `gist, project, read:org, repo, workflow`; Status Backlog/Ready/In Progress/Blocked/Review/Done; Agent, Feature ID, Blocked Reason; views Kanban and Active Tasks. After the D-29 change, `validate` → **READY** and a repeated `setup` → "change: none" |
| 2 | Project #1 unchanged | Full snapshot (fields, options, views, workflows, items and their values, `updatedAt` 2026-09-28T05:07:25Z) **byte-identical** to the session-start snapshot, before and after the finalization's writes and again at the end |
| 3 | Coordinator status synchronization | `feature.py github project task` against the live test's state (read-only): #15 local `review` → board Review / Coordination; #16 local `verified` (evidence on the branch) → board Done / Backend; both **in sync**. `test_fc_project_sync.py` 42 passed (mapping, Done rules, holds, invalidation, drift, recovery) |
| 4 | Marking an item Done no longer closes its Issue | Both workflows **disabled** (API). Behavior check: #16 reopened, card moved Review → Done, **stayed open** for 45 s; #15 reopened and closed, card **stayed Review** for 45 s ([github-projects §9](github-projects.md#9-githubs-built-in-project-workflows)) |
| 5 | A manual card move cannot authorize execution | Static: no claim, lock, supervisor, launcher, sandbox, gate, Git, model or store module imports or reads board code; `workflow.py` only calls `board.sync` and `board.authorize` (both write outward). Tests: `test_a_card_moved_on_the_board_never_releases_claims_or_completes`, the claim/lock subsets (41 passed). Live (extension run, B6/B7): cards moved to In Progress, Done and Ready started nothing; a held task stayed unclaimable |
| 6 | Completed test tasks are not in Active Tasks | GitHub evaluates the view filter server-side (`ProjectV2.items(query:)`): Active Tasks → **0** items; Kanban → #15 (closed, Review) and #16 (closed, Done). Before the change, Active Tasks returned #15 |
| 7 | Coordinator regression tests | `scripts/tests`: **430 passed, 18 opt-in skipped** (+1: `test_setup_limits_the_active_tasks_view_to_open_issues`). Pylint 10.00/10 (package and tests); docs check 6/6; `feature.py config` identical; `sandbox-check --apply` PASSED; `export_openapi --docs-agents --check` 91/91; `feature.py demo` passed |
| 8 | Credential isolation, without reading secrets | See [Credential isolation](#credential-isolation-item-8): no credential path visible and no secret-like variable in any role sandbox; no token-shaped string in the changes or the coordinator state |
| 9 | B-7 review | See [B-7 review](#b-7-review-item-9): still open on every branch, including `dev` |
| 10 | Phase 5 prerequisites and security restrictions | See [Phase 5 prerequisites](#phase-5-prerequisites-and-remaining-security-restrictions) |

### GitHub actions in the finalization (as `spyderbg` through `gh`, 2026-10-17)

| Time (UTC) | Action |
| --- | --- |
| 10:4x–10:56 | Read-only: `inspect`, `validate`, introspection of `ProjectV2.items(query:)`, view-filter queries, Project #1 snapshots, Issue/PR listings |
| 10:52:55 | `feature.py github project setup --apply`: **one** change on Project #2, the Active Tasks filter → `is:open status:Ready,"In Progress",Blocked,Review`. Nothing else changed |
| 10:54–10:56 | D-28 behavior check (guarded clients, ownership of #15/#16 verified first), **6 writes**: #16 reopened → Status Review → Done → closed as completed; #15 reopened → closed as completed. End state = start state (#15 closed / Review, #16 closed / Done) |
| Not done | No Issue created; no Issue #1–#14 touched (last updates ≤ 03:39 UTC); no PR (5 in the repository, unchanged); no label change; no push, merge or deploy; Project #1 not written; no request to the remote Qwen server |

### Code and documentation changes in the finalization

- `project_sync.active_filter()` (+ `ACTIVE_STATUS_KEYS`): the one place that builds the Active Tasks filter, used by `setup`, the local board and `validate`. `is:open` is prepended because a closed coordinator Issue is never active work. The Issue sync closes Issues only when the feature is done or a test cleanup finished it, and it reopens Issues a person closed.
- Test `test_setup_limits_the_active_tasks_view_to_open_issues` (dry run lists exactly one change; apply writes once; idempotent); two assertions updated.
- D-22: `docs_agents/architecture/README.md` (implementation status; §11.4 and §11.6 marked verified in Phase 3; §11.9 "No Projects v2 is required" superseded by the board; register: D-4, D-5, D-10, D-17, D-19, D-22, D-28, D-29); `docs_agents/README.md` (github-projects.md in the navigation).
- Status lines now say approved: `github-projects.md` (also §8 D-29, §9 D-28 verification, §10 view check, §12), `github-integration.md`, `operations.md` (§8 notes), `feature-workflow.md`, `task-lifecycle.md`, `agent-ownership.md` (Phase 3), `automation/README.md`. Also the `project-config.example.yaml` comment, and a stray blank line at the end of `operations.md` and `feature-workflow.md`.

### Credential isolation (item 8)

Method: no credential file was opened and no value printed. A probe ran inside the real role sandboxes (`SandboxBuilder.plan` for the live checkout, real `HOME`) for backend, cli, frontend, automation and the read-only gate sandbox. It reported only whether paths exist, the **names** of environment variables, and the exit code of `gh` (absent).

| Check | Result **[V]** |
| --- | --- |
| Exist on the host | `~/.config/gh/` and `hosts.yml`, `~/.local/state/mx-admin-agents/`, `~/.ssh/`, `~/.claude/.credentials.json`, `~/.codex/auth.json`, the repository `.env` |
| Visible in any role sandbox | **none** (all absent; private HOME, the checkout's top-level secret files are not mounted) |
| `gh` inside the sandboxes | not on the sandbox `PATH` (installed under `/snap/bin`) |
| Environment inside the sandboxes | exactly 12 allowlisted names (`HOME`, `PATH`, `LANG`, `LC_ALL`, `TMPDIR`, `TERM`, `USER`, `LOGNAME`, `PWD`, `GIT_OPTIONAL_LOCKS`, `GIT_TERMINAL_PROMPT`, `PYTHONDONTWRITEBYTECODE`); no token-, key- or D-Bus-like name |
| Token-shaped strings (GitHub, Anthropic, OpenAI-style, AWS, private keys) | **0** in the 52 changed files; **0** in 124 coordinator state files (events, logs, reports; Git internals excluded) |
| Not covered by the probe | Runner launches bind the runner's own credential files read-only into its sandbox and share the network for the runner's API: residual risks D-20 and D-21 (unchanged) |

### B-7 review (item 9)

- **Still open, unchanged.** `test_delete_item_image_last_remaining_is_allowed` still asserts `provider.item_images(...)` **outside** its `with patch_fs_settings()` block (line 367). This is true at `HEAD`, `origin/main`, `origin/dev` (the Phase 5 base, `daa8190`) and `gitlab/dev`; the file last changed in `eb8a6a0` (2026-10-13). So it resolves the real `content/` tree and can create `content/.categories_registry.lock` and `content/foods/.items_registry.lock` ([backend-task-b7.md](backend-task-b7.md)).
- **Current safeguards hold.** `backend-tests` is in `unsafe_gates` (config and example), so it is blocked against real checkouts. Gates run sandboxed read-only anyway. No leaked lock file exists under `content/` today.
- **Impact on Phase 5.** Without the fix, the integration gate set cannot include `backend-tests` on the shared feature branch, so Backend changes would be verified by `contract-check`, lint and docs gates only. That is too weak for a feature that changes the API.
- **Recommendation.** Land the B-7 fix before the Phase 5 feature, as an approved, test-only Backend change into `dev` (scope `backend/src/services/content/__tests__/`). Acceptance: 919+ passed; a writable-export run leaves `content/` unchanged. Then remove `backend-tests` from `unsafe_gates` (one coordination change) and adopt D-23 with `backend-tests`. Modifying `backend/` needs explicit approval, so it was not done here.

### Uncommitted change inventory (2026-10-17, final)

> Committed on 2026-10-17 as C4 and C5 ([execution record](#execution-record-d-10)). The table is kept as the record of what the plan contained.

`git status`: 48 paths in the plan, plus the operator's own untracked notes. Columns: C4 = the proposed Phase 4 commit; C5 = the proposed extension and finalization commit.

| Group | Paths | C4 | C5 |
| --- | --- | --- | --- |
| Phase 4 code, new | `scripts/feature_coordinator/{github_api,github_commands,issue_sync}.py` | Phase 4 version | extension hooks |
| Phase 4 code, new, unchanged since | `scripts/feature_coordinator/{escalation,live_test}.py` | yes | — |
| Phase 4 code, modified | `scripts/feature_coordinator/{__init__,cli,commands,config,errors,issues,operations,store,workflow}.py` | Phase 4 version | extension hooks |
| Phase 4 code, modified, unchanged since | `scripts/feature_coordinator/{locks,model}.py` | yes | — |
| Extension hook only | `scripts/feature_coordinator/gitops.py` (`branch_contains`) | unchanged | yes |
| Extension code, new | `scripts/feature_coordinator/{github_projects,projects_local,project_sync,project_commands,live_projects}.py` | — | yes |
| Phase 4 tests, new / modified | `scripts/tests/feature_coordinator/{fc_fake_gh,test_fc_github_client,test_fc_issue_sync,test_fc_github_live}.py`; `test_fc_workflow.py` | yes | — |
| Extension tests, new | `scripts/tests/feature_coordinator/{test_fc_github_projects,test_fc_project_sync,test_fc_projects_live}.py` | — | yes |
| Phase 4 docs, new | `docs_agents/coordination/{github-integration,task-lifecycle}.md`, `docs_agents/coordination/templates/` (7 files) | yes | board sections, status lines (templates unchanged) |
| Phase 4 docs, modified | `docs_agents/coordination/{feature-workflow,operations,phase-status}.md`, `project-config.example.yaml`, `docs_agents/README.md`, `docs_agents/automation/README.md` | Phase 4 version | extension and finalization |
| Extension docs, new | `docs_agents/coordination/github-projects.md` | — | yes |
| D-22 / status, committed docs | `docs_agents/architecture/README.md`, `docs_agents/coordination/agent-ownership.md` | — | yes |
| **Operator's files (not in the plan)** | `docs/multiple_agents/execution/` (6 files, untracked): saved copies of the Phase 1–4 chat reports; no token-shaped string; one IPv4-like string (presumably loopback, not checked). **Later committed by the operator as `0d4d8f5` (7 files)** | — | — (operator's decision) |
| Outside Git | `~/.local/state/mx-admin-agents/github-tests/{fc-p4-20261017033506,fc-p4x-20261017090916}/` (live evidence), `commit-plan-2026-10-17/` (this plan) | — | — |

Unchanged **[V]**: `backend/`, `cli/`, `frontend/`, `content/`, `data/`, `.env`, `docker/`, `scripts/deploy.sh`, `scripts/mxfix/`, `scripts/mxissues/`, `CLAUDE.md`, `AGENTS.md` (`git status` empty); `AUTH_DISABLED`/`DEBUG_MODE`; D-15/D-16/D-18 settings.

### Commit plan (D-10) — prepared, rehearsed, executed 2026-10-17

The first three requested groups already exist as the operator's commits on `agents` (when the plan was made, local only; `origin/agents` was at `d1a3f5c`):

| Group (instruction) | Commit | Content (verified with `git show --name-status`) |
| --- | --- | --- |
| Phase 0/1 documentation and tooling | `12183e0` "Add agent documentation tree and per-owner OpenAPI export" (on top of the operator's `e2ddd9c`) | `docs_agents/` tree, `scripts/{check_docs_agents,openapi_docs}.py`, `export_openapi.py` extension, tests, the v4 and v5 prompts |
| Phase 2 coordinator foundation | `8454e63` "Add feature coordinator tooling and v4 workflow docs" | `scripts/feature.py`, `scripts/feature_coordinator/` (16 modules), tests, `feature-workflow.md`, configuration example |
| Phase 3 real runners and sandbox | `b3f3691` "Add supervisor, sandbox, and launch/operations tooling" | sandbox, supervisor, launchers, gates, lint baseline, HTTP transport; tests; `agent-ownership.md`, `operations.md`, `backend-task-b7.md` |
| **Phase 4 GitHub Issues integration** | **C4** `ce2d1fc` "Add GitHub Issue coordination and task dependencies (Phase 4)" | the 37 Phase 4 paths **in their Phase 4 versions** (inventory above) |
| **GitHub Projects V2 integration** | **C5** `80cb1d6` "Add GitHub Projects board sync and finalize Phase 4" | the extension, the finalization and every path's final version (48 paths) |

Recommendation: keep the three existing commits as they are; they already match the requested split. Add C4 and C5 on top. No history rewrite is needed.

**How C4 gets its Phase 4 versions.** 18 files were changed by both Phase 4 and the extension. Their Phase 4 versions were rebuilt by undoing the extension's recorded edits from this session, newest first. The rebuild was verified two ways:

- re-running every recorded edit for real on the rebuilt files reproduces the current files **byte for byte**;
- the Phase 4 tree passes on its own: 367 passed, 13 skipped (the 12 opt-in tests, plus `test_fc_statedir_locks`, which needs the checkout's FUSE mount and runs in the real checkout: 368/12 as recorded for Phase 4); Pylint 10.00/10 (package and tests); docs check 6/6; configuration identical; no reference to the extension.

**Execution (approved and run 2026-10-17):** `bash ~/.local/state/mx-admin-agents/commit-plan-2026-10-17/commit_plan.sh <repo>`. The script:

- refuses to run unless the branch is `agents`, `HEAD` is `b3f3691`, nothing is staged, and the changed set and each file's content equal what was reviewed (`c5_paths.txt`, `c5_blobs.txt`);
- applies `c4.patch` to the **index only** (`git apply --cached`), so the working tree is never touched, and commits C4;
- stages the explicit C5 list (never `git add -A`) and commits C5;
- checks both trees against the rehearsal and that only the operator's untracked notes remain;
- never pushes.

The plan was rehearsed in two throwaway clones; results are in the plan directory's `REHEARSAL.txt`.

**Before anything is pushed (separate approval):** review the literal remote-Qwen address in the already committed `agents_prompt_v5.md` (v5 §10.1.1; history can still be rewritten before the first push). Decide also about `docs/multiple_agents/execution/` (commit separately, keep untracked, or ignore). *Overtaken:* the operator committed the execution notes and pushed `agents` on 2026-10-17 (below). This record does not show the outcome of that review. Both files are now on `origin/agents`; any follow-up is the operator's decision.

### Execution record (D-10)

| When | Commit / action | Result **[V]** |
| --- | --- | --- |
| 2026-10-17 15:30 +0300 | `commit_plan.sh` → C4 `ce2d1fc` "Add GitHub Issue coordination and task dependencies (Phase 4)" | Tree `9f9bff8` **equal to the rehearsal**; 36 files, +5292/−273 |
| same run | C5 `80cb1d6` "Add GitHub Projects board sync and finalize Phase 4" | Each of the 48 paths has its reviewed blob; 32 files, +4938/−88. The script's last check **stopped**: tree `b8e6f2d` ≠ rehearsal `1e8b9f1`. The trees differ only in file modes (below); contents are identical |
| 2026-10-17 17:11 +0300 | `0d4d8f5` "Add documentation for multi-agent execution phases 1–3." (operator) | The 7 files under `docs/multiple_agents/execution/`, all `100644`; nothing else |
| 2026-10-17 17:12 +0300 | Push by the operator | `origin/agents` = `0d4d8f5` (C4, C5 and the operator's commit are published) |
| 2026-10-19 | `545b9d3` "Make Phase 4 coordinator files non-executable" | The mode fix below; **not pushed** |
| 2026-10-19 | The documentation commit with this status update | `phase-status.md` only; **not pushed** |

**File-mode correction.** `c4.patch` was generated in a rehearsal clone that had `core.fileMode` enabled. That clone took its files from the checkout's FUSE file system, where every file reads as `0777`, so C4 recorded the **18 files it adds** as `100755`:

- `scripts/feature_coordinator/{escalation,github_api,github_commands,issue_sync,live_test}.py`
- `scripts/tests/feature_coordinator/{fc_fake_gh,test_fc_github_client,test_fc_github_live,test_fc_issue_sync}.py`
- `docs_agents/coordination/{github-integration,task-lifecycle}.md`
- `docs_agents/coordination/templates/` (7 files)

The checkout has `core.fileMode=false`, so C5's nine new files were staged as `100644`. The rehearsal had them at `100755`, which is why the C5 tree check failed. `agents` was already pushed, so the operator chose a forward fix over a history rewrite. `545b9d3` sets the 18 index entries to `100644` with their existing blob IDs (`git update-index --index-info`). There is no `chmod` and no working-tree change. Verified:

- exactly 18 entries change, each `100755 → 100644` with the same blob ID; no other path or mode changes;
- all 2,595 path/blob pairs are identical between `0d4d8f5` and `545b9d3`;
- the executable files are again exactly the 28 of `b3f3691`, with the same blobs;
- C4 and C5 are unchanged: history was not rewritten, and `ce2d1fc`/`80cb1d6` still show the 18 files as `100755`;
- after the fix the working tree is clean and the index equals `HEAD`. `scripts/tests`: 430 passed, 18 opt-in skipped. Pylint 10.00/10 (package and tests). Docs check 6/6. `feature.py config` identical.

### Phase 5 prerequisites and remaining security restrictions

Phase 5 is **not started** and needs an explicit authorization. Before it:

1. ~~**D-10:** approve and execute the commit plan (C4, C5)~~ **Done 2026-10-17**: C4 `ce2d1fc`, C5 `80cb1d6`, pushed by the operator together with `0d4d8f5`. The mode fix `545b9d3` and this status update are local ([execution record](#execution-record-d-10)).
2. **D-11 / B-8:** push the two local commits and open the human-reviewed bootstrap PR `agents` → `dev`, so the coordinator exists on the feature base. This needs its own approval; it is not a feature PR. The planned pre-push review was overtaken by the operator's push (above).
3. **B-7:** an approved, test-only Backend fix landed in `dev`; then remove `backend-tests` from `unsafe_gates` (review above).
4. **D-23:** the Phase 5 gate set. Recommended: the full catalog with D-19 lint baselines and `backend-tests` after B-7.
5. **D-24:** `github.write_scope: all-features`, only together with the Phase 5 feature approval.
6. **D-25:** the Git coordinator's non-force push of `feature/<slug>`, and `LIVE_PULL_REQUESTS_ENABLED` for **one** PR per feature, each with explicit approval; never merge.
7. ~~**D-20 and D-21** for supervised runs~~ **Implemented in W1** (2026-10-21; credential brokering and network namespaces, [worker-security](worker-security.md)). Still open: the broker's credential source (D-20).
8. **Optional:** D-26 (a separate GitHub identity), and switching off Project #2's two pull-request workflows. They would move the parent's card when the single PR is linked or merged; the coordinator restores the value.
9. **The feature:** an operator-approved **new** feature spanning Backend, CLI and Frontend, with its spec (tasks, contract, acceptance criteria, gates).
10. **Only if the Automation role should use the remote Qwen model:** D-15, D-16, D-18 and an approved live validation. Otherwise Automation stays off or on an approved default runner.

**Restrictions in force until changed by an explicit decision:**

- No Phase 5 and no real feature implementation.
- No push, PR, merge or deploy. `create_pull_request` is refused; live GitHub writes are limited to `test: true` features.
- Project #1 is never written: excluded in the configuration, refused by the write guard, and caught by a test tripwire.
- The board and GitHub comments are never authority. Locks, claims, permissions and approvals come only from the local state and the operator's own commands. The shared `spyderbg` account means a GitHub edit or comment can never authorize a sensitive operation (D-26).
- No request to the remote Qwen server (`LIVE_REMOTE_ENABLED = False`; D-15/D-16/D-18 open).
- Runner sandboxes:
  - *(superseded by W1, 2026-10-21)* runner credentials were bound read-only (D-20) and Claude Code shared the host network (D-21). Since W1 no credential is mounted and no sandbox shares the host network; real runs need a broker credential;
  - every other credential path stays invisible;
  - gate sandboxes are offline and read-only.
- `backend-tests` is blocked against real checkouts (B-7).
- `AUTH_DISABLED`/`DEBUG_MODE` and the application defaults are unchanged (D-5). `mxfix`/`mxissues` stay independent and untouched.
- No server-side branch protection or CI (B-2): PR review and gates are procedural.

## Phase 4 extension — GitHub Projects board: record (approved 2026-10-17)

**Authorized** 2026-10-17: "Extend the existing Feature Coordinator with GitHub Projects V2 Kanban integration … a Phase 4 extension, performed BEFORE Phase 5." Target: the existing Project #2 "mx-admin-cloud automation" (`PVT_kwHOACEIPc4BndH-`). Never modify Project #1 (mxissues) or Issues #1–#14; up to two test Issues; no PR, merge, deploy, commit or push.
**Design:** [github-projects.md](github-projects.md) · **Commands:** [operations §8](operations.md#8-github-projects-board-phase-4-extension) · [feature-workflow §20](feature-workflow.md#20-phase-4-extension--github-projects-board) · configuration `github.project` in [project-config.example.yaml](project-config.example.yaml).

### Exit gates (extension assignment §11)

| # | Criterion | Result **[V]** |
| --- | --- | --- |
| 1 | Project #2 configured correctly | **Met.** `feature.py github project setup --apply` ([github-projects §1.1](github-projects.md#11-what-setup-configured-on-project-2-2026-10-17-v)); `validate` → READY; a second `setup` changes nothing |
| 2 | The Kanban board supports the required statuses | **Met.** Status options Backlog, Ready, In Progress, Blocked, Review, Done; view `Kanban` (board, columns = Status) and `Active Tasks` (board, filter `status:Ready,"In Progress",Blocked,Review`; since D-29 `is:open status:…`) |
| 3 | Existing coordinator-owned Issues can be associated when authorized | **Met.** Live B1–B3: parent Issue #15, created by a coordinator without the board, was **not** touched by a board sync (B2: feature not authorized). After `feature.py github project associate … --apply` (a separate process), it was on the board exactly once |
| 4 | New feature Issues are added automatically | **Met.** Live B4: `activate` created task Issue #16 and added it to the board in the same sync; `test_plan_adds_new_feature_issues_automatically_with_derived_values` |
| 5 | Task transitions update Project Status | **Met.** Live, each read back through the Projects API: Backlog → Ready → Blocked (hold) → Ready (release) → **In Progress** while the agent ran → **Review** while its commit was verified → Done → Ready (re-opened) → Done; parent Backlog → In Progress → Review. `test_task_status_mapping_follows_the_state_machine` and 8 feature-state cases |
| 6 | Blocked tasks show the correct reason | **Met.** Live B7: `held for a human decision by phase4x-live-test: waiting for a UX review`, cleared on release (B8). Dependency (`waiting for <task> (<state>)`) and failure (`failed: …`) reasons: `test_dependency_invalidation_reblocks_dependents_on_the_board`, `test_task_status_mapping_follows_the_state_machine` |
| 7 | Agent and Feature ID synchronized | **Met.** Live: parent Coordination, task Backend, Feature ID `fc-p4x-20261017090916` on both; an Agent edited on the board is restored (`test_a_card_moved_on_the_board_never_releases_claims_or_completes`) |
| 8 | Repeated synchronization creates no duplicates | **Met.** Live B11: one item per Issue, and the next sync of Issues and board sent no write; `test_concurrent_board_syncs_never_create_duplicate_items` (3 processes) |
| 9 | Recovery after a crash works | **Met.** Live B4/B5: a crash injected right after GitHub added #16's item. The restarted coordinator **adopted** `PVTI_lAHOACEIPc4BndH-zhBbwo8`, with no second item. Lost add responses, failed field writes and restarts are covered by unit tests (§7 of the design page) |
| 10 | Manual card moves cannot authorize local execution | **Met.** Live B6: task card → In Progress and parent card → Done started and completed nothing. B7: a held task's card moved to Ready → `run_task` refused (`needs-human, not ready`). Each time the next sync reported drift and restored the values |
| 11 | Project #1 unchanged | **Met.** Fields, options, views, workflows, items with their values and `updatedAt` (2026-09-28T05:07:25Z) identical to the snapshot taken at the start of the session, after `setup` and after the live test (B14 and an independent re-check); the test Issues are on no other project |
| 12 | All required automated tests pass | **Met.** `scripts/tests`: **429 passed, 18 opt-in skipped**. New: `test_fc_github_projects.py` 20, `test_fc_project_sync.py` 41 (all extension test topics, §8 of the assignment) |
| 13 | The real Projects integration test passes | **Met.** `FC_GITHUB_PROJECTS_LIVE=1 … test_fc_projects_live.py`: **6 passed** in 420 s; the scenario report records **35/35** checks |
| 14 | Documentation updated | **Met.** New [github-projects.md](github-projects.md); updated github-integration, feature-workflow, operations, this file and the configuration example; docs check 6/6 |
| 15 | No real feature implementation started | **Met.** Only the disposable test repository received (scripted) commits; no change in `backend/`, `cli/`, `frontend/`, `content/`, `data/` |
| 16 | No pull request created | **Met.** The adapter refused `open_pull_request` before any request (B12); the repository's PR set is unchanged |

### Project #2 configuration (operator-visible result)

| Element | Value |
| --- | --- |
| Status (single select, board order) | Backlog `f75ad846` (GitHub's default `Todo`, renamed; ID kept), Ready `b62cdbed`, In Progress `47fc9ee4` (reused), Blocked `dc3fb247`, Review `ec596878`, Done `98236657` (reused) |
| Agent (single select) | Coordination, Backend, CLI, Frontend, Automation, Full Project — field `PVTSSF_lAHOACEIPc4BndH-zhmB2f0` |
| Feature ID (text) | `PVTF_lAHOACEIPc4BndH-zhmB2f4` |
| Blocked Reason (text) | `PVTF_lAHOACEIPc4BndH-zhmB2g0` |
| Views | 1 "View 1" (table, unchanged); 2 **Kanban** (the former "View 2", board grouped by Status); 3 **Active Tasks** (board grouped by Status, filter `status:Ready,"In Progress",Blocked,Review`; **since D-29 (finalization): `is:open status:Ready,"In Progress",Blocked,Review`**); cards show Agent, Feature ID and Blocked Reason |
| Nothing deleted | No field, option or view removed; Project #1 untouched |

### Live GitHub actions (as `spyderbg` through `gh`, 2026-10-17 08:57–09:16 UTC)

| Action | Detail |
| --- | --- |
| Project #2 setup (08:57:2x UTC) | The changes above (Agent field created 08:57:24, Active Tasks view 08:57:29); 0 items at the time |
| Test feature | `fc-p4x-20261017090916`, task `fc-p4x-20261017090916.contract` (backend) |
| Issues created (2) | [#15](https://github.com/spyderbg/mx-admin-cloud/issues/15) "[Feature Coordinator Test] Board sync (Phase 4 Projects extension test)" (parent) · [#16](https://github.com/spyderbg/mx-admin-cloud/issues/16) "[Feature Coordinator Test] [backend] Board sync contract"; both labeled `feature:test` |
| Project items (2) | #15 → `PVTI_lAHOACEIPc4BndH-zhBbwdk`; #16 → `PVTI_lAHOACEIPc4BndH-zhBbwo8` (Project #2 only) |
| Simulated human board edits (after the ownership check) | #16's card → In Progress, #15's card → Done (B6); #16's card → Ready while held (B7). All restored by the next sync |
| Closed | #16, then #15, as completed, after a final verification comment each. Afterwards GitHub's "Item closed" workflow set #15's card to Done; the coordinator restored **Review** (the feature was never merged). Final board: #15 Review / Coordination, #16 Done / Backend |
| Volume | The test process's client: 303 requests, 58 writes (REST and GraphQL); the separate publisher, associate and reconcile processes sent more. No rate limit was hit |
| Not done | No pull request, push or merge; no label created, edited or deleted; Project #1 and Issues/PRs #1–#14 untouched (B14 and an independent re-check of every Issue's `updated_at`, state, labels and comment count) |

Evidence on disk: `~/.local/state/mx-admin-agents/github-tests/fc-p4x-20261017090916/report.json` (every check, the observations, the drift log), `state/events.jsonl`, `state/logs/publish-*.log` (the detached publisher runs) and `repo/`.

### Observations from the live run

- **GitHub's "Auto-close issue" workflow was active on Project #2.** It closed #15 when a person moved its card to Done, and closed #16 each time the coordinator set Done (the observations in the report). The Issue sync reopened both, because only the local state closes Issues, and commented why. Correct, but noisy. **Since switched off (D-28; verified in the finalization).**
- **"Item added to project"** set the new items' Status to Backlog by itself: the renamed `Todo` option kept its ID, so the workflow still works. B3's writes therefore contain only Agent and Feature ID.
- **Parent block change without an edit.** As in Phase 4, the sync reported drift in parent #15's managed block right after GitHub closed #16 and restored it. It is the same pattern (a referenced Issue closed → the parent's task-list line changes); the cause remains **[U]**.
- B11 needed three sync rounds to converge (reopening #16 after the automatic close, then nothing). This is expected with the workflow enabled.

### Implemented components

| Requirement (extension assignment) | Where | Tests |
| --- | --- | --- |
| Configurable target project; Project #2 in the mx-admin-cloud configuration only (§3) | `config.GitHubProjectConfig` (generic default disabled), `MX_ADMIN_CLOUD_PROJECT`, `github.project` in the YAML | `test_project_configuration_is_validated` (8), `test_the_generic_default_is_disabled_and_mx_admin_cloud_uses_project_2` |
| GraphQL through the existing `gh` login; scope check; no token handling (§3) | `GitHubClient.graphql()` (GraphQL errors classified like HTTP statuses), `token_scopes()` (response header only), `github_projects.ProjectsClient` | `test_fc_github_projects.py` (20) |
| Board configuration: Status options, fields, views (§2) | `ProjectSync.setup`; `feature.py github project setup` | `test_setup_discovers_and_reuses_fields_options_and_views`, `test_setup_adds_missing_options_and_never_changes_a_conflicting_field` |
| Status mapping and Done rules (§4) | `ProjectSync.board_status`, `evidence_problem`; claim checkpoint `verifying` | mapping tests above; `test_the_parent_is_never_done_because_its_tasks_are_verified` |
| Item management, ownership, recovery (§5) | `ProjectSync._ensure_item`, `_verify_item`, `ProjectLink`, `reconcile`; `BoardGuard` | crash, lost-response, concurrency, restart, foreign/orphan, guard tests |
| Workflow integration without locks during requests | `workflow.sync_board`, `_publish`; `operations.DetachedPublisher`; `plan` authorizes | `test_no_coordinator_lock_is_held_during_board_requests`, `test_run_task_publishes_from_another_process_without_waiting`, `test_the_publisher_starts_an_independent_process` |
| CLI (§9) | `project_commands.py`: `github project inspect / validate / setup / associate / sync / reconcile / active / task / test`; `github sync` includes the board | `test_cli_project_commands_with_the_local_board` |
| Real integration test (§8) | `live_projects.py`, `test_fc_projects_live.py` (opt-in `FC_GITHUB_PROJECTS_LIVE=1`) | live: 6 tests, 35 checks; rehearsal in the regular suite (`test_projects_rehearsal_with_the_local_tracker`) |

### Changes to existing Phase 4 code

All additive; the Phase 4 test suites pass unchanged.

- `RemoteIssue.node_id` (parsed and validated from REST `node_id`).
- An IssueSync fault point `crash-after-link`.
- `GitHubClient._call(write=…)` (a GraphQL query is a POST but not a write).
- `github sync` also syncs the board (`--no-board` to skip).
- `build_coordinator(tracker_kind=…, publisher=…)`.
- The `project` record kind.
- `GitCoordinator.contains` / `gitops.branch_contains`.
- `run_task` records the `verifying` checkpoint before its gates run.

The local board used with `--tracker local` starts already set up, like the real Project #2.

### Tests executed and observed results

| Command | Result **[V]** |
| --- | --- |
| `.venv/bin/python -m pytest -p no:cacheprovider scripts/tests` (before the extension) | 368 passed, 12 skipped |
| same, final | **429 passed, 18 skipped** (4 Phase 3 opt-in host tests, 8 Phase 4 and 6 Projects opt-in live tests) |
| `FC_GITHUB_PROJECTS_LIVE=1 FC_GITHUB_LIVE_DIR=~/.local/state/mx-admin-agents/github-tests … test_fc_projects_live.py -v` | **6 passed** in 420 s; 35/35 checks |
| `feature.py --tracker local github project test --apply` (rehearsals before going live) | PASSED (33 checks; no GitHub contact) |
| `feature.py github project inspect` / `validate` (read-only, before and after setup) | identity verified, scopes include `project`; NOT READY before setup, READY after |
| `python scripts/check_docs_agents.py` · Pylint (package / tests) · `feature.py config` | 6/6 · 10.00/10 / 10.00/10 · identical |

### Files (committed as `80cb1d6`, C5)

| Status | Path |
| --- | --- |
| new | `scripts/feature_coordinator/{github_projects,projects_local,project_sync,project_commands,live_projects}.py` |
| modified (on top of the uncommitted Phase 4 work) | `scripts/feature_coordinator/{__init__,cli,commands,config,errors,github_api,github_commands,gitops,issue_sync,issues,operations,store,workflow}.py` |
| new | `scripts/tests/feature_coordinator/{test_fc_github_projects,test_fc_project_sync,test_fc_projects_live}.py` |
| new | `docs_agents/coordination/github-projects.md` |
| modified | `docs_agents/coordination/{github-integration,feature-workflow,operations,phase-status}.md`, `docs_agents/coordination/project-config.example.yaml` |
| outside Git | `~/.local/state/mx-admin-agents/github-tests/fc-p4x-20261017090916/` (live test evidence) |

Unchanged **[V]**: `backend/`, `cli/`, `frontend/`, `content/`, `data/`, `scripts/mxfix/`, `scripts/mxissues/`, `.env`, `docker/`, `scripts/deploy.sh`, root instruction files, `docs_agents/architecture/`, `AUTH_DISABLED`/`DEBUG_MODE`; `docs_agents/README.md` was not touched by the extension (its navigation edit belongs to the uncommitted Phase 4 work). No request to the remote Qwen server.

### Remaining limitations

[github-projects §12](github-projects.md#12-limitations):

- **GitHub workflows act asynchronously.** Project #2's built-in workflows changed items and closed Issues on their own; the coordinator repairs this. *(Finalization: the two workflows that did so are off, D-28.)*
- **Review for a task is brief.** It is shown only while a worker verifies its commit, and only through the detached publisher.
- **Filtered board columns.** Whether a filtered board hides its empty columns cannot be set through the API **[U]**.
- **Last writer wins on fields.**
- **One shared account (D-26).** Board edits are never treated as authorization.
- **The architecture page was outdated.** `docs_agents/architecture/README.md` said "No Projects v2 is required". *(Finalization: updated under D-22.)*

## Phase 4 — record (approved 2026-10-17)

**Authorized** 2026-10-17 ("Proceed with Phase 4 ONLY: REAL GITHUB ISSUE COORDINATION AND TASK DEPENDENCIES"), with Phases 0–3 approved and the earlier credential exposure reported resolved by the operator. **Real** GitHub Issue operations were authorized on `spyderbg/mx-admin-cloud` for coordinator-owned test Issues and their `feature:*` labels only; no PRs, commits or pushes. Started on `b3f3691`.
**Design:** [github-integration.md](github-integration.md) · **Lifecycle:** [task-lifecycle.md](task-lifecycle.md) · **Formats:** [templates/](templates/README.md) · **Commands:** [operations §7](operations.md#7-github-issues-phase-4) · [feature-workflow §19](feature-workflow.md#19-phase-4--github-issue-coordination-and-task-dependencies).

### Exit gate (Phase 4 assignment §16)

| # | Criterion | Result **[V]** |
| --- | --- | --- |
| 1 | Real GitHub adapter implemented | **Met.** `github_api.py` (`gh api --include` transport, retrying and validating client) and `issue_sync.py`. No mock was used for the integration test |
| 2 | GitHub authentication verified | **Met.** `feature.py github status`: `gh` 2.86.0, authenticated as `spyderbg`, repository private and not archived, Issues enabled, permissions admin/maintain/push/triage/pull, issues, labels and pulls endpoints OK, rate limit 4995/5000 |
| 3 | A real parent feature Issue is created | **Met.** [#11](https://github.com/spyderbg/mx-admin-cloud/issues/11) |
| 4 | Real component Issues are created | **Met.** [#12](https://github.com/spyderbg/mx-admin-cloud/issues/12) Backend, [#13](https://github.com/spyderbg/mx-admin-cloud/issues/13) CLI, [#14](https://github.com/spyderbg/mx-admin-cloud/issues/14) Frontend |
| 5 | Parent–child relationships recorded | **Met.** Parent task list and dependency graph reference #12–#14; each component names "parent Issue #11" (check C) |
| 6 | Real labels and comments updated | **Met.** Nine `feature:*` labels created; state labels followed every transition; 30 coordinator comments, each with an operation marker (checks H, K, K2, K3) |
| 7 | Duplicate Issue creation prevented | **Met.** A crash was injected right after GitHub created #14; the restarted synchronizer **adopted** #14 instead of creating another (A1–A3). Exactly four owned Issues, one per key (M); a repeated sync sent no write (L) |
| 8 | Missing Backend dependency blocks consumers | **Met.** Both consumers reuse one Backend task (D/E); the real sandboxed `contract-check` failed → dependency unavailable (F); CLI and Frontend blocked (G); `feature:blocked` and comments on #13/#14 (H) |
| 9 | Verified Backend dependency unblocks consumers | **Met.** Backend run → serialized commit `60ac4ce` → real sandboxed `contract-check` and `backend-tests` passed at the branch HEAD → verified → CLI ready (I, J); GitHub updated (K) |
| 10 | Independent tasks remain runnable | **Met.** The Backend task stayed `ready` while consumers were blocked (G); `test_blocked_consumer_does_not_freeze_unrelated_work` |
| 11 | Atomic claiming prevents duplicate execution | **Met.** A duplicate claim and a second Backend writer were rejected (I1); multiprocess races in `test_fc_claims.py` |
| 12 | Restart and reconciliation preserve Issue IDs | **Met.** A new process ran `feature.py github reconcile --apply`: no drift, orphan, duplicate or write; numbers {parent 11, backend 12, cli 13, web 14} unchanged (N, O, P) |
| 13 | GitHub synchronization failures are recoverable | **Met.** Live crash recovery (A1–A3); unit tests for uncertain writes, 5xx, 429 and secondary rate limits, timeouts, crashes after comments, mid-sync unavailability and concurrent sync processes |
| 14 | Existing unrelated Issues remain unchanged | **Met.** All 10 pre-existing Issues/PRs compared before/after (Q), and independently against the snapshot taken at the start of this session: identical `updated_at`, state, labels and comment counts |
| 15 | `mxfix`/`mxissues` remain independent | **Met.** AST import scan; `git status` of both trees empty (Q4); their labels and Issue #8 untouched |
| 16 | Exactly one final PR candidate per feature | **Met.** One candidate `feature/fc-p4-20261017033506` → `dev` at `9671c4e`; a second integration refused (R) |
| 17 | No real PR created | **Met.** The adapter refused `open_pull_request` before any request; no PR for the branch; the repository's PR set is unchanged (R, Q3) |
| 18 | All mandatory local tests pass | **Met.** `scripts/tests`: **368 passed, 12 opt-in skipped** |
| 19 | Real GitHub integration tests pass | **Met.** `FC_GITHUB_LIVE=1 … test_fc_github_live.py`: **8 passed** (256 s); the scenario report records **41/41** checks |
| 20 | Documentation and quality checks pass | **Met.** `check_docs_agents.py` 6/6; Pylint 10.00/10 for the package and the tests; `feature.py config` identical; the containment self-test PASSED |
| 21 | Live test Issues closed or reported | **Met.** #11–#14 each got a final verification comment and were closed as completed; a repeated cleanup sent no write. They remain in GitHub for audit |
| 22 | External actions documented with numbers and URLs | **Met.** The next table |

### Live GitHub actions (all as `spyderbg` through `gh`, 2026-10-17 03:35–03:39 UTC)

| Action | Detail |
| --- | --- |
| Test feature | `fc-p4-20261017033506`; tasks `fc-p4-20261017033506.contract-items-export` (backend), `….cli`, `….web` (frontend) |
| Labels created (missing only) | `feature:backend`, `feature:blocked`, `feature:cli`, `feature:frontend`, `feature:in-progress`, `feature:parent`, `feature:review`, `feature:test`, `feature:verified` |
| Issues created | [#11](https://github.com/spyderbg/mx-admin-cloud/issues/11) "[Feature Coordinator Test] Items export (Phase 4 dependency lifecycle test)"; [#12](https://github.com/spyderbg/mx-admin-cloud/issues/12) "[backend] Items export API contract"; [#13](https://github.com/spyderbg/mx-admin-cloud/issues/13) "[cli] CLI command for items export"; [#14](https://github.com/spyderbg/mx-admin-cloud/issues/14) "[frontend] Frontend export button" (all prefixed "[Feature Coordinator Test]", labeled `feature:test`) |
| Coordinator comments | 30 (#11: 3, #12: 7, #13: 9, #14: 11), including the final verification comment on each |
| Simulated operator actions (after `inspect` confirmed ownership) | On #12: added `feature:verified`, closed it. On #13: removed `feature:blocked`; commented `/feature release …cli` (rejected: a dependency block). On #14: `/feature hold …web waiting for a UX review` (accepted), later `/feature release …web` (accepted). The coordinator restored the labels and reopened #12 |
| Closed | #12, #13, #14, then #11, as completed, after verification |
| Volume | The scenario's client sent 322 requests (103 writes); the restart process and the read-only checks sent more. No rate limit was hit |
| Not done | No pull request, no push, no label edited or deleted, no other Issue touched, no Projects v2 |

Evidence on disk: `~/.local/state/mx-admin-agents/github-tests/fc-p4-20261017033506/report.json` (every check with its detail), `state/events.jsonl` (each sync, command and transition), `state/logs/` (the sandboxed gate logs) and `repo/` (the disposable repository with its single feature branch).

### Implemented components

| Requirement (assignment) | Where | Tests |
| --- | --- | --- |
| Real GitHub adapter (gh/REST), capability checks (§3) | `github_api.py`; `feature.py github status` | `test_fc_github_client.py` (27): fake `gh` executable and scripted transport |
| Issue management: create, retrieve, search, labels, bodies, comments, dependency references, state sync, close, recovery, duplicates (§4) | `issue_sync.py`, `issues.py` | `test_fc_issue_sync.py` (51) |
| Versioned ownership marker, ownership by marker + account + exact identity (§2, §4) | `issues.OwnershipMarker`, `verify_ownership` | `test_ownership_needs_every_proof` (9 cases), `test_forged_and_foreign_issues_are_never_adopted_or_modified` |
| Feature/task structure (§5) | `model.Feature` (+ `test`), `Task`, `IssueLink` | [task-lifecycle §1](task-lifecycle.md#1-records) |
| Dependency lifecycle (§6) | `workflow.request_contract`, `probe_dependency`, `reopen_task`, `_finish`, `verify_task`; `escalation.py` | dependency tests in `test_fc_issue_sync.py`, `test_fc_workflow.py` |
| Bidirectional sync with a command policy, idempotency, outbox, bounded retries (§7) | `issue_sync.py`, `github_api.py` | crash-recovery, command and failure tests |
| Concurrency (§8) | Sync outside coordinator locks; `github-sync.lock` | `test_no_coordinator_lock_is_held_during_tracker_requests`, `test_concurrent_sync_processes_never_create_duplicates`, `test_sync_runs_while_a_worker_holds_its_claim_and_one_sync_at_a_time` |
| One-PR policy without remote PRs (§9) | `GitHubClient.create_pull_request` refuses; `open_pull_request` restructured | `test_the_live_adapter_never_creates_a_pull_request`, existing one-PR tests |
| Real GitHub integration tests (§10, §12) | `live_test.py`, `test_fc_github_live.py` (opt-in) | 8 live tests; a rehearsal against the local tracker in the regular suite |
| Cleanup (§11) | `IssueSync.cleanup_test_feature`; `feature.py github cleanup-test` | `test_cleanup_only_touches_owned_test_issues_and_is_repeatable` |
| CLI (§14) | `github_commands.py`; global `--tracker` | `test_cli_local_tracker_commands` |

### Tests executed and observed results

| Command | Result **[V]** |
| --- | --- |
| `.venv/bin/python -m pytest -p no:cacheprovider scripts/tests` (baseline before any change) | 290 passed, 4 skipped |
| same, final | **368 passed, 12 skipped** (4 Phase 3 opt-in host tests, 8 opt-in live GitHub tests) |
| `… scripts/tests/feature_coordinator` | 338 passed, 12 skipped |
| `FC_GITHUB_LIVE=1 FC_GITHUB_LIVE_DIR=~/.local/state/mx-admin-agents/github-tests … test_fc_github_live.py -v` | **8 passed** in 256 s; scenario 41/41 checks |
| `feature.py --tracker local github test-lifecycle --apply` (rehearsal before going live) | PASSED (39 checks; no GitHub contact). Its first attempt failed only steps N–P because the rehearsal's tracker file was not where the restart process looked; fixed in the command before the live run |
| `python scripts/check_docs_agents.py` | 6/6 |
| `pylint --rcfile=.pylintrc scripts/feature_coordinator scripts/feature.py` / tests (with `PYTHONPATH=scripts:scripts/tests/feature_coordinator`) | 10.00/10 / 10.00/10 |
| `feature.py config` · `feature.py demo` · `feature.py sandbox-check --apply` · `export_openapi.py --docs-agents --check` | identical · 1 PR, all verified, foreign issue untouched · PASSED · 91/91 up to date |
| `git status` of `backend/ cli/ frontend/ content/ data/ .env docker/ scripts/deploy.sh scripts/mxfix scripts/mxissues` | empty |

Not run: backend pytest and the frontend build/tests. No application source changed (B-7 still blocks `backend-tests` on the real checkout).

### Observation from the live run

During step T, the coordinator also reported **drift on the parent #11**, which the test did not edit. Every version in #11's edit history (GraphQL `userContentEdits`) shows `[x] #12` only together with "**verified**", so the coordinator's own writes were consistent. The change was made by GitHub without an edit-history entry. The likely cause is GitHub's task-list behavior — closing a referenced Issue (#12, in step T) checks its box in the parent — but that cause is **[U]**. The synchronizer detected the change and restored the block from the local state, which is the intended behavior. Recorded in [github-integration §14](github-integration.md#14-limitations).

### Fixes and deviations

- **Dependency correctness (Phase 2 behavior changed):** `reopen_task` now also re-blocks *ready* dependents (it blocked only verified ones), and a task can no longer be verified while a dependency is unverified.
- **The workflow no longer calls the tracker inside state transactions.** Phase 2 did, which would have held `coordinator.lock` during network requests; the sync now runs afterwards.
- **`issues.py` rewritten:** the unversioned Phase 2 marker `<!-- feature-coordinator key=… -->` was replaced by the versioned ownership marker, and the mock implements the live protocol. Two Phase 2 tests changed only how they read the mock (`test_contract_requests_are_deduplicated`, `test_single_pr_needs_approval_and_done_needs_a_merge`); their assertions are unchanged.
- **Stale-command check:** first written against the local clock; changed before the live run to compare GitHub comment IDs for holds that came from GitHub, which removes clock-skew risk.

### Files (committed as `ce2d1fc`, C4; file modes fixed in `545b9d3`)

| Status | Path |
| --- | --- |
| new | `scripts/feature_coordinator/{github_api,issue_sync,escalation,live_test,github_commands}.py` |
| modified | `scripts/feature_coordinator/{__init__,cli,commands,config,errors,issues,locks,model,operations,store,workflow}.py` |
| new | `scripts/tests/feature_coordinator/{fc_fake_gh,test_fc_github_client,test_fc_issue_sync,test_fc_github_live}.py` |
| modified | `scripts/tests/feature_coordinator/test_fc_workflow.py` (two mock reads, see above) |
| new | `docs_agents/coordination/{github-integration,task-lifecycle}.md`, `docs_agents/coordination/templates/{README,feature-issue,component-task-issue,progress-comment,command-comments,handoff,pull-request}.md` |
| modified | `docs_agents/coordination/{feature-workflow,operations,phase-status,project-config.example.yaml}`, `docs_agents/README.md` (navigation; listed for update by the Phase 4 instruction), `docs_agents/automation/README.md` |
| outside Git | `~/.local/state/mx-admin-agents/github-tests/fc-p4-20261017033506/` (live test evidence) |

Unchanged **[V]**: `backend/`, `cli/`, `frontend/`, `content/`, `data/`, `scripts/mxfix/`, `scripts/mxissues/`, `.env`, `docker/`, `scripts/deploy.sh`, root instruction files, `docs_agents/architecture/`, `AUTH_DISABLED`/`DEBUG_MODE`. No request to the remote Qwen server; D-15, D-16 and D-18 unchanged.

### Remaining limitations

[github-integration §14](github-integration.md#14-limitations): body updates are last-writer-wins (GitHub has no conditional update); key search depends on the fixed label surviving until a pending creation is recorded; relationships are Issue references, not GitHub sub-issues; coordinator and operator share one account; PRs refused and writes limited to test features until Phase 5 decisions (D-24, D-25).

### Recommended Phase 5 plan (not started)

| Step | Role | Work |
| --- | --- | --- |
| P5-0 | operator | ~~Approve Phase 4; commit it (D-10)~~ (done 2026-10-17); decide D-24 (write scope), D-25 (push + one PR), D-20/D-21 (supervised runs), D-23 (gates); land [B-7](backend-task-b7.md); complete the D-11 bootstrap PR into `dev` so the coordinator exists on the base branch |
| P5-1 | operator + coordination | Choose one small **new** feature that spans Backend, CLI and Frontend (not a bug repair); write its spec (tasks, acceptance criteria, contract, gates) |
| P5-2 | coordination | `plan --apply --tracker github` (real parent and component Issues), `activate` (one `feature/<slug>` from `dev`) |
| P5-3 | backend (Claude Code) | Implement the API with tests, regenerate the service OpenAPI; verified by `contract-check` + `backend-tests` on the shared branch |
| P5-4 | cli (Claude Code), frontend (Codex) | Launched in parallel once the contract is verified (or on independent parts earlier); Frontend regenerates the TS types (destination ownership) |
| P5-5 | coordination | Serialized commits, quiescent integration with the agreed gate set, one PR candidate; with D-25: push and open **one** PR to `dev`; no merge |
| P5-6 | coordination | Record evidence, Issue and PR links in this file; stop for approval |

## Phase 3 — record (approved 2026-10-17; committed as `b3f3691`)

**Authorized** 2026-10-17 ("Proceed with Phase 3 only"), with D-4, D-5, D-17 and D-19 approved. Started on `8454e63`. **Design:** [feature-workflow §17–§18](feature-workflow.md#17-phase-3--runtime-design) · **Enforcement and evidence:** [agent-ownership.md](agent-ownership.md) · **Operations:** [operations.md](operations.md) · **Backend fix:** [backend-task-b7.md](backend-task-b7.md).

### Exit gate (v5 Phase 3)

| Criterion | Result **[V]** |
| --- | --- |
| Backend + Frontend write concurrently in disjoint paths | **Met.** Two real worker processes (the Claude Code and Codex launchers with the fake agent, real bubblewrap) ran at the same time on one shared checkout, proven by a cross-sandbox barrier. Each landed one single-role commit; another role's dirty file was not captured (`test_backend_and_frontend_write_concurrently_and_land_separately`) |
| A second Backend writer is rejected | **Met.** `RoleBusyError` in-process and from a third process while both writers ran |
| Unauthorized symlink, traversal and generated-output writes are rejected | **Met.** The containment matrix (19 write targets × 5 roles, 0 mismatches) plus targeted traversal, symlink and `/etc` tests |
| A Git branch switch is rejected while any writer is active | **Met.** `WritersActiveError` while both sandboxes ran |
| Concurrent commits cannot corrupt the index or capture foreign files | **Met.** Two single-role commits, a clean `git fsck`, the foreign dirty file still uncommitted |
| Full-project cannot overlap scoped writers | **Met.** `claim_full_project` refused, naming both active roles |
| No unproven enforcement assertions | **Met.** Every claim names its test or command; open points are marked **[U]** ([agent-ownership §5](agent-ownership.md#5-residual-risks-operator-decisions-in-phase-status)) |
| Phase 1 and 2 gates still pass | **Met.** `scripts/tests`: **294 collected, 290 passed, 4 opt-in skipped** (stable over repeated runs); `check_docs_agents.py` **6/6**; pylint **10.00/10** (package and tests) |

### Implementation requirements → evidence

| # | Requirement | Where | Evidence **[V]** |
| --- | --- | --- | --- |
| 1 | Real Claude Code and Codex launchers | `launchers.py` | Exact command lines ([feature-workflow §17.2](feature-workflow.md#172-real-launchers-launcherspy)); **approved live smoke runs**: each real CLI created its file in its own sandbox |
| 2 | Verify CLI permission and sandbox flags | `probe_runner()` | Claude Code 2.1.302 and Codex 0.163.0 support every required flag; bypass flags can never be emitted; Codex's own sandbox verified nested (`codex sandbox`, no model) |
| 3 | OS-level write containment (bubblewrap) | `sandbox.py` | bubblewrap 0.9.0 unprivileged; containment matrix; fails closed without bwrap |
| 4 | Enforce directory ownership | sandbox mounts built from `PathPolicy` | Each role writes exactly its own roots; protected, approval and generated-output rules hold at the OS layer |
| 5 | Git metadata read-only for agents | `.git` bound read-only | `commit`, `branch`, `update-ref` and `config` fail inside every sandbox; `status` and `log` work |
| 6 | Only the Git coordinator mutates Git | `gitops.py` (outside sandboxes) | As in item 5, plus Phase 2 serialization tests |
| 7 | Parallel Backend and Frontend | separate worker processes | Parallel exit-gate test |
| 8 | Reject same-role concurrent writers | role `flock` | Parallel test plus `test_fc_claims.py` races |
| 9 | Heartbeat, cancellation, recovery, safe termination | `supervisor.py`, `workflow.py`, `claims.py` | Operator stop, SIGTERM, timeout, SIGKILL then recover then resume, a detached grandchild killed, unresponsive reporting (16 tests) |
| 10 | Integration checks with timeouts and logs | `gates.py` | Sandboxed gates: timeout, logs, offline, read-only (7 tests). Live checkout: `contract-check`, `docs-check` and all five frontend gates **passed** read-only and offline; the checkout was unchanged |
| 11 | Concurrent-agent tests in disposable repositories and isolated state | test fixtures | Every Phase 3 test uses `tmp_path` repositories and state directories, a fake `HOME` and fake credentials |
| 12 | Qwen HTTP transport disabled for live remote execution | `http_transport.py` | `LIVE_REMOTE_ENABLED = False`; remote endpoints refused before DNS or a socket |
| 13 | Qwen tested against a local fake server only | `fc_fake_openai.py` | 23 tests: path joining, headers, streaming with fragmented tool calls, 401/403/429/5xx, invalid JSON, deadline, cancellation, model mismatch, capability validation |
| 14 | mxfix/mxissues independent | — | AST import scan; `git status` empty for both trees |
| 15 | No production data, credential, deployment or auth-default changes | — | `git status` empty for `backend/`, `cli/`, `frontend/`, `content/`, `data/`, `.env`, `docker/`, `scripts/deploy.sh` |
| 16 | No Issues or PRs | — | None created (local JSON tracker only) |
| 17 | No commit or push without approval | — | Nothing committed or pushed in Phase 3 |
| — | D-19 baseline-aware lint | `lint_baseline.py` | 7 tests; live baseline report: Backend **1**, CLI **34** existing findings (matches Phase 0); the policy cannot be relaxed |
| — | B-7 recorded with isolated fixtures | [backend-task-b7.md](backend-task-b7.md) | Pinpointed to one test via a throwaway export in a sandbox; the real `content/` was never touched; `backend-tests` stays **blocked** |

### Security note (operator attention)

During development, a smoke probe deliberately read the runner credential file *inside* the sandbox, to prove what a sandboxed process can see. It **printed the first 90 characters of the real Claude Code OAuth access token into this session's transcript.**

- Since then, every test uses a fake `HOME` with fake credentials.
- The real-credential checks are opt-in and assert only "logged in".
- Recommended: rotate the token (`claude auth logout`, then log in again).

This finding is residual risk 1 in [agent-ownership §5](agent-ownership.md#5-residual-risks-operator-decisions-in-phase-status) (D-20).

**Update (2026-10-17, Phase 4 instruction):** the operator reports the exposure **resolved**. Phase 4 read no credential file and printed no token (`gh auth status` output was shown with its token line redacted; the adapter never calls `gh auth token`).

### Files (committed by the operator as `b3f3691`)

| Status | Path |
| --- | --- |
| new | `scripts/feature_coordinator/{sandbox,supervisor,launchers,gates,lint_baseline,http_transport,capabilities,operations,commands}.py` |
| modified | `scripts/feature_coordinator/{__init__,cli,claims,config,demo,errors,gitops,model,paths,providers,runners,workflow}.py` |
| new | `scripts/tests/feature_coordinator/{fc_fake_agent,fc_fake_openai}.py`, `test_fc_{sandbox,launchers,supervisor,parallel,gates,lint_baseline,http_transport,worker_lifecycle}.py` |
| modified | `scripts/tests/feature_coordinator/{conftest,fc_helpers}.py`, `test_fc_{runners_issues,workflow}.py` |
| new | `docs_agents/coordination/{agent-ownership,operations,backend-task-b7}.md` |
| modified | `docs_agents/coordination/{feature-workflow.md,project-config.example.yaml,phase-status.md}`, `docs_agents/README.md` (navigation; approval-required, updated as plan step P3-7), `docs_agents/automation/README.md` |
| outside Git | `~/.local/state/mx-admin-agents/logs/` (logs of the read-only gate runs) |

Unchanged **[V]**: `backend/`, `cli/`, `frontend/`, `content/`, `data/`, `scripts/mxfix/`, `scripts/mxissues/`, `.env`, `docker/`, `scripts/deploy.sh`, root instruction files, `docs_agents/architecture/` (approval-required; see D-22). No commit, push, Issue, label or PR.

### Deviations and fixes found while testing

- **Codex needs a writable shadow root.** Codex's own sandbox (bubblewrap) mounts guards in its working root, so the repository root inside the sandbox is a private **shadow** directory: read-only for Claude Code and gates, writable but discarded and checked for strays for Codex.
- **Six bugs fixed:**
  - `check_bwrap` did not fail closed for a missing executable;
  - the stream cancellation did not abort `Connection: close` responses;
  - `npm` toolchain binding was wrong (symlinked tools);
  - pre-existing untracked files in a role tree wrongly rejected a run;
  - nested env files were not protected (`**/.env*` added);
  - the coordinator state dir could be exposed through a bound prefix (now masked).
- **The unsandboxed `subprocess_gate_runner` was removed.** Gates always run sandboxed.

## Phase 2 — record (approved 2026-10-17)

**Authorized** 2026-10-16 ("Proceed with Phase 2 ONLY"); started on `12183e0`, interrupted once by a usage limit and resumed without repeating work. **Design and evidence: [`feature-workflow.md`](feature-workflow.md).**

### Exit gate (v5 Phase 2)

| Criterion | Result **[V]** |
| --- | --- |
| Mock feature with Backend/CLI/Frontend tasks on one branch under the one-PR policy | **Met.** `python scripts/feature.py demo` and `test_mock_feature_end_to_end_meets_the_phase2_exit_gate` show: only `dev` and `feature/items-export` exist; 3 serialized single-role commits; 4 tasks verified, including the Backend contract task created when the CLI requested a missing API (CLI blocked, Frontend continued, CLI resumed after the contract was verified on the shared HEAD); 1 PR, with a second refused; a second backend writer rejected; the foreign `mx-auto` issue untouched; the feature ends in `review`, not `done` |
| Remote Qwen provider configuration and its offline/error behavior documented and mocked; no live connection | **Met.** [feature-workflow §9.3–§9.4](feature-workflow.md#93-remote-qwen-profile-exact-v5-values); the default transport refuses every request. **No request was sent to the remote server** |
| No dependent calls to the independent bug systems | **Met.** An AST import scan of the package; `git status` empty for `scripts/mxfix` and `scripts/mxissues`; feature-tracker writes to foreign issues are refused |
| Safety and approval model documented | **Met.** [feature-workflow §14](feature-workflow.md#14-safety-and-approval-model) |
| All Phase 2 tests pass; Phase 1 gates still pass | **Met.** `pytest -p no:cacheprovider scripts/tests`: **204 passed** (30 Phase 1 + 174 coordinator), stable over 5 repeated coordinator runs; `check_docs_agents.py`: **6/6**; pylint 10.00/10 for the package and its tests |

### Implemented components

| Requirement | Where | Tests |
| --- | --- | --- |
| Feature and task state models | `model.py`, `store.py` | `test_fc_model.py` (27) |
| Task dependencies with blocking/resuming | `model.py` (`validate_dag`, `refresh_readiness`, `invalidate_dependents`), `workflow.request_contract` | `test_fc_model.py`, `test_fc_workflow.py` |
| Role-based locking, atomic task claiming | `locks.py`, `claims.py` | `test_fc_claims.py` (14), including multiprocess races |
| Filesystem write-scope validation | `paths.py` | `test_fc_paths.py` (34) |
| Git operation serialization | `gitops.py` | `test_fc_gitops.py` (12), including 3 processes committing in parallel |
| Mock Claude Code / Codex / OpenAI-compatible Qwen runners | `runners.py`, `providers.py` | `test_fc_runners_issues.py` (18), `test_fc_config_providers.py` (31) |
| Mock GitHub issue coordination | `issues.py` | `test_fc_runners_issues.py` |
| Dry-run CLI | `cli.py`, `scripts/feature.py` | `test_cli_demo_and_dry_run_commands`, `test_doctor_apply_creates_the_state_directory_and_proves_flock` |
| Crash and lock recovery | `claims.recover`, `locks.ProcessIdentity` | SIGKILL, zombie, PID-reuse, boot-ID and stale-flag tests in `test_fc_claims.py` and `test_fc_statedir_locks.py` (16) |
| One feature, one branch, one PR | `workflow.py`, `demo.py` | `test_fc_workflow.py` (22) |

### Files (committed by the operator as `8454e63`)

| Status | Path |
| --- | --- |
| new | `scripts/feature.py` |
| new | `scripts/feature_coordinator/{__init__,errors,config,providers,paths,statedir,locks,model,store,claims,gitops,issues,runners,workflow,demo,cli}.py` |
| new | `scripts/tests/feature_coordinator/{conftest,fc_helpers}.py` and `test_fc_{model,config_providers,paths,statedir_locks,claims,gitops,runners_issues,workflow}.py` |
| new | `docs_agents/coordination/feature-workflow.md`, `docs_agents/coordination/project-config.example.yaml` |
| modified | `docs_agents/coordination/phase-status.md` (this record), `docs_agents/README.md` (navigation; an approval-required path, updated as plan step P2-7), `docs_agents/automation/README.md` (tools, tests, open items) |
| outside Git | `~/.local/state/mx-admin-agents/` (empty state directory; mode 0700; created by `feature.py doctor --apply`) |

Unchanged **[V]**: `backend/`, `cli/`, `frontend/`, `content/`, `data/`, `scripts/mxfix/`, `scripts/mxissues/`, `.env`, root instruction files, authentication and debug defaults. No commit, push, Issue, label or PR was created.

### Deviations from the Phase 2 plan

- The planned `state.py` is split into `statedir.py` (location and suitability) and `store.py` (records). `claims.py`, `workflow.py`, `demo.py` and `errors.py` were added.
- A minimal OpenAI-compatible tool loop exists in `runners.py`, but with mock transports only and **no HTTP client** (the plan said "no HTTP client yet"; this is still true).
- Found and fixed while testing:
  - a task could be stranded `in-progress` when a commit was refused or the branch moved between the gates and verification;
  - integration did not wait for quiescence;
  - the `frontend-tests` gate covered only `test:voices` (`test:content` and `test:auth` added);
  - `backend-lint` lacked the documented `PYTHONPATH=src`, and `cli-lint` was missing.

### Remaining limitations

See [feature-workflow §15](feature-workflow.md#15-limitations-as-of-phase-3). In short:

- runners are mocks, and there is no OS containment yet;
- the live model transport is disabled, and streaming, partial tool calls and `apply-patch` are not implemented;
- there is no live GitHub adapter, push or PR;
- gate commands are simulated in tests (`backend-tests` would write into `content/`, B-7);
- tokens are estimated (bytes / 3);
- heartbeats are recorded but not monitored.

## v5 adoption (2026-10-16)

### Operator decisions recorded

| ID | Decision (operator's words, in substance) | Effect |
| --- | --- | --- |
| V5-1 | Adopt v5 as the authoritative specification | v5 governs; all v4 feature-workflow decisions carry over |
| V5-2 | v4 migration report accepted; Phase 0 and Phase 1 approved and must not be repeated | Phases 0–1 **verified and approved**; nothing rerun |
| D-11 | Bootstrap work integrated into `dev` through a **human-reviewed PR before the first feature** | Approved. Requires D-10 (commit) first; not a feature PR |
| D-12 | Coordinator state and `flock` locks outside Git under `$XDG_STATE_HOME/mx-admin-agents/`; verify a suitable local filesystem before relying on `flock` | Approved. Filesystem verified (below) |
| D-13 | `scripts/feature_coordinator/` package, `scripts/feature.py` entry point | Approved |
| D-14 | Qwen is **not** local: a remote inference server behind an OpenAI-compatible Chat Completions API, using the **exact** v5 model profile (context limits, model ID, configurable endpoint, capabilities) | Approved. Profile recorded in [architecture §11.11](../architecture/README.md#1111-remote-openai-compatible-qwen-integration-contract-v5-101) |
| V5-5 | Do not assume tool calling or other advertised features work until validated | All four declared flags (`toolCalling`, `vision`, `streaming`, `thinking`) are recorded as **unvalidated** |
| V5-3 | Keep `mxfix` and `mxissues` fully independent | Re-verified untouched (below) |
| V5-4 | Preserve the one-feature, one-branch, one-final-PR policy | Unchanged (architecture §11) |

D-1 and D-2 hold their **actual operator approval**, which v5 §0.4 asks to record: the operator confirmed both explicitly on 2026-10-16 ("Architectural decisions D-1 and D-2 are confirmed"). They are no longer provisional.

### Handoff verification under v5 (§0.3) — 2026-10-16

| Check | Result |
| --- | --- |
| Branch / HEAD / working tree | `agents` / `e2ddd9c`; only the expected uncommitted bootstrap changes, plus the operator-added v4 and v5 prompt files **[V]** |
| `check_docs_agents.py` | **6/6 pass** **[V]** |
| `export_openapi.py --docs-agents --check` | 9 documents, **91/91** operations, up to date **[V]** |
| `pytest -p no:cacheprovider scripts/tests` | **30 passed** **[V]** |
| Service READMEs | 8 present; the "services" and "providers" checks pass **[V]** |
| `scripts/mxfix/**`, `scripts/mxissues/**`, `backend/`, `cli/`, `frontend/`, `content/`, `data/` | No changes (`git status` empty for these paths) **[V]** |
| `users.db` checksums | Unchanged **[V]** |
| CLI tally (v5 §0.5) | Already resolved from the **underlying matrix**, not inferred: 41 command-covered + 2 auth-flow-covered + 3 broken + 38 gaps + 7 exceptions = 91 ([reconciliation](#cli-coverage-count-reconciliation)) **[V]** |
| Running writers | None started by this session; no writer is needed for documentation updates |

### D-12 filesystem verification (2026-10-16; read-only, nothing created)

| Item | Observed **[V]** |
| --- | --- |
| `XDG_STATE_HOME` | Unset in the shell and in the systemd user environment, so the XDG default `~/.local/state` applies |
| Resolved state directory | `~/.local/state/mx-admin-agents/` (does not exist yet) |
| Filesystem of the existing parent `~/.local/state` | **ext4**, local NVMe, `rw,relatime`; directory mode `0700`, owned by the operator |
| Suitability for `flock(2)` | Suitable: a local native filesystem with full `flock` support. Unlike the checkout's `fuseblk` mount, there is no FUSE layer |
| Still required before relying on `flock` | Coordinator start-up check (reject network/FUSE filesystems, create the directory with mode `0700`) and a two-process contention self-test, demonstrated by Phase 2 tests |
| **Phase 2 result (2026-10-17)** | `feature.py doctor --apply` created `~/.local/state/mx-admin-agents/` (ext4, `0700`) and passed the two-process `flock` self-test **[V]** |

### Remote Qwen facts established (no live request was made)

- The exact operator profile is recorded in [architecture §11.11](../architecture/README.md#1111-remote-openai-compatible-qwen-integration-contract-v5-101):
  - model `Qwen3.6-35B-A3B-Q4_K_M.gguf`, display name "Qwen 3.6 35B A3B";
  - context window 131,072, max output 16,384, declared max input 128,000;
  - **hard input cap 114,688**, **recommended working cap 110,592**;
  - temperature 0.2;
  - client-side edit tools `apply-patch` / `multi-find-replace` / `find-replace`;
  - endpoint configured at runtime through `AGENT_QWEN_BASE_URL` / `AGENT_QWEN_MODEL` / optional `AGENT_QWEN_API_KEY`.
- The endpoint is **plain HTTP**. By default, no source code goes to it until D-15 and D-16 are decided.
- The endpoint's host and port equal the `base_url` of the backend's three enabled `qwen_translate` configs; it shares its IP address with the self-hosted GitLab remote **[V]**. The machine identity is unknown, and it is not conflated with the bug-workflow machine.
- **Not done:** no request was sent to the Qwen server. Capability validation is planned (mock first), and a live smoke test only with approval.

### v5 documentation changes

| File | Change |
| --- | --- |
| `docs_agents/architecture/README.md` | v5 status header; §1 automation runner → remote Qwen preferred; §10 D-3/D-12 updated with the verification result; §11.2 and §11.4 remote-Qwen wiring (v4 "local Qwen" annotated as superseded); **new §11.11 remote Qwen integration contract**; §12 register: V5-1…V5-5, D-11…D-14 approved, new open D-15…D-18 |
| `docs_agents/coordination/phase-status.md` | This section; header/overview; decisions, blockers and the Phase 2–7 plan amended for remote Qwen |
| `docs_agents/README.md` | Governing assignment → v5 (index line only) |
| `docs_agents/architecture/api-contracts.md` | D-2 status line notes the v5 re-affirmation |

## v4 adoption (2026-10-16)

> **Superseded by v5 where they conflict:** v4 is retained as the record of the first migration. Its decisions remain in force except the "local Qwen" wording in D-14, which v5 replaces.

### Operator decisions recorded

| ID | Decision (operator's words, in substance) | Effect |
| --- | --- | --- |
| — | Phase 1 approved | Phase 1 → **verified** |
| D-1, D-2 | Confirmed | Provisional handling becomes final ([api-contracts](../architecture/api-contracts.md)) |
| V4-1 | Adopt the v4 architecture | v4 governs Phases 2–7 |
| V4-2 | Preserve all completed Phase 0 and Phase 1 work; do not repeat either phase | Nothing regenerated or reset |
| V4-3 | Update architecture and coordination documentation to reflect v4 | Done (see "Documentation changes" below) |
| V4-4 | Replace the old Phase 2–7 plan with the v4 feature-development architecture | Done ("Plan for Phases 2–7 (v4)" below); old plan kept under "Superseded" |
| V4-5 | Keep `mxfix` and `mxissues` completely independent; do not modify their code or behavior | Verified untouched (below) |
| V4-6 | Use one shared checkout and one feature branch | Architecture §11.5 |
| V4-7 | Allow parallel agents with non-overlapping write scopes | Architecture §11.5–§11.6; only after Phase 3 proves enforcement |
| V4-8 | Claude Code for Backend and CLI | Architecture §11.4 |
| V4-9 | Codex for Frontend | Architecture §11.4 |
| V4-10 | Agent providers independently configurable | Architecture §11.4 |
| V4-11 | Exactly one final PR per completed feature | Architecture §11.7 |
| V4-12 | No separate Git worktrees for individual agents | Architecture §11.5 |

The full register, including resolved, superseded and open items, is in [architecture §12](../architecture/README.md#12-decision-register).

### Handoff verification (v4 §0.3) — Phase 1 gates re-run, 2026-10-16

| Check | Result |
| --- | --- |
| Branch / HEAD / working tree | `agents` / `e2ddd9c`. Changes are exactly the Phase 0–1 set plus the operator-added `docs/multiple_agents/agents_prompt_v4.md` (untracked) **[V]** |
| `python scripts/check_docs_agents.py` | **6/6 pass** (links, services, providers, openapi, readmes, drift) **[V]** |
| `python scripts/export_openapi.py --docs-agents --check` | 9 documents, 91 operations, up to date **[V]** |
| `python -m pytest -p no:cacheprovider scripts/tests` | **30 passed** **[V]** |
| `scripts/mxfix/**`, `scripts/mxissues/**` | `git diff` and `git status` empty, so untouched **[V]**. The Phase 1 tooling contains no reference to either **[V]** |
| `backend/data/users.db`, `data/users.db` | Checksums unchanged since the Phase 0 baseline **[V]** |
| Discrepancies found | One **reporting** discrepancy in the CLI coverage summary (next section). No documentation or API gate discrepancy |

Re-checked after the v4 documentation edits (final run in "Documentation changes" below).

### CLI coverage count reconciliation

The Phase 1 chat report said "41 covered, 3 broken, 38 gaps, 7 deliberate exceptions, out of 91 operations". Those four numbers add up to **89**, not 91.

**The missing 2** are a fifth category that the report left out: **covered indirectly**. `POST /api/v1/login` and `POST /api/v1/cli-login` have no CLI *command*; they are called by the CLI's authentication flow (`cli/src/kiddo_cli/api/auth.py`). `cli-coverage.md` already listed them as "covered (indirect)", and its per-group table already summed to 91. Only the one-line summary in the report dropped them.

Recomputed independently on 2026-10-16 from the generated specs and the CLI call sites **[V]**: 41 covered + **2 indirect** + 3 broken + 38 gaps + 7 exceptions = **91**. That makes 43 operations reachable from the CLI.

As a cross-check, the CLI makes 44 distinct HTTP calls: 41 match a runtime operation, and 3 hit paths that do not exist. `cli-coverage.md` was updated so its summary states "43 covered = 41 by commands + 2 by the auth flow", which makes the categories add up on their face.

## Phase 1 — record (verified)

### Exit gate

| Gate condition | Status | Evidence |
| --- | --- | --- |
| All verified services documented | **met** | 8 service READMEs with the 9 required sections, plus `api/`, `core/` and `security/` pages and the backend catalog. `check_docs_agents.py` "services" and "providers" pass: every registered provider class and every config file is named **[V]** |
| OpenAPI specs parse and match verified routes | **met** (structural) | 9 generated specs, **91/91** runtime operations, each owned exactly once. `--check` up to date. Matches the legacy prefix split for all 7 shared services. **Full JSON Schema validation not run (B-1)** **[V]** |
| Existing documentation preserved | **met** | One pre-existing file changed (`scripts/export_openapi.py`, +44/−0, code). No pre-existing documentation edited, moved or deleted. All 6 pre-existing READMEs linked **[V]** |
| Broken links / drift addressed or explicitly blocked | **met** | 0 broken links (anchors included). `docs_agents` drift: none. Legacy drift explicitly blocked in [api-contracts](../architecture/api-contracts.md#explicitly-blocked-drift-phase-1) **[V]** |

### Deliverables

| Deliverable | Location |
| --- | --- |
| Agent docs index | [`docs_agents/README.md`](../README.md) |
| Component indexes | [`backend/README.md`](../backend/README.md), [`cli/README.md`](../cli/README.md) (user guide), [`frontend/README.md`](../frontend/README.md), [`automation/README.md`](../automation/README.md) |
| Backend layer pages | [`backend/api/`](../backend/api/README.md) (+ `openapi.yaml`), [`backend/core/`](../backend/core/README.md), [`backend/security/`](../backend/security/README.md) |
| Service docs (8) | `docs_agents/backend/services/{configuration,content,evaluation,logging,text_to_image,text_to_speech,text_translation,voice_optimization}/{README.md,openapi.yaml}` |
| API contract provenance report | [`architecture/api-contracts.md`](../architecture/api-contracts.md) |
| Documentation checks | `scripts/check_docs_agents.py`; `scripts/export_openapi.py --docs-agents --check`; tests in `scripts/tests/` |
| CLI capability matrix (coordination data) | [`coordination/cli-coverage.md`](cli-coverage.md): 91 operations = 43 covered (41 by commands + 2 by the auth flow) + 3 broken + 38 gaps + 7 exceptions |
| D-3 design record | [architecture §10](../architecture/README.md#10-approved-decision-d-3--single-machine-agent-execution) |

### Changed files (Phase 1)

| Role step | Files |
| --- | --- |
| coordination | `docs_agents/architecture/README.md`, `docs_agents/coordination/phase-status.md`, `docs_agents/README.md`, `docs_agents/architecture/api-contracts.md`, `docs_agents/coordination/cli-coverage.md` |
| automation | `scripts/export_openapi.py` (extended: `--docs-agents`, `--check`), `scripts/openapi_docs.py`, `scripts/check_docs_agents.py`, `scripts/tests/{conftest.py,test_openapi_docs.py,test_check_docs_agents.py}`, `docs_agents/automation/README.md` |
| backend | `docs_agents/backend/README.md`, `docs_agents/backend/{api,core,security}/README.md`, `docs_agents/backend/api/openapi.yaml`, `docs_agents/backend/services/*/{README.md,openapi.yaml}` |
| cli | `docs_agents/cli/README.md` |
| frontend | `docs_agents/frontend/README.md` |

No application code (`backend/`, `cli/`, `frontend/`) changed. No content, DB, `.env` or tracked instruction file was touched.

### Commands executed and observed results (Phase 1)

| Command | Result |
| --- | --- |
| `.venv/bin/python -m pytest -p no:cacheprovider scripts/tests` | **30 passed** (18 `test_openapi_docs.py`, 12 `test_check_docs_agents.py`) **[V]** |
| `pylint --rcfile=.pylintrc scripts/openapi_docs.py scripts/check_docs_agents.py scripts/tests/` | **10.00/10** **[V]** |
| `pylint --rcfile=.pylintrc scripts/export_openapi.py` before vs after | Identical message set (6 pre-existing messages) **[V]** |
| Modified `export_openapi.py` default mode and `--all`, run in an isolated export | **Byte-identical** to the pre-change outputs **[V]** |
| `python scripts/export_openapi.py --docs-agents` | 9 documents, 91 operations (api 17, configuration 16, content 37, evaluation 2, logging 8, text_to_image 3, text_to_speech 3, text_translation 3, voice_optimization 2) **[V]** |
| `python scripts/check_docs_agents.py` | **6/6 pass** **[V]**; a negative control detected injected faults **[V]** |
| Backend pytest / frontend build | Not re-run: no application source changed. Phase 0 baselines stand (919 passed; build pass; 42/42) |

### Verified findings recorded in Phase 1 (not fixed)

| ID | Finding | Owner | Evidence |
| --- | --- | --- | --- |
| F-11 | `TTSProvider` docstring claims an `is_active` override that does not exist | backend | **[V]** |
| F-12 | `AudioOptimizerBase` and `EvaluationServiceProvider` use `@abstractmethod` without `ABC` | backend | **[V]** |
| F-13 | `VoiceOptimizationService.optimize()` has no callers | backend | **[S]** |
| F-14 | `core/log_client.py` imports `src.services.logging.routes`; `core/pipeline.py` is almost entirely commented out | backend | **[V]** |
| F-15 | TTS credentials derive from the config file name | backend | **[S]** |
| F-16 | CLI: `KIDDO_TOKEN` silently overrides `--token` | cli | **[S]** |
| F-17 | CLI: `--url` vs `KIDDO_API_BASE_URL` mismatch; Docker `cli` service sets only the latter | cli (+ approval for compose) | **[S]** |
| SEC-11 | CLI `--verbose` prints the token; hard-coded default credentials fallback | cli | **[S]** |
| SEC-12 | nginx proxies `/internal/` from the public frontend port | backend / approval (deploy) | **[S]** |
| N-1 | Vite dev server serves `/content` from disk (dev only) | frontend | **[S]** |
| N-2 | Content registry already uses `RLock` + `fcntl.flock` + a commit marker (precedent for D-3) | — | **[S]** |

## Phase 0 — record (verified, historical)

| Gate condition | Evidence |
| --- | --- |
| Baseline commands and results recorded | Discovery §12 (B1–B15): backend 919 passed; frontend type-check/build pass, 42/42 node tests; backend pylint 10.00/10; CLI pylint 9.62/10; generator drift measured |
| Skipped checks with reasons | Discovery §12 "Not run" |
| Assumptions vs facts | Evidence tags; U-1..U-8 (discovery §14); U-5 resolved by D-3 |
| Application behavior unaltered | Isolated exports; `users.db` checksums unchanged |

> **Superseded by v4:** Phase 0 treated `mxfix`/`mxissues` as the basis for agent coordination (discovery §9.4, architecture §8). v4 classifies them as a separate bug workflow that is not to be used or modified. The Phase 0 *observations* remain valid evidence.

## Issues / PRs

- **Created (Phase 4 live test, coordinator-owned, `feature:test`, all closed as completed after verification, kept for audit):** [#11](https://github.com/spyderbg/mx-admin-cloud/issues/11) parent · [#12](https://github.com/spyderbg/mx-admin-cloud/issues/12) Backend · [#13](https://github.com/spyderbg/mx-admin-cloud/issues/13) CLI · [#14](https://github.com/spyderbg/mx-admin-cloud/issues/14) Frontend. Details in the Phase 4 record.
- **Labels created:** `feature:backend`, `feature:blocked`, `feature:cli`, `feature:frontend`, `feature:in-progress`, `feature:parent`, `feature:review`, `feature:test`, `feature:verified`. No existing label was changed or deleted.
- **Created (Phase 4 extension live test, coordinator-owned, `feature:test`, closed as completed after verification, kept for audit):** [#15](https://github.com/spyderbg/mx-admin-cloud/issues/15) parent · [#16](https://github.com/spyderbg/mx-admin-cloud/issues/16) Backend task. Project #2 items: `PVTI_lAHOACEIPc4BndH-zhBbwdk` (#15, Review), `PVTI_lAHOACEIPc4BndH-zhBbwo8` (#16, Done).
- **Project #2** "mx-admin-cloud automation" configured (Status options, Agent / Feature ID / Blocked Reason, Kanban and Active Tasks views). **Project #1** unchanged.
- **Finalization (2026-10-17):** the Active Tasks filter became `is:open status:…` (D-29). The D-28 behavior check reopened and re-closed #16 (card Review → Done) and #15; both ended as before: closed as completed, #15 Review, #16 Done. #11–#14 stay off the board (D-29). No Issue or PR created.
- **PRs:** none created in any phase. **Pushes:** none by an agent. The operator pushed `agents` to `origin` (up to `0d4d8f5`) on 2026-10-17.
- Pre-existing Issues/PRs #1–#10 (including open #9 "S3 content provider", #10 "Database content provider", and the bug workflow's #8): **unchanged** (`updated_at`, state, labels and comment counts compared before and after) **[V]**.

## Approved decisions

### D-3 — Single-machine agent execution (approved 2026-10-16; confirmed by v4 and v5)

- All agents execute **exclusively on the same Linux machine**. Multi-machine or distributed execution is not required.
- The coordination architecture must use:
  - local OS-level file locking (`flock`);
  - persistent local task state outside Git;
  - one active writer per agent specialization;
  - parallel execution across different specializations;
  - exclusive execution for full-project operations;
  - automatic lock release when a process terminates;
  - recovery mechanisms for interrupted tasks.
- **Do not introduce** Redis, distributed databases, or distributed locking infrastructure.
- Phase 1 complied: no locking code was written. Under v4, `flock` is **demonstrated** in Phase 2 and **implemented and validated** in Phase 3. v4 adds a serialized Git lock (architecture §10).

### D-11, D-12, D-13, D-14 (approved 2026-10-16, v5)

| ID | Approved decision |
| --- | --- |
| D-11 | Bootstrap work → `dev` through a human-reviewed PR before the first feature (not a feature PR) |
| D-12 | State and `flock` locks outside Git under `$XDG_STATE_HOME/mx-admin-agents/` (resolves to `~/.local/state/mx-admin-agents/`; ext4 verified); contention self-test before reliance |
| D-13 | Package `scripts/feature_coordinator/`, entry point `scripts/feature.py` |
| D-14 | Remote Qwen over an OpenAI-compatible Chat Completions API, using the exact v5 profile; declared capabilities unvalidated |

> **Superseded by v5:** the earlier D-14 entry ("Automation runner (local Qwen) — keep the automation role on an approved default until a Qwen tool-capable runner is validated") assumed a local model. The remote topology replaces it. The "validate before use" principle carries over as V5-5 and D-17.

### D-4, D-5, D-17, D-19 (approved 2026-10-17, with the Phase 3 authorization)

| ID | Approved decision |
| --- | --- |
| D-4 | `dev` is the base branch for future `feature/*` branches |
| D-5 | Preserve the existing development authentication and debug defaults; do not modify them in Phase 3. Unattended or parallel agent execution must still use appropriate isolation and security controls (bubblewrap containment, Phase 3) |
| D-17 | A minimal native OpenAI-compatible Qwen adapter and a narrowly scoped tool loop; no general-purpose replacement for Claude Code, Codex or OpenCode. Remote Qwen stays disabled for real source-code transmission until the transport, disclosure, authentication and capability-validation requirements are approved |
| D-19 | Baseline-aware lint gates: existing Pylint findings are reported and do not block; new findings an agent introduces block integration; Pylint is never globally disabled and new failures are never suppressed |

### D-10 (plan), D-22, D-28, D-29 (approved 2026-10-17, Phase 4 finalization)

| ID | Approved decision |
| --- | --- |
| D-10 | Prepare a reviewed commit plan for all completed phases, preferring separate logical commits (Phase 0/1, 2, 3, 4, Projects); **no commit or push yet**. Prepared: the three existing commits plus C4 and C5 ([plan](#commit-plan-d-10--prepared-rehearsed-executed-2026-10-17)). Execution approved and run on 2026-10-17 (C4 `ce2d1fc`, C5 `80cb1d6`); file modes fixed by `545b9d3` on 2026-10-19 ([execution record](#execution-record-d-10)) |
| D-22 | Update `docs_agents/architecture/README.md` and `docs_agents/README.md` for the completed Phase 4 and Projects integration. Done |
| D-28 | Project #2 must not close Issues or overwrite coordinator-managed states; "Auto-close issue" and "Item closed" disabled. Verified |
| D-29 | #11–#14 stay off Project #2; #15–#16 stay in GitHub for audit; completed test tasks excluded from Active Tasks without deleting their Issues. Applied |

### D-1, D-2 (approved 2026-10-16) · D-8 (approved, completed) · V4-1…V4-12, V5-1…V5-5 (approved 2026-10-16)

See the tables above and [architecture §12](../architecture/README.md#12-decision-register).

## Human decisions required

| ID | Decision | Recommendation | Needed by |
| --- | --- | --- | --- |
| D-9 | `backend/docs/openapi.yaml` (stale, 50/91 operations) | Regenerate it as a Backend task, or mark it superseded by `docs_agents/backend/**/openapi.yaml` (README edit needs approval) | Optional |
| D-10 | ~~Commit the bootstrap, Phase 2 and Phase 3 work~~ **Settled by the operator:** `12183e0`, `8454e63`, `b3f3691` on `agents`. **Phase 4 and the extension: executed** 2026-10-17 as C4 `ce2d1fc` and C5 `80cb1d6` ([plan](#commit-plan-d-10--prepared-rehearsed-executed-2026-10-17)); file modes fixed by `545b9d3` on 2026-10-19 ([execution record](#execution-record-d-10)). The operator pushed `agents` up to `0d4d8f5`. `545b9d3` and this status update are not pushed | Push the two local commits with the D-11 PR |
| D-15 | Transport for the remote Qwen endpoint (plain HTTP today) | Secure it (TLS reverse proxy, HTTPS with certificate verification, or a WireGuard/Tailscale tunnel), **or** record an explicit plain-HTTP exception after a network security assessment. Until decided, **no source code or repository context is sent** | Before any live Qwen use with code (Phase 3 at the earliest) |
| D-16 | Disclosure policy for the remote model | Approve which code/context categories may be sent; never `.env`, production data, tokens or whole repositories | Same as D-15 |
| D-18 | Qwen API authentication | Whether the server requires `AGENT_QWEN_API_KEY`; if yes, provision it via a private secret store only | Before any live request |
| D-20 | Runner credentials inside the sandbox | **Isolation implemented in W1 (2026-10-21)**: no login file is mounted and no API key is forwarded; a host-side broker holds the upstream credential and gives the runner a per-run token ([worker-security §4](worker-security.md#4-credential-broker-d-20)). **Still open — the broker's credential source:** (a) `api-key-file` with Anthropic/OpenAI API keys (billed per token; the only documented brokered mode of both CLIs), or (b) keep `none` (real runners unavailable). A Claude or ChatGPT subscription login cannot be brokered with the mechanisms verified in W1. **Potential alternative (not investigated):** official OpenAI documentation now describes ChatGPT plan usage through Sign in with ChatGPT and the Codex app-server; it needs a separate security and compatibility investigation ([worker-security §4.4](worker-security.md#44-credential-sources--and-why-subscriptions-cannot-be-brokered)). Also still: rotate the Claude token exposed in Phase 3 (security note above) | Before any real agent run |
| D-21 | Network policy for runner sandboxes | **Implemented in W1 (2026-10-21), verified offline**: every runner sandbox has its own network namespace whose only route is the broker endpoint; agent commands run in the CLI's nested sandbox (Claude Code strict Bash sandbox, Codex sandbox) ([worker-security §5](worker-security.md#5-network-isolation-d-21)). To approve: the W1 changes | With W1 review |
| D-22 | ~~Update the architecture page and the docs index~~ **Approved and done 2026-10-17** (finalization record) | — | — |
| D-24 | GitHub write scope for Phase 5 | `github.write_scope` is `test-features-only`. Recommended: switch to `all-features` only together with the Phase 5 feature approval (one config line) | Phase 5 |
| D-25 | Push and the single real PR | Recommended for Phase 5: allow the Git coordinator's non-force push of `feature/<slug>` and set `LIVE_PULL_REQUESTS_ENABLED` for **one** PR per feature, each with explicit operator approval; never merge | Phase 5 |
| D-26 | Separate GitHub identity for the coordinator | The coordinator and the operator share the `spyderbg` account; coordinator comments are told apart by their operation marker. Recommended: a dedicated bot account or GitHub App, limited to Issues, labels, and later one PR | Before unattended runs (Phase 7); optional for Phase 5 |
| D-27 | Phase 4 test artifacts | Issues #11–#16 stay for audit (D-29). Still open: keep the nine `feature:*` labels (recommended), and whether to delete the local test directory `~/.local/state/mx-admin-agents/github-tests/` (possible at any time) | Optional |
| D-28 | ~~Project #2 built-in workflows~~ **Approved; verified 2026-10-17** ("Auto-close issue" and "Item closed" off). Still optional: switch off "Pull request linked to issue" and "Pull request merged" before Phase 5 | Optional, before Phase 5 |
| D-29 | ~~Phase 4 test Issues on the board~~ **Approved; applied 2026-10-17**: #11–#14 off the board, #15–#16 kept, Active Tasks shows open Issues only | — | — |
| D-23 | Integration gate set for Phase 5 | The default is `contract-check` + `docs-check`. Recommended: the full catalog ([feature-workflow §11](feature-workflow.md#11-gate-catalog-existing-generic-checks)), with lint gates in D-19 baseline mode and `backend-tests` once B-7 is fixed | Phase 5 |

## Blockers and constraints

| ID | Blocker / constraint | Impact | Resolution path |
| --- | --- | --- | --- |
| B-1 | No OpenAPI validator installed | Specs validated structurally only | Approve a dev-only validator in `scripts/requirements.txt` |
| B-2 | Branch protection/rulesets unavailable (private repo, current plan); no CI test workflow | "No merge without CI/review" is not server-enforced; PR CI evidence will be local-only unless CI is added | Operator choice (plan, visibility, or a procedural gate) plus Phase 6 CI with an approved `.github/` scope |
| B-3 | *(resolved for the feature workflow)* `GITHUB_PAT`/`GITLAB_TOKEN` absent | **Does not block the feature workflow:** the Phase 4 adapter and the Projects board run over the authenticated `gh`. The `gh` login **has** the `project` scope since the operator added it (observed 2026-10-17: `gist, project, read:org, repo, workflow`) | PR creation (Phase 5) needs D-25 |
| B-4 | Docker and Playwright unavailable | e2e not runnable; the integration barrier lacks browser e2e | Use a host with Docker, or approve installing Playwright for native mode |
| B-5 | GitHub→GitLab mirror failing (3/5 runs) | Out of scope for agents | Operator |
| B-6 | ~~Runner permission and sandbox flags unproven~~ **Resolved in Phase 3** **[V]**: flags probed on the installed CLIs, OS containment tested, live smoke runs observed ([agent-ownership §4](agent-ownership.md#4-real-runner-verification-2026-10-17)) | — | — |
| B-7 | Backend test writes into the real `content/` (F-5). **Pinpointed in Phase 3:** `test_delete_item_image_last_remaining_is_allowed` creates `content/.categories_registry.lock` and `content/foods/.items_registry.lock`; it is hidden by `.gitignore` and tolerated on read-only trees. **Re-reviewed 2026-10-17:** unchanged on `HEAD`, `origin/main`, `origin/dev` and `gitlab/dev`; no leaked lock file today | `backend-tests` is **blocked** against real checkouts (`unsafe_gates`); Phase 5 Backend verification would lack the backend suite | [Backend task B-7](backend-task-b7.md): an approved test-only fix into `dev` before the Phase 5 feature, then unblock the gate |
| B-8 | Bootstrap work not on any feature base | Phase 5 cannot start from `dev` with the tooling | D-11 approved (PR into `dev`). The D-10 commits exist, and `agents` is on `origin` up to `0d4d8f5`. Still needed: a push of `545b9d3` and the status update, then the human-reviewed PR `agents` → `dev` |
| B-9 | Remote Qwen endpoint is plain HTTP | No source code may be sent; live validation with code is blocked | D-15 (+ D-16) |
| B-10 | *(narrowed in Phase 3)* D-17 decided and implemented (native tool loop plus a loopback-only HTTP transport, validated against a local fake server). Capabilities are still unvalidated against the real server | Automation cannot use Qwen write-capably | D-15, D-16, D-18, then an approved live validation run |

## Plan for Phases 2–7 (v5) — replaces the v3 plan; v4 plan amended for remote Qwen

Execution remains **sequential and supervised** until Phase 3 proves enforcement. Each phase stops for approval.

### Phase 2 — New Feature Coordinator Design and Minimal Skeleton (revised implementation plan)

**Objective (v5 §11):**

- Document an independent feature/task model, a GitHub Issues adapter, the one-feature-one-branch lifecycle, provider/role adapter configuration **including the remote OpenAI-compatible Qwen**, and the shared-checkout Git mutation service.
- Evaluate generic scripts only.
- Select the local lock/state approach.
- Demonstrate `flock` and **one serialized Git mutation path with mock / dry-run interfaces**.

**Scope guards:**

- No changes to application code, `mxfix` or `mxissues` (no import, call, extension or test).
- No real agent runner invocations and no live GitHub writes.
- **No request to the remote Qwen server.** A live connection is not required for the Phase 2 gate (v5).
- No OS sandbox yet (that is Phase 3).
- Git tests run only in temporary repositories (`git init` under pytest `tmp_path`), never in the live checkout.

| Step | Role | Work | Output |
| --- | --- | --- | --- |
| P2-0 | operator | ~~Decide D-12 and D-13~~ **done (approved 2026-10-16)**. Optionally D-10 (commit bootstrap) before code | Recorded here |
| P2-1 | coordination | `docs_agents/coordination/feature-workflow.md`: feature and task records, legal transitions with idempotency rules, atomic claims, the one-branch/one-PR policy, approval points, safety model, dependency DAG semantics (consumer unblock = Backend commit + `--docs-agents --check` + API tests on the feature HEAD), and explicit boundaries (generic scripts called; bug workflow never touched) | New doc |
| P2-2 | coordination | Schema proposal (versioned JSON for feature/task/claim records) and `docs_agents/coordination/project-config.example.yaml`. It covers: roles → owned/excluded paths, provider + model per role, base/feature branch policy, the D-12 state dir, the Git lock and gate commands. It includes a `remote_qwen` provider of `kind: openai-compatible` with the exact v5 profile values: model ID, 131,072 / 16,384 / 128,000 declared limits, 4,096 margin, **110,592 effective input**, temperature 0.2, edit tools, timeouts and retries, `require_tool_use_validation: true` and `require_tls: true`. Endpoint, model override and key go **only through env-var names** (`AGENT_QWEN_BASE_URL`, `AGENT_QWEN_MODEL`, `AGENT_QWEN_API_KEY`); no literal IP, no credentials | New docs |
| P2-3 | automation | Gate command catalog: the existing *generic* checks a feature must pass — backend pytest/pylint, frontend `type-check`/`build`/`test:*`, `export_openapi.py --docs-agents --check`, `check_docs_agents.py`. Note that `validate_*.py` install dependencies and are therefore not used as-is | Section in `feature-workflow.md` and `docs_agents/automation/README.md` |
| P2-4 | automation | Read-only runner capability probe: `claude --help`, `codex exec --help`; record which permission and sandbox flags exist (B-6). Shortlist local runner candidates for the remote Qwen model (D-17) from documentation and `--help` only. No model calls, no network requests to Qwen, no cost | Recorded in `feature-workflow.md` |
| P2-5 | automation | Minimal stdlib-only skeleton in `scripts/feature_coordinator/` (per D-13): `model.py` (dataclasses + transition table), `state.py` (atomic JSON store: temp file + fsync + rename, schema version), `locks.py` (`flock`: coordinator, project shared/exclusive, role slots, git; liveness via PID + `/proc` start time), `paths.py` (deny-by-default role policy: normalization, symlink resolution, traversal, protected and excluded trees including `scripts/mxfix/**`/`scripts/mxissues/**`), `gitops.py` (serialized commit-by-manifest via private index + `update-ref` compare-and-swap; feature-branch creation gated on exclusive project lock; push/PR as **dry-run renderers only**), `issues.py` (adapter interface + in-memory mock tracker), `runners.py` (runner config + mock runner), `providers.py` (provider-profile model and validation: per-role provider selection, the OpenAI-compatible profile, URL joining, the context budget, the transport/disclosure gate; **no HTTP client yet**), `scripts/feature.py` CLI with `--dry-run` default | New package |
| P2-6 | automation | Tests under `scripts/tests/feature_coordinator/` | See "Phase 2 tests" below |
| P2-7 | coordination | Update `phase-status.md`, `docs_agents/README.md`, `docs_agents/automation/README.md`; run `check_docs_agents.py` (still 6/6) and the full `scripts/tests` suite | Phase 2 report |

**Phase 2 tests (planned):**

- Transitions:
  - legal and illegal transitions;
  - idempotent re-application;
  - `verified` requires evidence bound to a HEAD SHA;
  - feature `done` cannot follow from PR creation alone.
- Claims and locks:
  - atomic claims across processes, so two processes cannot claim one task;
  - a second same-role writer is rejected;
  - two different roles can be claimed concurrently;
  - a full-project claim waits for, or is rejected while, scoped writers hold the project lock shared;
  - the kernel releases `flock` when the holder process dies;
  - a stale record is reclaimed only when its lock is free and the PID/start time no longer matches.
- Path policy:
  - traversal and symlink-escape writes are rejected;
  - protected paths and `scripts/mxfix/**` are rejected for every role;
  - generated-output destination ownership is enforced.
- Git serialization (temporary repos only):
  - two roles' commit requests in parallel are serialized, both land, and neither captures the other's uncommitted file;
  - pre-existing foreign staged content is rejected;
  - HEAD/compare-and-swap mismatch is rejected;
  - a branch switch is rejected while any writer is active;
  - an unstable file hash defers the commit.
- Mock end-to-end feature:
  - one feature, one `feature/<slug>`, Backend/CLI/Frontend tasks;
  - the CLI step is blocked on a Backend contract task, while the Frontend task proceeds;
  - the Backend contract task is verified on the shared HEAD, and the CLI step then resumes;
  - **exactly one** PR candidate is rendered after all tasks are verified, and none before;
  - the mock tracker sees no per-task PRs.
- Independence:
  - a test asserts that the `feature_coordinator` package imports nothing from `mxfix`/`mxissues`/`backend`;
  - `git diff -- scripts/mxfix scripts/mxissues` stays empty.
- State directory (D-12):
  - resolution of `$XDG_STATE_HOME` and its default;
  - rejection of a non-local or FUSE filesystem (simulated);
  - a two-process `flock` contention self-test on the real resolved directory under `~/.local/state`, or on an ext4 temp directory.
- Remote Qwen provider configuration (mocked/offline; v5 Phase 2 gate):
  - the exact model ID is preserved;
  - path joining: the `/v1` base plus `chat.completions` never duplicates `/v1` or `/chat/completions`, and a full-URL input is handled;
  - the effective input cap is computed as 114,688 hard and 110,592 working, and requests above it are refused;
  - declared capabilities stay **disabled until validated**;
  - a plain-HTTP endpoint is rejected for code-bearing requests unless an explicit exception flag is set (D-15);
  - an API key appears only through its env-var name and is redacted in errors;
  - a "provider unavailable" state marks only the Automation role unavailable while Backend/CLI/Frontend tasks continue in the mock feature.

**Phase 2 exit gate (v5):**

- A mock feature with Backend/CLI/Frontend tasks is modeled on one branch with the one-PR policy.
- The mocked remote Qwen provider configuration and its offline/error behavior are documented; no live connection is required.
- There are no dependent calls to the bug systems.
- The safety and approval model is documented.
- All Phase 2 tests pass, and the Phase 1 gates still pass.

### Phase 3 — Role Launchers, Scoped Write Enforcement, Concurrent Shared Checkout

**Implementation plan (refined after Phase 2) — executed 2026-10-17; see the Phase 3 record above.** P3-0 decisions D-5 and D-17 were approved; B-7 is recorded as a Backend task.

| Step | Role | Work |
| --- | --- | --- |
| P3-0 | operator | Approve Phase 2; decide D-5 and D-17; schedule the Backend fix for B-7 (backend tests must not write into `content/`) |
| P3-1 | automation | **Launch profiles:** real `claude` / `codex` adapters that execute the planned invocations from [feature-workflow §17.2](feature-workflow.md#172-real-launchers-launcherspy). Settings files are generated into the state directory, never the repo; per-role tool allowlists; `--setting-sources user,local`; `codex exec --sandbox workspace-write -C <repo>`. Paid runs only with approval |
| P3-2 | automation | **OS containment** with `bwrap` (Landlock as the fallback): the checkout read-only, the role roots read-write, `.git` read-only (Git metadata is mutated only by `GitCoordinator`), a private `/tmp`, no access to the state directory. Negative tests for writes outside the role roots, symlinks, traversal, generated outputs and `.git` |
| P3-3 | automation | **Worker lifecycle:** one process per claim holding its flocks; a heartbeat and checkpoint; cancellation; `feature.py launch\|stop\|resume\|recover`, dry run by default and `--apply` to act |
| P3-4 | automation | **Real gates:** interpreter resolution (repo `.venv`), timeouts, logs captured into the state directory; D-19 for lint gates |
| P3-5 | automation | **Git service under load:** Backend + Frontend writing concurrently in a disposable clone (or an approved test branch), never the live checkout; a branch switch is rejected while writers are active; no foreign capture |
| P3-6 | automation | **Remote Qwen adapter:** a stdlib HTTP client behind `ChatTransport`, tested against a **loopback fake server** first: streaming chunks, partial tool-call assembly, timeouts, cancellation, retries, invalid responses, redaction. Then the capability-validation sequence. A live smoke test only after D-15, D-16, D-18 and approval |
| P3-7 | coordination | `docs_agents/coordination/agent-ownership.md` and the launch/stop/resume guide; the Phase 3 record |

- Launch profiles per role.
- OS containment with `bwrap` (verified usable unprivileged **[V]**), or Landlock: checkout read-only, role roots read-write, `.git` read-only, private `/tmp`, per-role ports.
- Runner permission settings (B-6).
- Heartbeat and recovery.
- The serialized Git service used for real role-scoped commits on a test branch.
- **Remote Qwen adapter (v5):** an OpenAI-compatible Chat Completions client and the chosen local runner (D-17), with mocked tests against a fake server *before* any write-capable use:
  - exact model ID, path joining, `temperature: 0.2`, bounded `max_tokens`, the 110,592 working cap;
  - auth redaction, connect/request timeouts, cancellation, bounded retries and backoff, invalid responses;
  - streaming chunk and partial tool-call assembly, tool-call approval and rejection;
  - client-side edit-tool path enforcement (`apply-patch`, `multi-find-replace`, `find-replace` limited to Automation roots);
  - unavailability isolated to Automation.

  An optional live smoke test runs only after D-15/D-16/D-18 and operator approval, comparing actual behavior with the declared profile.
- **Exit gate:**
  - Backend + Frontend writing concurrently in disjoint paths;
  - a second Backend writer rejected;
  - symlink/traversal/generated-output writes rejected;
  - a branch switch rejected while writers are active;
  - concurrent commits leave no foreign capture;
  - full-project cannot overlap scoped writers.
- Precondition: B-7 (F-5) fixed by Backend; D-5 decided.

### Phase 4 — Feature Task Issue Coordination and Dependencies

- A separate GitHub adapter (no Projects v2) with `feature:*` labels (no collision with `mx-auto`/`source::*`/`severity::*` **[V]**).
- Dependency DAG, deduplication, cycle rejection, role claims, blocked/resume and escalation, with mock tests.
- A live exercise only with approval.
- Deliver `docs_agents/coordination/{task-lifecycle.md,templates/}`.
- **Exit gate:** a missing API creates or reuses one Backend issue; the CLI step waits while unrelated tasks continue; the CLI resumes after a verified Backend contract on the shared feature branch; no per-issue PRs.

**Implementation plan — executed 2026-10-17 with a real GitHub exercise (see the Phase 4 record above).** The operator replaced the mock-only option with an authorized live test on `spyderbg/mx-admin-cloud`.

| Step | Role | Work |
| --- | --- | --- |
| P4-0 | operator | Approve Phase 3; approve the GitHub scope for a live exercise (repository, labels to create, a test issue range), or keep Phase 4 mock-only |
| P4-1 | automation | `GhIssueTracker` behind the existing `IssueTracker` interface, over the authenticated `gh` CLI (scope `repo` [V]), with no Projects v2. Idempotent `ensure_issue` via the hidden key marker; `feature:*` labels only; issues carrying `mx-auto`/`source::*`/`severity::*` stay read-only; rate-limit and offline handling, so that a local PR candidate is kept when GitHub is unavailable. Dry-run rendering by default |
| P4-2 | automation | Recorded `gh` responses (fixtures) for tests; a contract test that runs the existing mock-tracker test cases against both implementations |
| P4-3 | coordination | Wire contract requests to issue links (parent ↔ component, consumer → Backend contract), human escalation (`needs-human` → an issue comment and label) and resume on verification |
| P4-4 | coordination | `docs_agents/coordination/{task-lifecycle.md,templates/}`: the issue and PR body templates already rendered by the coordinator |
| P4-5 | operator + automation | Optional live exercise on approved test issues; then close them |

### Phase 5 — First End-to-End Feature

- An operator-approved **new** feature, not a bug repair.
- One `feature/<slug>` from the approved base (D-4, after D-11).
- Backend (Claude Code), CLI (Claude Code) and Frontend (Codex) in parallel where safe.
- Consistent OpenAPI/TS artifacts, tests and user help.
- Serialized commits, combined tests once all writers are quiescent, then **one** PR (or a local PR candidate if GitHub is unavailable).
- The Phase 0 candidates must be re-evaluated against "new feature, not bug repair":
  - the CLI `config strategy get/set` slice is a new CLI capability, but CLI-only (the API and the Frontend Configuration page already exist);
  - the F-1/F-2 pipeline repair is a repair, and belongs to D-5 or ordinary tasks.

  A feature that genuinely spans Backend, CLI and Frontend should be proposed by or with the operator before Phase 5.

### Phase 6 — CI, API Drift and Final PR Quality Gates

- A quality-gate matrix, a TS↔OpenAPI drift check and the PR checklist.
- Any CI configuration only with an approved `.github/` scope (B-2).
- Report CI limitations precisely.

### Phase 7 — Budget-Aware Optional Continuous Feature Delivery

- Only if separately authorized.
- Features only, with budgets, pause/resume, recovery and provider switching.
- No auto-merge; the bug workflow stays independent.
- The exit gate includes **remote Qwen service unavailability** (v5): the work survives, other roles continue, and there is no hidden fallback or cost.

### Superseded v3 plan for Phases 2–7 (historical; do not execute)

> **Superseded by v4:**
> - Phase 2 was to add pytest suites for `mxissues`/`mxfix` and an extension design (roles/states/dependencies in `mx-meta`, a claim API).
> - Phase 3 was role-slot leases plus a changed-path checker inside `mxfix.review_diff`, with per-task worktrees.
> - Phase 4 was `agent:*`/`ai:*` labels mapped onto `mxissues`.
> - Phase 5 offered three vertical-slice candidates.
> - Phase 6 was CI.
> - Phase 7 was a dry-run scheduler built on `testenv.py run --dry-run`.
>
> v4 forbids building on `mxfix`/`mxissues` and per-task worktrees. The CI and vertical-slice intents carry over into the v4 Phases 5–6 above.

## Documentation changes for v4 adoption (2026-10-16)

| File | Change |
| --- | --- |
| `docs_agents/architecture/README.md` | v4 status header; §1 rewritten to v4 ownership (D-6 resolved), with the v3 table annotated as superseded; §6 D-2 approved; §7 shared-checkout collision table; §8 bug workflow marked superseded and independent; §10 D-3 aligned with v4 (Git lock, state dir); **new §11 v4 design baseline**; **new §12 decision register** |
| `docs_agents/coordination/phase-status.md` | This rewrite: v4 adoption, handoff verification, CLI count reconciliation, Phases 0–1 records retained, v4 Phase 2–7 plan, v3 plan kept under "Superseded" |
| `docs_agents/coordination/cli-coverage.md` | Summary restated as "43 covered (41 commands + 2 auth flow)"; reconciliation note |
| `docs_agents/architecture/api-contracts.md` | D-2 status → approved; consumer gates evaluated on the combined feature branch |
| `docs_agents/automation/README.md` | Bug-workflow tools marked as independent and out of scope; the v3 Phase 2 plan for them withdrawn; Phase 2 coordinator pointer |
| `docs_agents/discovery/README.md` | v4 annotations only (header, §9); historical evidence unchanged |
| `docs_agents/README.md` | Governing assignment → v4; planned pages per the v4 tree |
| `docs_agents/backend/README.md`, `docs_agents/cli/README.md`, `docs_agents/frontend/README.md`, `docs_agents/backend/services/content/README.md` | Header metadata only: v4 owned paths / default runner, D-1 approved, v4 references. Made by the bootstrap docs owner; no technical content changed |

## Exact next action

0. **W1 (2026-10-21):** review the uncommitted changes on `security/worker-d20-d21` ([record](#w1--worker-security-d-20-d-21-record-2026-10-21)); decide the broker credential source (D-20); approve commit and push separately; then, if wanted, the operator-approved real smoke runs through the broker.
1. Operator reviews the [execution record](#execution-record-d-10): C4 `ce2d1fc` and C5 `80cb1d6` (pushed), and the two local commits, the mode fix `545b9d3` and this status update.
2. Operator pushes the two local commits, then the D-11 bootstrap PR `agents` → `dev` (separate approval; human-reviewed, not a feature PR). The Qwen address in `agents_prompt_v5.md` and `docs/multiple_agents/execution/` are already on `origin/agents` since the operator's push; any follow-up is the operator's decision. No PR has been opened.
3. Schedule [Backend task B-7](backend-task-b7.md); decide D-20, D-21, D-23–D-25 (D-26, D-27 optional).
4. Approve a concrete **new feature** for Phase 5 (Backend + CLI + Frontend), then explicitly authorize Phase 5. **Phase 5 has not been started.**

## Resume checklist (for the next session)

- Re-read this file, `docs/multiple_agents/agents_prompt_v5.md`, [`feature-workflow.md`](feature-workflow.md), [`agent-ownership.md`](agent-ownership.md), [`operations.md`](operations.md), then architecture §1, §10, §11 (including §11.11) and §12.
- Run `python scripts/check_docs_agents.py` (expect 6/6), `.venv/bin/python -m pytest -p no:cacheprovider scripts/tests` (expect 430 passed, 18 opt-in skipped), `python scripts/feature.py config` (expect "identical"), `python scripts/feature.py sandbox-check --apply` (expect PASSED), `python scripts/feature.py github status` (expect OK; read-only) and `python scripts/feature.py github project validate` (expect READY; read-only). `github project inspect` should list "Auto-close issue" and "Item closed" as disabled (D-28).
- Confirm `git status --porcelain -- scripts/mxfix scripts/mxissues` is empty, and `git ls-tree -r HEAD | grep -c '^100755'` is 28 (the executable files of `b3f3691`).
- Do not modify `content/`, `data/`, `backend/data/`, `.env`, root instruction files, `mxfix`/`mxissues`, or application code without explicit approval. Do not create Issues (beyond an approved scope), PRs, commits or pushes without approval. Re-read [github-integration.md](github-integration.md) and [github-projects.md](github-projects.md) before any live GitHub operation. Never write Project #1.
- Do not send any request (and never source code) to the remote Qwen endpoint before D-15, D-16 and D-18 are decided and a live test is approved. Never write its literal address or any key into tracked files.
