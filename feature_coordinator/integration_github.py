"""W4.7 (M2): complete pull-request discovery, the PR reuse rules, and GitHub commit attribution for the read-only
inspection (integration_inspect.py). READ-ONLY: GET requests and one GraphQL query type; no PR is created, edited,
commented on or closed, and nothing is merged.

PR discovery (design §4.1).
  * Every PR of `github.repository` in every state (open, closed, merged) is listed — `GET /repos/{repo}/pulls?
    state=all&sort=created&direction=asc` — with STRICT pagination (strict_pages): the `Link` header must announce
    each next page exactly; a short page that announces another page, a full page without a next link whose
    following page is not empty, more than `github.max_pages` pages, a page that is not a list, a PR listed twice and
    a malformed PR all raise IncompleteListing (a partial listing is never used). Every PR object is parsed strictly
    (strict_pull): each field the reuse rules read must be PRESENT — a missing `state`, `draft`, `merged_at`, head or
    base `ref`/`sha`/`repo` is a malformed answer, never a default.
  * Cross-checks (cross_check): the PRs that GitHub returns for the head `owner:branch` and for the commit
    (`/commits/{sha}/pulls`), and the intake PR as read on its own (`/pulls/{n}`), must all appear in the full
    listing with the SAME security-relevant snapshot (pr_snapshot: state, merged, draft, head repository, branch and
    SHA, base repository and branch). A PR missing from the listing, two answers that disagree about one PR (it
    changed between the reads, or one answer is stale), or a head query answer that does not have the queried head
    raise IncompleteListing / ConflictingObservation: the inspection DEFERS and no PR decision is taken from either
    snapshot. Because every PR another query reports is in the listing with the identical snapshot, a PR that any
    query reports as matching the branch or the SHA is necessarily a candidate: "no matching PR" is never concluded
    while one query sees a possible match. The local mock tracker (not live) lists its PRs completely from memory.
  * Candidates are the UNION, de-duplicated by PR number, of (1) head repository == source_repo and head branch ==
    source_ref, whatever the head SHA, and (2) head SHA == source_sha, whatever the head repository or branch.
  * decide_reuse applies the design's rules exactly: no candidate → `none` (a PR may be created in a later milestone;
    M2 creates nothing); exactly one candidate that is open, from source_repo:source_ref at source_sha into
    github.repository:base_ref (and, when the job recorded a PR at intake, that PR) → `reuse`; anything else (a closed
    or merged candidate, another base or repository, a branch match at another SHA, several candidates) → `refused`,
    listing every candidate and why. A PR recorded at intake whose head, branch or base changed is `stale` (a pinned
    value no longer holds, M1 decision 1) — checked before the candidates, and only once the complete lookup agrees
    with the read of that PR (cross_check): a change reported by one answer alone defers.
  * fetch_pull: the `/pulls/{n}` answer must be PR #n (ConflictingObservation otherwise).
  * Any GitHub failure or an incomplete listing propagates (GitHubError / IncompleteListing): the inspection DEFERS
    and records no PR decision (design §8.4: "no side effect without lookup").

Attribution (fetch_attribution, assess_provenance). For every commit of the verified range, GitHub's GraphQL `Commit`
gives the GitHub account matched to the author e-mail (`author.user`) and the signature verification (`signature {
isValid state signer wasSignedByGitHub }`). Parsing is fail-closed (parse_attribution): every requested field must be
present (`null` only where GitHub's schema allows it); a missing field — in particular a missing or null
`wasSignedByGitHub` — or a contradictory combination (`state` VALID with `isValid` false, or the reverse) is a
malformed answer and DEFERS the inspection: incomplete attribution is never read as an explicit "false" and never
becomes verified provenance. An author e-mail alone never proves identity, and GitHub's account match is derived from
that same self-asserted e-mail, so:
  * verified    — GitHub verified the signature (state VALID) and the signer is a trusted login; a commit GitHub
                  signed itself (web UI/API, "web-flow") is NOT verified: its author fields can be set freely through
                  the API, so they do not identify who made it;
  * unverified  — no valid signature (unsigned, unknown key, …): the identity is only claimed;
  * unverifiable— GitHub's attribution could not be obtained (the local tracker has none).
  A commit whose author e-mail GitHub matches to an account that is NOT trusted, or whose valid signature is by an
  untrusted login, is REFUSED: there is positive evidence of another identity. Transient verification states
  (GPGVERIFY_UNAVAILABLE, OCSP_PENDING, …) DEFER. Unverified and unverifiable commits need the operator's explicit
  `accept_unverified_provenance` in the signed authorization record; the record keeps that acceptance distinct from
  verified provenance.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping
from urllib.parse import parse_qs, urlencode, urlparse

from .errors import GitHubError, GitHubResponseError
from .github_api import PER_PAGE, parse_pull
from .integration import clean_text
from .integration_objects import CommitObject
from .issues import PullRequest

_LINK_RE = re.compile(r'<([^>]*)>\s*;\s*rel="([a-z]+)"')
_LOGIN_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})(?:\[bot\])?")
_SHA_RE = re.compile(r"[0-9a-f]{40}")
ATTRIBUTION_BATCH = 50
TRANSIENT_SIGNATURE_STATES = frozenset({"GPGVERIFY_UNAVAILABLE", "GPGVERIFY_ERROR", "OCSP_PENDING", "OCSP_ERROR"})
MAX_LISTED = 20


class IncompleteListing(GitHubError):
    """A GitHub listing could not be proven complete: the inspection defers (fail closed)."""


class ConflictingObservation(IncompleteListing):
    """Two GitHub answers disagree about the same PR: the inspection defers and uses neither (fail closed)."""


class AttributionUnavailable(GitHubError):
    """GitHub's attribution of a commit could not be obtained: the inspection defers."""


