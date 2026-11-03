"""Serialized Git mutations on a shared checkout — temporary repositories only."""

# pylint: disable=missing-function-docstring,redefined-outer-name

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from feature_coordinator.config import ProjectConfig
from feature_coordinator.errors import (
    ForeignStagedContentError,
    GitOperationError,
    HeadMismatchError,
    NothingToCommitError,
    ScopeViolationError,
    UnstableFileError,
    WritersActiveError,
)
from feature_coordinator.gitops import (
    COMMIT_SUBJECT_MAX,
    CommitRequest,
    GitCoordinator,
    commit_subject,
    file_digest,
)
from feature_coordinator.locks import LockSet
from feature_coordinator.paths import PathPolicy

from fc_helpers import child_env, git

BRANCH = "feature/f"


@pytest.fixture
def gc(repo: Path, state_dir: Path, config: ProjectConfig) -> GitCoordinator:
    coordinator = GitCoordinator(repo, LockSet(state_dir / "locks"), PathPolicy(repo, config),
                                 tmp_dir=state_dir / "tmp")
    coordinator.create_feature_branch(BRANCH, "dev")
    return coordinator


def write(repo: Path, rel: str, text: str) -> str:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return rel


def request(gc: GitCoordinator, role: str, paths: list[str], expected: str | None = None) -> CommitRequest:
    return CommitRequest(feature_id="f", task_id=f"f.{role}", role=role, branch=BRANCH,
                         expected_head=expected or gc.rev_parse(BRANCH),
                         files={p: file_digest(gc.root / p) for p in paths}, message=f"{role} work")


def test_feature_branch_is_created_from_the_base(gc: GitCoordinator, repo: Path) -> None:
    assert gc.current_branch() == BRANCH
    assert gc.rev_parse(BRANCH) == gc.rev_parse("dev")
    with pytest.raises(GitOperationError, match="already exists"):
        gc.create_feature_branch(BRANCH, "dev")
    assert gc.push_plan(BRANCH)[:3] == ["git", "push", "--set-upstream"] and "--force" not in gc.push_plan(BRANCH)
    del repo


def test_commit_contains_only_the_manifest_never_another_agents_unfinished_work(gc: GitCoordinator,
                                                                                repo: Path) -> None:
    write(repo, "backend/src/app.py", "ROUTES = ['/x']\n")
    write(repo, "frontend/src/view.ts", "export const views = ['half-done']\n")  # frontend's unfinished change
    write(repo, "cli/src/new.py", "pending = True\n")  # untracked file of another role
    result = gc.commit(request(gc, "backend", ["backend/src/app.py"]))
    assert git(repo, "show", "--name-only", "--format=", result.sha).split() == ["backend/src/app.py"]
    assert "Feature-Task: f.backend" in git(repo, "log", "-1", "--format=%B", result.sha)
    status = git(repo, "status", "--porcelain")
    assert " M frontend/src/view.ts" in status and "?? cli/" in status
    assert "backend/src/app.py" not in status  # shared index refreshed for the committed path
    assert (repo / "frontend/src/view.ts").read_text(encoding="utf-8").endswith("['half-done']\n")


PROMPT = ("Create the file backend/smoke/backend.txt containing exactly one line: backend ok\n"
          "Do not create, change or delete anything else. Do not run any command. Then reply 'done'.")
FITS = COMMIT_SUBJECT_MAX - len("backend: ")


