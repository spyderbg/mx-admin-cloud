# W4.6.1 R3.2 - Asynchronous AI review lifecycle

Short reference for the lifecycle of the advisory AI review (documentation-only smoke test).

## Lifecycle sequence

1. An approved GitHub Issue and its execution plan.
2. Verified Worker tasks and integration checks (`contract-check`, `docs-check`).
3. A Draft PR.
4. A background advisory AI review.
5. A Coordinator PR comment with the result.
6. A human decision.

## Review outcomes

- `PASS`: the reviewer found no issues.
- `PASS_WITH_NOTES`: no blocking issues, but notes for the human to consider.
- `REQUEST_CHANGES`: problems the reviewer recommends fixing before merge.
- `ERROR`: the review did not finish. It says nothing about code quality.

## Binding and staleness

A review belongs to one PR and to the exact head commit it reviewed. If the PR head changes, the older review becomes stale.

## Limits

An advisory review does not grant GitHub approval, does not mark the PR Ready for Review, and does not authorize an automatic merge. A human makes the decision.

## History

In the first R3 test (Issue #57, PR #58) the Draft PR was merged while the reviewer was still running. That review was therefore invalidated and no review comment was posted.