# ---------------------------------------------------------------------------
# Strict pagination
# ---------------------------------------------------------------------------

def parse_link(header: str) -> dict[str, str]:
    """rel → URL of an HTTP `Link` header."""
    return {rel: url for url, rel in _LINK_RE.findall(header or "")}


def _page_of(url: str) -> int | None:
    values = parse_qs(urlparse(url).query).get("page")
    return int(values[0]) if values and values[0].isdigit() else None


def strict_pages(client: Any, path: str, params: Mapping[str, str], *, max_pages: int) -> list[Any]:
    """Every item of a paginated GET, or IncompleteListing (see the module docstring). Uses the client's request
    core (retries, rate limits, validation of the status) for each page."""
    items: list[Any] = []
    page, last, confirming = 1, None, False
    while True:
        if page > max_pages:
            raise IncompleteListing(f"GET {path}: more than {max_pages} pages; refusing a partial result")
        query = urlencode({**params, "per_page": str(PER_PAGE), "page": str(page)})
        resp = client._call("GET", f"{path}?{query}", idempotent=True)  # pylint: disable=protected-access
        data = resp.data
        if not isinstance(data, list) or len(data) > PER_PAGE:
            raise IncompleteListing(f"GET {path} page {page}: not a list of at most {PER_PAGE} items")
        links = parse_link(resp.headers.get("link", ""))
        if "last" in links:
            announced = _page_of(links["last"])
            if announced is None or (last is not None and announced != last):
                raise IncompleteListing(f"GET {path}: inconsistent last page in the Link headers")
            last = announced
        if confirming:
            if data or "next" in links:
                raise IncompleteListing(f"GET {path}: page {page - 1} was full and announced no next page, but page "
                                        f"{page} is not empty — the listing changed or is inconsistent")
            return items
        items.extend(data)
        if "next" in links:
            if _page_of(links["next"]) != page + 1:
                raise IncompleteListing(f"GET {path}: the next link of page {page} does not point to page {page + 1}")
            if len(data) < PER_PAGE:
                raise IncompleteListing(f"GET {path}: page {page} is short ({len(data)}) but announces another page")
            page += 1
            continue
        if last is not None and page != last:
            raise IncompleteListing(f"GET {path}: the listing ended at page {page}, but page {last} was announced")
        if len(data) == PER_PAGE:   # GitHub omits Link only for a single page: confirm that the next one is empty
            page, confirming = page + 1, True
            continue
        return items


