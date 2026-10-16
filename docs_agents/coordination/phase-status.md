# Phase Status — Multi-Agent Development (v5)

> Durable progress record. **Governing assignment: [`docs/multiple_agents/agents_prompt_v5.md`](../../docs/multiple_agents/agents_prompt_v5.md)** (authoritative since 2026-10-16). It supersedes [`agents_prompt_v4.md`](../../docs/multiple_agents/agents_prompt_v4.md) (adopted and reported earlier the same day; its feature-workflow decisions all carry over) and [`agents_prompt_v3.md`](../../docs/multiple_agents/agents_prompt_v3.md), under which Phases 0–1 were executed and are preserved as-is. Re-read this file before resuming; chat history is not the record.
> Index: [`../README.md`](../README.md) · Evidence: [`../discovery/README.md`](../discovery/README.md) · Architecture and v4 design: [`../architecture/README.md`](../architecture/README.md) · Contracts: [`../architecture/api-contracts.md`](../architecture/api-contracts.md)

| Field | Value |
| --- | --- |
| Current phase | **Phase 2 — New Feature Coordinator Design and Minimal Skeleton: implemented and tested** |
| State | **awaiting-approval** (Phase 2 review; Phase 3 not started) |
| Execution policy | Supervised, phase by phase. No autonomous phased execution. Phase 2 authorized 2026-10-16 ("Proceed with Phase 2 ONLY … Stop after Phase 2. Do NOT start Phase 3 automatically") |
| Code base | Branch `agents`, `HEAD` = `12183e0` (the operator committed Phases 0–1 + v4/v5 docs; not pushed, 2 ahead of `origin/agents`). All Phase 2 work is **uncommitted** (list below) |
| Last updated | 2026-10-17 |
| Active writers / locks | None running. `flock` locks and claims are implemented and tested in Phase 2 (mock runners only); the real state directory `~/.local/state/mx-admin-agents/` exists and holds no features or claims |

## Phase overview

| Phase (v5) | Title | State | Gate summary |
| --- | --- | --- | --- |
| 0 | Inspect and Baseline (historical, v3) | **verified** | Approved by the operator (2026-10-16, v5 instruction); not to be rerun |
| 1 | Documentation Foundation (v3 scope, preserved) | **verified** | Approved by the operator 2026-10-16 (twice); v4 and v5 handoff gates re-run and passing (below) |
| 2 | **New Feature Coordinator Design and Minimal Skeleton** (incl. remote Qwen provider configuration, mocked) | **awaiting-approval** | Exit gate met with evidence (record below); stop for operator review |
| 3 | Role Launchers, Scoped Write Enforcement, Concurrent Shared Checkout (+ remote Qwen adapter mock tests) | not-started | — |
| 4 | Feature Task Issue Coordination and Dependencies | not-started | Live GitHub writes only with approval |
| 5 | First End-to-End Feature (vertical slice) | not-started | Needs a feature approval and D-4/D-11 |
| 6 | CI, API Drift and Final PR Quality Gates | not-started | Constrained by B-2 |
| 7 | Budget-Aware Optional Continuous Feature Delivery | not-started | Needs separate authorization |

> **Superseded by v4 (historical v3 phase titles):** v3 Phase 2 was "Existing Automation Audit and Minimal Extensions" of `mxfix`/`mxissues`; v3 Phase 3 was "Agent Profiles and Scope Enforcement" with per-task worktrees; v3 Phase 4 was "GitHub Issue Coordination" extending `mxissues`; v3 Phases 5–7 assumed per-task PRs. All of these are replaced by the v4 titles above.

## Phase 2 — record (awaiting approval, 2026-10-17)

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

### Files (all uncommitted)

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

