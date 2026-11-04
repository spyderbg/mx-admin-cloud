"""GitHub Actions step for `issues: labeled` — the Feature Coordinator's planning labels (W4.5).

Run by .github/workflows/worker-issue-labels.yml as `python3 -I .github/scripts/worker_issue_labels.py`
with the job's own GITHUB_TOKEN (issues: write, contents: read). Standard library only; the digest code
is feature_coordinator/plan_digest.py from the same default-branch checkout. It never runs a model and
decides nothing by itself: the Desktop Worker's coordinator re-verifies everything (intake_plan.py).

  worker:plan     the labeling account needs at least WORKER_MIN_PERMISSION (default `write`), else
                  one refusal comment and the label is removed; otherwise nothing (the Worker plans).
  worker:approve  the newest plan comment (marker `feature-coordinator:plan`, by a WORKER_PLAN_AUTHORS
                  login — default: the repository owner — created before the label) is checked against
                  the Issue as it was when the label was added (the event payload): same repository and
                  Issue, unchanged title and body. Then ONE approval receipt is posted (marker
                  `feature-coordinator:approval-receipt`): repository and Issue identity, the label event
                  (ID, actor, time), the actor's permission, the plan comment/version/digest, the spec
                  digest, the approval digest (plan_digest.approval_statement) and this run's ID. Any
                  failed check → one refusal comment (marker `feature-coordinator:approval-rejected`)
                  and the label is removed.

Idempotent: a receipt or refusal for the same label event is never posted twice (a re-run or a duplicate
event finds it). Untrusted input (titles, bodies, comments) is only ever data: it is never interpolated into
a shell command, and the workflow passes nothing from the event on a command line.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Mapping

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from feature_coordinator import plan_digest as pd  # noqa: E402  pylint: disable=wrong-import-position

BOT = "github-actions[bot]"
REJECTION_FORMAT = "mx-worker-approval-rejection/v1"
PER_PAGE = 100
MAX_PAGES = 30


class Api:
    """A minimal GitHub REST client (urllib, the job token)."""

    def __init__(self, token: str, base: str = "https://api.github.com", *,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.token, self.base, self.sleep = token, base.rstrip("/"), sleep

    def request(self, method: str, path: str, body: Any = None, *, ok: tuple[int, ...] = ()) -> tuple[int, Any]:
        """One call; GETs are retried on 5xx and connection errors (a POST never blindly)."""
        data = None if body is None else json.dumps(body).encode("utf-8")
        for attempt in range(3):
            req = urllib.request.Request(f"{self.base}/{path.lstrip('/')}", data=data, method=method, headers={
                "Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "mx-admin-worker-issue-labels"})
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:  # nosec B310 - fixed https API base
                    raw = resp.read()
                    return resp.status, json.loads(raw) if raw else None
            except urllib.error.HTTPError as exc:
                if exc.code in ok:
                    return exc.code, None
                if exc.code >= 500 and method == "GET" and attempt < 2:
                    self.sleep(2 ** attempt)
                    continue
                raise RuntimeError(f"{method} {path}: HTTP {exc.code}") from None
            except (urllib.error.URLError, TimeoutError) as exc:
                if method == "GET" and attempt < 2:
                    self.sleep(2 ** attempt)
                    continue
                raise RuntimeError(f"{method} {path}: {type(exc).__name__}") from None
        raise RuntimeError(f"{method} {path}: no response")  # pragma: no cover

    def pages(self, path: str) -> list[Any]:
        """Every page of a list endpoint (bounded)."""
        items: list[Any] = []
        for page in range(1, MAX_PAGES + 1):
            _status, data = self.request("GET", f"{path}?per_page={PER_PAGE}&page={page}")
            if not isinstance(data, list):
                raise RuntimeError(f"GET {path}: expected a list")
            items += data
            if len(data) < PER_PAGE:
                return items
        raise RuntimeError(f"GET {path}: more than {MAX_PAGES} pages")


def _login(user: Any) -> str:
    return str(user.get("login", "")) if isinstance(user, dict) else ""


def label_events(api: Api, repo: str, number: int) -> list[dict[str, Any]]:
    """The Issue's events in plan_digest's shape."""
    out = []
    for item in api.pages(f"repos/{repo}/issues/{number}/events"):
        if isinstance(item, dict) and isinstance(item.get("id"), int):
            label = item.get("label") if isinstance(item.get("label"), dict) else {}
            out.append({"id": item["id"], "event": str(item.get("event", "")), "label": str(label.get("name", "")),
                        "actor": _login(item.get("actor")), "created_at": str(item.get("created_at", ""))})
    return out


def answered(comments: list[dict[str, Any]], event_id: int) -> bool:
    """True when this job already posted a receipt or refusal for the label event."""
    for comment in comments:
        if _login(comment.get("user")) != BOT:
            continue
        body = str(comment.get("body") or "")
        receipt = pd.decode_marker(pd.RECEIPT_MARKER, body)
        refusal = pd.decode_marker(pd.REJECTION_MARKER, body)
        if (isinstance(receipt, dict) and (receipt.get("label_event") or {}).get("id") == event_id) or \
                (isinstance(refusal, dict) and refusal.get("label_event_id") == event_id):
            return True
    return False


def refuse(api: Api, repo: str, number: int, label: str, event_id: int, reason: str) -> None:
    """One refusal comment, then the label is removed."""
    marker = pd.encode_marker(pd.REJECTION_MARKER, {"format": REJECTION_FORMAT, "label_event_id": event_id,
                                                     "label": label, "reason": reason[:500]})
    api.request("POST", f"repos/{repo}/issues/{number}/comments", {"body": (
        f"**`{label}` not accepted** by the approval workflow: {reason}\n\nThe label was removed; nothing was "
        f"approved or started.\n\n{marker}")})
    api.request("DELETE", f"repos/{repo}/issues/{number}/labels/{urllib.parse.quote(label, safe='')}", ok=(404,))


def _plan_comments(comments: list[dict[str, Any]], authors: set[str], before: str) -> list[tuple[dict, dict]]:
    found = []
    for comment in comments:
        if _login(comment.get("user")) not in authors or str(comment.get("created_at", "")) > before:
            continue
        plan = pd.decode_marker(pd.PLAN_MARKER, str(comment.get("body") or ""))
        if isinstance(plan, dict):
            found.append((comment, plan))
    return sorted(found, key=lambda pair: (str(pair[0].get("created_at", "")), int(pair[0].get("id", 0))))


def run(env: Mapping[str, str], api: Api, out: Callable[[str], None] = print) -> int:  # pylint: disable=too-many-locals,too-many-return-statements,too-many-branches
    """Handle one `issues: labeled` event (see the module docstring). Returns the exit code."""
    event = json.loads(Path(env["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
    label = str((event.get("label") or {}).get("name", ""))
    issue, repository = event.get("issue") or {}, event.get("repository") or {}
    if event.get("action") != "labeled" or label not in (pd.LABEL_PLAN, pd.LABEL_APPROVE):
        out(f"nothing to do for {event.get('action')!r} {label!r}")
        return 0
    if issue.get("pull_request") is not None or issue.get("state") != "open":
        out("not an open Issue: nothing to do")
        return 0
    repo, number, sender = str(repository["full_name"]), int(issue["number"]), _login(event.get("sender"))
    label_event = None
    for attempt in range(4):  # the events API may lag the webhook by a few seconds
        label_event = pd.latest_label_event(label_events(api, repo, number), label)
        if label_event is not None and label_event["actor"] == sender:
            break
        api.sleep(5 * (attempt + 1))
    if label_event is None or label_event["actor"] != sender:
        out(f"the `{label}` label event by {sender} is not visible yet; re-run this job")
        return 1
    comments = api.pages(f"repos/{repo}/issues/{number}/comments")
    if answered(comments, label_event["id"]):
        out(f"label event {label_event['id']} was already answered")
        return 0
    minimum = env.get("WORKER_MIN_PERMISSION", "write")
    status, data = api.request("GET", f"repos/{repo}/collaborators/{urllib.parse.quote(sender)}/permission",
                               ok=(404,))
    permission = str((data or {}).get("permission", "none")) if status == 200 else "none"
    if pd.PERMISSION_RANK.get(permission, -1) < pd.PERMISSION_RANK.get(minimum, 2):
        refuse(api, repo, number, label, label_event["id"],
               f"`{sender}` has repository permission `{permission}`; `{minimum}` is required")
        return 0
    if label == pd.LABEL_PLAN:
        out(f"plan request by {sender} ({permission}) accepted: the Desktop Worker plans it")
        return 0
    authors = {a.strip() for a in env.get("WORKER_PLAN_AUTHORS", "").split(",") if a.strip()} \
        or {_login(repository.get("owner"))}
    plans = _plan_comments(comments, authors, label_event["created_at"])
    if not plans:
        refuse(api, repo, number, label, label_event["id"], "there is no published plan to approve")
        return 0
    comment, plan = plans[-1]
    identity = {"number": number, "id": int(issue["id"]), "node_id": str(issue["node_id"])}
    if plan.get("schema") != pd.PLAN_SCHEMA or plan.get("issue") != identity \
            or plan.get("repository") != repo.lower():
        refuse(api, repo, number, label, label_event["id"], "the newest plan comment is not a plan of this Issue")
        return 0
    title, body = str(issue.get("title") or ""), str(issue.get("body") or "")
    spec = pd.spec_sha256(title, body)
    if spec != plan.get("spec_sha256"):
        refuse(api, repo, number, label, label_event["id"],
               f"the Issue's title or body changed after plan v{plan.get('version')} was made; remove and re-add "
               f"`{pd.LABEL_PLAN}` for a revised plan")
        return 0
    repo_identity = {"full_name": repo, "id": int(repository["id"])}
    try:
        approval = pd.approval_sha256(pd.approval_statement(repository=repo_identity, issue=identity, title=title,
                                                            body=body, plan=plan))
    except (KeyError, TypeError, ValueError):
        refuse(api, repo, number, label, label_event["id"], "the newest plan comment cannot be read")
        return 0
    receipt = {
        "format": pd.RECEIPT_FORMAT, "repository": {"full_name": repo.lower(), "id": int(repository["id"])},
        "issue": identity, "label_event": label_event, "actor_permission": permission,
        "plan": {"comment_id": int(comment["id"]), "version": int(plan["version"]), "sha256": pd.plan_sha256(plan)},
        "spec_sha256": spec, "approval": {"format": pd.APPROVAL_FORMAT, "sha256": approval},
        "workflow_run": {"id": int(env["GITHUB_RUN_ID"]), "attempt": int(env.get("GITHUB_RUN_ATTEMPT", "1"))},
    }
    problems = pd.receipt_problems(receipt)
    if problems:  # pragma: no cover - built above
        raise RuntimeError(f"internal error: {problems}")
    tasks = ", ".join(f"`{t['id']}` ({t['role']})" for t in plan["tasks"])
    api.request("POST", f"repos/{repo}/issues/{number}/comments", {"body": (
        f"**Approval receipt** for plan v{plan['version']} ({tasks}), label `{label}` added by `{sender}` "
        f"(`{permission}`).\n\nApproval digest `{pd.display_digest(approval)}`. This receipt is evidence only: the "
        "Desktop Worker's coordinator verifies it independently before anything is started.\n\n"
        + pd.encode_marker(pd.RECEIPT_MARKER, receipt))})
    out(f"receipt posted for plan v{plan['version']} (approval sha256 {approval})")
    return 0


def main() -> int:
    """Entry point (the workflow's step)."""
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print("GITHUB_TOKEN is not set")
        return 2
    try:
        return run(os.environ, Api(token, os.environ.get("GITHUB_API_URL", "https://api.github.com")))
    except (RuntimeError, KeyError, ValueError) as exc:
        print(f"failed: {type(exc).__name__}: {str(exc)[:300]}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
