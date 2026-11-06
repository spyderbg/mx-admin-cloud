# W4.6.1 R3 - Supervised asynchronous AI review smoke test

Operator note for the supervised smoke test of the asynchronous AI review.

## W4.5 vs W4.6.1

- **W4.5**: Issue -> Execution Plan -> human plan approval -> Worker task(s) -> Draft PR.
- **W4.6.1** adds a separate, read-only, advisory AI review. It runs in the background after the Draft PR is published.

## Sequence

Approved Issue -> tasks done and task/integration gates verified -> Draft PR against `dev` -> background advisory AI review -> human decision.

## Review outcomes

- `PASS`: the reviewer found no issues.
- `PASS_WITH_NOTES`: no blocking issues, but the reviewer left notes for the human to consider.
- `REQUEST_CHANGES`: the reviewer found problems it recommends fixing before merge.
- `ERROR`: the review could not finish; this says nothing about code quality.

## Binding and staleness

A review applies to one PR and one exact head SHA. It becomes stale once new commits change the head. Its result is posted as a PR comment through the trusted Coordinator.

## Advisory only

AI review never creates a GitHub PR approval, never marks the PR Ready for Review, and never merges, squashes or rebases. Merging stays a manual human decision.

This smoke-test document contains no credentials and no production instructions.
