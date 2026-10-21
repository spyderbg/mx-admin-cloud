"""OS-level write containment with bubblewrap (Phase 3 exit gates) — disposable repositories only."""

# pylint: disable=missing-function-docstring,redefined-outer-name

from __future__ import annotations

import dataclasses
import json
import os
import subprocess
from pathlib import Path

import pytest

from feature_coordinator.config import ProjectConfig
from feature_coordinator.demo import init_demo_repo
from feature_coordinator.errors import ConfigError
from feature_coordinator.operations import SELF_TEST_FILES, UNTRACKED_SECRETS, containment_self_test
from feature_coordinator.paths import PathPolicy, matches
from feature_coordinator.sandbox import SandboxBuilder, SandboxUnavailableError, check_bwrap
from feature_coordinator.supervisor import supervise

from fc_helpers import git


@pytest.fixture
def repo3(tmp_path: Path) -> Path:
    """A disposable repository with every role's tree, protected data and secrets."""
    path = tmp_path / "repo3"
    init_demo_repo(path, {**SELF_TEST_FILES, "frontend/.env.development.local": "VITE_DEBUG_MODE=true\n"})
    for rel, text in UNTRACKED_SECRETS.items():
        (path / rel).write_text(text, encoding="utf-8")
    return path


def run_in(plan, *command: str, tmp: Path) -> subprocess.CompletedProcess[str]:
    out, err = tmp / "out.txt", tmp / "err.txt"
    supervise(plan.argv(list(command)), cwd=plan.repo_root, env={"PATH": os.environ["PATH"]}, stdout_path=out,
              stderr_path=err, timeout_seconds=60)
    return subprocess.CompletedProcess(command, 0, out.read_text(encoding="utf-8"), err.read_text(encoding="utf-8"))


# -- plan structure (no bwrap needed) ----------------------------------------------------

def test_writable_roots_and_read_only_islands(repo3: Path, config: ProjectConfig) -> None:
    builder = SandboxBuilder(repo3, config)
    assert builder.writable_roots("backend") == ["backend", "docs_agents/backend"]
    assert builder.writable_roots("automation") == ["docs_agents/automation", "scripts", "test_environment"]
    overlays = builder.readonly_overlays("automation", builder.writable_roots("automation"))
    assert "scripts/mxfix" in overlays and "scripts/deploy.sh" in overlays
    assert "backend/data" in builder.readonly_overlays("backend", ["backend"])
    # A nested env file inside a role root is protected (D-5: debug defaults stay unchanged).
    assert "frontend/.env.development.local" in builder.readonly_overlays("frontend", ["frontend"])
    # Approval-required paths become writable only with a grant.
    assert builder.writable_roots("automation", ("scripts/deploy.sh",)).count("scripts/deploy.sh") == 1


def test_untracked_secrets_are_hidden_tracked_files_are_not(repo3: Path, config: ProjectConfig) -> None:
    assert SandboxBuilder(repo3, config).secret_files() == [".env", "backend/.env.local"]
    plan = SandboxBuilder(repo3, config).plan(run_dir=repo3.parent / "run", role="backend")
    assert plan.hidden == (".env",) and plan.masked == ("backend/.env.local",)
    assert ".env" not in plan.top_level and ".env.example" in plan.top_level


def test_any_depth_patterns() -> None:
    assert matches("**/.env", ".env") and matches("**/.env", "frontend/.env")
    assert matches("**/.env.*", "frontend/.env.development.local") and not matches("**/.env", "frontend/x.env")


@pytest.mark.parametrize("change,match", [
    (lambda c: dataclasses.replace(c, runtime=dataclasses.replace(c.runtime, containment="none")), "never unconfined"),
    (lambda c: dataclasses.replace(c, runtime=dataclasses.replace(c.runtime, gate_network=True)), "offline"),
    (lambda c: dataclasses.replace(c, runtime=dataclasses.replace(c.runtime, agent_network=True)), "D-21"),
    (lambda c: dataclasses.replace(c, unsafe_gates={"nope": "x"}), "unknown gates"),
    (lambda c: dataclasses.replace(c, runtime=dataclasses.replace(c.runtime, stale_heartbeat_seconds=5)), "exceed"),
])
def test_runtime_ceilings(config: ProjectConfig, change, match: str) -> None:
    with pytest.raises(ConfigError, match=match):
        change(config)


