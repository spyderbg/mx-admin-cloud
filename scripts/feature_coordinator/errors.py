"""Exception hierarchy. Every refusal the coordinator makes is a CoordinatorError subclass,
so callers (the CLI, tests) can tell a deliberate safety stop from a programming error."""

from __future__ import annotations


class CoordinatorError(Exception):
    """Base class for every deliberate refusal or failure of the feature coordinator."""


class ConfigError(CoordinatorError):
    """The project configuration is missing, malformed or violates a fixed safety ceiling."""


class StateDirError(CoordinatorError):
    """The runtime state directory is unusable (wrong filesystem, permissions, broken flock)."""


class StateConflictError(CoordinatorError):
    """A state record changed underneath the caller (optimistic revision check failed)."""


class LockBusyError(CoordinatorError):
    """A lock could not be acquired within its timeout because another holder has it."""


class RoleBusyError(LockBusyError):
    """The role slot already has an active writer — at most one writer per role."""


class FullProjectActiveError(LockBusyError):
    """A full-project writer holds (or has requested) exclusive access to the checkout."""


class WritersActiveError(LockBusyError):
    """An exclusive operation needs every scoped writer to be quiescent, and some are not."""


class ClaimError(CoordinatorError):
    """A task cannot be claimed (wrong state, wrong role, already claimed, stale claim)."""


class IllegalTransitionError(CoordinatorError):
    """A feature or task state change is not allowed by the lifecycle tables."""


class DependencyError(CoordinatorError):
    """The task dependency graph is invalid (unknown task, self-dependency or cycle)."""


class EvidenceError(CoordinatorError):
    """Verification evidence is missing, failing, incomplete or bound to the wrong HEAD."""


class ScopeViolationError(CoordinatorError):
    """A path is outside the caller's write scope, protected, or escapes the repository."""


class GitOperationError(CoordinatorError):
    """A Git command failed or a Git safety check refused the operation."""


class HeadMismatchError(GitOperationError):
    """The branch or HEAD is not what the request expected (or moved concurrently)."""


class ForeignStagedContentError(GitOperationError):
    """The shared index already has staged content this request does not own."""


class UnstableFileError(GitOperationError):
    """A file changed between the commit request and staging — retry once it is stable."""


class NothingToCommitError(GitOperationError):
    """The manifest paths already match HEAD."""


class CheckpointError(CoordinatorError):
    """W3: checkpoint metadata is missing, corrupt, incompatible or inconsistent with Git."""


class RecoveryConflictError(CheckpointError):
    """W3: automatic recovery would be unsafe; an operator decision is required."""


class PullRequestPolicyError(CoordinatorError):
    """The one-feature/one-final-PR policy would be violated."""


class TrackerError(CoordinatorError):
    """The issue tracker refused an operation (foreign issue, duplicate, unknown issue)."""


class OwnershipError(TrackerError):
    """An Issue is not provably owned by this coordinator and feature, so it is never modified."""


class SyncScopeError(TrackerError):
    """The GitHub write scope (e.g. test features only, Phase 4) does not cover this feature."""


class ProjectBoardError(TrackerError):
    """The GitHub project board is not the configured one, is a protected project, or lacks a
    required field, option or view (`feature.py github project setup`). Nothing is written."""


class GitHubError(TrackerError):
    """A GitHub API failure. Messages are sanitized: no tokens, no control characters, bounded."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class GitHubCliMissingError(GitHubError):
    """The GitHub CLI (`gh`) is not installed or not executable."""


class GitHubAuthError(GitHubError):
    """HTTP 401, or `gh` is not authenticated (not retried)."""


class GitHubPermissionError(GitHubError):
    """HTTP 403 that is not a rate limit: the account lacks a capability (not retried)."""


class GitHubNotFoundError(GitHubError):
    """HTTP 404/410: the repository, Issue or label does not exist or is not visible."""


class GitHubValidationError(GitHubError):
    """HTTP 409/422: GitHub rejected the request content."""


class GitHubResponseError(GitHubError):
    """The response does not have the expected format (GitHub data is untrusted input)."""


class GitHubUnavailableError(GitHubError):
    """A transient failure: 5xx, timeout, connection error (retried with backoff when safe)."""


class GitHubRateLimitError(GitHubUnavailableError):
    """A primary or secondary rate limit whose required wait exceeds the configured bound."""


class GitHubUncertainWriteError(GitHubUnavailableError):
    """A non-idempotent write (create Issue/comment) failed in a way that may have applied it.
    The caller must search for the result before trying again."""


class ProviderError(CoordinatorError):
    """A model provider/runner failure. Subclasses tell callers whether to retry."""


class ProviderUnavailableError(ProviderError):
    """The provider endpoint is unreachable, not configured, timed out or rate limited."""


class ProviderAuthError(ProviderError):
    """The provider rejected the credentials (not retried)."""


class ProviderResponseError(ProviderError):
    """The provider answered with something that is not a valid Chat Completions response."""


class TransportPolicyError(ProviderError):
    """The request is blocked by the transport/disclosure policy (e.g. plain HTTP, D-15/D-16)."""


class ContextBudgetError(ProviderError):
    """The request would exceed the model's effective input or output token budget."""


class ProviderRateLimitError(ProviderUnavailableError):
    """The provider answered HTTP 429 (retried with backoff like other unavailability)."""


class ProviderCancelledError(ProviderError):
    """The request was cancelled by the coordinator (operator stop or worker shutdown)."""
