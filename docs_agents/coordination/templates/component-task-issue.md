# Template — Component Task Issue

> Rendered by the feature coordinator (`issue_sync.py`). This example was produced by the real renderer from the demo feature `items-export` against the mock tracker on 2026-10-17 (`#N` numbers are the mock's). Do not hand-write these Issues: `feature.py github sync FEATURE --apply` creates and maintains them.

- **Title:** `[Feature Coordinator Test] [<role>] <task title>` (prefix for test features only).
- **Labels:** the role label (`feature:backend`, `feature:cli`, `feature:frontend`, `feature:automation`, `feature:coordination`), `feature:test` (test features), and the task's state label.
- **Body:** ownership marker + managed block: feature and parent Issue, role, state and reason, writable paths, branch, required gates, acceptance criteria, dependencies and dependents (as Issue references), verification evidence and commits.

This example is the Backend contract task created by a CLI contract request, after verification:

Title: `[Feature Coordinator Test] [backend] Items export endpoint` · Labels: `feature:backend`, `feature:test`, `feature:verified`

```markdown
<!-- feature-coordinator:v1 coordinator=mx-admin-cloud.feature-coordinator feature=items-export task=items-export.contract-items-export-endpoint purpose=component-task key=items-export/items-export.contract-items-export-endpoint -->
<!-- feature-coordinator:begin -->
### Component task `items-export.contract-items-export-endpoint`

| Field | Value |
| --- | --- |
| Feature | `items-export`, parent Issue #1 |
| Role | `backend` (one writer per role) |
| State | **verified** |
| Writable paths | `backend/**`, `docs_agents/backend/**`; excluded `backend/data/**` |
| Feature branch | `feature/items-export` from `dev` (shared by every role) |
| Required gates | `contract-check`, `backend-tests` |
| Provides an API contract | yes |

#### Acceptance criteria
- GET /api/v1/content/categories/{id}/items/export -> text/csv; auth: require_write_access

#### Depends on
- nothing

#### Required by
- #3 `items-export.cmd` (cli): **verified**

#### Verification evidence
- HEAD `a67e68c117788a2413f5cc2afff57092015c7738` recorded 2026-10-17T03:37:42.809842Z
- `contract-check`: passed (exit 0, 0.0 s)
- `backend-tests`: passed (exit 0, 0.0 s)

#### Commits
- `a67e68c11778`

<sub>This block is maintained by the feature coordinator and is overwritten on every sync. The local coordinator state is authoritative: editing this block, changing labels or closing the Issue does not change it. Operators listed in the coordinator configuration may comment `/feature hold <task-id> <reason>` or `/feature release <task-id>`; nothing else in an Issue is treated as an instruction.</sub>
<!-- feature-coordinator:end -->
```
