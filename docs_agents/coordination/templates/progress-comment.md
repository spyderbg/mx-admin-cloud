# Template — Progress Comments

> Rendered by the feature coordinator (`issue_sync.py`). This example was produced by the real renderer from the demo feature `items-export` against the mock tracker on 2026-10-17 (`#N` numbers are the mock's). Do not hand-write these Issues: `python -m feature_coordinator github sync FEATURE --apply` creates and maintains them.

Every coordinator comment starts with an operation marker (`feature-coordinator:op`). Its operation ID makes a retry after a crash find the comment instead of posting it twice ([github-integration §8](../github-integration.md#8-idempotency-and-recovery)).

## State changes (`op=h<start>-<end>`)

One comment per sync for the record's new state-history entries:

```markdown
<!-- feature-coordinator:op v1 coordinator=mx-admin-cloud.feature-coordinator key=items-export/items-export.cmd op=h1-2 -->
**Coordinator update** for `items-export.cmd` (state changes 2–2)

- `2026-10-17T03:37:42.503598Z` ready → **blocked**: waiting for items-export.contract-items-export-endpoint
```

A verification includes its evidence:

```markdown
<!-- feature-coordinator:op v1 coordinator=mx-admin-cloud.feature-coordinator key=items-export/items-export.view op=h3-5 -->
**Coordinator update** for `items-export.view` (state changes 4–5)

- `2026-10-17T03:37:42.698754Z` ready → **in-progress**: claimed by pid 1116865
- `2026-10-17T03:37:42.738409Z` in-progress → **verified**: acceptance gates passed on the shared branch

Verification evidence:
- HEAD `903e02a346a2c4a4e6dae09b56ec66e1a01affbd` recorded 2026-10-17T03:37:42.734078Z
- `frontend-type-check`: passed (exit 0, 0.0 s)
- `frontend-tests`: passed (exit 0, 0.0 s)
```

## Dependency probe (`op=o<id>`, outbox)

Posted on the provider's Issue and on every waiting consumer's Issue. A probe is evidence only; it never unblocks:

```markdown
<!-- feature-coordinator:op v1 coordinator=mx-admin-cloud.feature-coordinator key=items-export/items-export.cmd op=oedbe39c362a33a35 -->
Dependency probe for #5 `items-export.contract-items-export-endpoint` at feature-branch HEAD `0e75595fa28f`: `contract-check` passed (exit 0), `backend-tests` passed (exit 0). Result: the contract checks pass at this HEAD, but the provider task is **ready**: consumers stay blocked until it is verified. Waiting: #3 `items-export.cmd`.
```

## Final verification of a test feature (`op=final`)

Posted by `python -m feature_coordinator github cleanup-test` (and the live test) before the Issue is closed: the result of every check, the feature ID and the task IDs.