def test_sandbox_command_line_invariants(repo3: Path, config: ProjectConfig) -> None:
    builder = SandboxBuilder(repo3, config, bwrap="bwrap")
    agent = builder.plan(run_dir=repo3.parent / "a", role="frontend", shadow_writable=True).argv(["x"])
    gate = builder.plan(run_dir=repo3.parent / "g", role=None).argv(["x"])
    for argv in (agent, gate):
        for flag in ("--die-with-parent", "--new-session", "--unshare-all", "--clearenv"):
            assert flag in argv
        assert argv[argv.index("--remount-ro") + 1] == "/"
        assert [argv[i + 1] for i, a in enumerate(argv) if a == "--ro-bind" and argv[i + 2] == str(repo3 / ".git")]
    assert "--share-net" not in agent and "--share-net" not in gate  # W1 (D-21): never the host network
    assert not any(a.startswith("/run/systemd/resolve") for a in agent)
    triples = [tuple(agent[i:i + 3]) for i in range(len(agent) - 2)]
    frontend = str(repo3 / "frontend")
    assert triples.index(("--bind", frontend, frontend)) > triples.index(("--ro-bind", frontend, frontend))
    assert not [a for i, a in enumerate(gate) if a == "--bind" and gate[i + 2].startswith(str(repo3) + "/")]


def test_missing_bwrap_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(SandboxUnavailableError):
        check_bwrap(str(tmp_path / "no-such-bwrap"))


def test_stray_writes_and_residue(repo3: Path, config: ProjectConfig) -> None:
    plan = SandboxBuilder(repo3, config).plan(run_dir=repo3.parent / "s", role="frontend", shadow_writable=True)
    (plan.shadow / "notes.txt").write_text("agent wrote here\n", encoding="utf-8")
    (plan.shadow / ".aws").mkdir()  # an empty mount point left by a nested sandbox
    strays, residue = plan.stray_writes()
    assert strays == ["notes.txt"] and residue == [".aws/"]
    plan.discard_shadow()
    assert not plan.shadow.exists()


# -- real bubblewrap ------------------------------------------------------------------------

@pytest.mark.usefixtures("needs_bwrap")
def test_containment_matrix_for_every_role(config: ProjectConfig) -> None:
    """Exit gate: each role writes exactly its own roots; .git, protected, approval-required paths,
    other roles' trees, symlink escapes, generated outputs of other roles and secrets are all refused."""
    report = containment_self_test(config)
    assert report.ok, report.mismatches
    assert set(report.roles) == set(config.roles)
    written = {role: sorted(k for k, v in r["writes"].items() if v == "written") for role, r in report.roles.items()}
    assert written["backend"] == ["backend/src/probe.py", "docs_agents/backend/probe.md"]
    assert "frontend/src/types/probeSchemas.ts" in written["frontend"]  # generated output: destination owner
    assert "frontend/src/types/probeSchemas.ts" not in written["automation"]  # the generator's role may not
    for role, result in report.roles.items():
        assert result["writes"][".git/probe"] != "written" and result["git_commit"] == "refused", role
        assert result["writes"]["scripts/mxfix/probe.py"] != "written", role
        assert result["secret_read"].startswith("denied"), role


@pytest.mark.usefixtures("needs_bwrap")
def test_traversal_and_symlink_escapes_never_reach_the_host(repo3: Path, config: ProjectConfig, tmp_path: Path) -> None:
    builder = SandboxBuilder(repo3, config)
    plan = builder.plan(run_dir=tmp_path / "run", role="backend")
    script = (
        "import os\n"
        "os.symlink('/etc', 'backend/etc-link')\n"
        "for target in ('backend/../frontend/src/x.ts', 'backend/etc-link/passwd-copy', "
        "'docs_agents/backend/../../cli/src/x.py', '/etc/hostname'):\n"
        "    try:\n"
        "        open(target, 'a').write('x')\n"
        "        print('WRITTEN', target)\n"
        "    except OSError as exc:\n"
        "        print('DENIED', target, exc.errno)\n")
    out = run_in(plan, "/usr/bin/python3", "-c", script, tmp=tmp_path).stdout
    assert "WRITTEN" not in out and out.count("DENIED") == 4, out
    assert not (repo3 / "frontend/src/x.ts").exists() and not (repo3 / "cli/src/x.py").exists()
    os.unlink(repo3 / "backend/etc-link")


