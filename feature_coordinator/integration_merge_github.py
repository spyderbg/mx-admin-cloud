"""W4.7 (M5): the GitHub READS the merge and its verification need, parsed fail-closed (GitHub data is untrusted input).
READ-ONLY: every function here sends GET requests through the client's request core (retries, rate limits, error
classification — github_api.GitHubClient._call); the two writes of an integration job live in the client itself, behind
its one-shot grants. Anything malformed, incomplete or contradictory is a GitHubResponseError: the caller defers (the
merge: nothing is written; the verification: pending), it never reads a missing field as a pass.

  * commit_facts — `GET /git/commits/{sha}`: the commit's tree and parents as GitHub reports them (cross-checked by
    the caller against the hash-verified Git object);
  * branch_head — `GET /branches/{branch}`: the commit GitHub currently has at the branch tip;
  * compare_status — `GET /compare/{base}...{head}`: `identical`, `ahead`, `behind` or `diverged` — `base` is
    contained in
    `head` exactly for `identical` and `ahead`;
  * pull_merge_state — `GET /pulls/{n}`: GitHub's merge requirements for one PR (draft, `mergeable`, `mergeable_state`);
  * check_observations / evaluate_checks — the check runs and commit statuses of one commit, complete (every page), the
    newest per name; evaluate_checks judges the CONFIGURED names only. Not configured, unreadable and still-running are
    three different results (`not-configured`, `unavailable`, `pending`): none is ever reported as `pass`. The
    listing is untrusted DATA (Codex F5): every observation has a positive integer identity (id) and belongs to the
    requested commit when it says so; an identity that occurs twice — across pages, across the two listings' pages,
    identical or contradictory — makes the listing unreliable (a repeated id can hide a missing run behind a matching
    `total_count`, and contradictory results for one identity have no right answer), so it is a GitHubResponseError:
    the merge defers, the verification stays pending, nothing is ever read as `pass` from it. Distinct identities with
    the same name (re-runs) are legitimate: the newest of each source decides.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping
from urllib.parse import quote, urlencode

from .errors import GitHubError, GitHubResponseError
from .github_api import PER_PAGE
from .integration import IntegrationJob, clean_text
from .integration_github import strict_pages

_SHA_RE = re.compile(r"[0-9a-f]{40}")
_BRANCH_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}")
COMPARE_STATUSES = ("identical", "ahead", "behind", "diverged")
BLOCKING_STATES = {"dirty": "the PR has merge conflicts", "blocked": "GitHub blocks the merge (a branch rule: required "
                                                                    "reviews or status checks are not satisfied)",
                   "draft": "the PR is a draft"}
PENDING_STATES = ("unknown", "")
MAX_CHECKS_READ = 2000
PASSING_CONCLUSIONS = ("success", "neutral", "skipped")
RUN_STATUSES = ("queued", "in_progress", "completed", "waiting", "requested", "pending")


def _live(client: Any) -> None:
    if not getattr(client, "live", False) or not hasattr(client, "_call"):
        raise GitHubError("a live GitHub client is required (the local tracker cannot merge or verify a merge)")


def _get(client: Any, path: str) -> Any:
    _live(client)
    return client._call("GET", path, idempotent=True).data  # pylint: disable=protected-access


@dataclass(frozen=True)
class CommitFacts:
    """A commit as GitHub reports it."""

    sha: str
    tree: str
    parents: tuple[str, ...]


def commit_facts(client: Any, repository: str, sha: str) -> CommitFacts:
    """The tree and parents GitHub reports for commit *sha* (it must answer for that commit)."""
    data = _get(client, f"repos/{repository}/git/commits/{sha}")
    tree = data.get("tree") if isinstance(data, dict) else None
    parents = data.get("parents") if isinstance(data, dict) else None
    tree_ok = isinstance(tree, dict) and isinstance(tree.get("sha"), str) and _SHA_RE.fullmatch(tree["sha"]) is not None
    parents_ok = isinstance(parents, list) and all(isinstance(p, dict) and isinstance(p.get("sha"), str)
                                                  and _SHA_RE.fullmatch(p["sha"]) for p in parents)
    if not isinstance(data, dict) or data.get("sha") != sha or not tree_ok or not parents_ok:
        raise GitHubResponseError(f"commit {sha[:12]}: GitHub's answer does not give its tree and parents")
    return CommitFacts(sha=sha, tree=tree["sha"], parents=tuple(p["sha"] for p in parents))


def branch_head(client: Any, repository: str, branch: str) -> str:
    """The commit at the tip of *branch* as GitHub reports it now."""
    if not _BRANCH_RE.fullmatch(branch):
        raise GitHubResponseError(f"refusing to read the branch {branch[:80]!r}")
    data = _get(client, f"repos/{repository}/branches/{quote(branch, safe='/')}")
    commit = data.get("commit") if isinstance(data, dict) else None
    if not isinstance(commit, dict) or not isinstance(commit.get("sha"), str) or not _SHA_RE.fullmatch(commit["sha"]):
        raise GitHubResponseError(f"branch {branch}: GitHub's answer does not give its commit")
    return commit["sha"]


def compare_status(client: Any, repository: str, base: str, head: str) -> str:
    """GitHub's comparison of *base* with *head*: `identical`, `ahead` (head has commits base lacks), `behind` or
    `diverged`. *base* is contained in *head* exactly for identical and ahead."""
    if not (_SHA_RE.fullmatch(base) and (_SHA_RE.fullmatch(head) or _BRANCH_RE.fullmatch(head))):
        raise GitHubResponseError("refusing to compare those references")
    data = _get(client, f"repos/{repository}/compare/{base}...{quote(head, safe='/')}?"
                        + urlencode({"per_page": "1"}))
    status = data.get("status") if isinstance(data, dict) else None
    if status not in COMPARE_STATUSES:
        raise GitHubResponseError(f"compare {base[:12]}...{head[:12]}: GitHub's answer has no valid status")
    return status


@dataclass(frozen=True)
class MergeState:
    """GitHub's merge requirements for one PR."""

    number: int
    state: str
    merged: bool
    draft: bool
    mergeable: bool | None
    mergeable_state: str

    def problem(self) -> tuple[str, str]:
        """(`ok` | `pending` | `blocked`, why) for a merge now."""
        if self.merged or self.state != "open":
            return "blocked", f"the PR is {'merged' if self.merged else self.state}"
        if self.draft or self.mergeable_state == "draft":
            return "blocked", BLOCKING_STATES["draft"]
        if self.mergeable is False or self.mergeable_state in BLOCKING_STATES:
            return "blocked", BLOCKING_STATES.get(self.mergeable_state, "GitHub reports the PR as not mergeable")
        if self.mergeable is None or self.mergeable_state in PENDING_STATES:
            return "pending", "GitHub has not computed whether the PR is mergeable yet"
        return "ok", f"GitHub reports the PR as mergeable ({self.mergeable_state})"


