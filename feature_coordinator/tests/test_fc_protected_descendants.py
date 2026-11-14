"""Security S1: a protected name used as a DIRECTORY protects everything below it (disposable repositories only).

Regression tests for the protected-descendant bypass: PathPolicy judged protected patterns against the full path
only, so `backend/src/.env/secret.py` and `frontend/.env.local/config.ts` were writable by their owning roles and
landed through GitCoordinator.commit(). Every enforcement boundary is covered: PathPolicy (check, owner_of,
validate_grants), ScopedWorkspace, the commit gate, the bwrap sandbox plan and its real mounts, both runner
backends' post-run judgement, the planner's path check and the reviewer's secret filter.
"""

# pylint: disable=missing-function-docstring,redefined-outer-name

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from feature_coordinator.config import FULL_PROJECT, ProjectConfig
from feature_coordinator.demo import init_demo_repo
from feature_coordinator.errors import ScopeViolationError
from feature_coordinator.gitops import CommitRequest, GitCoordinator, file_digest
from feature_coordinator.integration_paths import path_class
from feature_coordinator.launchers import RealCliRunner, run_context
from feature_coordinator.locks import LockSet
from feature_coordinator.native_backend import NativeCliRunner, changed_paths, workspace_state
from feature_coordinator.operations import SELF_TEST_FILES
from feature_coordinator.paths import PathPolicy, ScopedWorkspace, path_and_ancestors, protected_prefix
from feature_coordinator.planning import path_problem
from feature_coordinator.review_context import withheld
from feature_coordinator.runners import RunRequest
from feature_coordinator.sandbox import SandboxBuilder
from feature_coordinator.supervisor import supervise

from fc_helpers import actions_prompt, fake_broker_factory, git

BRANCH = "feature/s1"

# The two bypasses Codex reproduced, and further protected names used as directories at any depth.
CODEX_BYPASSES = (("backend", "backend/src/.env/secret.py"), ("frontend", "frontend/.env.local/config.ts"))
NESTED_ENV = (
    ("backend", "backend/src/.env/secret.py"),
    ("backend", "backend/.env/settings.py"),
    ("backend", "docs_agents/backend/.env/notes.md"),
    ("frontend", "frontend/src/a/b/.env/c/d.ts"),
    ("cli", "cli/.env/x"),
)
NESTED_ENV_LOCAL = (
    ("frontend", "frontend/.env.local/config.ts"),
    ("frontend", "frontend/.env.development.local/vite.ts"),
    ("backend", "backend/.env.production/x/y.py"),
    ("cli", "cli/src/.env.test/fixture.json"),
)
NESTED_GIT = (
    ("backend", "backend/sub/.git/config"),
    ("backend", "backend/sub/.git/hooks/pre-commit"),
    ("backend", "backend/sub/.git"),            # a gitfile (`gitdir: …`) is Git's directory entry too
    ("frontend", "frontend/vendor/.GIT/config"),  # Git refuses `.git` in any case, with trailing dots or spaces
    ("cli", "cli/x/.git./config"),
    ("cli", "cli/x/.Git /hooks/post-checkout"),
    ("coordination", "docs_agents/coordination/.git/info/attributes"),
)
LEGITIMATE = (
    ("backend", "backend/src/app.py"),
    ("backend", "backend/src/environment.py"),
    ("backend", "backend/src/env/settings.py"),
    ("backend", "backend/src/x.env"),
    ("backend", "backend/src/.envrc"),
    ("backend", "backend/src/git/util.py"),
    ("backend", "backend/src/.gitignore"),
    ("backend", "backend/src/.gitkeep"),
    ("backend", "docs_agents/backend/services/content/README.md"),
    ("cli", "cli/src/new_command.py"),
    ("frontend", "frontend/src/environment.ts"),
    ("frontend", "frontend/src/types/contentSchemas.ts"),
    ("frontend", "frontend/.github_cache/x.json"),
    ("coordination", "docs_agents/coordination/feature-workflow.md"),
)


@pytest.fixture
def policy(repo: Path, config: ProjectConfig) -> PathPolicy:
    return PathPolicy(repo, config)


@pytest.fixture
def gc(repo: Path, state_dir: Path, config: ProjectConfig) -> GitCoordinator:
    coordinator = GitCoordinator(repo, LockSet(state_dir / "locks"), PathPolicy(repo, config),
                                 tmp_dir=state_dir / "tmp")
    coordinator.create_feature_branch(BRANCH, "dev")
    return coordinator