@pytest.mark.parametrize("title,subject", [
    ("Export endpoint", "backend: Export endpoint"),               # a short one-line title is the subject
    ("  Export \t  endpoint  ", "backend: Export endpoint"),       # whitespace collapsed
    ("x" * FITS, "backend: " + "x" * FITS),                        # exactly at the limit
    ("x" * (FITS + 1), "backend: task f.api"),                     # too long
    (PROMPT, "backend: task f.api"),                               # a multi-line instruction
    ("first\rsecond", "backend: task f.api"),                      # any line break counts
    ("red \x1b[31malert", "backend: task f.api"),                  # control characters
    ("", "backend: task f.api"),
    ("   ", "backend: task f.api"),
], ids=["short", "whitespace", "at-limit", "too-long", "prompt", "carriage-return", "control", "empty", "blank"])
def test_commit_subject_is_short_and_never_a_task_prompt(title: str, subject: str) -> None:
    assert commit_subject("backend", "f.api", title) == subject
    assert "\n" not in subject and len(subject) <= COMMIT_SUBJECT_MAX


def test_a_trailer_like_title_cannot_change_the_trailers(gc: GitCoordinator, repo: Path) -> None:
    write(repo, "backend/src/app.py", "ROUTES = ['/y']\n")
    req = request(gc, "backend", ["backend/src/app.py"])
    subject = commit_subject("backend", req.task_id, "Feature-Task: other.task")
    sha = gc.commit(CommitRequest(**{**req.__dict__, "message": subject})).sha
    assert git(repo, "log", "-1", "--format=%(trailers:key=Feature-Task,valueonly)", sha).strip() == "f.backend"
    assert git(repo, "log", "-1", "--format=%B", sha) == (
        "backend: Feature-Task: other.task\n\nFeature: f\nFeature-Task: f.backend\nFeature-Role: backend\n\n")


def test_foreign_staged_content_is_refused(gc: GitCoordinator, repo: Path) -> None:
    write(repo, "frontend/src/view.ts", "staged by someone else\n")
    git(repo, "add", "frontend/src/view.ts")
    write(repo, "backend/src/app.py", "ROUTES = ['/y']\n")
    with pytest.raises(ForeignStagedContentError):
        gc.commit(request(gc, "backend", ["backend/src/app.py"]))


def test_paths_outside_the_role_are_refused(gc: GitCoordinator, repo: Path) -> None:
    write(repo, "cli/src/commands.py", "COMMANDS = ['x']\n")
    with pytest.raises(ScopeViolationError, match="owned by role 'cli'"):
        gc.commit(request(gc, "backend", ["cli/src/commands.py"]))
    with pytest.raises(ScopeViolationError):
        gc.commit(request(gc, "backend", ["backend/data/users.db"]))


def test_disjoint_concurrent_commit_is_built_on_the_new_head(gc: GitCoordinator, repo: Path) -> None:
    seen = gc.rev_parse(BRANCH)  # both workers started from this HEAD
    write(repo, "frontend/src/view.ts", "export const views = ['a']\n")
    write(repo, "backend/src/app.py", "ROUTES = ['/z']\n")
    first = gc.commit(request(gc, "frontend", ["frontend/src/view.ts"], expected=seen))
    second = gc.commit(request(gc, "backend", ["backend/src/app.py"], expected=seen))
    assert second.parent == first.sha and second.moved_over == (first.sha,)
    assert git(repo, "status", "--porcelain").strip() == ""


def test_overlapping_or_diverged_history_is_refused(gc: GitCoordinator, repo: Path) -> None:
    seen = gc.rev_parse(BRANCH)
    write(repo, "backend/src/app.py", "v1\n")
    gc.commit(request(gc, "backend", ["backend/src/app.py"]))
    write(repo, "backend/src/app.py", "v2\n")
    with pytest.raises(HeadMismatchError, match="changed on the branch"):
        gc.commit(request(gc, "backend", ["backend/src/app.py"], expected=seen))
    with pytest.raises(HeadMismatchError, match="not an ancestor"):
        gc.commit(request(gc, "backend", ["backend/src/app.py"], expected="0" * 40))


def test_unstable_or_unchanged_files(gc: GitCoordinator, repo: Path) -> None:
    write(repo, "backend/src/app.py", "declared\n")
    req = request(gc, "backend", ["backend/src/app.py"])
    write(repo, "backend/src/app.py", "still being written\n")
    with pytest.raises(UnstableFileError):
        gc.commit(req)
    with pytest.raises(NothingToCommitError):
        gc.commit(request(gc, "cli", ["cli/src/commands.py"]))  # unchanged since the seed commit