@pytest.mark.usefixtures("needs_bwrap")
def test_git_metadata_is_read_only_but_readable(repo3: Path, config: ProjectConfig, tmp_path: Path) -> None:
    plan = SandboxBuilder(repo3, config).plan(run_dir=tmp_path / "run", role="backend")
    script = ("import subprocess\n"
              "for args in (['status', '--short'], ['log', '-1', '--format=%s'],"
              " ['commit', '--allow-empty', '-m', 'x'], "
              "['branch', 'evil'], ['update-ref', 'refs/heads/dev', 'HEAD'], ['config', 'user.name', 'x']):\n"
              "    p = subprocess.run(['git', *args], capture_output=True, text=True)\n"
              "    print(args[0], p.returncode)\n")
    lines = run_in(plan, "/usr/bin/python3", "-c", script, tmp=tmp_path).stdout.splitlines()
    out = dict(line.split() for line in lines)
    assert out["status"] == "0" and out["log"] == "0"
    assert out["commit"] != "0" and out["branch"] != "0" and out["update-ref"] != "0" and out["config"] != "0"
    assert git(repo3, "branch", "--list", "evil") == "" and git(repo3, "log", "--format=%s").splitlines() == ["Seed"]


@pytest.mark.usefixtures("needs_bwrap")
def test_private_home_tmp_and_no_state_dir(repo3: Path, config: ProjectConfig, tmp_path: Path, state_dir: Path) -> None:
    plan = SandboxBuilder(repo3, config).plan(run_dir=tmp_path / "run", role="backend")
    script = ("import json, os\n"
              f"print(json.dumps({{'state': os.path.exists({str(state_dir)!r}),"
              " 'home': sorted(os.listdir(os.environ['HOME'])), 'env': sorted(os.environ), 'uid': os.getuid(),"
              " 'pid1': open('/proc/1/cmdline').read().split(chr(0))[0]}))\n")
    info = json.loads(run_in(plan, "/usr/bin/python3", "-c", script, tmp=tmp_path).stdout)
    assert info["state"] is False  # the coordinator state (claims, tokens) is invisible
    assert info["home"] == []  # private, empty HOME
    assert "AGENT_QWEN_API_KEY" not in info["env"] and "XDG_STATE_HOME" not in info["env"]
    assert info["pid1"].endswith("bwrap") or info["pid1"] == "/usr/bin/python3"  # own PID namespace


@pytest.mark.usefixtures("needs_bwrap")
def test_no_sandbox_profile_reaches_the_host_network(repo3: Path, config: ProjectConfig, tmp_path: Path) -> None:
    """W1 (D-21): neither a gate nor a runner sandbox can reach a server on the host's loopback."""
    import socket  # pylint: disable=import-outside-toplevel
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(5)
    port = server.getsockname()[1]
    script = (f"import socket\ns = socket.socket()\ns.settimeout(2)\n"
              f"try:\n    s.connect(('127.0.0.1', {port}))\n    print('connected')\nexcept OSError as exc:\n"
              f"    print('refused', exc.errno)\n")
    builder = SandboxBuilder(repo3, config)
    gate = run_in(builder.plan(run_dir=tmp_path / "g", role=None), "/usr/bin/python3", "-c", script,
                  tmp=tmp_path).stdout
    agent = run_in(builder.plan(run_dir=tmp_path / "a", role="backend"), "/usr/bin/python3", "-c",
                   script, tmp=tmp_path).stdout
    server.close()
    assert gate.startswith("refused") and agent.startswith("refused")


def test_policy_and_sandbox_agree_on_ownership(repo3: Path, config: ProjectConfig) -> None:
    """The OS layer is built from the same PathPolicy the commit gate uses."""
    builder = SandboxBuilder(repo3, config)
    policy = PathPolicy(repo3, config)
    for role in config.roles:
        for root in builder.writable_roots(role):
            assert policy.check(role, f"{root}/new-file.txt").allowed or (repo3 / root).is_file()