def write(repo: Path, rel: str, text: str = "SECRET=exfiltrated\n") -> str:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return rel


def commit_request(gc: GitCoordinator, role: str, files: dict[str, str | None],
                   grants: tuple[str, ...] = ()) -> CommitRequest:
    return CommitRequest(feature_id="s1", task_id=f"s1.{role}", role=role, branch=BRANCH,
                         expected_head=gc.rev_parse(BRANCH), files=files, message=f"{role} work", grants=grants)


def assert_refused_and_untouched(gc: GitCoordinator, repo: Path, request: CommitRequest, *protected: str) -> None:
    head = gc.rev_parse(BRANCH)
    with pytest.raises(ScopeViolationError, match="protected") as excinfo:
        gc.commit(request)
    for rel in protected:
        assert rel in str(excinfo.value)
    assert gc.rev_parse(BRANCH) == head, "nothing may land"
    assert not git(repo, "diff", "--cached", "--name-only").strip(), "nothing may be staged in the shared index"


# -- PathPolicy -----------------------------------------------------------------------------------------------

def test_path_and_ancestors() -> None:
    assert path_and_ancestors("a/b/c") == ["a", "a/b", "a/b/c"] and path_and_ancestors("a") == ["a"]


@pytest.mark.parametrize("role,path", [*NESTED_ENV, *NESTED_ENV_LOCAL, *NESTED_GIT],
                         ids=lambda v: v if "/" in str(v) else None)
def test_protected_descendants_are_writable_by_no_role(policy: PathPolicy, config: ProjectConfig,
                                                       role: str, path: str) -> None:
    """Not by the role whose tree holds them, not by any other role, not by full-project, whatever is granted."""
    assert policy.owner_of(path) is None, "a protected descendant has no owner"
    assert policy.protected_by(path) is not None
    for who in (role, *config.roles, FULL_PROJECT):
        for grants in ((), (path,), ("docker/**",)):
            decision = policy.check(who, path, grants=grants)
            assert not decision.allowed and "protected" in decision.reason, (who, grants, decision.reason)
    if policy.protected_by(path) != path:  # the gitfile `backend/sub/.git` is itself the protected entry
        assert "it lies inside the protected path" in policy.check(role, path).reason


@pytest.mark.parametrize("role,path", CODEX_BYPASSES)
def test_the_codex_reproductions_are_denied(policy: PathPolicy, role: str, path: str) -> None:
    decision = policy.check(role, path)
    directory = path.rsplit("/", 1)[0]
    assert not decision.allowed and decision.owner is None
    assert decision.reason == (f"{path!r} is protected (it lies inside the protected path {directory!r}) "
                               "(never writable)")
    with pytest.raises(ScopeViolationError, match="protected"):
        policy.require(role, [path])


@pytest.mark.parametrize("role,path", LEGITIMATE)
def test_legitimate_paths_keep_their_owner_and_stay_writable(policy: PathPolicy, role: str, path: str) -> None:
    decision = policy.check(role, path)
    assert decision.allowed and decision.owner == role, decision.reason
    assert policy.owner_of(path) == role and policy.protected_by(path) is None


@pytest.mark.parametrize("path", [".env", ".env.local", ".git/config", ".git", "content/foods/items.csv", "data/x",
                                  "backend/data/users.db", "backend/.env", "frontend/.env.development.local",
                                  "test_environment/mxfix/fixer.py", "test_environment/testenv.local.toml"])
def test_the_configured_protections_are_unchanged(policy: PathPolicy, config: ProjectConfig, path: str) -> None:
    assert policy.protected_by(path) is not None and policy.owner_of(path) is None
    for role in (*config.roles, FULL_PROJECT):
        assert not policy.check(role, path, grants=(path,)).allowed


def test_paths_owned_by_other_roles_still_report_their_owner(policy: PathPolicy) -> None:
    decision = policy.check("backend", "frontend/src/view.ts")
    assert not decision.allowed and "owned by role 'frontend'" in decision.reason
    # ...but a protected descendant inside another role's tree is protected, not "owned by" that role.
    decision = policy.check("backend", "frontend/.env.local/config.ts")
    assert not decision.allowed and "protected" in decision.reason and decision.owner is None