# ---------------------------------------------------------------------------
# PR discovery and the reuse rules
# ---------------------------------------------------------------------------

@dataclass
class Discovery:
    """The complete PR lookup for one head."""

    candidates: list[PullRequest]
    listed: int
    method: str                      # github | local-tracker


_PULL_FIELDS = ("number", "state", "draft", "merged_at", "head", "base")
_PULL_END_FIELDS = ("ref", "sha", "repo")
SNAPSHOT_FIELDS = ("state", "merged", "draft", "head_repo", "head", "head_sha", "base_repo", "base")


def strict_pull(item: Any, *, single: bool = False) -> PullRequest:
    """A PR object of a live answer: github_api.parse_pull, after checking that every field the reuse rules read is
    PRESENT (parse_pull fills in defaults for some — a missing `draft`, `merged` or head `repo` — which must never stand
    for an answer GitHub did not give). *single*: the `/pulls/{n}` answer, which must also carry `merged`."""
    if not isinstance(item, dict):
        raise GitHubResponseError("pull request: not an object")
    where = f"pull request #{item.get('number', '?')}"
    missing = [key for key in (*_PULL_FIELDS, *(("merged",) if single else ())) if key not in item]
    for side in ("head", "base"):
        end = item.get(side)
        if isinstance(end, dict):
            missing += [f"{side}.{key}" for key in _PULL_END_FIELDS if key not in end]
    if missing:
        raise GitHubResponseError(f"{where}: the answer lacks {missing} (an incomplete PR is never judged)")
    base_repo = item["base"].get("repo") if isinstance(item["base"], dict) else None
    if not isinstance(base_repo, dict) or not isinstance(base_repo.get("full_name"), str):
        raise GitHubResponseError(f"{where}: the base repository is missing")
    return parse_pull(item)


def pr_snapshot(pr: PullRequest) -> dict[str, Any]:
    """The security-relevant view of one PR that every observation of it must agree on (SNAPSHOT_FIELDS)."""
    return {"state": pr.state, "merged": bool(pr.merged), "draft": bool(pr.draft),
            "head_repo": (pr.head_repo or "").lower(), "head": pr.head, "head_sha": pr.head_sha,
            "base_repo": (pr.base_repo or "").lower(), "base": pr.base}


def cross_check(listing: list[PullRequest], observations: Mapping[str, Iterable[PullRequest]]) -> None:
    """Every PR of every other observation must be in the complete *listing* with the identical pr_snapshot
    (IncompleteListing / ConflictingObservation otherwise; see the module docstring)."""
    listed = {pr.number: pr for pr in listing}
    missing: set[int] = set()
    conflicts: list[str] = []
    for name, observed in observations.items():
        for pr in observed:
            known = listed.get(pr.number)
            if known is None:
                missing.add(pr.number)
                continue
            before, after = pr_snapshot(known), pr_snapshot(pr)
            differing = [f"{key} {before[key]!r} vs {after[key]!r}" for key in SNAPSHOT_FIELDS
                         if before[key] != after[key]]
            if differing:
                conflicts.append(f"PR #{pr.number}: the listing and {name} disagree on {', '.join(differing)}")
    if missing:
        raise IncompleteListing(f"the complete PR listing misses PR(s) {sorted(missing)} that GitHub returns for the "
                                "head, the commit or the intake PR: the listing is not complete")
    if conflicts:
        raise ConflictingObservation(clean_text("GitHub's answers about the same PR disagree (it changed while it was "
                                                "read, or an answer is stale), so neither is used: "
                                                + "; ".join(conflicts[:5]) + " — repeat the inspection", 900))


