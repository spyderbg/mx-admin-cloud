"""Real Claude Code / Codex launchers: flag probe, command lines, sandboxed runs (fake agent + fake HOME)."""

# pylint: disable=missing-function-docstring,redefined-outer-name

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from feature_coordinator.config import ProjectConfig
from feature_coordinator.demo import init_demo_repo
from feature_coordinator.launchers import (
    FORBIDDEN_ARGS,
    LAUNCHERS as LAUNCHERS_BY_KIND,
    REQUIRED_FLAGS,
    ClaudeCodeLauncher,
    CodexLauncher,
    RealCliRunner,
    _check_forbidden,
    probe_runner,
    run_context,
)
from feature_coordinator.operations import SELF_TEST_FILES
from feature_coordinator.paths import PathPolicy, ScopedWorkspace
from feature_coordinator.providers import CLAUDE_CODE, CODEX, ProviderKind
from feature_coordinator.runners import RunRequest
from feature_coordinator.sandbox import SandboxBuilder, install_prefix
from feature_coordinator.supervisor import supervise

from fc_helpers import FAKE_CREDENTIAL, actions_prompt


@pytest.fixture
def repo3(tmp_path: Path) -> Path:
    path = tmp_path / "repo3"
    init_demo_repo(path, SELF_TEST_FILES)
    return path


def request_for(repo: Path, config: ProjectConfig, role: str, actions, tmp: Path, **ctx) -> RunRequest:
    return RunRequest(f"f.{role}", role, actions_prompt(actions), ScopedWorkspace(PathPolicy(repo, config), role),
                      context=run_context(tmp / "runs", f"f.{role}", **ctx))


def report_of(result) -> dict:
    return json.loads(result.transcript[0])


# -- probe and command lines ---------------------------------------------------------------

def test_probe_accepts_a_cli_with_every_required_flag(fake_agent: Path) -> None:
    probe = probe_runner(type(CLAUDE_CODE)(name="claude", kind=ProviderKind.CLAUDE_CODE, executable=str(fake_agent)))
    assert probe.ok and probe.version == "fake-agent 1.0"


def test_probe_fails_closed_when_a_flag_is_missing(tmp_path: Path, agent_config: ProjectConfig) -> None:
    weak = tmp_path / "old-claude"
    weak.write_text("#!/bin/sh\necho 'Usage: claude -p --print --output-format --model'\n", encoding="utf-8")
    weak.chmod(0o755)
    profile = type(CLAUDE_CODE)(name="claude", kind=ProviderKind.CLAUDE_CODE, executable=str(weak))
    probe = probe_runner(profile)
    assert not probe.ok and "--restricted" in probe.missing_flags and "--permission-prompts" in probe.missing_flags
    result = RealCliRunner(profile, agent_config).run(RunRequest("t", "backend", "x", None))  # type: ignore[arg-type]
    assert result.status == "unavailable" and "--restricted" in result.error
    missing = type(CLAUDE_CODE)(name="claude", kind=ProviderKind.CLAUDE_CODE, executable=str(tmp_path / "nope"))
    no_cli = RealCliRunner(missing, agent_config).run(RunRequest("t", "backend", "x", None))  # type: ignore[arg-type]
    assert no_cli.status == "unavailable"


def test_bypass_flags_can_never_be_emitted() -> None:
    for flag in FORBIDDEN_ARGS:
        with pytest.raises(ValueError, match="bypass"):
            _check_forbidden(["claude", "-p", flag])