def test_a_path_inside_an_excluded_path_is_excluded(policy: PathPolicy) -> None:
    """automation excludes the exact paths scripts/deploy.sh and test_environment/testenv.toml: the same names used
    as directories cannot launder files past the exclusion (they stay approval-required / deny-by-default)."""
    for path in ("scripts/deploy.sh/payload.sh", "test_environment/testenv.toml/x.py"):
        assert policy.owner_of(path) is None
        decision = policy.check("automation", path)
        assert not decision.allowed and "excluded from role 'automation'" in decision.reason, decision.reason
    assert policy.check("automation", "scripts/deploy_helpers/x.sh").allowed  # a sibling name is still owned


# -- symlinks -------------------------------------------------------------------------------------------------

def test_a_symlinked_directory_into_a_protected_directory(policy: PathPolicy, repo: Path) -> None:
    write(repo, "backend/src/.env/real.py")
    os.symlink(".env", repo / "backend/src/cfg")
    decision = policy.check("backend", "backend/src/cfg/secret.py")
    assert not decision.allowed and "'backend/src/.env/secret.py' is protected" in decision.reason


def test_a_dangling_symlink_into_a_protected_name(policy: PathPolicy, repo: Path) -> None:
    os.symlink(".env.local", repo / "frontend/later")  # the target does not exist yet
    decision = policy.check("frontend", "frontend/later/config.ts")
    assert not decision.allowed and "frontend/.env.local" in decision.reason


def test_a_symlink_into_a_nested_git_directory(policy: PathPolicy, repo: Path) -> None:
    (repo / "backend/sub/.git").mkdir(parents=True)
    os.symlink("sub/.git", repo / "backend/gitdir")
    os.symlink("../.git", repo / "backend/rootgit")
    for path in ("backend/gitdir/config", "backend/rootgit/hooks/pre-commit"):
        decision = policy.check("backend", path)
        assert not decision.allowed and "protected" in decision.reason, path


def test_a_symlink_named_like_a_protected_directory(policy: PathPolicy, repo: Path) -> None:
    (repo / "backend/src/real").mkdir(parents=True)
    os.symlink("real", repo / "backend/src/.env")
    decision = policy.check("backend", "backend/src/.env/x.py")  # the lexical path is protected
    assert not decision.allowed and "protected" in decision.reason


def test_a_symlink_into_another_roles_protected_directory(policy: PathPolicy, repo: Path) -> None:
    write(repo, "frontend/.env.local/config.ts")
    os.symlink("../frontend/.env.local", repo / "backend/ui")
    decision = policy.check("backend", "backend/ui/config.ts")
    assert not decision.allowed and "'frontend/.env.local/config.ts' is protected" in decision.reason


# -- grants ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("grant", [
    "docker/.env/compose.yml", "docker/.env.local/x.yml", "docker/.env.local/**", ".github/.env.ci/token",
    "docker/sub/.git/config", ".claude/.git/hooks/pre-commit", "docker/.GIT/**", ".env.d/**",
])
def test_grants_targeting_protected_descendants_are_refused(policy: PathPolicy, grant: str) -> None:
    with pytest.raises(ScopeViolationError, match="targets a protected path"):
        policy.validate_grants((grant,))
    with pytest.raises(ScopeViolationError, match="targets a protected path"):
        ScopedWorkspace(policy, FULL_PROJECT, grants=(grant,))


def test_check_refuses_protected_descendants_even_with_a_matching_grant(policy: PathPolicy) -> None:
    """check() puts protection before grants: a grant passed straight to it, past validate_grants, opens nothing."""
    for path in ("docker/.env.local/x.yml", "docker/.env/compose.yml", "docker/sub/.git/config"):
        for grants in ((path,), ("docker/**",)):
            for role in ("frontend", FULL_PROJECT):
                decision = policy.check(role, path, grants=grants)
                assert not decision.allowed and "protected" in decision.reason, (path, grants, role)
    # The same broad grant still opens the approval-required tree's legitimate files.
    assert policy.validate_grants(("docker/**",)) == ("docker/**",)
    assert policy.check(FULL_PROJECT, "docker/compose.yml", grants=("docker/**",)).allowed


# -- ScopedWorkspace (mock and OpenAI-compatible runners) --------------------------------------------------------

