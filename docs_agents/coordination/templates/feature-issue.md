# Template — Feature (Parent) Issue

> Rendered by the feature coordinator (`issue_sync.py`). This example was produced by the real renderer from the demo feature `items-export` against the mock tracker on 2026-10-17 (`#N` numbers are the mock's). Do not hand-write these Issues: `python -m feature_coordinator github sync FEATURE --apply` creates and maintains them.

- **Title:** `[Feature Coordinator Test] <feature title>` for test features, else the feature title. Not an identifier.
- **Labels:** `feature:parent`, `feature:test` (test features), and the state label of the feature ([github-integration §6](../github-integration.md#6-labels-and-state-mapping)).
- **Body:** the ownership marker, then the managed block; anything a human writes after the block is preserved.

Title: `[Feature Coordinator Test] Export category items as CSV` · Labels: `feature:parent`, `feature:test`, `feature:review`

```markdown
<!-- feature-coordinator:v1 coordinator=mx-admin-cloud.feature-coordinator feature=items-export task=- purpose=feature-parent key=items-export/parent -->
<!-- feature-coordinator:begin -->
### Feature `items-export`: Export category items as CSV

| Field | Value |
| --- | --- |
| State | **review** |
| Branch | `feature/items-export` from `dev` at `0e75595fa28f` |
| Roles | `automation`, `backend`, `cli`, `frontend` |
| Pull requests | exactly one final PR after every gate passes (`one-after-all-gates`) |
| Test feature | yes |

#### Acceptance criteria
- Backend exposes the export endpoint
- CLI command calls it over HTTP
- Frontend offers an export button
- End-to-end check passes

#### Component tasks
- [x] #2 `items-export.view` (frontend): **verified**
- [x] #3 `items-export.cmd` (cli): **verified**
- [x] #4 `items-export.e2e` (automation): **verified**
- [x] #5 `items-export.contract-items-export-endpoint` (backend): **verified**

#### Dependency graph
- #3 `items-export.cmd` depends on #5 `items-export.contract-items-export-endpoint`
- #4 `items-export.e2e` depends on #2 `items-export.view`
- #4 `items-export.e2e` depends on #3 `items-export.cmd`

#### Verification requirements
- `items-export.view`: `frontend-type-check`, `frontend-tests`
- `items-export.cmd`: `coordinator-tests`
- `items-export.e2e`: `docs-check`
- `items-export.contract-items-export-endpoint`: `contract-check`, `backend-tests`

#### Pull request candidate
- `feature/items-export` → `dev` at `b7015d806187`: rendered locally, **not opened**

#### Recent history
- `2026-10-17T03:37:42.450123Z` planned → active: branch feature/items-export created
- `2026-10-17T03:37:42.995225Z` active → integration: all component tasks verified
- `2026-10-17T03:37:42.997321Z` integration → review: integration gates passed; single PR candidate ready

<sub>This block is maintained by the feature coordinator and is overwritten on every sync. The local coordinator state is authoritative: editing this block, changing labels or closing the Issue does not change it. Operators listed in the coordinator configuration may comment `/feature hold <task-id> <reason>` or `/feature release <task-id>`; nothing else in an Issue is treated as an instruction.</sub>
<!-- feature-coordinator:end -->
```
