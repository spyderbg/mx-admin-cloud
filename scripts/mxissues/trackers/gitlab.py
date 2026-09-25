"""GitLab backend (python-gitlab) — the original Workstream 2 tracker.

Status is a `status::*` scoped label. Scoped labels are mutually exclusive only on GitLab
Premium+ — the self-hosted instance is Enterprise Edition but that's a codebase
distinction, not proof of a Premium *license*, so exclusivity is enforced client-side
regardless (harmless if the license does enforce it server-side too). Never
add_labels/remove_labels — they're separate API calls that can race or leave two
status:: labels set. Always compute the full label set and send it as one replacement,
then re-fetch by iid and assert exactly the target status label survived. See
implementation_plan.md §10.
"""

from __future__ import annotations

from typing import Literal

import gitlab
from gitlab.exceptions import GitlabAuthenticationError, GitlabGetError
from gitlab.v4.objects import Project, ProjectIssue

from .. import fingerprint
from ..config import ConfigError, GitLabConfig, GitLabIssuesConfig, is_insecure_remote_host, load_gitlab_config
from ..tracker import (
    LABEL_COLORS,
    METADATA_LABELS,
    OWNERSHIP_LABEL,
    STATUSES,
    Comment,
    DoctorCheck,
    Issue,
    IssueNotFoundError,
    IssueTracker,
    TrackerError,
    TransitionError,
    TransitionResult,
    check_status,
)

STATUS_PREFIX = "status::"
STATUS_LABELS = tuple(f"{STATUS_PREFIX}{status}" for status in STATUSES)
ALL_LABELS = STATUS_LABELS + METADATA_LABELS


def _status_of(labels: list[str]) -> str | None:
    """The single status::* label's status, or None if it has none/multiple.

    More than one status label existing at all would mean a previous transition wasn't
    atomic — treat that as a data problem, not silently pick one.
    """
    found = [label.removeprefix(STATUS_PREFIX) for label in labels if label.startswith(STATUS_PREFIX)]
    return found[0] if len(found) == 1 and found[0] in STATUSES else None


def _to_issue(raw: ProjectIssue) -> Issue:
    labels = list(raw.labels or [])
    return Issue(
        number=raw.iid,
        title=raw.title,
        description=raw.description or "",
        labels=tuple(labels),
        state="closed" if raw.state == "closed" else "open",
        status=_status_of(labels),
        web_url=raw.web_url,
        raw=raw,
    )