def test_scoped_workspace_refuses_new_protected_descendants_before_creating_anything(policy: PathPolicy,
                                                                                    repo: Path) -> None:
    ws = ScopedWorkspace(policy, "backend")
    for path in ("backend/src/.env/secret.py", "backend/new/.env.local/x.py", "backend/sub/.git/config"):
        with pytest.raises(ScopeViolationError, match="protected"):
            ws.write_text(path, "x\n")
        assert not (repo / path).parent.exists(), "the protected directory must not even be created"
    assert ws.write_text("backend/src/new.py", "ok\n") == "backend/src/new.py" and ws.changed == ["backend/src/new.py"]


def test_scoped_workspace_never_reads_inside_env_directories_or_through_symlinks(policy: PathPolicy,
                                                                                 repo: Path) -> None:
    write(repo, "backend/src/.env/key", "SECRET=1\n")
    write(repo, ".env", "SECRET=2\n")
    os.symlink(".env/key", repo / "backend/src/innocent.py")
    os.symlink("../../.env", repo / "backend/src/config.py")
    ws = ScopedWorkspace(policy, "backend")
    for path in ("backend/src/.env/key", "backend/src/innocent.py", "backend/src/config.py"):
        with pytest.raises(ScopeViolationError, match="secrets"):
            ws.read_text(path)
    assert ws.read_text("backend/src/app.py") == "ROUTES = []\n"


# -- the commit gate (GitCoordinator.commit) ---------------------------------------------------------------------

@pytest.mark.parametrize("role,path", CODEX_BYPASSES)
def test_codex_commit_reproduction_is_refused(gc: GitCoordinator, repo: Path, role: str, path: str) -> None:
    """The original reproduction: the owning role commits a file inside a protected directory."""
    write(repo, path)
    assert_refused_and_untouched(gc, repo, commit_request(gc, role, {path: file_digest(repo / path)}), path)


@pytest.mark.parametrize("role,path", [*NESTED_ENV, *NESTED_ENV_LOCAL, ("backend", "backend/sub/.git/config")])
def test_commit_gate_refuses_every_protected_descendant(gc: GitCoordinator, repo: Path, role: str, path: str) -> None:
    write(repo, path)
    ok = write(repo, f"{role}/ok.txt", "fine\n")
    # One protected path refuses the whole manifest: the legitimate file does not land either.
    request = commit_request(gc, role, {ok: file_digest(repo / ok), path: file_digest(repo / path)})
    assert_refused_and_untouched(gc, repo, request, path)
    assert gc.commit(commit_request(gc, role, {ok: file_digest(repo / ok)})).paths == (ok,)


def test_commit_gate_refuses_a_granted_protected_descendant(gc: GitCoordinator, repo: Path) -> None:
    path = write(repo, "docker/.env.local/x.yml")
    for grants in ((path,), ("docker/**",)):
        assert_refused_and_untouched(gc, repo, commit_request(gc, FULL_PROJECT, {path: file_digest(repo / path)},
                                                              grants), path)


def test_commit_gate_refuses_a_rename_into_a_protected_directory(gc: GitCoordinator, repo: Path) -> None:
    os.makedirs(repo / "backend/src/.env")
    os.rename(repo / "backend/src/app.py", repo / "backend/src/.env/app.py")
    request = commit_request(gc, "backend", {"backend/src/app.py": None,
                                             "backend/src/.env/app.py": file_digest(repo / "backend/src/.env/app.py")})
    assert_refused_and_untouched(gc, repo, request, "backend/src/.env/app.py")


def test_commit_gate_refuses_renaming_or_deleting_a_tracked_protected_descendant(tmp_path: Path, state_dir: Path,
                                                                                 config: ProjectConfig) -> None:
    """A protected descendant already in the history (e.g. committed before this fix) cannot be removed, moved out
    or changed by a role either: a deletion is a write."""
    repo = tmp_path / "legacy"
    init_demo_repo(repo, {"backend/src/app.py": "ROUTES = []\n", "backend/src/.env/legacy.py": "KEY = 'x'\n"})
    gc = GitCoordinator(repo, LockSet(state_dir / "locks"), PathPolicy(repo, config), tmp_dir=state_dir / "tmp")
    gc.create_feature_branch(BRANCH, "dev")
    os.rename(repo / "backend/src/.env/legacy.py", repo / "backend/src/legacy.py")
    rename = commit_request(gc, "backend", {"backend/src/.env/legacy.py": None,
                                            "backend/src/legacy.py": file_digest(repo / "backend/src/legacy.py")})
    assert_refused_and_untouched(gc, repo, rename, "backend/src/.env/legacy.py")
    assert_refused_and_untouched(gc, repo, commit_request(gc, "backend", {"backend/src/.env/legacy.py": None}),
                                 "backend/src/.env/legacy.py")


