# Template — Operator Commands and Acknowledgements

> Rendered by the feature coordinator (`issue_sync.py`). This example was produced by the real renderer from the demo feature `items-export` against the mock tracker on 2026-10-17 (`#N` numbers are the mock's). Do not hand-write these Issues: `feature.py github sync FEATURE --apply` creates and maintains them.

Only two commands exist, on the **first non-empty line** of a comment of at most 2,000 characters, from a login in `github.command_authors` with at least `write` permission ([github-integration §7.2](../github-integration.md#72-github--local-validated-commands-only)):

```text
/feature hold <task-id> <reason>
/feature release <task-id>
```

Anything else — labels, edits, closing, other text — is never an instruction. A command never grants paths, tools, shell access or permissions.

## Accepted hold (on the Frontend task's Issue)

Operator comment:

```markdown
/feature hold items-export.view waiting for a UX review
```

Coordinator acknowledgement and the state change it caused:

```markdown
<!-- feature-coordinator:op v1 coordinator=mx-admin-cloud.feature-coordinator key=items-export/items-export.view op=o2c053159899b63c7 -->
Command from `spyderbg` (comment 1008): `/feature hold items-export.view` was **accepted**. `items-export.view` is now needs-human
```

```markdown
<!-- feature-coordinator:op v1 coordinator=mx-admin-cloud.feature-coordinator key=items-export/items-export.view op=h1-2 -->
**Coordinator update** for `items-export.view` (state changes 2–2)

- `2026-10-17T03:37:42.601016Z` ready → **needs-human**: held for a human decision by spyderbg: waiting for a UX review
```

## Rejected release of a dependency block (on the CLI task's Issue)

Operator comment:

```markdown
/feature release items-export.cmd
```

Coordinator acknowledgement (the task stays blocked):

```markdown
<!-- feature-coordinator:op v1 coordinator=mx-admin-cloud.feature-coordinator key=items-export/items-export.cmd op=oa2b946813b1a7e42 -->
Command from `spyderbg` (comment 1009): `/feature release items-export.cmd` was **rejected**. `items-export.cmd` is blocked, not held for a human; dependency blocks are released only by verified evidence on the feature branch
```

Comments from logins that are not authorized are recorded locally as ignored and are not answered.