def test_claude_settings_allow_only_file_tools_and_role_commands(agent_config: ProjectConfig, tmp_path: Path) -> None:
    launcher = ClaudeCodeLauncher(agent_config.providers["claude"], agent_config, "claude")  # type: ignore[arg-type]
    venv_python = tmp_path / ".venv/bin/python"
    venv_python.parent.mkdir(parents=True)
    venv_python.symlink_to("/usr/bin/python3")
    allow = launcher.settings("backend", tmp_path)["permissions"]["allow"]
    deny = launcher.settings("backend", tmp_path)["permissions"]["deny"]
    assert {"Read", "Edit", "Write", "Glob", "Grep"} <= set(allow)
    # W2: {python} is the workspace's interpreter, absolute and workspace-relative — never a bare `python`.
    assert f"Bash({venv_python} -m pytest:*)" in allow and "Bash(.venv/bin/python -m pytest:*)" in allow
    assert not any(a.startswith(("Bash(python ", "Bash(python3 ", "Bash({")) for a in allow)
    assert not any(a in ("Bash", "Bash(*)") for a in allow)
    assert "Read(~/.claude/**)" in deny and "Bash(git commit:*)" in deny and "WebFetch" in deny
    assert launcher.settings("frontend", tmp_path)["permissions"]["allow"] == ["Read", "Glob", "Grep", "Edit", "Write"]
    assert launcher.settings("backend", tmp_path)["permissions"]["defaultMode"] == "acceptEdits"


def test_claude_command_line(repo3: Path, agent_config: ProjectConfig, tmp_path: Path, fake_home: Path) -> None:
    profile = agent_config.providers["claude"]
    spec = ClaudeCodeLauncher(profile, agent_config, "/opt/claude").prepare(  # type: ignore[arg-type]
        request_for(repo3, agent_config, "backend", [], tmp_path), tmp_path / "run", ["backend"])
    argv = spec.argv
    for flag in ("-p", "--restricted", "--strict-mcp-config", "--no-session-persistence", "--disable-slash-commands"):
        assert flag in argv
    assert argv[argv.index("--permission-mode") + 1] == "acceptEdits"
    assert argv[argv.index("--permission-prompts") + 1] == "none"
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    assert argv[argv.index("--tools") + 1] == "Read,Glob,Grep,Edit,Write,Bash"
    assert argv[argv.index("--max-budget-usd") + 1] == "5"
    assert not set(argv) & FORBIDDEN_ARGS and "--setting-sources" not in argv
    settings = json.loads((tmp_path / "run/home/.fc/claude-settings.json").read_text(encoding="utf-8"))
    assert argv[argv.index("--settings") + 1] == str(fake_home / ".fc/claude-settings.json") and settings["permissions"]
    prompt = spec.stdin_path.read_text(encoding="utf-8")
    assert "You may write ONLY inside: backend" in prompt and "Do not run Git commands" in prompt
    assert spec.env["DISABLE_AUTOUPDATER"] == "1" and not spec.shadow_writable
    assert [b.dest for b in spec.binds if b.dest.endswith(".credentials.json")] == [
        str(fake_home / ".claude/.credentials.json")]


def test_codex_command_line(repo3: Path, agent_config: ProjectConfig, tmp_path: Path, fake_home: Path) -> None:
    spec = CodexLauncher(agent_config.providers["codex"], agent_config, "/opt/codex").prepare(  # type: ignore[arg-type]
        request_for(repo3, agent_config, "frontend", [], tmp_path), tmp_path / "run", ["frontend"])
    argv = spec.argv
    assert argv[:2] == ["/opt/codex", "exec"] and argv[-1] == "-"
    assert argv[argv.index("--sandbox") + 1] == "workspace-write"  # Codex's own sandbox stays on, nested
    assert "--ephemeral" in argv and "--ignore-user-config" in argv and "--json" in argv
    assert argv[argv.index("-C") + 1] == str(repo3)
    assert "sandbox_workspace_write.network_access=false" in argv
    assert not set(argv) & FORBIDDEN_ARGS
    assert spec.shadow_writable and spec.env["CODEX_HOME"] == str(fake_home / ".codex")


def test_install_prefix() -> None:
    assert install_prefix("/usr/bin/true") == Path("/usr")
    assert install_prefix("/opt/x/tool") == Path("/opt/x")


# -- sandboxed runs with the fake agent ---------------------------------------------------------