def test_commit_gate_refuses_a_symlinked_path_into_a_protected_directory(gc: GitCoordinator, repo: Path) -> None:
    write(repo, "backend/src/.env/key")
    os.symlink(".env", repo / "backend/src/cfg")
    request = commit_request(gc, "backend", {"backend/src/cfg/key": file_digest(repo / "backend/src/.env/key")})
    assert_refused_and_untouched(gc, repo, request, "backend/src/cfg/key")


# -- the bwrap sandbox: plan ------------------------------------------------------------------------------------

@pytest.fixture
def repo3(tmp_path: Path) -> Path:
    """A disposable repository holding EXISTING protected directories inside writable role roots."""
    path = tmp_path / "repo3"
    init_demo_repo(path, {**SELF_TEST_FILES, ".gitignore": ".env\n", "frontend/.env.local/config.ts": "tracked\n"})
    for rel, text in {"backend/src/.env/secret.py": "SECRET=nested-dir\n", "backend/.env/notes.example": "S=1\n",
                      "backend/sub/.git/config": "[core]\n", "frontend/.env.local/extra.ts": "SECRET=local\n"}.items():
        write(path, rel, text)
    return path


def test_existing_protected_directories_are_read_only_as_a_whole(repo3: Path, config: ProjectConfig) -> None:
    builder = SandboxBuilder(repo3, config)
    assert builder.writable_roots("backend") == ["backend", "docs_agents/backend"]  # unchanged
    backend = builder.readonly_overlays("backend", builder.writable_roots("backend"))
    assert {"backend/src/.env", "backend/.env", "backend/sub/.git", "backend/data"} <= set(backend)
    assert not any(p.startswith(("backend/src/.env/", "backend/sub/.git/")) for p in backend)  # the directory, whole
    assert "backend/src/app.py" not in backend
    frontend = builder.readonly_overlays("frontend", builder.writable_roots("frontend"))
    assert "frontend/.env.local" in frontend and "frontend/src/view.ts" not in frontend


def test_untracked_files_inside_secret_directories_are_masked(repo3: Path, config: ProjectConfig) -> None:
    secrets = SandboxBuilder(repo3, config).secret_files()
    # The exemption (.example) applies to the matching name, not to a file inside a secret directory.
    assert {"backend/src/.env/secret.py", "backend/.env/notes.example", "frontend/.env.local/extra.ts"} <= set(secrets)
    assert "frontend/.env.local/config.ts" in secrets  # tracked: masked all the same (policy, not Git, decides)
    assert ".env.example" not in secrets


def probe_in(plan, spec: dict, tmp: Path) -> dict:
    out, err = tmp / "probe.json", tmp / "probe.err"
    supervise(plan.argv(["/usr/bin/python3", "-c", PROBE, json.dumps(spec)]), cwd=plan.repo_root,
              env={"PATH": os.environ["PATH"]}, stdout_path=out, stderr_path=err, timeout_seconds=60)
    plan.discard_shadow()
    text = out.read_text(encoding="utf-8")
    assert text, err.read_text(encoding="utf-8")
    return json.loads(text)


PROBE = r"""
import json, os, sys
spec, out = json.loads(sys.argv[1]), {}
def attempt(label, fn):
    try:
        out[label] = "ok:" + str(fn() or "")
    except OSError as exc:
        out[label] = f"{type(exc).__name__}:{exc.errno}"
def create(rel):
    os.makedirs(os.path.dirname(rel), exist_ok=True)
    open(rel, "w", encoding="utf-8").write("new\n")
for rel in spec["append"]:
    attempt("append " + rel, lambda: open(rel, "a", encoding="utf-8").write("tampered\n") and None)
for rel in spec["create"]:
    attempt("create " + rel, lambda: create(rel))
for src, dst in spec["rename"]:
    attempt("rename " + dst, lambda: os.rename(src, dst))
for rel in spec["read"]:
    attempt("read " + rel, lambda: open(rel, encoding="utf-8").read())
print(json.dumps(out))
"""
EROFS, EACCES, EXDEV = "OSError:30", "PermissionError:13", "OSError:18"