def test_deletion_is_committed(gc: GitCoordinator, repo: Path) -> None:
    (repo / "cli/src/commands.py").unlink()
    result = gc.commit(request(gc, "cli", ["cli/src/commands.py"]))
    assert git(repo, "show", "--name-status", "--format=", result.sha).split() == ["D", "cli/src/commands.py"]
    assert git(repo, "status", "--porcelain").strip() == ""


def test_branch_switch_is_refused_while_a_scoped_writer_is_active(gc: GitCoordinator, state_dir: Path) -> None:
    writer = LockSet(state_dir / "locks").project(shared=True).acquire()
    with pytest.raises(WritersActiveError, match="quiescent"):
        gc.switch_branch("dev")
    with pytest.raises(WritersActiveError):
        gc.create_feature_branch("feature/other", "dev")
    writer.release()
    gc.switch_branch("dev")
    assert gc.current_branch() == "dev"


def test_wrong_branch_is_refused(gc: GitCoordinator, repo: Path) -> None:
    gc.switch_branch("dev")
    write(repo, "backend/src/app.py", "x\n")
    with pytest.raises(HeadMismatchError, match="not 'feature/f'"):
        gc.commit(request(gc, "backend", ["backend/src/app.py"], expected=gc.rev_parse(BRANCH)))


def test_tree_changing_operations_refuse_a_dirty_checkout(gc: GitCoordinator, repo: Path) -> None:
    write(repo, "backend/src/app.py", "dirty\n")
    with pytest.raises(GitOperationError, match="uncommitted"):
        gc.switch_branch("dev")


PARALLEL_COMMIT = """
import sys
from pathlib import Path
from feature_coordinator.config import default_config
from feature_coordinator.gitops import CommitRequest, GitCoordinator, file_digest
from feature_coordinator.locks import LockSet
from feature_coordinator.paths import PathPolicy
repo, state, role, rel, expected = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], sys.argv[4], sys.argv[5]
gc = GitCoordinator(repo, LockSet(state / "locks"), PathPolicy(repo, default_config()), tmp_dir=state / "tmp")
for attempt in range(20):
    try:
        res = gc.commit(CommitRequest("f", "f." + role, role, "feature/f", expected, {rel: file_digest(repo / rel)}, role))
        print(res.sha)
        break
    except Exception as exc:
        print("retry", type(exc).__name__, exc, file=sys.stderr)
"""


def test_parallel_commits_from_two_processes_are_serialized(gc: GitCoordinator, repo: Path, state_dir: Path) -> None:
    seen = gc.rev_parse(BRANCH)
    work = {"backend": write(repo, "backend/src/app.py", "ROUTES = ['/p']\n"),
            "frontend": write(repo, "frontend/src/view.ts", "export const views = ['p']\n"),
            "cli": write(repo, "cli/src/commands.py", "COMMANDS = ['p']\n")}
    procs = [subprocess.Popen([sys.executable, "-c", PARALLEL_COMMIT, str(repo), str(state_dir), role, rel, seen],  # pylint: disable=consider-using-with
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=child_env())
             for role, rel in work.items()]
    shas = [p.communicate(timeout=120)[0].strip() for p in procs]
    assert all(len(s) == 40 for s in shas), shas
    log = git(repo, "log", "--format=%H", f"{seen}..{BRANCH}").split()
    assert sorted(log) == sorted(shas)  # three commits, a linear chain, none lost
    for sha in shas:
        assert len(git(repo, "show", "--name-only", "--format=", sha).split()) == 1  # one role's file each
    assert git(repo, "status", "--porcelain").strip() == ""
    git(repo, "fsck", "--no-progress")
