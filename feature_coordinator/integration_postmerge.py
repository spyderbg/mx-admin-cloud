"""W4.7 (M5): post-merge verification — the ONLY thing that completes an integration job (design §5.10).

Neither the job record nor commit ancestry proves completion. The merge commit GitHub wrote is a different object from
the candidate commit the coordinator built (its SHA normally differs; only its TREE is expected to be the candidate's),
so the verification reads the ACTUAL merge commit and checks, every time it runs:

  1. pr-merged     GitHub reports the bound PR as merged, into the approved base branch of the target repository, from
                   the pinned source branch at exactly the approved source SHA, and reports THE recorded merge commit;
  2. dev-contains  the base branch contains that merge commit — GitHub's comparison of the merge commit with the branch
                   tip says `identical` or `ahead`, and Git agrees (below);
  3. commit        the merge commit is read as a Git object: the base branch is fetched into the coordinator's PRIVATE
                   repository (integration_isolation.isolated_fetch: never the checkout), the commit is read through the
                   hash-verifying reader (its SHA-1 is recomputed) and must be an ancestor of the fetched tip;
  4. parents       its first parent is the approved `base_sha` and its second parent is the approved `source_sha` —
                   `source_sha` merely being reachable from the base proves nothing;
  5. tree          its tree is exactly the approved candidate `tree_sha`;
  6. agreement     GitHub's REST description of the commit (tree, parents) equals the Git object — two sources, one
                   immutable object: a difference is a failure, never a pass;
  7. checks        the configured post-merge checks (`github.integration.post_merge_checks`) of the merge commit.

Results: `pass`, `fail` (the merge is on the base branch but is not what was approved → the job is FAILED: manual
operator remediation, never an automatic revert, reset or force-push), `pending` (a check is running or not reported
yet),
`unavailable` (GitHub or origin could not be read — not a failure and never a pass) and `not-configured` (no check is
required: the project has no branch protection or CI on its current plan, so no CI result is claimed — the local M3
validation and M4 review are the evidence). Only pass and not-configured may complete the job; a pending or unavailable
check leaves it MERGED (pending verification), a failed one makes it FAILED.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .config import ProjectConfig
from .errors import CoordinatorError, GitHubError, GitHubNotFoundError
from .integration import IntegrationJob, clean_text
from .integration_github import fetch_pull
from .integration_intake import ReadOnlyGit
from .integration_isolation import isolated_fetch
from .integration_merge_github import (
    branch_head,
    check_observations,
    commit_facts,
    compare_status,
    evaluate_checks,
)
from .integration_objects import HistoryRefused, HistoryUnverifiable

CONTAINED = ("identical", "ahead")


@dataclass
class Verification:
    """The result of one verification run (module docstring)."""

    checks: list[dict[str, str]] = field(default_factory=list)
    parents: list[str] = field(default_factory=list)
    tree: str = ""

    def add(self, name: str, status: str, detail: str) -> None:
        """Record one check: pass | fail | pending | unavailable | not-configured."""
        self.checks.append({"name": name, "status": status, "detail": clean_text(detail, 600)})

    @property
    def state(self) -> str:
        """`failed` (any fail), else `pending` (any pending or unavailable), else `verified`."""
        statuses = {c["status"] for c in self.checks}
        if "fail" in statuses:
            return "failed"
        return "pending" if statuses & {"pending", "unavailable"} else "verified"

    def reasons(self, *statuses: str) -> list[str]:
        """The checks with one of *statuses*, as readable lines."""
        return [f"{c['name']}: {c['detail']}" for c in self.checks if c["status"] in statuses]


def verify_merge(job: IntegrationJob, result: Mapping[str, Any], *, client: Any, config: ProjectConfig,
                 git: ReadOnlyGit) -> Verification:
    """Run every check of the module docstring against the merge GitHub recorded in *result* (merge_result). Reads
    only: GitHub (GET), origin through a private repository, and the objects it fetched. Never raises for a GitHub or
    Git failure — that is `unavailable`."""
    out = Verification()
    repository, sha, candidate = config.github.repository, result["commit_sha"], job.candidate
    assert candidate is not None
    _pull(job, result, client, repository, out)
    head = _contained(job, sha, client, repository, out)
    commit = _git_commit(job, sha, head, git, out)
    facts = _github_commit(sha, client, repository, out)
    if commit is not None:
        out.parents, out.tree = list(commit.parents), commit.tree
        _judge(job, candidate, commit.parents, commit.tree, "the Git object", out)
    if commit is not None and facts is not None:
        if (commit.tree, tuple(commit.parents)) != (facts.tree, facts.parents):
            out.add("agreement", "fail", f"GitHub describes the merge commit {sha[:12]} as tree {facts.tree[:12]} with "
                                         f"parents {[p[:12] for p in facts.parents]}, but the hash-verified Git object "
                                         f"has tree {commit.tree[:12]} and parents {[p[:12] for p in commit.parents]}")
        else:
            out.add("agreement", "pass", "GitHub's description of the merge commit equals the hash-verified Git object")
    elif commit is None and facts is not None:
        out.add("agreement", "unavailable", "the Git object could not be read, so GitHub's description of the merge "
                                            "commit cannot be cross-checked (and is not used alone)")
    _checks(sha, client, config, out)
    return out


def _pull(job: IntegrationJob, result: Mapping[str, Any], client: Any, repository: str, out: Verification) -> None:
    try:
        pr = fetch_pull(client, repository, result["pr"])
    except GitHubNotFoundError as exc:
        out.add("pr-merged", "unavailable", f"PR #{result['pr']} cannot be found (HTTP 404; check the GitHub login's "
                                            f"access): {exc}")
        return
    except (GitHubError, CoordinatorError, OSError) as exc:
        out.add("pr-merged", "unavailable", f"PR #{result['pr']} could not be read: {exc}")
        return
    if not pr.merged or not pr.merge_commit_sha:
        # a read right after the merge may still show the PR as it was before it (read-after-write lag): not a
        # contradiction, so not a failure — pending, never a pass
        out.add("pr-merged", "pending", f"GitHub does not (yet) report PR #{pr.number} as merged with a merge commit "
                                        f"(state {pr.state}, merge commit {pr.merge_commit_sha or 'none'}): a lagging "
                                        "read — repeat later")
        return
    problems = []
    if pr.base != job.base["ref"] or (pr.base_repo or "").lower() != repository.lower():
        problems.append(f"it targets {pr.base_repo or '?'}:{pr.base}, not {repository}:{job.base['ref']}")
    if (pr.head_repo or "").lower() != job.source["repo"].lower() or pr.head != job.source["ref"] \
            or pr.head_sha != job.source["sha"]:
        problems.append(f"it was merged from {pr.head_repo or '?'}:{pr.head} at {pr.head_sha or '?'}, not from "
                        f"{job.source['repo']}:{job.source['ref']} at {job.source['sha']}")
    if pr.merge_commit_sha != result["commit_sha"]:
        problems.append(f"GitHub reports the merge commit {pr.merge_commit_sha}, not the recorded "
                        f"{result['commit_sha']}")
    if problems:
        out.add("pr-merged", "fail", "; ".join(problems))
    else:
        out.add("pr-merged", "pass", f"PR #{pr.number} is merged into {pr.base_repo}:{pr.base} from {pr.head} at the "
                                     f"approved source SHA; merge commit {pr.merge_commit_sha}"
                                     + (f", merged by {pr.merged_by}" if pr.merged_by else ""))


def _contained(job: IntegrationJob, sha: str, client: Any, repository: str, out: Verification) -> str:
    """GitHub's view: the base branch's tip, and whether it contains the merge commit. Returns the tip ('' unknown)."""
    try:
        head = branch_head(client, repository, job.base["ref"])
        status = compare_status(client, repository, sha, head)
    except (GitHubError, CoordinatorError, OSError) as exc:
        out.add("dev-contains", "unavailable", f"GitHub could not say whether {job.base['ref']} contains the merge "
                                               f"commit: {exc}")
        return ""
    if status in CONTAINED:
        out.add("dev-contains", "pass", f"GitHub: {job.base['ref']} ({head[:12]}) contains the merge commit "
                                        f"{sha[:12]} ({status})")
    elif status == "behind":
        # GitHub's branch tip is an ancestor of the merge commit: its branch read has not caught up with the merge (or
        # the branch was moved back — only a later read tells them apart): pending, never a pass, not yet a failure
        out.add("dev-contains", "pending", f"GitHub: {job.base['ref']} ({head[:12]}) is behind the merge commit "
                                           f"{sha[:12]} — the branch read has not caught up — repeat later")
    else:
        out.add("dev-contains", "fail", f"GitHub: {job.base['ref']} ({head[:12]}) does not contain the merge commit "
                                        f"{sha[:12]} ({status}) — the branch was rewritten or the commit is elsewhere")
    return head


def _git_commit(job: IntegrationJob, sha: str, head: str, git: ReadOnlyGit, out: Verification) -> Any:
    """The merge commit as a hash-verified Git object (None: not readable now), plus the local containment check."""
    base_ref = job.base["ref"]
    fetched = ""
    try:
        tips = tuple(s for s in (job.base["expected_sha"], job.source["sha"]) if git.commit_exists(s))
        fetched = isolated_fetch(git.isolated(), git.origin_url(), base_ref, negotiation_tips=tips, timeout=git.timeout)
        with git.reader() as reader:
            commit = reader.read(sha, strict=False)
        contained = git.is_ancestor(sha, fetched)
    except HistoryRefused as exc:   # not raised for strict=False, kept for a reader that refuses
        out.add("commit", "fail", f"the merge commit object is malformed: {exc}")
        return None
    except (CoordinatorError, HistoryUnverifiable, OSError) as exc:
        contains = next((c for c in out.checks if c["name"] == "dev-contains"), None)
        if fetched and contains is not None and contains["status"] == "pending":
            # origin's branch WAS fetched and does not hold the merge commit's objects, and GitHub's branch read is
            # behind the merge commit too: the branch was moved back or rewritten, not a lagging read
            _moved_back(contains, base_ref, sha, fetched)
            out.add("commit", "fail", f"origin's {base_ref} ({fetched[:12]}) does not hold the merge commit {sha[:12]}")
        else:
            out.add("commit", "unavailable", f"the merge commit could not be read as a Git object from origin's "
                                             f"{base_ref}: {exc}")
        return None
    if not contained:
        contains = next((c for c in out.checks if c["name"] == "dev-contains"), None)
        if contains is not None and contains["status"] == "pass":
            out.add("commit", "pending", f"GitHub does not contradict the merge commit {sha[:12]} being on "
                                         f"{base_ref}, but origin's {base_ref} ({fetched[:12]}) does not contain it "
                                         "(yet): a read has not caught up — repeat later")
        else:
            if contains is not None and contains["status"] == "pending":
                _moved_back(contains, base_ref, sha, fetched)   # GitHub's read is behind AND the git server agrees
            out.add("commit", "fail", f"origin's {base_ref} ({fetched[:12]}) does not contain the merge commit "
                                      f"{sha[:12]}")
        return None
    github = "" if not head or head == fetched else f", GitHub: {head[:12]}"
    out.add("commit", "pass", f"the merge commit {sha[:12]} is a hash-verified Git object contained in origin's "
                              f"{base_ref} ({fetched[:12]}{github})")
    return commit


def _moved_back(contains: dict[str, str], base_ref: str, sha: str, fetched: str) -> None:
    """GitHub's branch read is behind the merge commit and origin's branch does not hold it either: not a lagging API
    read — the `dev-contains` check becomes a failure."""
    contains.update(status="fail", detail=clean_text(
        f"GitHub's {base_ref} read is behind the merge commit {sha[:12]} and origin's {base_ref} ({fetched[:12]}) does "
        "not contain it either — the branch was moved back or rewritten", 600))


def _github_commit(sha: str, client: Any, repository: str, out: Verification) -> Any:
    try:
        return commit_facts(client, repository, sha)
    except (GitHubError, CoordinatorError, OSError) as exc:
        out.add("commit-github", "unavailable", f"GitHub could not describe the merge commit: {exc}")
        return None


def _judge(job: IntegrationJob, candidate: Mapping[str, Any], parents: tuple[str, ...], tree: str, source: str,
           out: Verification) -> None:
    base, src = job.base["expected_sha"], job.source["sha"]
    if len(parents) != 2 or parents[0] != base or parents[1] != src:
        out.add("parents", "fail", f"{source}: the merge commit's parents are {[p[:12] for p in parents]}, not the "
                                   f"approved base {base[:12]} and source {src[:12]} (in that order) — a squash, "
                                   "rebase or a moved base; source_sha being reachable from the branch is not "
                                   "enough")
    else:
        out.add("parents", "pass", f"first parent is the approved base {base[:12]}, second parent the approved "
                                   f"source {src[:12]}")
    if tree != candidate["tree_sha"]:
        out.add("tree", "fail", f"{source}: the merge commit's tree {tree} is not the approved candidate tree "
                                f"{candidate['tree_sha']}")
    else:
        out.add("tree", "pass", f"the merge commit's tree is the approved candidate tree {tree[:12]}")


def _checks(sha: str, client: Any, config: ProjectConfig, out: Verification) -> None:
    required = config.github.integration.post_merge_checks
    observed, why = None, ""
    if required:
        try:
            observed = check_observations(client, config.github.repository, sha, max_pages=config.github.max_pages)
        except (GitHubError, CoordinatorError, OSError) as exc:
            why = f"the checks of the merge commit could not be read: {exc}"
    for item in evaluate_checks(required, observed, unavailable=why):
        out.add(f"check {item['name']}" if required else "checks", item["status"], item["detail"])
