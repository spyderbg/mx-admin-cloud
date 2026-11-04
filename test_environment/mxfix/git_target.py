"""Landing a fix: commit (with a close-on-merge reference the issue tracker will act on),
push to GitHub, open a PR via the REST API (no `gh` CLI dependency — GITHUB_PAT +
httpx), and cross-link both ways. See implementation_plan.md §17 "Landing the fix".

The closing reference and the PR's back-link come from the configured issue tracker
(mxissues.tracker.IssueTracker.closing_reference()/link_line()), not from here:

  - GitLab: the full `<host>/<project>/-/issues/<iid>` URL ([issues.gitlab]), **never a bare
    `#<iid>`** — a deliberate correction versus the plan's literal `Closes #<iid>`. The
    commit is pushed to **GitHub** first, as part of this PR, and GitHub recognizes
    `Closes #<number>` as *its own* auto-close syntax against GitHub's numbering, which
    has nothing to do with the GitLab iid — a bare "Closes #7" could close an unrelated
    GitHub issue or PR numbered 7. GitLab also accepts a full issue URL in a closing
    pattern, and GitHub's parser ignores a non-github.com URL, so the URL form satisfies
    GitLab's auto-close (once the mirror pushes it to GitLab's default branch) while
    being inert on GitHub.
  - GitHub: `owner/repo#<number>` — GitHub closes the issue once the commit reaches the
    repository's default branch.

Either way the PR body's back-link avoids closing keywords entirely ("GitLab issue:
<url>", not "Closes"), so merging the PR into [git] base_branch never closes anything by
itself — the commit reaching the default branch does.
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


def commit_all(worktree: Path, *, title: str, summary: str, closing_reference: str) -> str:
    """git add -A && git commit; message ends with `Closes <closing_reference>` (the
    tracker's — see module docstring). Returns the resulting commit sha, or raises
    GitTargetError if there was nothing to commit.
    """
    add_result = _run_git(["add", "-A"], cwd=worktree)
    if add_result.returncode != 0:
        raise GitTargetError(f"git add -A failed: {add_result.stderr.strip()}")

    status = _run_git(["status", "--porcelain"], cwd=worktree)
    if not status.stdout.strip():
        raise GitTargetError("nothing to commit — the fixer reported success but left no changes in the worktree")

    message = f"{title}\n\n{summary.strip()}\n\nCloses {closing_reference}"

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
        issue_link: str,
        summary: str,
) -> PullRequest:
    """POST /repos/{owner_repo}/pulls. Body carries *issue_link* (the tracker's
    link_line(), which never uses a closing keyword — see module docstring): the issue
    itself is closed by the *commit's* closing reference reaching the default branch,
    not by this PR merging.
    """
    body = f"{summary.strip()}\n\n---\n{issue_link}\n\n_Opened automatically by test_environment/testenv.py run — see test_environment/README.md's \"Execution strategies\" section._"

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