@pytest.mark.usefixtures("needs_bwrap")
def test_real_mounts_prevent_writes_into_existing_protected_directories(repo3: Path, config: ProjectConfig,
                                                                        tmp_path: Path) -> None:
    """OS-level PREVENTION for EXISTING protected directories inside a writable root: tracked or other unmasked
    files are on a read-only mount (EROFS), untracked secrets are masked with /dev/null (EACCES: a device node on
    a nodev mount), and nothing new can be created in them. A NEW protected directory is NOT prevented by the OS
    (nothing to mount over yet) — it is only DETECTED after the run and refused by the commit gate."""
    builder = SandboxBuilder(repo3, config)
    backend = probe_in(builder.plan(run_dir=tmp_path / "backend", role="backend"), {
        "append": ["backend/sub/.git/config", "backend/src/.env/secret.py", "backend/src/app.py"],
        "create": ["backend/src/.env/new.py", "backend/sub/.git/hooks/pre-commit", "backend/.env/new.txt",
                   "backend/src/fresh.py", "backend/brand-new/.env/x.py"],
        "rename": [["backend/src/fresh.py", "backend/src/.env/fresh.py"]],
        "read": ["backend/src/.env/secret.py", "backend/.env/notes.example", "backend/src/app.py"]}, tmp_path)
    assert backend["append backend/sub/.git/config"] == EROFS
    assert backend["append backend/src/.env/secret.py"] in (EROFS, EACCES)  # masked secret on a read-only mount
    for rel in ("backend/src/.env/new.py", "backend/sub/.git/hooks/pre-commit", "backend/.env/new.txt"):
        assert backend[f"create {rel}"] == EROFS, (rel, backend[f"create {rel}"])
    assert backend["rename backend/src/.env/fresh.py"] in (EROFS, EXDEV)  # never lands inside the directory
    for rel in ("backend/src/.env/secret.py", "backend/.env/notes.example"):
        assert backend[f"read {rel}"] in ("ok:", EACCES), (rel, backend[f"read {rel}"])  # masked, never the content
    assert backend["append backend/src/app.py"] == "ok:" and backend["read backend/src/app.py"].startswith("ok:ROUTES")
    assert (repo3 / "backend/src/.env/secret.py").read_text(encoding="utf-8") == "SECRET=nested-dir\n"
    assert (repo3 / "backend/sub/.git/config").read_text(encoding="utf-8") == "[core]\n"
    assert not (repo3 / "backend/src/.env/new.py").exists() and not (repo3 / "backend/sub/.git/hooks").exists()
    assert not (repo3 / "backend/src/.env/fresh.py").exists()
    # The documented OS-level gap: a brand-new protected directory can be created inside the role's own root ...
    assert backend["create backend/brand-new/.env/x.py"] == "ok:" and (repo3 / "backend/brand-new/.env/x.py").exists()
    # ... and is caught only afterwards, by the same PathPolicy the post-run checks and the commit gate use.
    assert not PathPolicy(repo3, config).check("backend", "backend/brand-new/.env/x.py").allowed

    frontend = probe_in(builder.plan(run_dir=tmp_path / "frontend", role="frontend"), {
        "append": ["frontend/.env.local/config.ts", "frontend/src/view.ts"],
        "create": ["frontend/.env.local/new.ts"], "rename": [], "read": ["frontend/.env.local/extra.ts"]}, tmp_path)
    assert frontend["append frontend/.env.local/config.ts"] in (EROFS, EACCES)  # tracked: read-only AND masked
    assert frontend["create frontend/.env.local/new.ts"] == EROFS
    assert frontend["read frontend/.env.local/extra.ts"] in ("ok:", EACCES)  # untracked: masked
    assert frontend["append frontend/src/view.ts"] == "ok:"
    assert (repo3 / "frontend/.env.local/config.ts").read_text(encoding="utf-8") == "tracked\n"


# -- both runner backends: post-run judgement ---------------------------------------------------------------------

def request_for(repo: Path, config: ProjectConfig, role: str, actions: list, tmp: Path) -> RunRequest:
    return RunRequest(f"s1.{role}", role, actions_prompt(actions), ScopedWorkspace(PathPolicy(repo, config), role),
                      context=run_context(tmp / "runs", f"s1.{role}"))


@pytest.mark.usefixtures("needs_bwrap", "fake_home")
@pytest.mark.parametrize("provider,role,path", [("claude", "backend", "backend/new/.env.local/secret.py"),
                                                ("codex", "frontend", "frontend/.env.local/config.ts")])