@pytest.mark.usefixtures("needs_bwrap")
def test_claude_style_run_is_contained(repo3: Path, agent_config: ProjectConfig, tmp_path: Path,
                                       fake_home: Path) -> None:
    (repo3 / ".env").write_text("SECRET=1\n", encoding="utf-8")
    actions = [{"do": "write", "path": "backend/src/feature.py", "text": "X = 1\n"},
               {"do": "try_write", "path": "frontend/src/view.ts"}, {"do": "try_write", "path": ".git/HEAD"},
               {"do": "try_write", "path": "backend/data/users.db"}, {"do": "try_read", "path": ".env"},
               {"do": "try_read", "path": str(fake_home / ".claude/.credentials.json")},
               {"do": "try_write", "path": str(fake_home / ".claude/.credentials.json")},
               {"do": "git", "args": ["commit", "--allow-empty", "-m", "x"]}]
    runner = RealCliRunner(agent_config.providers["claude"], agent_config)  # type: ignore[arg-type]
    result = runner.run(request_for(repo3, agent_config, "backend", actions, tmp_path))
    assert result.status == "completed", result.error
    assert result.changed_paths == ["backend/src/feature.py"]
    outcomes = {r["action"]: r for r in report_of(result)["report"]}
    for denied in ("try_write frontend/src/view.ts", "try_write .git/HEAD", "try_write backend/data/users.db"):
        assert not outcomes[denied]["ok"] and "Errno 30" in outcomes[denied]["error"], denied
    assert not outcomes["read .env"]["ok"]
    # The runner's own credential file is readable (the CLI needs it) but never writable.
    assert FAKE_CREDENTIAL in outcomes[f"read {fake_home}/.claude/.credentials.json"]["value"]
    assert not outcomes[f"try_write {fake_home}/.claude/.credentials.json"]["ok"]
    assert not outcomes["git commit --allow-empty -m x"]["ok"]
    assert (repo3 / "frontend/src/view.ts").read_text(encoding="utf-8") == SELF_TEST_FILES["frontend/src/view.ts"]
    env_keys = report_of(result)["env_keys"]
    assert "XDG_STATE_HOME" not in env_keys and "AGENT_QWEN_API_KEY" not in env_keys
    sandbox = json.loads((Path(result.details["run_dir"]) / "sandbox.json").read_text(encoding="utf-8"))
    assert sandbox["writable"] == ["backend", "docs_agents/backend"] and ".env" in sandbox["hidden"]


@pytest.mark.usefixtures("needs_bwrap")
def test_codex_style_run_stray_root_write_is_rejected(repo3: Path, agent_config: ProjectConfig,
                                                       tmp_path: Path, fake_home: Path) -> None:
    del fake_home
    actions = [{"do": "write", "path": "frontend/src/view.ts", "text": "export const views = ['x']\n"},
               {"do": "write", "path": "NOTES.md", "text": "agent notes at the root\n"}]
    runner = RealCliRunner(agent_config.providers["codex"], agent_config)  # type: ignore[arg-type]
    result = runner.run(request_for(repo3, agent_config, "frontend", actions, tmp_path))
    assert result.status == "rejected" and result.stray_writes == ["NOTES.md"]
    assert not (repo3 / "NOTES.md").exists()  # it only ever reached the discarded shadow
    assert report_of(result)["argv"][0] == "exec"


@pytest.mark.usefixtures("needs_bwrap")
def test_new_protected_or_symlink_entries_are_rejected_but_preexisting_secrets_are_not(
        repo3: Path, agent_config: ProjectConfig, tmp_path: Path, fake_home: Path) -> None:
    del fake_home
    (repo3 / "backend/.env.local").write_text("S=1\n", encoding="utf-8")  # pre-existing, untracked
    runner = RealCliRunner(agent_config.providers["claude"], agent_config)  # type: ignore[arg-type]
    clean = runner.run(request_for(repo3, agent_config, "backend",
                                   [{"do": "write", "path": "backend/src/a.py", "text": "A = 1\n"}], tmp_path))
    assert clean.status == "completed" and clean.changed_paths == ["backend/src/a.py"]
    bad = runner.run(request_for(repo3, agent_config, "backend", [
        {"do": "write", "path": "backend/.env", "text": "NEW=1\n"},  # a new protected name inside its root
        {"do": "symlink", "target": "../frontend", "link": "backend/ui"}], tmp_path))
    assert bad.status == "rejected" and "backend/.env" in bad.error and "backend/ui" in bad.error