See [feature-workflow §15](feature-workflow.md#15-limitations-of-the-phase-2-skeleton). In short:

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

- Created: **none**. No GitHub Issue, label, PR or push in any phase so far (Phase 2 used the mock tracker only).
- Pre-existing open issues (untouched): #9 "S3 content provider", #10 "Database content provider" on `spyderbg/mx-admin-cloud`.

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

### D-1, D-2 (approved 2026-10-16) · D-8 (approved, completed) · V4-1…V4-12, V5-1…V5-5 (approved 2026-10-16)

See the tables above and [architecture §12](../architecture/README.md#12-decision-register).

## Human decisions required

| ID | Decision | Recommendation | Needed by |
| --- | --- | --- | --- |
| D-4 | Feature base branch | **Confirm `dev`.** D-11 already makes `dev` the bootstrap integration target. Features reach `main` only via a human-reviewed `dev→main` PR | Phase 5 |
| D-5 | Priority of the security remediation (SEC-1..SEC-6, SEC-11, SEC-12) | Open Backend-owned feature/security tasks **before** any unattended or parallel write-capable run; confirm U-1 on the deploy host without sharing values | Before Phase 3 parallel runs |
| D-9 | `backend/docs/openapi.yaml` (stale, 50/91 operations) | Regenerate it as a Backend task, or mark it superseded by `docs_agents/backend/**/openapi.yaml` (README edit needs approval) | Optional |
| D-10 | ~~When and how to commit the bootstrap work~~ **Settled by the operator:** one commit `12183e0` on `agents` (not pushed). Still review the literal Qwen address in `agents_prompt_v5.md` before the files leave this private repository (v5 §10.1.1). New: how to commit the Phase 2 work (recommended: one reviewed commit on `agents`, coordinator + tests + docs) | Before the D-11 PR |
| D-15 | Transport for the remote Qwen endpoint (plain HTTP today) | Secure it (TLS reverse proxy, HTTPS with certificate verification, or a WireGuard/Tailscale tunnel), **or** record an explicit plain-HTTP exception after a network security assessment. Until decided, **no source code or repository context is sent** | Before any live Qwen use with code (Phase 3 at the earliest) |
| D-16 | Disclosure policy for the remote model | Approve which code/context categories may be sent; never `.env`, production data, tokens or whole repositories | Same as D-15 |
| D-17 | Local runner for the remote Qwen model | **Phase 2 shortlist:** (1) the coordinator's own Chat Completions tool loop (implemented against mocks; recommended), (2) Codex with a custom provider via `-c` [U], (3) OpenCode with a custom provider [U]. Validate the choice with a mock server in Phase 3; until then Automation is read-only (`advisory`) or uses an approved default runner | Phase 3 |
| D-18 | Qwen API authentication | Whether the server requires `AGENT_QWEN_API_KEY`; if yes, provision it via a private secret store only | Before any live request |
| D-19 | Gate set for the integration barrier | Phase 2 defaults to `contract-check` + `docs-check`. Recommended for Phase 5: the full catalog ([feature-workflow §11](feature-workflow.md#11-gate-catalog-existing-generic-checks)) once B-7 is fixed; decide whether the lint gates block: on the unchanged baseline `backend-lint` exits 8 (10.00/10, one `R0915`) and `cli-lint` exits 28 (9.62/10, 34 messages) per Phase 0 B3/B4 | Phase 5 |

## Blockers and constraints

| ID | Blocker / constraint | Impact | Resolution path |
| --- | --- | --- | --- |
| B-1 | No OpenAPI validator installed | Specs validated structurally only | Approve a dev-only validator in `scripts/requirements.txt` |
| B-2 | Branch protection/rulesets unavailable (private repo, current plan); no CI test workflow | "No merge without CI/review" is not server-enforced; PR CI evidence will be local-only unless CI is added | Operator choice (plan, visibility, or a procedural gate) plus Phase 6 CI with an approved `.github/` scope |
| B-3 | *(narrowed under v4)* `GITHUB_PAT`/`GITLAB_TOKEN` absent; `gh` lacks `project` scopes | **No longer blocks the feature workflow**: the v4 adapter can use the authenticated `gh` (scope `repo` **[V]**) and needs no Projects v2. It still affects only the separate bug workflow | Live feature Issue/PR writes still need operator approval (Phase 4/5) |
| B-4 | Docker and Playwright unavailable | e2e not runnable; the integration barrier lacks browser e2e | Use a host with Docker, or approve installing Playwright for native mode |
| B-5 | GitHub→GitLab mirror failing (3/5 runs) | Out of scope for agents | Operator |
| B-6 | *(narrowed in Phase 2)* The flags exist **[V]** (`--setting-sources user,local` skips the repo's `bypassPermissions` project settings; `codex exec --sandbox workspace-write`), but their enforcement is unproven | Phase 3 containment depends on them | Phase 3 negative tests with real, approved runner invocations |
| B-7 | Backend test writes into the real `content/` (F-5) | Under a role sandbox it will fail (correctly); unsandboxed it touches protected paths | Backend task before Phase 3 parallel gates or Phase 5 |
| B-8 | Bootstrap work not on any feature base | Phase 5 cannot start from `dev` with the tooling | D-11 approved (PR into `dev`); still needs D-10 (commit) and then the human-reviewed PR |
| B-9 | Remote Qwen endpoint is plain HTTP | No source code may be sent; live validation with code is blocked | D-15 (+ D-16) |
| B-10 | No local runner selected for the remote Qwen model; declared tool calling/streaming/thinking/vision unvalidated | Automation cannot use Qwen write-capably | D-17, then the §11.11 validation sequence (mock first) |

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

**Recommended implementation plan (refined after Phase 2; not started):**

| Step | Role | Work |
| --- | --- | --- |
| P3-0 | operator | Approve Phase 2; decide D-5 and D-17; schedule the Backend fix for B-7 (backend tests must not write into `content/`) |
| P3-1 | automation | **Launch profiles:** real `claude` / `codex` adapters that execute the planned invocations from [feature-workflow §9.1](feature-workflow.md#91-runners-phase-2-mocks-only). Settings files are generated into the state directory, never the repo; per-role tool allowlists; `--setting-sources user,local`; `codex exec --sandbox workspace-write -C <repo>`. Paid runs only with approval |
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

1. Operator reviews the Phase 2 record above, [`feature-workflow.md`](feature-workflow.md) and the uncommitted files (`git status`), and runs `python scripts/feature.py demo` if desired.
2. Operator decides how to commit the Phase 2 work (D-10 follow-up). Nothing has been committed, pushed or opened.
3. Before Phase 3 write-capable runs: D-5 (security priority) and the Backend fix for B-7. Before any live Qwen request: D-15, D-16 and D-18. For Phase 3 itself: confirm D-17 (recommended: the coordinator's own tool loop).
4. Operator explicitly authorizes Phase 3 (supervised). **Phase 3 has not been started.**

## Resume checklist (for the next session)

- Re-read this file, `docs/multiple_agents/agents_prompt_v5.md`, [`feature-workflow.md`](feature-workflow.md), then architecture §1, §10, §11 (including §11.11) and §12.
- Run `python scripts/check_docs_agents.py` (expect 6/6), `.venv/bin/python -m pytest -p no:cacheprovider scripts/tests` (expect 204 passed) and `python scripts/feature.py config` (expect "identical").
- Confirm `git status --porcelain -- scripts/mxfix scripts/mxissues` is empty.
- Do not modify `content/`, `data/`, `backend/data/`, `.env`, root instruction files, `mxfix`/`mxissues`, or application code without explicit approval. Do not create Issues, PRs, commits or pushes without approval.
- Do not send any request (and never source code) to the remote Qwen endpoint before D-15, D-16 and D-18 are decided and a live test is approved. Never write its literal address or any key into tracked files.