def test_bwrap_run_creating_a_protected_descendant_is_rejected(tmp_path: Path, agent_config: ProjectConfig,
                                                               provider: str, role: str, path: str) -> None:
    repo = tmp_path / "repo"
    init_demo_repo(repo, SELF_TEST_FILES)
    own = f"{role}/src/ok.txt"
    runner = RealCliRunner(agent_config.providers[provider], agent_config,  # type: ignore[arg-type]
                           broker_factory=fake_broker_factory())
    result = runner.run(request_for(repo, agent_config, role, [{"do": "write", "path": own, "text": "ok\n"},
                                                               {"do": "write", "path": path, "text": "S=1\n"}],
                                    tmp_path))
    assert result.status == "rejected" and path in result.error, result.error
    assert path not in result.changed_paths and own in result.changed_paths  # never offered to the commit gate


def test_native_snapshot_watches_ignored_files_inside_protected_directories(repo3: Path,
                                                                            config: ProjectConfig) -> None:
    """native-cli has no OS sandbox: an ignored file inside `backend/src/.env/` is watched (post-run detection)."""
    before = workspace_state(repo3, config)
    assert {"backend/src/.env/secret.py", "backend/.env/notes.example"} <= set(before.inventory.entries)
    write(repo3, "backend/src/.env/secret.py", "SECRET=changed\n")
    write(repo3, "backend/src/.env/added.py", "SECRET=added\n")
    _changed, sensitive, _new = changed_paths(before, workspace_state(repo3, config))
    # the directory itself is unchanged (same inode and mode); its files are judged one by one
    assert [c.path for c in sensitive] == ["backend/src/.env/added.py", "backend/src/.env/secret.py"]
    assert sensitive[0].before is None and sensitive[1].before != sensitive[1].after


@pytest.mark.parametrize("role,path,ignored", [("backend", "backend/src/.env/secret.py", True),
                                               ("frontend", "frontend/.env.local/config.ts", False)])
def test_native_run_writing_a_protected_descendant_is_rejected(tmp_path: Path, agent_config: ProjectConfig,
                                                               fake_home: Path, role: str, path: str,
                                                               ignored: bool) -> None:
    del fake_home
    repo = tmp_path / "repo"
    init_demo_repo(repo, {**SELF_TEST_FILES, ".gitignore": ".env\n"})
    runner = NativeCliRunner(agent_config.provider_for(role), agent_config)  # type: ignore[arg-type]
    result = runner.run(request_for(repo, agent_config, role, [{"do": "write", "path": path, "text": "S=1\n"}],
                                    tmp_path))
    if ignored:  # backend/src/.env/ is Git-ignored: only the protected-ignored watch can see it
        assert git(repo, "check-ignore", path).strip() == path
    assert result.status == "rejected" and path in result.error, result.error
    assert path not in result.changed_paths
    assert (repo / path).exists()  # left in place for the operator, never silently discarded


# -- planner and reviewer -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("role,path", [("backend", "backend/src/.env/secret.py"),
                                       ("frontend", "frontend/.env.local/config.ts"),
                                       ("frontend", "frontend/.env.local/**"),
                                       ("backend", "backend/sub/.git/config")])
def test_a_plan_cannot_name_a_protected_descendant(config: ProjectConfig, role: str, path: str) -> None:
    problem = path_problem(config, role, path)
    assert problem is not None and problem.startswith("is protected"), problem
    assert path_problem(config, role, f"{role}/src/**") is None


def test_protected_descendants_are_withheld_from_review_prompts(config: ProjectConfig) -> None:
    for path in ("backend/src/.env/secret.py", "frontend/.env.local/config.ts", "backend/sub/.git/config"):
        assert withheld(config, path), path
    assert not withheld(config, "backend/src/app.py") and not withheld(config, "backend/src/environment.py")


def test_the_integration_classification_agrees(policy: PathPolicy, config: ProjectConfig) -> None:
    """W4.7 M2 already classified protected descendants as protected; PathPolicy now agrees with it."""
    for _role, path in (*NESTED_ENV, *NESTED_ENV_LOCAL):
        assert path_class(policy, config, path) == ("protected", None)
        assert protected_prefix(config.protected, path) is not None
    assert path_class(policy, config, "backend/src/app.py") == ("owned", "backend")