@pytest.mark.usefixtures("needs_bwrap")
def test_failed_run_is_reported(repo3: Path, agent_config: ProjectConfig, tmp_path: Path, fake_home: Path) -> None:
    del fake_home
    runner = RealCliRunner(agent_config.providers["claude"], agent_config)  # type: ignore[arg-type]
    result = runner.run(request_for(repo3, agent_config, "backend", [{"do": "exit", "code": 3}], tmp_path))
    assert result.status == "failed" and "exit 3" in result.error


# -- the real installed CLIs (no model call) ----------------------------------------------------

@pytest.mark.parametrize("profile", [CLAUDE_CODE, CODEX], ids=["claude", "codex"])
def test_installed_cli_supports_every_required_flag(profile) -> None:
    if not shutil.which(profile.executable):
        pytest.skip(f"{profile.executable} is not installed")
    probe = probe_runner(profile)
    assert probe.ok, (probe.missing_flags, probe.error)
    assert set(REQUIRED_FLAGS[profile.kind]) and probe.version


@pytest.mark.usefixtures("needs_bwrap")
@pytest.mark.parametrize("profile", [CLAUDE_CODE, CODEX], ids=["claude", "codex"])
def test_installed_cli_starts_inside_the_sandbox(profile, repo3: Path, config: ProjectConfig, tmp_path: Path) -> None:
    if not shutil.which(profile.executable):
        pytest.skip(f"{profile.executable} is not installed")
    real = str(Path(shutil.which(profile.executable)).resolve())
    from feature_coordinator.sandbox import Bind  # pylint: disable=import-outside-toplevel
    plan = SandboxBuilder(repo3, config).plan(run_dir=tmp_path / "run", role="backend",
                                              extra=[Bind(str(install_prefix(real)), str(install_prefix(real)), "ro")])
    sup = supervise(plan.argv([real, "--version"]), cwd=repo3, env={"PATH": os.environ["PATH"]},
                    stdout_path=tmp_path / "v.out", stderr_path=tmp_path / "v.err", timeout_seconds=60)
    assert sup.ok and probe_runner(profile).version in (tmp_path / "v.out").read_text(encoding="utf-8")


@pytest.mark.usefixtures("needs_bwrap")
def test_codex_own_sandbox_works_nested_inside_ours(repo3: Path, config: ProjectConfig, tmp_path: Path) -> None:
    """Codex keeps its own (bubblewrap) sandbox inside the Frontend sandbox: writes stay in the
    Frontend roots and model-generated commands get no network. Uses `codex sandbox`, no model."""
    if not shutil.which("codex"):
        pytest.skip("codex is not installed")
    real = str(Path(shutil.which("codex")).resolve())
    from feature_coordinator.sandbox import Bind  # pylint: disable=import-outside-toplevel
    home = tmp_path / "run" / "home"
    plan = SandboxBuilder(repo3, config).plan(
        run_dir=tmp_path / "run", role="frontend", network=True, shadow_writable=True,
        env={"CODEX_HOME": f"{os.environ['HOME']}/.codex"},
        extra=[Bind(str(install_prefix(real)), str(install_prefix(real)), "ro")])
    (home / ".codex").mkdir(parents=True, exist_ok=True)
    inner = ("{ touch frontend/nested_ok && echo frontend-ok;"
             " touch backend/nested_bad 2>/dev/null || echo backend-denied;"
             " touch .git/x 2>/dev/null || echo git-denied; (getent hosts example.com >/dev/null 2>&1 && echo net)"
             " || echo no-net; } > frontend/report.txt 2>&1")
    sup = supervise(plan.argv([real, "sandbox", "-P", ":workspace", "-C", str(repo3), "--", "sh", "-c", inner]),
                    cwd=repo3, env={"PATH": os.environ["PATH"]}, stdout_path=tmp_path / "c.out",
                    stderr_path=tmp_path / "c.err", timeout_seconds=60)
    strays, _ = plan.stray_writes()
    report = (repo3 / "frontend/report.txt").read_text(encoding="utf-8").split()
    assert sup.exit_code == 0, (tmp_path / "c.err").read_text(encoding="utf-8")
    assert report == ["frontend-ok", "backend-denied", "git-denied", "no-net"]
    assert not (repo3 / "backend/nested_bad").exists() and not strays


