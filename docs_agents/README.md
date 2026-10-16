# `docs_agents/` — Agent Documentation Index

Central home of all documentation written for and by the multi-agent **feature-development** workflow. The governing assignment is [`docs/multiple_agents/agents_prompt_v5.md`](../docs/multiple_agents/agents_prompt_v5.md), authoritative since 2026-10-16. It supersedes [`agents_prompt_v4.md`](../docs/multiple_agents/agents_prompt_v4.md) (same feature workflow, but Qwen is a **remote** OpenAI-compatible endpoint) and [`agents_prompt_v3.md`](../docs/multiple_agents/agents_prompt_v3.md), under which Phases 0–1 were completed. **Current progress and the next action are always in [`coordination/phase-status.md`](coordination/phase-status.md).**

The v4 model:

- one Linux host, one shared checkout and one `feature/<slug>` branch per feature;
- parallel role writers with disjoint paths;
- one serialized Git coordinator;
- exactly one PR per completed feature ([architecture §11](architecture/README.md#11-v4-feature-development-architecture-design-baseline-for-phases-27)).

The `scripts/mxfix/` and `scripts/mxissues/` bug workflow is a **separate system** and is not part of it.

## Rules for this tree

- **English only.** New agent documentation lives **here**. Pre-existing documentation stays where it is and is linked from this index (below); it is never moved, deleted or bulk-rewritten.
- **Code is authoritative.** Claims carry evidence tags: **[V]** verified by execution, **[S]** verified by reading source or config, **[U]** unverified, with the next verification step named.
- **Generated files are marked.** Every `openapi.yaml` here is produced by `scripts/export_openapi.py --docs-agents` from the runtime FastAPI schema ([contract decision](architecture/api-contracts.md)). Do not edit them by hand.
- **Ownership** follows the subtree (v4 §3):
  - `backend/` → backend role; `cli/` → cli; `frontend/` → frontend; `automation/` → automation; `coordination/` → coordination.
  - `architecture/` and this `README.md` → the designated docs owner during bootstrap, afterwards an assigned coordination or approved integration owner.
  - `discovery/` is historical Phase 0 evidence: annotate it, do not rewrite it.
- **No secrets.** Never paste credentials, tokens or `.env` values into these files.

## Navigation

| Area | Pages |
| --- | --- |
| Discovery (Phase 0 baseline, historical) | [`discovery/README.md`](discovery/README.md) — verified inventory, baseline results, findings, unknowns |
| Architecture | [`architecture/README.md`](architecture/README.md) — boundaries, flows, D-3 single-machine locking, **v4 feature-development design (§11)**, decision register (§12) · [`architecture/api-contracts.md`](architecture/api-contracts.md) — contract authority, artifacts, pipeline |
| Backend | [`backend/README.md`](backend/README.md) (catalog) · [`backend/api/`](backend/api/README.md) · [`backend/core/`](backend/core/README.md) · [`backend/security/`](backend/security/README.md) |
| Backend services | [`configuration`](backend/services/configuration/README.md) · [`content`](backend/services/content/README.md) · [`evaluation`](backend/services/evaluation/README.md) · [`logging`](backend/services/logging/README.md) · [`text_to_image`](backend/services/text_to_image/README.md) · [`text_to_speech`](backend/services/text_to_speech/README.md) · [`text_translation`](backend/services/text_translation/README.md) · [`voice_optimization`](backend/services/voice_optimization/README.md) — each with a generated `openapi.yaml` |
| CLI | [`cli/README.md`](cli/README.md) — user guide |
| Frontend | [`frontend/README.md`](frontend/README.md) — structure, data layer, cache layers, tests |
| Automation | [`automation/README.md`](automation/README.md) — generic tools by side effect (incl. the feature coordinator CLI), the independent bug workflow (observe only), tests |
| Coordination | [`coordination/phase-status.md`](coordination/phase-status.md) — phase state, v4 adoption, decisions, blockers, Phase 2–7 plan · [`coordination/feature-workflow.md`](coordination/feature-workflow.md) — the feature coordinator: data model, state machines, locks, Git protocol, providers incl. remote Qwen, safety model (Phase 2) · [`coordination/project-config.example.yaml`](coordination/project-config.example.yaml) — the documented coordinator configuration · [`coordination/cli-coverage.md`](coordination/cli-coverage.md) — API-to-CLI capability matrix |

Planned later per the v4 layout (not created as placeholders; the Phase 2 pages above now exist):

- Phase 3: `coordination/agent-ownership.md` and the launch/stop/resume operations guide.
- Phase 4: `coordination/{README.md,task-lifecycle.md,templates/}`.
- Generic tests: `automation/testing.md`.
- A Frontend task: `frontend/caching.md` and `frontend/help/`.

> **Superseded by v4:** the v3 plan listed `automation/{mxfix,mxissues}.md` audits for Phase 2. They are withdrawn because those tools are an independent system.

## Pre-existing documentation (preserved in place)

| Component | READMEs | Other documentation |
| --- | --- | --- |
| Repository | [`README.md`](../README.md) | [`CLAUDE.md`](../CLAUDE.md), [`AGENTS.md`](../AGENTS.md), [`docs/required_endpoints.md`](../docs/required_endpoints.md) (legacy endpoint wish list), [`docs/backend_first_build.md`](../docs/backend_first_build.md), [`docs/game_plugin_generate.md`](../docs/game_plugin_generate.md), [`docs/content_service_features/categories_features.md`](../docs/content_service_features/categories_features.md), [`docs/item_voices/initial_spec.md`](../docs/item_voices/initial_spec.md), [`docs/item_voices/initial_result.md`](../docs/item_voices/initial_result.md), [`docs/multiple_agents/`](../docs/multiple_agents/agents_init.md) |
| Backend | [`backend/README.md`](../backend/README.md) | [`backend/CLAUDE.md`](../backend/CLAUDE.md), [`backend/code_improvements.md`](../backend/code_improvements.md), [`backend/docs/openapi.yaml`](../backend/docs/openapi.yaml) (stale snapshot) |
| CLI | [`cli/README.md`](../cli/README.md) | [`cli/CLAUDE.md`](../cli/CLAUDE.md) |
| Frontend | [`frontend/README.md`](../frontend/README.md) | [`frontend/CLAUDE.md`](../frontend/CLAUDE.md), [`frontend/code_improvements.md`](../frontend/code_improvements.md), [`frontend/docs/`](../frontend/docs/types-usage.md) (`types-usage.md`, `item-voices-api.md`, `backend-regeneration-agent-tasks.md`, legacy `openapi.yaml`) |
| Test environment and automation | [`test_environment/README.md`](../test_environment/README.md) | [`docs/test_environment/implementation_plan.md`](../docs/test_environment/implementation_plan.md), [`docs/test_environment/initial_spec.md`](../docs/test_environment/initial_spec.md), [`docs/test_environment/what_next.md`](../docs/test_environment/what_next.md), [`testenv.toml`](../testenv.toml) |
| Docker | [`docker/README.md`](../docker/README.md) | — |

Existing agent-tool configuration (rules, prompts, plans) lives in `.claude/`, `.cursor/`, `.opencode/`, `.aiassistant/`, `.agents/`, `backend/.claude/`, `backend/.cursor/` and `frontend/.cursor/`. It is preserved and not indexed page by page.

## Validating this tree

```bash
python scripts/check_docs_agents.py                     # all checks (read-only)
python scripts/export_openapi.py --docs-agents --check  # contract drift only
python -m pytest -p no:cacheprovider scripts/tests      # tests of the docs tooling and the feature coordinator
python scripts/feature.py config                        # the example configuration equals the built-in one
```