def _head_answers(prs: list[PullRequest], owner: str, source_ref: str) -> list[PullRequest]:
    """The head query's PRs, each of which must have the queried head (ConflictingObservation otherwise)."""
    for pr in prs:
        head_owner = (pr.head_repo or "").split("/", 1)[0].lower()
        if pr.head != source_ref or (pr.head_repo and head_owner != owner.lower()):
            raise ConflictingObservation(f"GitHub's head query for {owner}:{source_ref} returned PR #{pr.number} with "
                                         f"head {pr.head_repo or '?'}:{pr.head}, which does not answer it")
    return prs


def fetch_pull(client: Any, repository: str, number: int) -> PullRequest:
    """One PR as GitHub reports it on its own (`/pulls/{n}`), strictly parsed when live (GitHubError otherwise); an
    answer about another PR is ConflictingObservation."""
    if getattr(client, "live", False):
        resp = client._call("GET", f"repos/{repository}/pulls/{int(number)}",  # pylint: disable=protected-access
                            idempotent=True)
        pr = strict_pull(resp.data, single=True)
    else:
        pr = client.get_pull_request(number)
    if pr.number != int(number):
        raise ConflictingObservation(f"GitHub's answer for PR #{int(number)} describes PR #{pr.number}")
    return pr


def _unique(prs: list[PullRequest], what: str) -> list[PullRequest]:
    numbers = [pr.number for pr in prs]
    if len(set(numbers)) != len(numbers):
        raise IncompleteListing(f"{what} lists a PR more than once: the listing shifted while it was read")
    return prs


def match_candidates(prs: Iterable[PullRequest], *, source_repo: str, source_ref: str,
                     source_sha: str) -> list[PullRequest]:
    """The union of the branch match and the SHA match, de-duplicated by number, sorted by number."""
    found: dict[int, PullRequest] = {}
    for pr in prs:
        by_branch = (pr.head_repo or "").lower() == source_repo.lower() and pr.head == source_ref
        if by_branch or pr.head_sha == source_sha:
            found[pr.number] = pr
    return [found[number] for number in sorted(found)]


def discover_pull_requests(client: Any, *, repository: str, source_repo: str, source_ref: str, source_sha: str,
                           max_pages: int, observed: Mapping[str, Iterable[PullRequest]] | None = None) -> Discovery:
    """Every candidate PR for the head, from a complete listing that every other observation agrees with
    (GitHubError / IncompleteListing / ConflictingObservation otherwise). *observed*: further reads of PRs made by the
    caller (the intake PR read on its own), cross-checked against the listing as well."""
    extra = dict(observed or {})
    if getattr(client, "live", False):
        base = f"repos/{repository}"
        raw = strict_pages(client, f"{base}/pulls", {"state": "all", "sort": "created", "direction": "asc"},
                           max_pages=max_pages)
        prs = _unique([strict_pull(item) for item in raw], "the PR listing")
        owner = source_repo.split("/", 1)[0]
        by_head = _unique([strict_pull(item) for item in strict_pages(
            client, f"{base}/pulls", {"state": "all", "head": f"{owner}:{source_ref}"}, max_pages=max_pages)],
            "the head query")
        by_commit = _unique([strict_pull(item) for item in strict_pages(
            client, f"{base}/commits/{source_sha}/pulls", {}, max_pages=max_pages)], "the commit query")
        extra = {"the head query": _head_answers(by_head, owner, source_ref), "the commit query": by_commit, **extra}
        method = "github"
    else:
        prs = _unique(list(client.list_pull_requests(state="all")), "the local tracker")
        method = "local-tracker"
    cross_check(prs, extra)
    candidates = match_candidates(prs, source_repo=source_repo, source_ref=source_ref, source_sha=source_sha)
    return Discovery(candidates=candidates, listed=len(prs), method=method)


