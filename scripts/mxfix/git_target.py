"""Landing a fix: commit (with a close-on-merge reference GitLab's mirror will act on),
push to GitHub, open a PR via the REST API (no `gh` CLI dependency — GITHUB_PAT +
httpx), and cross-link both ways. See implementation_plan.md §17 "Landing the fix".

**Correction versus the plan's literal wording, made deliberately:** the plan's example
commit message is `Closes #<iid>` — a *bare* `#<iid>`. That syntax is exactly what
GitLab's mirror is meant to trigger on once the commit reaches GitLab's default branch
(implementation_plan.md Workstream 2 finding 2). But the same commit is pushed to
**GitHub** first, as part of this PR — and GitHub recognizes the identical
`Closes #<number>` pattern as *its own* auto-close syntax, against GitHub's issue/PR
numbering in this repository, which has nothing to do with the GitLab issue's iid. A
bare "Closes #7" could therefore close an unrelated GitHub issue or PR numbered 7. GitLab
also accepts a **full issue URL** in a closing pattern, not just `#iid` — so every
closing reference here uses the full `GITLAB_HOST/GITLAB_PROJECT/-/issues/<iid>` URL,
never a bare `#<iid>`: GitLab still auto-closes on it once mirrored, and GitHub's parser
does not recognize a non-github.com URL as anything special. The PR body's back-link
goes further and avoids GitHub's closing keywords entirely ("GitLab issue:", not
"Closes"), so it cannot even attempt to act on it.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import httpx

GITHUB_API = "https://api.github.com"


class GitTargetError(Exception):
    """Raised for a git or GitHub API failure while landing a fix."""


def _run_git(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)


def gitlab_issue_url(*, gitlab_host: str, gitlab_project: str, iid: int) -> str:
    return f"{gitlab_host.rstrip('/')}/{gitlab_project}/-/issues/{iid}"


def github_owner_repo(*, cwd: Path = Path(".")) -> str:
    """Parse 'owner/repo' out of the origin remote — handles both SSH and HTTPS forms."""
    result = _run_git(["remote", "get-url", "origin"], cwd=cwd)
    if result.returncode != 0:
        raise GitTargetError(f"git remote get-url origin failed: {result.stderr.strip()}")
    url = result.stdout.strip()

    ssh_match = re.match(r"^git@github\.com:(?P<path>[^/]+/[^/]+?)(\.git)?$", url)
    https_match = re.match(r"^https://github\.com/(?P<path>[^/]+/[^/]+?)(\.git)?$", url)
    match = ssh_match or https_match
    if not match:
        raise GitTargetError(f"origin remote {url!r} doesn't look like a github.com remote")
    return match.group("path")


def commit_all(worktree: Path, *, title: str, summary: str, gitlab_host: str, gitlab_project: str, issue_iid: int) -> str:
    """git add -A && git commit; message ends with a close-on-merge GitLab issue URL
    (see module docstring for why it's a URL, never a bare `#<iid>`). Returns the
    resulting commit sha, or raises GitTargetError if there was nothing to commit.
    """
    add_result = _run_git(["add", "-A"], cwd=worktree)
    if add_result.returncode != 0:
        raise GitTargetError(f"git add -A failed: {add_result.stderr.strip()}")

    status = _run_git(["status", "--porcelain"], cwd=worktree)
    if not status.stdout.strip():
        raise GitTargetError("nothing to commit — the fixer reported success but left no changes in the worktree")

    url = gitlab_issue_url(gitlab_host=gitlab_host, gitlab_project=gitlab_project, iid=issue_iid)
    message = f"{title}\n\n{summary.strip()}\n\nCloses {url}"

    commit_result = _run_git(["commit", "-m", message], cwd=worktree)
    if commit_result.returncode != 0:
        raise GitTargetError(f"git commit failed: {commit_result.stderr.strip()}")

    sha_result = _run_git(["rev-parse", "HEAD"], cwd=worktree)
    return sha_result.stdout.strip()


def merge_branch(integration_worktree: Path, branch: str, *, message: str) -> bool:
    """git merge --no-ff *branch* into whatever is checked out in *integration_worktree*
    (strategy/batch.py's integration branch). Returns True on a clean merge; on
    conflict, aborts the merge (leaving the integration worktree usable for the next
    candidate) and returns False.
    """
    result = _run_git(["merge", "--no-ff", "--no-edit", "-m", message, branch], cwd=integration_worktree)
    if result.returncode == 0:
        return True
    _run_git(["merge", "--abort"], cwd=integration_worktree)
    return False


def push_branch(worktree: Path, branch: str) -> None:
    result = _run_git(["push", "-u", "origin", branch], cwd=worktree)
    if result.returncode != 0:
        raise GitTargetError(f"git push origin {branch} failed: {result.stderr.strip()}")


@dataclass(frozen=True)
class PullRequest:
    number: int
    html_url: str
    api_url: str


def _github_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def open_pr(
        *,
        token: str,
        owner_repo: str,
        base: str,
        head: str,
        title: str,
        gitlab_host: str,
        gitlab_project: str,
        issue_iid: int,
        summary: str,
) -> PullRequest:
    """POST /repos/{owner_repo}/pulls. Body cross-links the GitLab issue without using a
    GitHub closing keyword (see module docstring) — the issue itself is closed by the
    GitLab mirror parsing the *commit* message on merge, not by this PR merging.
    """
    url = gitlab_issue_url(gitlab_host=gitlab_host, gitlab_project=gitlab_project, iid=issue_iid)
    body = f"{summary.strip()}\n\n---\nGitLab issue: {url}\n\n_Opened automatically by scripts/testenv.py run — see test_environment/README.md's \"Execution strategies\" section._"

    with httpx.Client(headers=_github_headers(token), timeout=30.0) as client:
        response = client.post(
            f"{GITHUB_API}/repos/{owner_repo}/pulls",
            json={"title": title, "head": head, "base": base, "body": body},
        )
    if response.status_code >= 400:
        raise GitTargetError(f"GitHub PR creation failed ({response.status_code}): {response.text[:500]}")

    data = response.json()
    return PullRequest(number=data["number"], html_url=data["html_url"], api_url=data["url"])


def comment_pr(*, token: str, owner_repo: str, pr_number: int, body: str) -> None:
    with httpx.Client(headers=_github_headers(token), timeout=30.0) as client:
        response = client.post(
            f"{GITHUB_API}/repos/{owner_repo}/issues/{pr_number}/comments",
            json={"body": body},
        )
    if response.status_code >= 400:
        raise GitTargetError(f"GitHub PR comment failed ({response.status_code}): {response.text[:500]}")