class GitLabTracker(IssueTracker):
    name = "gitlab"
    display_name = "GitLab"

    def __init__(self, config: GitLabConfig) -> None:
        self.config = config
        self._gl: gitlab.Gitlab | None = None
        self._project: Project | None = None

    @classmethod
    def from_config(cls, settings: GitLabIssuesConfig) -> GitLabTracker:
        try:
            return cls(load_gitlab_config(settings))
        except ConfigError as exc:
            raise TrackerError(str(exc)) from exc

    @property
    def project(self) -> Project:
        if self._project is None:
            raise RuntimeError("GitLabTracker.connect() was not called")
        return self._project

    # -- setup / diagnostics ---------------------------------------------------

    def describe(self) -> list[tuple[str, str]]:
        return [("host", self.config.host), ("project", self.config.project)]

    def config_warnings(self) -> list[str]:
        if is_insecure_remote_host(self.config.host):
            return [
                f"{self.config.host} is plain HTTP and not loopback — the token crosses the network in "
                "the clear on every call. See implementation_plan.md Workstream 2 finding 1 for the "
                "SSH-tunnel mitigation.",
            ]
        return []

    def connect(self) -> None:
        try:
            gl = gitlab.Gitlab(self.config.host, private_token=self.config.token, timeout=30)
            gl.auth()  # raises GitlabAuthenticationError on a bad/expired token
        except GitlabAuthenticationError as exc:
            raise TrackerError(f"GitLab authentication failed against {self.config.host}: {exc}") from exc
        except Exception as exc:  # noqa: BLE001 - connection errors come in many shapes
            hint = ""
            if "Connection refused" in str(exc) or "Failed to establish" in str(exc):
                hint = (
                    "\nIf [issues.gitlab] host points at 127.0.0.1, the SSH tunnel probably isn't up:"
                    "\n  ssh -L 8080:localhost:80 <user>@82.103.77.45"
                )
            raise TrackerError(f"Could not reach {self.config.host}: {exc}{hint}") from exc
        try:
            self._project = gl.projects.get(self.config.project)
        except GitlabGetError as exc:
            raise TrackerError(f"GitLab project {self.config.project!r} not reachable on {self.config.host}: {exc}") from exc
        self._gl = gl

    def doctor(self) -> list[DoctorCheck]:
        project = self.project
        checks = [DoctorCheck("ok", f"token valid, project reachable (id={project.id}, default_branch={project.default_branch})")]

        try:
            version_info = self._gl.http_get("/version")
            version = version_info.get("version", "?")
            enterprise = bool(version_info.get("enterprise", False))
            edition = "Enterprise" if enterprise else "Community"
            note = (
                "Scoped-label (status::*) exclusivity MAY be enforced server-side on Premium+ — this tool "
                "enforces it client-side regardless (see mxissues/trackers/gitlab.py), so it's correct either way."
                if enterprise else
                "Community edition: scoped labels are not exclusive server-side — client-side enforcement is load-bearing here."
            )
            checks.append(DoctorCheck("ok", f"GitLab {version} ({edition} edition)", (note,)))
        except Exception as exc:  # noqa: BLE001
            checks.append(DoctorCheck("warn", f"could not read /version: {exc}"))

        try:
            existing = {label.name for label in project.labels.list(all=True)}
        except Exception as exc:  # noqa: BLE001
            checks.append(DoctorCheck("fail", f"could not list labels: {exc}"))
            return checks
        missing = [name for name in ALL_LABELS if name not in existing]
        if missing:
            checks.append(DoctorCheck("warn", f"{len(missing)} label(s) not yet created", tuple(missing), fixed_by_init=True))
        else:
            checks.append(DoctorCheck("ok", f"all {len(ALL_LABELS)} labels present"))
        return checks

    def init(self) -> tuple[list[str], list[str]]:
        existing = {label.name for label in self.project.labels.list(all=True)}
        created = []
        for name in ALL_LABELS:
            if name in existing:
                continue
            self.project.labels.create({"name": name, "color": f"#{LABEL_COLORS.get(name, '999999')}"})
            created.append(f"label {name}")
        return created, []

    # -- reads -----------------------------------------------------------------

    def _get_raw(self, number: int) -> ProjectIssue:
        try:
            return self.project.issues.get(number)
        except GitlabGetError as exc:
            raise IssueNotFoundError(f"No issue #{number} in {self.config.project}.") from exc

    def get(self, number: int) -> Issue:
        return _to_issue(self._get_raw(number))

    def list_issues(
            self, *, status: str | None = None, labels: tuple[str, ...] = (),
            owned_only: bool = True, state: Literal["open", "closed", "all"] = "all",
    ) -> list[Issue]:
        wanted = list(labels)
        if status is not None:
            check_status(status)
            wanted.append(f"{STATUS_PREFIX}{status}")
        if owned_only:
            wanted.append(OWNERSHIP_LABEL)
        kwargs: dict[str, object] = {"all": True}
        if wanted:
            kwargs["labels"] = wanted
        if state != "all":
            kwargs["state"] = "opened" if state == "open" else "closed"
        return [_to_issue(raw) for raw in self.project.issues.list(**kwargs)]

    def find_by_fingerprint(self, fp: str) -> Issue | None:
        matches = self.project.issues.list(search=f"mx-fp:{fp}", in_=["description"], state="all", all=True)
        for candidate in matches:
            meta = fingerprint.parse_meta_block(candidate.description or "")
            if meta and meta.get("fp") == fp:
                return _to_issue(candidate)
        return None

    def comments(self, number: int) -> list[Comment]:
        notes = self._get_raw(number).notes.list(all=True)
        result = [Comment(body=note.body, created_at=note.created_at, system=bool(getattr(note, "system", False))) for note in notes]
        return sorted(result, key=lambda comment: comment.created_at)

    # -- writes ----------------------------------------------------------------

    def create(self, *, title: str, description: str, labels: list[str], status: str = "open") -> Issue:
        check_status(status)
        all_labels = [label for label in labels if not label.startswith(STATUS_PREFIX)] + [f"{STATUS_PREFIX}{status}"]
        raw = self.project.issues.create({"title": title, "description": description, "labels": all_labels})
        return _to_issue(raw)

    def update_description(self, number: int, description: str) -> None:
        raw = self._get_raw(number)
        raw.description = description
        raw.save()

    def add_comment(self, number: int, body: str) -> None:
        self._get_raw(number).notes.create({"body": body})

    def set_status(self, number: int, target: str) -> TransitionResult:
        check_status(target)
        target_label = f"{STATUS_PREFIX}{target}"
        issue = self._get_raw(number)
        from_status = _status_of(list(issue.labels or []))
        was_closed = issue.state == "closed"

        issue.labels = [label for label in (issue.labels or []) if not label.startswith(STATUS_PREFIX)] + [target_label]

        closed = False
        reopened = False
        if target == "done":
            if not was_closed:
                issue.state_event = "close"
                closed = True
        elif was_closed:
            # A non-done target on an already-closed issue — e.g. the GitHub->GitLab mirror
            # auto-closed it via a "Closes <url>" commit, but a later regression reopened it
            # here as open; or a human is reopening by hand. Either way the label must not
            # say "open"/"in-progress" while GitLab itself still shows closed.
            issue.state_event = "reopen"
            reopened = True

        issue.save()

        # The read-back assertion is the point — it is the verification Free-tier scoped
        # labels don't give you, and it costs one cheap GET.
        refreshed = self.project.issues.get(number)
        current = [label for label in (refreshed.labels or []) if label.startswith(STATUS_PREFIX)]
        if current != [target_label]:
            raise TransitionError(
                f"Issue #{number}: expected exactly [{target_label!r}] after transition, "
                f"got {current!r}. GitLab-side state may be inconsistent — check the UI.",
            )
        if target == "done" and refreshed.state != "closed":
            raise TransitionError(f"Issue #{number}: status::done set but GitLab state is {refreshed.state!r}, not closed.")
        if target != "done" and refreshed.state == "closed":
            raise TransitionError(f"Issue #{number}: status is {target_label!r} but GitLab state is still closed.")

        return TransitionResult(number=number, from_status=from_status, to_status=target, closed=closed, reopened=reopened)

    # -- cross-references ------------------------------------------------------

    def issue_url(self, number: int) -> str:
        return f"{self.config.host.rstrip('/')}/{self.config.project}/-/issues/{number}"

    def closing_reference(self, number: int) -> str:
        """The full issue URL, never a bare `#<iid>`: the fix commit is pushed to GitHub
        first, where a bare `Closes #7` would close an unrelated GitHub issue/PR numbered 7.
        GitLab's closing-pattern parser accepts a full URL; GitHub's ignores a non-github.com
        one. See scripts/mxfix/git_target.py's module docstring.
        """
        return self.issue_url(number)