def recorded_pr_changes(recorded: Mapping[str, Any], pr: PullRequest) -> list[str]:
    """What changed in the PR verified and pinned at intake (empty: nothing), according to one read of it. Each change
    makes the job STALE only once the complete PR lookup agrees with that read (integration_inspect)."""
    changes = []
    if pr.head_sha != recorded["head_sha"]:
        changes.append(f"PR #{pr.number}'s head moved from {recorded['head_sha']} to {pr.head_sha or '?'}")
    if (pr.head_repo or "").lower() != str(recorded["head_repo"]).lower() or pr.head != recorded["head_ref"]:
        changes.append(f"PR #{pr.number}'s head is now {pr.head_repo or '?'}:{pr.head}, not "
                       f"{recorded['head_repo']}:{recorded['head_ref']}")
    if pr.base != recorded["base_ref"] or (pr.base_repo or "").lower() != str(recorded["base_repo"]).lower():
        changes.append(f"PR #{pr.number} now targets {pr.base_repo or '?'}:{pr.base}, not "
                       f"{recorded['base_repo']}:{recorded['base_ref']}")
    return changes


def reuse_problems(pr: PullRequest, *, repository: str, source_repo: str, source_ref: str, source_sha: str,
                   base_ref: str) -> list[str]:
    """Why one candidate PR cannot be reused (empty: it can)."""
    problems = []
    if pr.merged:
        problems.append("it is merged")
    elif pr.state != "open":
        problems.append(f"it is {pr.state}")
    if (pr.head_repo or "").lower() != source_repo.lower():
        problems.append(f"its head repository is {pr.head_repo or '(deleted)'}, not {source_repo}")
    if pr.head != source_ref:
        problems.append(f"its head branch is {pr.head}, not {source_ref}")
    if pr.head_sha != source_sha:
        problems.append(f"its head is at {pr.head_sha or '?'}, not source_sha {source_sha}")
    if pr.base != base_ref or (pr.base_repo or "").lower() != repository.lower():
        problems.append(f"it targets {pr.base_repo or '?'}:{pr.base}, not {repository}:{base_ref}")
    return problems


@dataclass
class PrDecision:
    """The outcome of the reuse rules."""

    result: str                      # none | reuse | refused
    number: int | None = None
    candidates: list[int] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    observed: list[dict[str, Any]] = field(default_factory=list)


def decide_reuse(candidates: list[PullRequest], *, repository: str, source_repo: str, source_ref: str,
                 source_sha: str, base_ref: str, recorded_number: int | None) -> PrDecision:
    """Design §4.1 (see the module docstring). Never proposes creating a second PR."""
    numbers = [pr.number for pr in candidates]
    observed = [{"number": pr.number, "state": "merged" if pr.merged else pr.state,
                 "head": f"{pr.head_repo or '?'}:{pr.head}", "head_sha": pr.head_sha, "base": pr.base}
                for pr in candidates[:MAX_LISTED]]
    if not candidates:
        if recorded_number is not None:
            return PrDecision("refused", None, [], [f"the PR #{recorded_number} recorded at intake is no longer a "
                                                    "candidate for this head"], observed)
        return PrDecision("none", None, [], [], observed)
    facts = {"repository": repository, "source_repo": source_repo, "source_ref": source_ref, "source_sha": source_sha,
             "base_ref": base_ref}
    problems = {pr.number: reuse_problems(pr, **facts) for pr in candidates}
    if len(candidates) == 1 and not problems[numbers[0]] and recorded_number in (None, numbers[0]):
        return PrDecision("reuse", numbers[0], numbers, [], observed)
    reasons = []
    if len(candidates) > 1:
        reasons.append(f"more than one candidate PR for this head: {', '.join(f'#{n}' for n in numbers)} — none is "
                       "reused and no other PR is created")
    if recorded_number is not None and recorded_number not in numbers:
        reasons.append(f"the PR #{recorded_number} recorded at intake is not a candidate for this head")
    reasons += [f"PR #{number} cannot be reused: {'; '.join(found)}" for number, found in problems.items() if found]
    return PrDecision("refused", None, numbers, [clean_text(r) for r in reasons[:MAX_LISTED]], observed)


