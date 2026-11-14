"""Shared rig for the W4.7 M2/M3 tests (approve, inspect, validate, GitHub discovery, Git isolation).

Everything is disposable: a temporary checkout with a bare `origin` (the conftest `repo` fixture plus `origin` here),
a temporary state directory, a throw-away operator SSH key generated per test, signed authorization records, the
in-memory mock tracker or a scripted GitHub transport behind the REAL GitHubClient. No network, no real GitHub, and the
developer's checkout, keys and coordinator state are never touched. The scripted transport refuses every write
request: a test that made one would fail."""

# pylint: disable=missing-function-docstring,too-many-arguments,redefined-outer-name

from __future__ import annotations

import dataclasses
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

import pytest

from feature_coordinator import integration_intake
from feature_coordinator.config import GitHubConfig, ProjectConfig, default_config
from feature_coordinator.demo import DEMO_AUTHOR
from feature_coordinator.github_api import ApiResponse, GitHubClient
from feature_coordinator.integration import APPROVAL_KIND, APPROVAL_NAMESPACE, IntakeRequest, IntegrationJob, \
    IntegrationJobs, JobState
from feature_coordinator.integration_approval import SshSignatureVerifier, approve
from feature_coordinator.integration_config import GitHubIntegrationConfig
from feature_coordinator.integration_inspect import IntegrationInspector
from feature_coordinator.integration_intake import IntegrationIntake, ReadOnlyGit
from feature_coordinator.integration_validate import IntegrationValidator
from feature_coordinator.paths import PathPolicy
from feature_coordinator.store import StateStore

from fc_helpers import git

REPOSITORY = default_config().github.repository  # spyderbg/mx-admin-cloud
KNOWN = DEMO_AUTHOR[1]
STRANGER = ("Somebody Else", "stranger@example.invalid")
OPERATOR = "spyderbg"
BRANCH = "integration/improvements"


# -- repositories ---------------------------------------------------------------------------------------------

@pytest.fixture
def origin(tmp_path: Path, repo: Path) -> Path:
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", "--initial-branch=dev", str(bare)], check=True)
    git(repo, "remote", "add", "origin", str(bare))
    git(repo, "push", "-q", "origin", "dev")
    return bare


@pytest.fixture
def github_origin(monkeypatch: pytest.MonkeyPatch, origin: Path) -> Path:
    """The bare remote counts as github.com/<github.repository> (its URL is a path in the tests)."""
    real = integration_intake.github_repository_of
    monkeypatch.setattr(integration_intake, "github_repository_of",
                        lambda url, host: REPOSITORY if url == str(origin) else real(url, host))
    return origin


def head(repo: Path, ref: str = "dev") -> str:
    return git(repo, "rev-parse", ref).strip()


def change_on(repo: Path, branch: str, edits: dict[str, str | None], *, message: str = "change",
              author: tuple[str, str] = DEMO_AUTHOR, start: str = "dev", push: bool = True,
              prepare: Callable[[Path], None] | None = None) -> str:
    """One commit on *branch* (created from *start* when missing) writing (str) or deleting (None) files; pushed."""
    exists = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
                            capture_output=True, check=False).returncode == 0
    git(repo, "checkout", "-q", branch if exists else "-b", *(() if exists else (branch, start)))
    try:
        for rel, content in edits.items():
            path = repo / rel
            if content is None:
                git(repo, "rm", "-q", "-r", "--", rel)
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        if prepare is not None:
            prepare(repo)
        git(repo, "add", "-A")
        subprocess.run(["git", "-C", str(repo), "-c", f"user.name={author[0]}", "-c", f"user.email={author[1]}",
                        "commit", "-q", "--allow-empty", "--author", f"{author[0]} <{author[1]}>", "-m", message],
                       check=True, capture_output=True)
        sha = head(repo, "HEAD")
    finally:
        git(repo, "checkout", "-q", "dev")
    if push:
        git(repo, "push", "-q", "-f", "origin", f"{branch}")
    return sha


def request_for(repo: Path, source_sha: str, **changes: Any) -> IntakeRequest:
    fields = {"source_repo": REPOSITORY, "source_ref": BRANCH, "source_sha": source_sha, "base_ref": "dev",
              "base_sha": head(repo), "issue": 63}
    fields.update(changes)
    return IntakeRequest(**fields)


