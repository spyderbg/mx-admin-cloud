# W4.5 Issue Planning Smoke Test — Operator Note

Purpose: help operators supervising the first W4.5 planning smoke test understand the GitHub Issue planning and approval lifecycle.

## Lifecycle stages

1. `worker:plan`: the planner is triggered for a GitHub Issue and produces an execution plan.
2. Published versioned execution plan: the plan is published on the Issue with a version identifier.
3. Human review: an operator reviews that specific plan version against the Issue specification.
4. `worker:approve`: the operator explicitly approves that plan version.
5. GitHub Actions receipt: the approval is recorded or acknowledged by a GitHub Actions run.
6. Coordinator validation: the Coordinator checks the receipt, Issue specification and plan version before any task runs.

## Guarantees

- Planning alone never authorizes task execution.
- Approval is bound to a specific Issue specification and plan version. If either changes, the existing approval no longer applies and a new plan and approval are required.