# ---------------------------------------------------------------------------
# GitHub commit attribution
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Attribution:
    """GitHub's view of one commit (validated)."""

    sha: str
    author_login: str | None
    committer_login: str | None
    signature_state: str          # GitHub's GitSignatureState, or UNSIGNED
    signature_valid: bool
    signer_login: str | None
    signed_by_github: bool


_FRAGMENT = ("fragment Attribution on Commit { oid author { user { login } } committer { user { login } } "
             "signature { isValid state wasSignedByGitHub signer { login } } }")


def attribution_query(count: int) -> str:
    """The GraphQL query for *count* commits (aliases c0…, variables $o0…)."""
    variables = ", ".join(f"$o{i}: GitObjectID!" for i in range(count))
    fields = " ".join(f"c{i}: object(oid: $o{i}) {{ ...Attribution }}" for i in range(count))
    return (f"query IntegrationCommitAttribution($owner: String!, $name: String!, {variables}) "
            f"{{ repository(owner: $owner, name: $name) {{ {fields} }} }} {_FRAGMENT}")


_COMMIT_FIELDS = ("oid", "author", "committer", "signature")
_SIGNATURE_FIELDS = ("isValid", "state", "wasSignedByGitHub", "signer")


def _user_login(user: Any, where: str) -> str | None:
    """The login of a GraphQL `User` node, or None for `null` (no account); anything else is malformed."""
    if user is None:
        return None
    login = user.get("login") if isinstance(user, dict) else None
    if not isinstance(login, str) or not _LOGIN_RE.fullmatch(login):
        raise GitHubResponseError(f"{where}: unexpected GitHub login")
    return login


def _actor_login(actor: Any, where: str) -> str | None:
    """The account of a GraphQL `GitActor` (`{ user { login } }`): None when GitHub reports no actor or no account;
    a malformed actor (not an object, or without its requested `user` field) raises."""
    if actor is None:
        return None
    if not isinstance(actor, dict) or "user" not in actor:
        raise GitHubResponseError(f"{where}: unexpected author or committer (the requested user field is missing)")
    return _user_login(actor["user"], where)


def parse_attribution(sha: str, node: Any) -> Attribution:
    """One validated `Commit` node (untrusted input; fail-closed, see the module docstring)."""
    where = f"attribution of {sha[:12]}"
    if node is None:
        raise AttributionUnavailable(f"GitHub does not know commit {sha} (not pushed, or not yet indexed)")
    if not isinstance(node, dict) or node.get("oid") != sha:
        raise GitHubResponseError(f"{where}: the answer is not that commit")
    missing = [key for key in _COMMIT_FIELDS if key not in node]
    if missing:
        raise GitHubResponseError(f"{where}: the answer lacks {missing} (incomplete attribution is never verified)")
    signature = node["signature"]
    if signature is None:
        state, valid, signer, by_github = "UNSIGNED", False, None, False
    else:
        if not isinstance(signature, dict):
            raise GitHubResponseError(f"{where}: unexpected signature")
        missing = [key for key in _SIGNATURE_FIELDS if key not in signature]
        if missing:
            raise GitHubResponseError(f"{where}: the signature lacks {missing} (incomplete attribution is never "
                                      "verified)")
        state, valid, by_github = signature["state"], signature["isValid"], signature["wasSignedByGitHub"]
        if not isinstance(state, str) or not re.fullmatch(r"[A-Z_]{2,40}", state) or not isinstance(valid, bool) \
                or not isinstance(by_github, bool):
            raise GitHubResponseError(f"{where}: unexpected signature (state, isValid and wasSignedByGitHub must be "
                                      "given explicitly)")
        if valid != (state == "VALID"):
            raise GitHubResponseError(f"{where}: contradictory signature (state {state} with isValid {valid})")
        signer = _user_login(signature["signer"], where)
    return Attribution(sha=sha, author_login=_actor_login(node["author"], where),
                       committer_login=_actor_login(node["committer"], where), signature_state=state,
                       signature_valid=valid, signer_login=signer, signed_by_github=by_github)