def pull_merge_state(client: Any, repository: str, number: int) -> MergeState:
    """`GET /pulls/{number}` reduced to its merge requirements (an answer for another PR is refused)."""
    data = _get(client, f"repos/{repository}/pulls/{int(number)}")
    flags_ok = isinstance(data, dict) and isinstance(data.get("draft"), bool) and isinstance(data.get("merged"), bool)
    if not flags_ok or data.get("number") != int(number) or data.get("state") not in ("open", "closed") \
            or data.get("mergeable") not in (True, False, None) or not isinstance(data.get("mergeable_state"), str):
        raise GitHubResponseError(f"PR #{number}: GitHub's answer lacks its merge requirements "
                                  "(state, draft, merged, mergeable, mergeable_state)")
    return MergeState(number=int(number), state=data["state"], merged=data["merged"], draft=data["draft"],
                      mergeable=data["mergeable"], mergeable_state=data["mergeable_state"])


# -- checks ------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class CheckObservation:
    """One check run or commit status of a commit, reduced to `pass` | `fail` | `pending`."""

    name: str
    status: str
    source: str       # check-run | status
    ident: int


def _run_status(run: Mapping[str, Any]) -> str:
    if run["status"] != "completed":
        return "pending"
    return "pass" if run.get("conclusion") in PASSING_CONCLUSIONS else "fail"


def _identity(item: Any, source: str, sha: str, name_key: str, where: str) -> int:
    """The id of one observation (a positive integer: not a bool, not a float), after checking that it names its commit
    when it says so."""
    ident = item.get("id") if isinstance(item, dict) else None
    if type(ident) is not int or ident <= 0:  # pylint: disable=unidiomatic-typecheck
        raise GitHubResponseError(f"the {where} of {sha[:12]}: a {source} lacks a positive integer id")
    if not isinstance(item.get(name_key), str) or not item[name_key]:
        raise GitHubResponseError(f"the {where} of {sha[:12]}: {source} {ident} lacks its {name_key}")
    claimed = item.get("head_sha") if source == "check-run" else item.get("sha")
    if claimed is not None and claimed != sha:
        raise GitHubResponseError(f"the {where} of {sha[:12]}: {source} {ident} belongs to {str(claimed)[:12]}, not to "
                                  "the commit that was asked about")
    return ident


def _remember(seen: dict[tuple[str, int], CheckObservation], found: CheckObservation, sha: str) -> None:
    """Register one observation; an identity seen before is an error (module docstring, Codex F5)."""
    key = (found.source, found.ident)
    earlier = seen.get(key)
    if earlier is not None:
        how = "with contradictory results" if (earlier.name, earlier.status) != (found.name, found.status) \
            else "again, identically"
        raise GitHubResponseError(f"the checks of {sha[:12]}: {found.source} {found.ident} ({found.name!r}) is listed "
                                  f"{how}: the listing is not a faithful enumeration (a repeated id can hide a "
                                  "missing run), so no result is taken from it")
    seen[key] = found