def tree_digest(git_dir: Path) -> str:
    import hashlib  # pylint: disable=import-outside-toplevel
    digest = hashlib.sha256()
    for path in sorted(p for p in git_dir.rglob("*") if p.is_file()):
        digest.update(str(path.relative_to(git_dir)).encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()


def snapshot(repo: Path, bare: Path) -> dict[str, Any]:
    return {"git": tree_digest(repo / ".git"), "origin": tree_digest(bare),
            "status": git(repo, "status", "--porcelain=v1", "--untracked-files=all")}


# -- the operator's key and authorization records ----------------------------------------------------------------

class OperatorKey:
    """A throw-away ed25519 key and an allowed-signers file in a private directory (outside the checkout)."""

    def __init__(self, home: Path, principal: str = OPERATOR) -> None:
        home.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.home = home
        self.allowed = home / "allowed_signers"
        self.allowed.write_text("", encoding="utf-8")
        self.allowed.chmod(0o600)
        self.keys: dict[str, Path] = {}
        self.key = self.new_key(principal)

    def new_key(self, principal: str, *, allow: bool = True, namespaces: str = APPROVAL_NAMESPACE) -> Path:
        key = self.home / f"key-{principal}-{len(self.keys)}"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", principal, "-f", str(key)], check=True)
        self.keys[principal] = key
        if allow:
            pub = key.with_suffix(".pub") if key.suffix else Path(f"{key}.pub")
            with self.allowed.open("a", encoding="utf-8") as out:
                out.write(f'{principal} namespaces="{namespaces}" {pub.read_text(encoding="utf-8").strip()}\n')
        return key

    def revoke_all(self) -> None:
        self.allowed.write_text("", encoding="utf-8")

    def sign(self, path: Path, *, key: Path | None = None, namespace: str = APPROVAL_NAMESPACE) -> Path:
        signature = Path(f"{path}.sig")
        signature.unlink(missing_ok=True)
        subprocess.run(["ssh-keygen", "-q", "-Y", "sign", "-f", str(key or self.key), "-n", namespace, str(path)],
                       check=True, capture_output=True)
        return signature


def stamp(when: datetime) -> str:
    """Whole seconds, as an operator typically writes it."""
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def precise_stamp(when: datetime) -> str:
    """Microseconds: consecutive default records are issued at distinct, increasing instants (approvals must be
    strictly ordered by issued_at)."""
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def record_for(job: IntegrationJob, **overrides: Any) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    data = {"kind": APPROVAL_KIND, "version": 1, "job": job.job_id, "stage": "inspect", "operator": OPERATOR,
            "issued_at": precise_stamp(now - timedelta(minutes=1)), "expires_at": stamp(now + timedelta(hours=2)),
            "source": {"repo": job.source["repo"], "ref": job.source["ref"], "sha": job.source["sha"]},
            "base": {"ref": job.base["ref"], "sha": job.base["expected_sha"]}, "issue": job.issue,
            "pr": (job.pr or {}).get("number"), "paths": [], "accept_unverified_provenance": True,
            "note": "inspect it"}
    data.update(overrides)
    return data


def integration_config(signers: Path | str = "", **changes: Any) -> ProjectConfig:
    config = default_config()
    fields = {"enabled": True, "known_authors": (KNOWN,), "operators": (OPERATOR,), "allowed_signers": str(signers),
              "trusted_logins": (OPERATOR,), **changes}
    return dataclasses.replace(config, github=dataclasses.replace(config.github,
                                                                  integration=GitHubIntegrationConfig(**fields)))


# -- the rig --------------------------------------------------------------------------------------------------

class Rig:
    """Intake, approve and inspect over one disposable checkout and state directory."""

    def __init__(self, repo: Path, state: Path, key: OperatorKey, *, config: ProjectConfig | None = None,
                 client: Any = None) -> None:
        self.repo, self.state, self.key = repo, state, key
        self.store = StateStore(state)
        self.config = config or integration_config(key.allowed)
        self.jobs = IntegrationJobs(self.store)
        self.client = client
        self.git = ReadOnlyGit(repo, self.config, self.store.locks, timeout=30)
        self.records = state.parent / "records"
        self.records.mkdir(exist_ok=True)
        self.counter = 0

    @property
    def verifier(self) -> SshSignatureVerifier:
        return SshSignatureVerifier(self.config.github.integration.allowed_signers, repo_root=self.repo,
                                    scratch=self.state / "tmp")

    def intake(self, request: IntakeRequest, *, apply: bool = True) -> Any:
        pr_client = (lambda: self.client) if self.client is not None else None
        return IntegrationIntake(self.store, self.config, self.git, pr_client=pr_client).run(request, apply=apply)

    def accepted(self, request: IntakeRequest) -> IntegrationJob:
        report = self.intake(request)
        assert report.outcome == "accepted", (report.outcome, report.reasons, report.deferred)
        return self.jobs.load(report.job["job_id"])

    def write_record(self, data: dict[str, Any], *, sign: bool = True, key: Path | None = None,
                     namespace: str = APPROVAL_NAMESPACE, text: str | None = None) -> Path:
        self.counter += 1
        path = self.records / f"approval-{self.counter}.json"
        path.write_text(text if text is not None else json.dumps(data, indent=2), encoding="utf-8")
        if sign:
            self.key.sign(path, key=key, namespace=namespace)
        return path

    def approve(self, job_id: str, *, apply: bool = True, path: Path | None = None, clock: Any = None,
                **overrides: Any) -> dict[str, Any]:
        if path is None:
            path = self.write_record(record_for(self.jobs.load(job_id), **overrides))
        kwargs = {"clock": clock} if clock is not None else {}
        return approve(self.jobs, self.config, job_id, stage="inspect", record_path=path, signature_path=None,
                       verifier=self.verifier, policy=PathPolicy(self.repo, self.config), apply=apply, **kwargs)

    def inspector(self, *, faults: tuple[str, ...] = (), clock: Any = None) -> IntegrationInspector:
        kwargs = {"clock": clock} if clock is not None else {}
        tracker = (lambda: self.client) if self.client is not None else None
        return IntegrationInspector(self.store, self.config, self.git, verifier=self.verifier, tracker=tracker,
                                    faults=faults, **kwargs)

    def inspect(self, job_id: str, *, apply: bool = True, faults: tuple[str, ...] = (), actor: str = "operator",
                clock: Any = None) -> Any:
        return self.inspector(faults=faults, clock=clock).run(job_id, apply=apply, actor=actor)

    def approved(self, request: IntakeRequest, **overrides: Any) -> IntegrationJob:
        """An accepted job with a recorded (valid) inspect approval."""
        job = self.accepted(request)
        result = self.approve(job.job_id, **overrides)
        assert result["outcome"] == "recorded", result["reasons"]
        return self.jobs.load(job.job_id)

    def state_of(self, job_id: str) -> JobState:
        return self.jobs.load(job_id).state

    def validator(self, *, gates: Any = None, faults: tuple[str, ...] = (), clock: Any = None) -> IntegrationValidator:
        """M3: the validator over this rig (*gates*: a gate runner factory; default the real sandboxed runner)."""
        kwargs = {"clock": clock} if clock is not None else {}
        tracker = (lambda: self.client) if self.client is not None else None
        return IntegrationValidator(self.store, self.config, self.git, verifier=self.verifier, tracker=tracker,
                                    gate_runner=gates, faults=faults, **kwargs)

    def validate(self, job_id: str, *, apply: bool = True, gates: Any = None, faults: tuple[str, ...] = (),
                 actor: str = "operator", clock: Any = None) -> Any:
        return self.validator(gates=gates, faults=faults, clock=clock).run(job_id, apply=apply, actor=actor)


# -- a scripted GitHub behind the real client -------------------------------------------------------------------

def pr_json(number: int, *, head_ref: str = BRANCH, head_sha: str, base: str = "dev", state: str = "open",
            merged: bool = False, head_repo: str | None = REPOSITORY, base_repo: str = REPOSITORY,
            draft: bool = False) -> dict[str, Any]:
    return {"number": number, "id": 700000 + number, "node_id": f"PR_kw{number}", "title": f"PR {number}", "body": "",
            "state": "closed" if merged else state, "draft": draft, "merged": merged,
            "merged_at": "2026-11-01T00:00:00Z" if merged else None, "merge_commit_sha": None,
            "html_url": f"https://github.com/{REPOSITORY}/pull/{number}", "user": {"login": "spyderbg"},
            "head": {"ref": head_ref, "sha": head_sha, "repo": {"full_name": head_repo} if head_repo else None},
            "base": {"ref": base, "sha": "b" * 40, "repo": {"full_name": base_repo}}}


def issue_json(number: int, *, state: str = "open", pull_request: bool = False) -> dict[str, Any]:
    """M3: an Issue as GitHub's REST API returns it (`pull_request` set: the number is a PR)."""
    data = {"number": number, "id": 800000 + number, "node_id": f"I_kw{number}", "title": f"Issue {number}",
            "body": "", "state": state, "state_reason": "completed" if state == "closed" else None, "labels": [],
            "user": {"login": OPERATOR}, "html_url": f"https://github.com/{REPOSITORY}/issues/{number}",
            "updated_at": "2026-11-01T00:00:00Z", "comments": 0}
    if pull_request:
        data["pull_request"] = {"url": f"https://api.github.com/repos/{REPOSITORY}/pulls/{number}"}
    return data


def open_issue(tracker: Any, number: int = 63, *, state: str = "open", pull_request: bool = False) -> None:
    """M3: Issue #*number* in the in-memory mock tracker (the job's Issue must exist and be open)."""
    tracker.issues[number] = {"number": number, "id": 900000 + number, "title": f"Issue {number}", "body": "",
                              "state": state, "state_reason": "completed" if state == "closed" else None,
                              "labels": [], "author": OPERATOR, "comments": [], "updated_at": "2026-11-01T00:00:00Z",
                              "is_pull_request": pull_request}


def commit_node(sha: str, *, author: str | None = OPERATOR, signature: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"oid": sha, "author": {"user": {"login": author} if author else None},
            "committer": {"user": {"login": author} if author else None}, "signature": signature}


class FakeGitHub:  # pylint: disable=too-many-instance-attributes
    """A Transport (github_api.Transport) serving PRs and commit attribution; any write raises AssertionError.
    *pages*: optional override page_number → (items, link header) for the full listing (pagination tests)."""

    def __init__(self, prs: list[dict[str, Any]] | None = None, *, attribution: dict[str, Any] | None = None,
                 per_page: int = 100) -> None:
        self.prs = list(prs or [])
        self.attribution = dict(attribution or {})
        self.default_author: str | None = OPERATOR
        self.per_page = per_page
        self.calls: list[tuple[str, str]] = []
        self.fail: list[tuple[str, Any]] = []     # (substring of "METHOD path", ApiResponse | Exception)
        self.pages: dict[int, tuple[list[dict[str, Any]], str]] = {}
        self.extra_by_commit: list[dict[str, Any]] = []
        self.issues: dict[int, dict[str, Any]] = {63: issue_json(63)}   # M3: the job's Issue (open)

    def request(self, method: str, path: str, body: Any = None) -> ApiResponse:  # pylint: disable=too-many-return-statements,too-many-branches
        self.calls.append((method, path))
        for index, (needle, outcome) in enumerate(self.fail):
            if needle in f"{method} {path}":
                del self.fail[index]
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome
        if method == "POST" and path == "graphql":
            return ApiResponse(200, {}, self._graphql(body))
        if method != "GET":
            raise AssertionError(f"the inspection made a write request: {method} {path}")
        parsed = urlparse(path)
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        route = parsed.path
        base = f"repos/{REPOSITORY}"
        if route == f"{base}/pulls":
            items = self.prs
            if "head" in query:
                owner, _sep, branch = query["head"].partition(":")
                items = [p for p in items if (p["head"]["repo"] or {}).get("full_name", "").split("/")[0] == owner
                         and p["head"]["ref"] == branch]
                return self._page(items, query, route)
            page = int(query.get("page", "1"))
            if page in self.pages:
                data, link = self.pages[page]
                return ApiResponse(200, {"link": link} if link else {}, data)
            return self._page(items, query, route)
        if route.startswith(f"{base}/commits/") and route.endswith("/pulls"):
            sha = route.split("/")[-2]
            return self._page([p for p in self.prs if p["head"]["sha"] == sha] + self.extra_by_commit, query, route)
        if route.startswith(f"{base}/pulls/"):
            number = int(route.rsplit("/", 1)[1])
            found = [p for p in self.prs if p["number"] == number]
            if not found:
                return ApiResponse(404, {}, {"message": "Not Found"})
            return ApiResponse(200, {}, found[0])
        if route.startswith(f"{base}/issues/"):
            number = int(route.rsplit("/", 1)[1])
            if number not in self.issues:
                return ApiResponse(404, {}, {"message": "Not Found"})
            return ApiResponse(200, {}, self.issues[number])
        raise AssertionError(f"unexpected request {method} {path}")

    def _page(self, items: list[dict[str, Any]], query: dict[str, str], route: str) -> ApiResponse:
        size = int(query.get("per_page", "100"))
        page = int(query.get("page", "1"))
        chunk = items[(page - 1) * size:page * size]
        total = max(1, -(-len(items) // size))
        links = []
        if page < total:
            links.append(f'<https://api.github.com/{route}?page={page + 1}&per_page={size}>; rel="next"')
            links.append(f'<https://api.github.com/{route}?page={total}&per_page={size}>; rel="last"')
        return ApiResponse(200, {"link": ", ".join(links)} if links else {}, chunk)

    def _graphql(self, body: Any) -> dict[str, Any]:
        variables = body["variables"]
        nodes = {}
        for name, sha in variables.items():
            if name.startswith("o"):
                index = name[1:]
                nodes[f"c{index}"] = self.attribution.get(sha, commit_node(sha, author=self.default_author))
        return {"data": {"repository": nodes}}

    def writes(self) -> list[tuple[str, str]]:
        return [(m, p) for m, p in self.calls if m != "GET" and p != "graphql"]


def live_client(transport: FakeGitHub, **settings: Any) -> GitHubClient:
    fields = {"repository": REPOSITORY, "min_write_interval_seconds": 0.0, "max_retries": 0,
              "backoff_base_seconds": 0.0, "max_pages": 20, **settings}
    return GitHubClient(transport, GitHubConfig(**fields), sleep=lambda _seconds: None)