# -- opt-in host checks (never run by default) ------------------------------------------------

HOST_CHECKS = pytest.mark.skipif(os.environ.get("FC_HOST_RUNNER_CHECKS") != "1",
                                 reason="set FC_HOST_RUNNER_CHECKS=1 to check real runner logins (no model call)")
LIVE_SMOKE = pytest.mark.skipif(os.environ.get("FC_LIVE_RUNNER_SMOKE") != "1",
                                reason="set FC_LIVE_RUNNER_SMOKE=1 for one tiny PAID run per runner (needs approval)")


def _real_home() -> Path:
    import pwd  # pylint: disable=import-outside-toplevel
    return Path(pwd.getpwuid(os.getuid()).pw_dir)


@HOST_CHECKS
@pytest.mark.usefixtures("needs_bwrap")
@pytest.mark.parametrize("profile,argv,expect", [
    (CLAUDE_CODE, ["auth", "status"], '"loggedIn": true'), (CODEX, ["login", "status"], "Logged in")],
    ids=["claude", "codex"])
def test_real_login_is_visible_inside_the_sandbox(profile, argv, expect, repo3: Path, config: ProjectConfig,
                                                  tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The real credentials are bound READ-ONLY into the private HOME; only a boolean is asserted."""
    monkeypatch.setenv("HOME", str(_real_home()))
    real = str(Path(shutil.which(profile.executable)).resolve())
    launcher = LAUNCHERS_BY_KIND[profile.kind](profile, config, real)
    spec = launcher.prepare(request_for(repo3, config, "backend", [], tmp_path), tmp_path / "run", ["backend"])
    plan = SandboxBuilder(repo3, config).plan(run_dir=tmp_path / "run", role="backend", network=True,
                                              env=spec.env, extra=spec.binds)
    sup = supervise(plan.argv([real, *argv]), cwd=repo3, env={"PATH": os.environ["PATH"]},
                    stdout_path=tmp_path / "s.out", stderr_path=tmp_path / "s.err", timeout_seconds=60)
    output = (tmp_path / "s.out").read_text(encoding="utf-8") + (tmp_path / "s.err").read_text(encoding="utf-8")
    assert sup.exit_code == 0 and expect in output


@LIVE_SMOKE
@pytest.mark.usefixtures("needs_bwrap")
@pytest.mark.parametrize("profile,role,path", [(CLAUDE_CODE, "backend", "backend/hello.txt"),
                                               (CODEX, "frontend", "frontend/hello.txt")], ids=["claude", "codex"])
def test_live_runner_smoke(profile, role: str, path: str, repo3: Path, config: ProjectConfig, tmp_path: Path,
                           monkeypatch: pytest.MonkeyPatch) -> None:
    """ONE tiny real model run per runner in a disposable repository (operator approval required)."""
    import dataclasses  # pylint: disable=import-outside-toplevel
    monkeypatch.setenv("HOME", str(_real_home()))
    config = dataclasses.replace(config, runtime=dataclasses.replace(config.runtime, claude_max_budget_usd=0.5,
                                                                     task_timeout_seconds=300))
    runner = RealCliRunner(profile, config)
    request = RunRequest(f"smoke.{role}", role, f"Create the file {path} containing exactly the word hello. "
                         "Do nothing else.", ScopedWorkspace(PathPolicy(repo3, config), role),
                         context=run_context(tmp_path / "runs", f"smoke.{role}", timeout_seconds=300))
    result = runner.run(request)
    assert result.status == "completed", (result.error, result.details)
    assert result.changed_paths == [path] and (repo3 / path).read_text(encoding="utf-8").strip() == "hello"