def fetch_attribution(client: Any, repository: str, shas: list[str]) -> dict[str, Attribution] | None:
    """GitHub's attribution of every commit, or None when the tracker has none (the local mock tracker). A live
    failure propagates (GitHubError: the inspection defers)."""
    if not getattr(client, "live", False) or not hasattr(client, "graphql"):
        return None
    owner, name = repository.split("/", 1)
    result: dict[str, Attribution] = {}
    for start in range(0, len(shas), ATTRIBUTION_BATCH):
        batch = shas[start:start + ATTRIBUTION_BATCH]
        if not all(_SHA_RE.fullmatch(sha) for sha in batch):
            raise GitHubResponseError("attribution: commit IDs must be full SHAs")
        data = client.graphql(attribution_query(len(batch)),
                              {"owner": owner, "name": name, **{f"o{i}": sha for i, sha in enumerate(batch)}})
        repo = data.get("repository") if isinstance(data, dict) else None
        if not isinstance(repo, dict):
            raise AttributionUnavailable(f"GitHub returned no repository {repository} for the attribution query")
        for index, sha in enumerate(batch):
            result[sha] = parse_attribution(sha, repo.get(f"c{index}"))
    return result


@dataclass
class ProvenanceAssessment:
    """Per-commit provenance levels and what they demand."""

    source: str                                      # github | unavailable
    levels: dict[str, str] = field(default_factory=dict)
    refusals: list[str] = field(default_factory=list)
    deferred: str = ""
    observed: list[dict[str, Any]] = field(default_factory=list)

    def count(self, level: str) -> int:
        """How many commits have *level*."""
        return sum(1 for value in self.levels.values() if value == level)

    @property
    def needs_acceptance(self) -> bool:
        """True when a commit is not verified (the operator must accept unverified provenance explicitly)."""
        return any(level != "verified" for level in self.levels.values())


def assess_provenance(commits: list[CommitObject], attributions: dict[str, Attribution] | None,
                      trusted_logins: Iterable[str]) -> ProvenanceAssessment:
    """The provenance level of every commit (see the module docstring)."""
    trusted = {login.lower() for login in trusted_logins}
    if attributions is None:
        return ProvenanceAssessment(source="unavailable", levels={c.sha: "unverifiable" for c in commits})
    result = ProvenanceAssessment(source="github")
    for commit in commits:
        found = attributions.get(commit.sha)
        if found is None:
            result.levels[commit.sha] = "unverifiable"
            continue
        short = commit.sha[:12]
        if found.author_login and found.author_login.lower() not in trusted:
            result.refusals.append(f"GitHub attributes the author e-mail {clean_text(commit.author.email, 120)} of "
                                   f"commit {short} to the account {found.author_login}, which is not in "
                                   "github.integration.trusted_logins")
        level = "unverified"
        if found.signature_valid is True and found.signature_state == "VALID" and found.signed_by_github is False:
            identity = found.signer_login
            if identity is not None and identity.lower() in trusted:
                level = "verified"
            elif identity is not None:
                result.refusals.append(f"commit {short} carries a valid signature of {identity}, which is not in "
                                       "github.integration.trusted_logins")
        # A commit GitHub signed itself (web UI / API, "web-flow") stays unverified: GitHub vouches that it created
        # the commit for SOME authenticated user, while the author fields it shows can be set freely through the API.
        elif found.signature_state in TRANSIENT_SIGNATURE_STATES:
            result.deferred = result.deferred or (f"GitHub could not verify the signature of commit {short} right now "
                                                  f"({found.signature_state}); repeat later")
        result.levels[commit.sha] = level
        if level != "verified" and len(result.observed) < MAX_LISTED:
            result.observed.append({"commit": commit.sha, "author_login": found.author_login,
                                    "signature": found.signature_state, "level": level})
    return result
