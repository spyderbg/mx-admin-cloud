# Template — The Single Feature PR (Candidate)

> Rendered by the feature coordinator (`issue_sync.py`). This example was produced by the real renderer from the demo feature `items-export` against the mock tracker on 2026-10-17 (`#N` numbers are the mock's). Do not hand-write these Issues: `feature.py github sync FEATURE --apply` creates and maintains them.

`integrate()` renders exactly one PR candidate per feature after every component task is verified and the integration gates pass at the final HEAD (quiescent barrier). In Phase 4 it is **never opened**: the GitHub adapter refuses `create_pull_request`. When a later phase enables PR creation with operator approval, this body is used for the one PR `feature/<slug>` → base.

```markdown
Feature `items-export`: Export category items as CSV

Parent issue: #1

## Component tasks
- `items-export.view` (frontend) — issue #2, commits 903e02a346a2
- `items-export.cmd` (cli) — issue #3, commits b7015d806187
- `items-export.e2e` (automation) — issue #4, commits none
- `items-export.contract-items-export-endpoint` (backend) — issue #5, commits a67e68c11778

## Acceptance criteria
- Backend exposes the export endpoint
- CLI command calls it over HTTP
- Frontend offers an export button
- End-to-end check passes

## Changed paths
- `backend/src/app.py`
- `cli/src/commands.py`
- `docs_agents/backend/services/content/export.md`
- `frontend/src/view.ts`

## Integration gates at `b7015d806187`
- contract-check: pass
- docs-check: pass

## CI
Remote CI: pending / not configured (branch protection is unavailable on the current plan).

Single aggregate PR for this feature. Do not merge without human review.
```
