"""Shared rig for the W4.7 M5 tests (signed merge approval, PR handling, the GitHub merge, post-merge verification).

Everything is disposable and scripted: the checkout and the bare `origin` of the M2-M4 rig, a throw-away operator key
and a SCRIPTED GitHub behind the REAL `GitHubClient` (`FakeMergeGitHub`). The scripted GitHub performs a REAL merge — on
the disposable bare `origin`, with Git plumbing, never on the developer's repository — so the post-merge verification
reads genuine Git objects, and it can be told to misbehave (a merge that is not a merge commit of the approved
parents, a wrong tree, a lost response, a timeout, a refusal). No network, no real GitHub, no real merge, no model."""

# pylint: disable=missing-function-docstring,too-many-arguments,redefined-outer-name,too-many-instance-attributes

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from feature_coordinator.errors import ConfigError
from feature_coordinator.github_api import ApiResponse, TransportFailure
from feature_coordinator.integration import APPROVAL_KIND, IntegrationJob, latest_evidence
from feature_coordinator.integration_approval import approve
from feature_coordinator.integration_authority import IntegrationAnchors
from feature_coordinator.integration_merge import IntegrationMerger
from feature_coordinator.integration_merge_record import known_pr_number
from feature_coordinator.integration_merge_status import merge_approval_blockers
from feature_coordinator.paths import PathPolicy

from fc_integration_rig import BRANCH, OPERATOR, REPOSITORY, FakeGitHub, Rig, live_client, pr_json, precise_stamp, stamp

GIT_IDENTITY = {"GIT_AUTHOR_NAME": "GitHub", "GIT_AUTHOR_EMAIL": "noreply@github.com",
                "GIT_COMMITTER_NAME": "GitHub", "GIT_COMMITTER_EMAIL": "noreply@github.com",
                "GIT_AUTHOR_DATE": "2026-11-16T10:00:00Z", "GIT_COMMITTER_DATE": "2026-11-16T10:00:00Z"}


def bare(origin: Path, *args: str, env: dict[str, str] | None = None, check: bool = True) -> str:
    done = subprocess.run(["git", f"--git-dir={origin}", *args], capture_output=True, text=True, check=False,
                          env={**os.environ, **GIT_IDENTITY, **(env or {})})
    if check and done.returncode:
        raise AssertionError(f"git {' '.join(args)} failed: {done.stderr}")
    return done.stdout


