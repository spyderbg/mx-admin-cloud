# Template — Backend Contract Handoff

> Rendered by the feature coordinator (`issue_sync.py`). This example was produced by the real renderer from the demo feature `items-export` against the mock tracker on 2026-10-17 (`#N` numbers are the mock's). Do not hand-write these Issues: `feature.py github sync FEATURE --apply` creates and maintains them.

When a consumer (CLI or Frontend) needs an API that does not exist, Coordination calls:

```python
coordinator.request_contract(
    "items-export.cmd",                       # the consumer task that waits
    key="items-export-endpoint",              # one Backend task per key, however often it is requested
    title="Items export endpoint",
    contract="GET /api/v1/content/categories/{id}/items/export -> text/csv; auth: require_write_access",
)
```

| Field | Content |
| --- | --- |
| Backend task | `<feature>.contract-<key>`, role `backend`, gates `contract-check` + `backend-tests`, `provides_contract: true` |
| Acceptance criterion | the exact request/response/auth contract text |
| Consumer | gains the dependency; **only it** is blocked ("waiting for …"); unrelated tasks continue |
| Issues | one `feature:backend` Issue (reused on repeat requests); the consumer's Issue lists it under "Depends on", the Backend Issue lists the consumer under "Required by" |
| Unblocking | the Backend task verified on the shared branch: its commit landed and both gates passed at the branch HEAD — never a comment, label, closed Issue or claim |
| Contract change later | `reopen_task` re-blocks every dependent; they resume after the new contract is verified |

The consumer's Issue right after the request:

```markdown
<!-- feature-coordinator:op v1 coordinator=mx-admin-cloud.feature-coordinator key=items-export/items-export.cmd op=h1-2 -->
**Coordinator update** for `items-export.cmd` (state changes 2–2)

- `2026-10-17T03:37:42.503598Z` ready → **blocked**: waiting for items-export.contract-items-export-endpoint
```

The resulting Backend Issue: [component-task-issue.md](component-task-issue.md).
