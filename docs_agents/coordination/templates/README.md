# Coordination Templates (Phase 4)

> Owner role: **coordination**. These are **examples of what the coordinator renders**, not forms to fill in by hand. The synchronizer (`feature_coordinator/issue_sync.py`) writes every Issue body, label and comment from the local state; the PR candidate comes from `workflow.FeatureCoordinator.integrate()`.

| Template | Rendered by | Used for |
| --- | --- | --- |
| [feature-issue.md](feature-issue.md) | `IssueSync.render_block` (parent) | The feature's parent Issue |
| [component-task-issue.md](component-task-issue.md) | `IssueSync.render_block` (task) | One Issue per component task |
| [progress-comment.md](progress-comment.md) | `IssueSync._history_comment`, `probe_dependency` | State changes, dependency probes, final verification |
| [command-comments.md](command-comments.md) | `IssueSync._handle_comment` | `/feature hold` and `/feature release`, and their acknowledgements |
| [handoff.md](handoff.md) | `FeatureCoordinator.request_contract` | Backend contract requests from CLI/Frontend |
| [pull-request.md](pull-request.md) | `FeatureCoordinator._render_pr` | The single PR candidate of a feature |

Schemas, ownership markers and label mapping: [github-integration.md](../github-integration.md). Lifecycle and dependency rules: [task-lifecycle.md](../task-lifecycle.md). A test (`test_templates_carry_valid_markers`) checks that the example markers in these files still parse with the current code.