class FakeMergeGitHub(FakeGitHub):
    """A Transport that serves PRs, commits, the base branch, comparisons and checks, creates PRs, and MERGES by
    really merging on the bare *origin*. `mode` decides what a merge does: `merge` (a merge commit with parents (dev
    tip, source) and the merged tree — what GitHub's merge-commit method does), `squash` (one parent: the source's
    tree on dev), `wrong-tree` (a merge commit with another tree), `wrong-parent` (a merge commit whose first parent is
    not dev)."""

    def __init__(self, origin: Path, prs: list[dict[str, Any]] | None = None, **kwargs: Any) -> None:
        super().__init__(prs, **kwargs)
        self.origin = origin
        self.mode = "merge"
        self.merge_state: dict[int, dict[str, Any]] = {}   # number → overrides (mergeable, mergeable_state, draft …)
        self.checks: dict[str, list[dict[str, Any]]] = {}  # sha → check runs
        self.statuses: dict[str, list[dict[str, Any]]] = {}
        self.check_pages: dict[str, list[list[dict[str, Any]]]] = {}   # sha → check runs by page (overrides `checks`)
        self.check_total: dict[str, int] = {}                         # sha → the total_count GitHub announces
        self.merge_requests: list[dict[str, Any]] = []
        self.created: list[dict[str, Any]] = []
        self.merge_failure: ApiResponse | Exception | None = None     # answers the merge INSTEAD of merging
        self.lose_merge_response = False     # merge for real, then fail like a lost connection
        self.create_failure: ApiResponse | Exception | None = None
        self.lose_create_response = False
        self.hide_prs_after_create = False   # the listing hides a PR created a moment ago (eventual consistency)
        self.unreadable: set[str] = set()    # route fragments that answer HTTP 500
        self.on_merge: Callable[[], None] | None = None
        self.commit_override: dict[str, dict[str, Any]] = {}
        self.viewer = OPERATOR              # the coordinator's own account (GET /user): its merges are "own"
        self.branch_override: dict[str, str] = {}   # branch → the tip GitHub's (lagging) branch read still shows

    # -- routing ------------------------------------------------------------------------------------------------------
    def request(self, method: str, path: str, body: Any = None) -> ApiResponse:  # pylint: disable=too-many-return-statements,too-many-branches
        parsed = urlparse(path)
        route, base = parsed.path, f"repos/{REPOSITORY}"
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        if any(fragment in route for fragment in self.unreadable):
            self.calls.append((method, path))
            return ApiResponse(500, {}, {"message": "Server Error"})
        if method == "GET" and route == "user":
            self.calls.append((method, path))
            return ApiResponse(200, {}, {"login": self.viewer})
        if method == "POST" and route == f"{base}/pulls":
            return self._create(method, path, body)
        if method == "PUT" and route.startswith(f"{base}/pulls/") and route.endswith("/merge"):
            return self._merge(method, path, int(route.split("/")[-2]), body)
        if method == "GET":
            if route.startswith(f"{base}/pulls/") and route.rsplit("/", 1)[1].isdigit():
                self.calls.append((method, path))
                number = int(route.rsplit("/", 1)[1])
                found = [p for p in self.prs if p["number"] == number]
                if not found:
                    return ApiResponse(404, {}, {"message": "Not Found"})
                return ApiResponse(200, {}, {"mergeable": True, "mergeable_state": "clean", **found[0],
                                             **self.merge_state.get(number, {})})
            if route.startswith(f"{base}/git/commits/"):
                return self._commit(method, path, route.rsplit("/", 1)[1])
            if route.startswith(f"{base}/branches/"):
                self.calls.append((method, path))
                branch = route[len(f"{base}/branches/"):]
                tip = self.branch_override.get(branch) or \
                    bare(self.origin, "rev-parse", f"refs/heads/{branch}", check=False).strip()
                return ApiResponse(200, {}, {"name": branch, "commit": {"sha": tip}}) if tip else \
                    ApiResponse(404, {}, {"message": "Branch not found"})
            if route.startswith(f"{base}/compare/"):
                return self._compare(method, path, route[len(f"{base}/compare/"):])
            if "/check-runs" in route:
                self.calls.append((method, path))
                sha = route.split("/")[-2]
                if sha in self.check_pages:
                    pages = self.check_pages[sha]
                    number = int(query.get("page", "1"))
                    total = self.check_total.get(sha, sum(len(page) for page in pages))
                    return ApiResponse(200, {}, {"total_count": total,
                                                 "check_runs": pages[number - 1] if number <= len(pages) else []})
                runs = self.checks.get(sha, [])
                return ApiResponse(200, {}, {"total_count": len(runs), "check_runs": runs})
            if route.startswith(f"{base}/commits/") and route.endswith("/statuses"):
                self.calls.append((method, path))
                return self._page(self.statuses.get(route.split("/")[-2], []), query, route)
        return super().request(method, path, body)

    # -- the base branch and commits -----------------------------------------------------------------------------------
    def _commit(self, method: str, path: str, sha: str) -> ApiResponse:
        self.calls.append((method, path))
        if sha in self.commit_override:
            return ApiResponse(200, {}, self.commit_override[sha])
        raw = bare(self.origin, "cat-file", "-p", sha, check=False)
        if not raw:
            return ApiResponse(404, {}, {"message": "Not Found"})
        tree = next(line.split()[1] for line in raw.splitlines() if line.startswith("tree "))
        parents = [{"sha": line.split()[1]} for line in raw.splitlines() if line.startswith("parent ")]
        return ApiResponse(200, {}, {"sha": sha, "tree": {"sha": tree}, "parents": parents})

    def _compare(self, method: str, path: str, basehead: str) -> ApiResponse:
        self.calls.append((method, path))
        base, _sep, head = basehead.partition("...")
        head_sha = bare(self.origin, "rev-parse", head, check=False).strip()
        if not head_sha:
            return ApiResponse(404, {}, {"message": "Not Found"})
        ahead = subprocess.run(["git", f"--git-dir={self.origin}", "merge-base", "--is-ancestor", base, head_sha],
                               check=False).returncode == 0
        behind = subprocess.run(["git", f"--git-dir={self.origin}", "merge-base", "--is-ancestor", head_sha, base],
                                check=False).returncode == 0
        status = "identical" if base == head_sha else "ahead" if ahead else "behind" if behind else "diverged"
        return ApiResponse(200, {}, {"status": status})

    # -- the two writes ---------------------------------------------------------------------------------------------
    def _create(self, method: str, path: str, body: Any) -> ApiResponse:
        self.calls.append((method, path))
        self.created.append(body)
        if self.create_failure is not None:
            outcome, self.create_failure = self.create_failure, None
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        sha = bare(self.origin, "rev-parse", f"refs/heads/{body['head']}").strip()
        number = max([p["number"] for p in self.prs] + [100]) + 1
        data = pr_json(number, head_ref=body["head"], head_sha=sha, base=body["base"], draft=body["draft"])
        data["title"], data["body"] = body["title"], body["body"]
        self.prs.append(data)
        if self.lose_create_response:
            self.lose_create_response = False
            raise TransportFailure("POST pulls: no response within 30s", maybe_applied=True)
        return ApiResponse(201, {}, data)

    def _merge(self, method: str, path: str, number: int, body: Any) -> ApiResponse:
        self.calls.append((method, path))
        self.merge_requests.append({"number": number, **body})
        if self.on_merge is not None:
            hook, self.on_merge = self.on_merge, None
            hook()  # pylint: disable=not-callable
        if self.merge_failure is not None:
            outcome, self.merge_failure = self.merge_failure, None
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        found = [p for p in self.prs if p["number"] == number]
        if not found:
            return ApiResponse(404, {}, {"message": "Not Found"})
        pr = found[0]
        if pr["merged"] or pr["state"] != "open":
            return ApiResponse(405, {}, {"message": "Pull Request is not mergeable"})
        if body.get("sha") != pr["head"]["sha"]:
            return ApiResponse(409, {}, {"message": "Head branch was modified. Review and try the merge again."})
        merge_sha = self.do_merge(pr)
        if self.lose_merge_response:
            self.lose_merge_response = False
            raise TransportFailure("PUT merge: no response within 30s", maybe_applied=True)
        return ApiResponse(200, {}, {"sha": merge_sha, "merged": True, "message": "Pull Request successfully merged"})

    def do_merge(self, pr: dict[str, Any], actor: str | None = None) -> str:
        """Really merge *pr* on the bare origin (per `mode`) and mark it merged by *actor* (default: the coordinator's
        own account, `viewer`); returns the merge commit."""
        dev, source = pr["base"]["ref"], pr["head"]["sha"]
        tip = bare(self.origin, "rev-parse", f"refs/heads/{dev}").strip()
        tree = bare(self.origin, "merge-tree", "--write-tree", tip, source).splitlines()[0].strip()
        parents, message = ["-p", tip, "-p", source], f"Merge pull request #{pr['number']} from {pr['head']['ref']}"
        if self.mode == "squash":
            tree, parents = bare(self.origin, "rev-parse", f"{source}^{{tree}}").strip(), ["-p", tip]
        elif self.mode == "wrong-tree":
            tree = bare(self.origin, "rev-parse", f"{tip}^{{tree}}").strip()
        elif self.mode == "wrong-parent":
            parents = ["-p", source, "-p", tip]
        commit = bare(self.origin, "commit-tree", tree, *parents, "-m", message).strip()
        bare(self.origin, "update-ref", f"refs/heads/{dev}", commit, tip)
        pr.update({"state": "closed", "merged": True, "merged_at": datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"), "merge_commit_sha": commit, "merged_by": {"login": actor or self.viewer}})
        return commit

    def writes(self) -> list[tuple[str, str]]:
        return [(m, p) for m, p in self.calls if m in ("POST", "PUT", "PATCH", "DELETE") and p != "graphql"]


# -- the signed merge approval -----------------------------------------------------------------------------------------

def merge_record_for(job: IntegrationJob, **overrides: Any) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    validation, review = latest_evidence(job, "validation"), latest_evidence(job, "review")
    assert validation is not None and review is not None and job.candidate is not None
    pr = known_pr_number(job)
    data: dict[str, Any] = {
        "kind": APPROVAL_KIND, "version": 2, "job": job.job_id, "stage": "merge", "operator": OPERATOR,
        "issued_at": precise_stamp(now - timedelta(minutes=1)), "expires_at": stamp(now + timedelta(hours=2)),
        "repository": REPOSITORY,
        "source": {"repo": job.source["repo"], "ref": job.source["ref"], "sha": job.source["sha"]},
        "base": {"ref": job.base["ref"], "sha": job.base["expected_sha"]}, "issue": job.issue, "pr": pr,
        "candidate": {"head_sha": job.candidate["head_sha"], "tree_sha": job.candidate["tree_sha"]},
        "validation": {"evidence": validation["id"], "sha256": validation["sha256"],
                       "plan_sha256": validation["plan"]["sha256"]},
        "review": {"evidence": review["id"], "sha256": review["sha256"], "outcome": review["outcome"]},
        "authorizes": {"operation": "merge", "method": "merge", "create_pr": pr is None}, "note": "merge it"}
    data.update(overrides)
    return data


def approve_merge(rig: Rig, job_id: str, *, apply: bool = True, path: Path | None = None, clock: Any = None,
                  **overrides: Any) -> dict[str, Any]:
    job = rig.jobs.load(job_id)
    if path is None:
        path = rig.write_record(merge_record_for(job, **overrides))
    kwargs = {"clock": clock} if clock is not None else {}
    return approve(rig.jobs, rig.config, job_id, stage="merge", record_path=path, signature_path=None,
                   verifier=rig.verifier, policy=PathPolicy(rig.repo, rig.config), apply=apply,
                   ready=lambda current: merge_approval_blockers(current, rig.config), **kwargs)


def authoritative_config(rig: Rig) -> Any:
    """The rig's stand-in for the project configuration FILE: `rig.config` as it is NOW (a test that replaces it changes
    the policy exactly as editing the file would); `rig.config_unreadable` set → the file cannot be read."""
    problem = getattr(rig, "config_unreadable", None)
    if problem:
        raise ConfigError(f"cannot read configuration: {problem}")
    return rig.config


def anchors_for(rig: Rig) -> IntegrationAnchors:
    """The trust anchors `integration merge` binds to its client — here the rig's state directory, checkout and
    configuration stand-in."""
    return IntegrationAnchors(state_dir=rig.state, repo=rig.repo, configuration=lambda: authoritative_config(rig))


def bind(rig: Rig) -> None:
    """Bind the rig's anchors to the rig's client (once; the client keeps them for good)."""
    if rig.client is not None and getattr(rig.client, "live", False) and not rig.client.integration_bound:
        rig.client._bind_integration(anchors_for(rig))  # pylint: disable=protected-access  # the test's composition


def merger(rig: Rig, *, faults: tuple[str, ...] = (), clock: Any = None, settle: float = 120.0) -> IntegrationMerger:
    """The merger of one run, over the rig's client with the rig's trust anchors bound (`anchors_for`): its write
    boundary re-reads the policy from `rig.config` at every GitHub write."""
    bind(rig)
    kwargs = {"clock": clock} if clock is not None else {}
    return IntegrationMerger(rig.store, rig.config, rig.git, verifier=rig.verifier,
                             tracker=(lambda: rig.client) if rig.client is not None else None, faults=faults,
                             sleep=lambda _seconds: None, settle_seconds=settle, **kwargs)


def merge(rig: Rig, job_id: str, *, apply: bool = True, faults: tuple[str, ...] = (), clock: Any = None,
          settle: float = 120.0, actor: str = "operator") -> Any:
    """One merge run."""
    return merger(rig, faults=faults, clock=clock, settle=settle).run(job_id, apply=apply, actor=actor)


def github_for(rig: Rig, origin: Path, **settings: Any) -> FakeMergeGitHub:
    """The scripted GitHub behind the real client, installed as the rig's client."""
    fake = FakeMergeGitHub(origin)
    rig.client = live_client(fake, integration=rig.config.github.integration, **settings)
    return fake


def ready_job(rig: Rig, launchers: Any, origin: Path, *, edits: dict[str, str | None] | None = None,
              sign: bool = True, **overrides: Any) -> tuple[IntegrationJob, FakeMergeGitHub]:
    """A job driven M1 → M4 (intake, inspect approval, inspection, M3 validation with a recording fake gate runner, M4
    review PASS by a fake reviewer process) to AWAITING_MERGE_APPROVAL, with the scripted GitHub installed as the
    client and — unless *sign* is false — a signed merge approval recorded (*overrides*: fields of the record,
    merge_record_for).
    Fixtures: `rrig` (test_fc_integration_review), `launchers`, `github_origin` (the `origin` of the rig)."""
    from test_fc_integration_review import review, validated_job  # pylint: disable=import-outside-toplevel
    job = validated_job(rig, edits)
    report = review(rig, job.job_id, launchers("pass"))
    assert report.outcome == "reviewed", (report.outcome, report.reasons)
    fake = github_for(rig, origin)
    if sign:
        result = approve_merge(rig, job.job_id, **overrides)
        assert result["outcome"] == "recorded", result["reasons"]
    return rig.jobs.load(job.job_id), fake


__all__ = ["BRANCH", "FakeMergeGitHub", "GIT_IDENTITY", "anchors_for", "approve_merge", "authoritative_config", "bare",
           "bind", "github_for", "merge", "merge_record_for", "merger", "ready_job"]