def check_observations(client: Any, repository: str, sha: str, *, max_pages: int) -> list[CheckObservation]:
    """Every check run and commit status of commit *sha* (all pages; GitHubResponseError when incomplete, malformed,
    or when an identity occurs twice)."""
    if not _SHA_RE.fullmatch(sha):
        raise GitHubResponseError("refusing to read the checks of that reference")
    seen: dict[tuple[str, int], CheckObservation] = {}
    total, read = None, 0
    for page in range(1, max_pages + 1):
        data = _get(client, f"repos/{repository}/commits/{sha}/check-runs?"
                            + urlencode({"per_page": str(PER_PAGE), "page": str(page)}))
        runs = data.get("check_runs") if isinstance(data, dict) else None
        count = data.get("total_count") if isinstance(data, dict) else None
        count_ok = type(count) is int and 0 <= count <= MAX_CHECKS_READ  # pylint: disable=unidiomatic-typecheck
        if not isinstance(runs, list) or not count_ok or (total is not None and count != total):
            raise GitHubResponseError(f"the check runs of {sha[:12]}: GitHub's answer is malformed or changed while "
                                      "read")
        total = count
        for run in runs:
            ident = _identity(run, "check-run", sha, "name", "check runs")
            if run.get("status") not in RUN_STATUSES:
                raise GitHubResponseError(f"the check runs of {sha[:12]}: run {ident} lacks its status")
            _remember(seen, CheckObservation(run["name"], _run_status(run), "check-run", ident), sha)
        read += len(runs)
        if read >= total:
            break
        if not runs:
            raise GitHubResponseError(f"the check runs of {sha[:12]}: {read} of {total} read (the listing is "
                                      "incomplete)")
    else:
        raise GitHubResponseError(f"the check runs of {sha[:12]}: more than {max_pages} pages")
    if read != total:
        raise GitHubResponseError(f"the check runs of {sha[:12]}: {read} read, {total} announced")
    for item in strict_pages(client, f"repos/{repository}/commits/{sha}/statuses", {}, max_pages=max_pages):
        ident = _identity(item, "status", sha, "context", "statuses")
        if item.get("state") not in ("success", "pending", "failure", "error"):
            raise GitHubResponseError(f"the statuses of {sha[:12]}: status {ident} lacks its state")
        _remember(seen, CheckObservation(item["context"], {"success": "pass", "pending": "pending"}.get(item["state"],
                                                                                                        "fail"),
                                         "status", ident), sha)
    return list(seen.values())


def evaluate_checks(required: Iterable[str], observed: list[CheckObservation] | None, *,
                    unavailable: str = "") -> list[dict[str, str]]:
    """One {name, status, detail} per configured check name: `pass`, `fail`, `pending` (running, or not reported yet),
    `unavailable` (*unavailable* names why GitHub could not be read) — or a single `not-configured` entry when no
    name is
    required. The NEWEST observation of each source decides (highest id); a name seen as a check run and as a status
    must pass in both. Never `pass` for a check that was not seen."""
    names = list(required)
    if not names:
        return [{"name": "(none)", "status": "not-configured",
                 "detail": "no check is required (github.integration.required_checks / post_merge_checks is empty): "
                           "no CI result is claimed"}]
    if observed is None:
        return [{"name": name, "status": "unavailable", "detail": clean_text(unavailable or "GitHub could not be read",
                                                                             300)} for name in names]
    results = []
    for name in names:
        newest: dict[str, CheckObservation] = {}
        for item in observed:
            if item.name == name and (item.source not in newest or item.ident > newest[item.source].ident):
                newest[item.source] = item
        states = {item.status for item in newest.values()}
        if not newest:
            status, detail = "pending", "GitHub reports no check run or status of this name (it may not have started)"
        elif "fail" in states:
            status, detail = "fail", "a check run or status of this name failed"
        elif "pending" in states:
            status, detail = "pending", "a check run or status of this name has not finished"
        else:
            status, detail = "pass", f"succeeded ({', '.join(sorted(newest))})"
        results.append({"name": name, "status": status, "detail": detail})
    return results


def pull_request_text(job: IntegrationJob, approval: Mapping[str, Any]) -> tuple[str, str]:
    """(title, body) of the PR the coordinator opens for *job* under its merge approval: fixed text from the job's
    immutable inputs, the approval and the candidate — no model or source text."""
    candidate = job.candidate or {}
    title = f"Integration {job.job_id}: {job.source['ref']} into {job.base['ref']}"
    body = "\n".join([
        f"Existing branch `{job.source['ref']}` @ `{job.source['sha']}` into `{job.base['ref']}` @ "
        f"`{job.base['expected_sha']}` (Issue #{job.issue}).",
        "",
        f"Opened by the Feature Coordinator's integration job {job.job_id} under the operator's signed merge approval "
        f"{approval['id']} (record sha256 `{approval['record_sha256']}`), which authorizes exactly this merge.",
        f"Validated and reviewed candidate: `{candidate.get('head_sha', '')}` "
        f"(tree `{candidate.get('tree_sha', '')}`).",
        "The merge uses GitHub's merge-commit method, pinned to the source SHA above.",
        "",
        f"<!-- w47-integration-job: {job.job_id} approval: {approval['id']} -->"])
    return title, body
