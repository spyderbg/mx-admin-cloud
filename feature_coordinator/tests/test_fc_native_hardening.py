"""W1 decision A: opt-in hardening of the native-cli backend (native_hardening.py) — configuration,
the generated Claude Code settings and Codex command line, the fail-closed preflight and the
post-run checks. Fake agents and fakes only: no model, no login, no real CLI (the installed CLIs
are exercised in test_fc_native_hardening_cli.py)."""

# pylint: disable=missing-function-docstring,redefined-outer-name

from __future__ import annotations

import dataclasses
import json
import os
import tomllib
from pathlib import Path

import pytest

from feature_coordinator import native_hardening as nh
from feature_coordinator.config import ProjectConfig, config_from_mapping
from feature_coordinator.demo import init_demo_repo
from feature_coordinator.errors import ConfigError
from feature_coordinator.launchers import ClaudeCodeLauncher, CodexLauncher, run_context
from feature_coordinator.native_backend import NativeCliRunner, SubscriptionCheck
from feature_coordinator.native_hardening import NativeHardening
from feature_coordinator.operations import SELF_TEST_FILES
from feature_coordinator.paths import PathPolicy, ScopedWorkspace
from feature_coordinator.providers import ProviderKind
from feature_coordinator.runners import RunRequest

from fc_helpers import actions_prompt, config_with_agents, install_fake_agent


def hardened(config: ProjectConfig, **values) -> ProjectConfig:
    return dataclasses.replace(config, runtime=dataclasses.replace(
        config.runtime, native_hardening=NativeHardening(**{"enabled": True, **values})))


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    init_demo_repo(path, SELF_TEST_FILES)
    (path / "backend").mkdir(exist_ok=True)
    (path / "backend/.env").write_text("KEY=FAKE-DOTENV\n", encoding="utf-8")  # untracked secret
    return path


def prepared(launcher, repo: Path, role: str, tmp: Path):
    run_dir = tmp / f"run-{role}"
    run_dir.mkdir()
    request = RunRequest(f"t.{role}", role, "the task", ScopedWorkspace(PathPolicy(repo, launcher.config), role))
    return launcher.prepare(request, run_dir, [role]), run_dir


def profile_table(argv: list[str]) -> dict:
    """The fc_worker filesystem table from a Codex command line, parsed as TOML."""
    value = next(a for a in argv if a.startswith(f"permissions.{nh.CODEX_PROFILE}.filesystem="))
    return tomllib.loads("table = " + value.split("=", 1)[1])["table"]


# -- configuration --------------------------------------------------------------------------------------

def test_hardening_is_off_by_default_and_the_native_command_lines_are_unchanged(config, repo, tmp_path) -> None:
    assert config.runtime.native_hardening == NativeHardening() and not config.runtime.native_hardening.enabled
    claude = ClaudeCodeLauncher(config.providers["claude"], config, "/opt/claude", native=True)  # type: ignore[arg-type]
    spec, run_dir = prepared(claude, repo, "backend", tmp_path)
    assert spec.argv[spec.argv.index("--tools") + 1] == "Read,Glob,Grep,Edit,Write,Bash"
    settings = json.loads((run_dir / "claude-settings.json").read_text(encoding="utf-8"))
    assert set(settings) == {"permissions"} and "sandbox" not in settings  # W2 settings, untouched
    codex = CodexLauncher(config.providers["codex"], config, "/opt/codex", native=True)  # type: ignore[arg-type]
    spec, _ = prepared(codex, repo, "frontend", tmp_path)
    assert spec.argv[spec.argv.index("--sandbox") + 1] == "workspace-write" and not spec.env
    assert "sandbox_workspace_write.network_access=false" in spec.argv and "--ignore-rules" not in spec.argv


def example(change) -> ProjectConfig:
    import yaml  # pylint: disable=import-outside-toplevel
    from feature_coordinator.cli import EXAMPLE_CONFIG  # pylint: disable=import-outside-toplevel
    data = yaml.safe_load(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
    change(data)
    return config_from_mapping(data)


def test_the_documented_example_enables_hardening_with_one_key() -> None:
    config = example(lambda d: d["runtime"]["native_hardening"].update(enabled=True))
    assert config.runtime.native_hardening == NativeHardening(enabled=True, claude_shell="disabled")


@pytest.mark.parametrize("change,match", [
    (lambda h: h.update(enabled="yes"), "true or false"),
    (lambda h: h.update(unknown=1), "unknown runtime.native_hardening keys"),
    (lambda h: h.update(claude_shell="unsandboxed"), "claude_shell"),
    (lambda h: h.update(codex_home="relative/dir"), "codex_home"),
    (lambda h: h.update(codex_home="/a/../b"), "codex_home"),
    (lambda h: h.update(read_paths=["/"]), "read_paths"),
    (lambda h: h.update(read_paths=["node"]), "read_paths"),
], ids=["enabled-not-bool", "unknown-key", "shell-mode", "relative-codex-home", "dotdot", "root", "relative"])
def test_invalid_hardening_configuration_is_rejected(change, match: str) -> None:
    def apply(data) -> None:
        change(data["runtime"]["native_hardening"])
    with pytest.raises(ConfigError, match=match):
        example(apply)


def test_hardening_is_refused_on_the_bwrap_backend() -> None:
    with pytest.raises(ConfigError, match="native-cli backend only"):
        example(lambda d: (d["runtime"].update(execution_backend="bwrap"),
                           d["runtime"]["native_hardening"].update(enabled=True)))


# -- the generated policy ---------------------------------------------------------------------------------

def test_hardened_claude_code_has_no_shell_and_confined_file_tools(config, repo, tmp_path, fake_home) -> None:
    config = hardened(config)
    launcher = ClaudeCodeLauncher(config.providers["claude"], config, "/opt/claude", native=True)  # type: ignore[arg-type]
    spec, run_dir = prepared(launcher, repo, "backend", tmp_path)
    assert spec.argv[spec.argv.index("--tools") + 1] == "Read,Glob,Grep,Edit,Write"   # no Bash at all
    assert "--restricted" in spec.argv and "--strict-mcp-config" in spec.argv
    assert "CLAUDE_CODE_SUBPROCESS_ENV_SCRUB" not in spec.env
    settings = json.loads((run_dir / "claude-settings.json").read_text(encoding="utf-8"))
    perms = settings["permissions"]
    assert not [rule for rule in perms["allow"] if rule.startswith("Bash")]
    assert {"Read(//proc/**)", "Edit(./.git/**)", "Edit(./.claude/**)", "Edit(./.mcp.json)", "Edit(./.codex/**)",
            "Edit(./.agents/**)", "Read(./backend/.env)", "Edit(./backend/.env)"} <= set(perms["deny"])
    assert perms["blockReadsOutsideWorkingDirectories"] is perms["blockWritesOutsideWorkingDirectories"] is True
    assert perms["disableBypassPermissionsMode"] == "disable"
    assert settings["disableAllHooks"] is settings["disableClaudeAiConnectors"] is True
    assert settings["enableAllProjectMcpServers"] is False and "sandbox" not in settings
    prompt = (run_dir / "prompt.txt").read_text(encoding="utf-8")
    assert "Shell commands are not available" in prompt and "Run Python as" not in prompt
    assert not (fake_home / ".claude" / "settings.json").exists()  # nothing written into HOME


def test_hardened_claude_code_sandboxed_shell_is_strict(config, repo, tmp_path) -> None:
    config = hardened(config, claude_shell="sandboxed", read_paths=("/opt/toolchain",))
    launcher = ClaudeCodeLauncher(config.providers["claude"], config, "/opt/claude", native=True)  # type: ignore[arg-type]
    spec, run_dir = prepared(launcher, repo, "backend", tmp_path)
    assert spec.argv[spec.argv.index("--tools") + 1].endswith(",Bash")
    sandbox = json.loads((run_dir / "claude-settings.json").read_text(encoding="utf-8"))["sandbox"]
    assert sandbox["enabled"] and sandbox["failIfUnavailable"] and not sandbox["allowUnsandboxedCommands"]
    assert not sandbox["autoAllowBashIfSandboxed"] and not sandbox["allowNestedUserNamespaces"]
    assert sandbox["excludedCommands"] == [] and not sandbox["enableWeakerNestedSandbox"]
    assert sandbox["network"] == {"allowedDomains": [], "strictAllowlist": True, "allowUnixSockets": [],
                                  "allowAllUnixSockets": False, "allowLocalBinding": False}
    assert {"~/", f"/run/user/{os.getuid()}", "/run/dbus", "/tmp/.X11-unix",
            str(repo / "backend/.env")} <= set(sandbox["filesystem"]["denyRead"])
    assert sandbox["filesystem"]["allowRead"] == [str(repo), "/opt/toolchain"]
    assert {v["name"] for v in sandbox["credentials"]["envVars"]} == set(nh.CLAUDE_CREDENTIAL_ENV)
    # deliberately NOT combined with the env scrub, which widens the Linux sandbox's writes (2.1.317)
    assert "CLAUDE_CODE_SUBPROCESS_ENV_SCRUB" not in spec.env


def test_hardened_codex_uses_the_permission_profile(config, repo, tmp_path, fake_home) -> None:
    config = hardened(config, codex_home=str(tmp_path / "worker-codex"), read_paths=("/opt/node",))
    launcher = CodexLauncher(config.providers["codex"], config, "/opt/codex/bin/codex", native=True)  # type: ignore[arg-type]
    spec, run_dir = prepared(launcher, repo, "frontend", tmp_path)
    argv = spec.argv
    assert "--sandbox" not in argv and not [a for a in argv if a.startswith("sandbox_")]  # would replace the profile
    for expected in ('forced_login_method="chatgpt"', 'default_permissions="fc_worker"',
                     "permissions.fc_worker.network.enabled=false", 'approval_policy="never"', 'web_search="disabled"',
                     "allow_login_shell=false", 'shell_environment_policy.inherit="core"',
                     *(f"features.{f}=false" for f in nh.CODEX_DISABLED_FEATURES)):
        assert expected in argv, expected
    assert "--ignore-rules" in argv and "--ignore-user-config" in argv and "--strict-config" in argv
    table = profile_table(argv)
    worker_home = tmp_path / "worker-codex"
    assert table[":minimal"] == "read" and table["/opt/codex"] == "read"           # the release, for its helper
    assert table[f"{worker_home}/tmp/arg0-linux"] == "read" and table["/opt/node"] == "read"
    assert table[f"{worker_home}/auth.json"] == table[f"{fake_home}/.codex/auth.json"] == "deny"
    assert table[str(run_dir / "tmp")] == "write"
    assert table[":workspace_roots"] == {".": "write", ".git": "read", ".claude": "read", ".mcp.json": "read",
                                         ".codex": "read", ".agents": "read", "backend/.env": "deny"}
    assert not [path for path, access in table.items() if access == "read" and path in (str(fake_home), "/")]
    assert spec.env == {"TMPDIR": str(run_dir / "tmp"), "CODEX_HOME": str(worker_home)}
    assert (run_dir / "tmp").stat().st_mode & 0o777 == 0o700


# -- fail closed: preflight -----------------------------------------------------------------------------

def private_dir(path: Path, mode: int = 0o700) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(mode)
    return path


def test_preflight_accepts_the_verified_setup(tmp_path, fake_home, repo) -> None:
    env = {"HOME": str(fake_home)}
    assert nh.preflight(ProviderKind.CLAUDE_CODE, "2.1.317 (Claude Code)", NativeHardening(enabled=True), env=env,
                        workspace=repo) == ""
    worker = NativeHardening(enabled=True, codex_home=str(private_dir(tmp_path / "worker-codex")),
                             read_paths=(str(fake_home / ".nvm/versions/node/v22"),))
    assert nh.preflight(ProviderKind.CODEX, "codex-cli 0.163.0", worker, env=env, workspace=repo) == ""


@pytest.mark.parametrize("kind,version,values,match", [
    (ProviderKind.CLAUDE_CODE, "2.1.316 (Claude Code)", {}, "needs claude-code >= 2.1.317"),
    (ProviderKind.CODEX, "codex-cli 0.162.1", {}, "needs codex >= 0.163.0"),
    (ProviderKind.CODEX, "", {}, "found '(none)'"),
    (ProviderKind.CODEX, "codex-cli 0.163.0", {"codex_home": "{tmp}/missing"}, "does not exist"),
    (ProviderKind.CODEX, "codex-cli 0.163.0", {"codex_home": "{tmp}/open"}, "mode 0700"),
    (ProviderKind.CODEX, "codex-cli 0.163.0", {"codex_home": "{tmp}/link"}, "not a symlink"),
    (ProviderKind.CODEX, "codex-cli 0.163.0", {"codex_home": "{repo}/.worker-codex"}, "inside the workspace"),
    (ProviderKind.CODEX, "codex-cli 0.163.0", {"read_paths": ("{home}",)}, "would expose"),
    (ProviderKind.CODEX, "codex-cli 0.163.0", {"read_paths": ("{home}/.ssh",)}, "would expose"),
    (ProviderKind.CODEX, "codex-cli 0.163.0", {"read_paths": ("{home}/.config",)}, "would expose"),
    (ProviderKind.CODEX, "codex-cli 0.163.0", {"read_paths": ("{home_parent}",)}, "would expose"),
    (ProviderKind.CODEX, "codex-cli 0.163.0", {"read_paths": ("{home}/.codex/packages",)}, "would expose"),
], ids=["claude-too-old", "codex-too-old", "no-version", "codex-home-missing", "codex-home-open", "codex-home-link",
        "codex-home-in-workspace", "read-home", "read-ssh", "read-config-parent", "read-home-parent", "read-codex"])
def test_preflight_fails_closed(kind, version, values, match, tmp_path, fake_home, repo) -> None:  # pylint: disable=too-many-arguments
    private_dir(tmp_path / "open", 0o755)
    (tmp_path / "link").symlink_to(private_dir(tmp_path / "real-codex"))
    private_dir(repo / ".worker-codex")

    def fill(value):
        return value.format(tmp=tmp_path, repo=repo, home=fake_home, home_parent=fake_home.parent)
    resolved = {k: tuple(map(fill, v)) if isinstance(v, tuple) else fill(v) for k, v in values.items()}
    reason = nh.preflight(kind, version, NativeHardening(enabled=True, **resolved), env={"HOME": str(fake_home)},
                          workspace=repo)
    assert match in reason


def test_preflight_refuses_managed_policy(monkeypatch, tmp_path, fake_home, repo) -> None:
    managed = tmp_path / "managed-settings.json"
    managed.write_text("{}", encoding="utf-8")
    monkeypatch.setitem(nh.MANAGED_POLICY_PATHS, ProviderKind.CLAUDE_CODE, (str(managed),))
    reason = nh.preflight(ProviderKind.CLAUDE_CODE, "2.1.317", NativeHardening(enabled=True),
                          env={"HOME": str(fake_home)}, workspace=repo)
    assert "managed claude-code policy" in reason


def test_sandboxed_claude_shell_needs_nested_user_namespaces(monkeypatch, fake_home, repo) -> None:
    monkeypatch.setattr(nh, "nested_userns_error", lambda bwrap=None: "denied by the host")
    reason = nh.preflight(ProviderKind.CLAUDE_CODE, "2.1.317", NativeHardening(enabled=True, claude_shell="sandboxed"),
                          env={"HOME": str(fake_home)}, workspace=repo)
    assert reason == "denied by the host"
    assert nh.preflight(ProviderKind.CLAUDE_CODE, "2.1.317", NativeHardening(enabled=True),
                        env={"HOME": str(fake_home)}, workspace=repo) == ""  # no shell, nothing to probe


# -- post-run checks ------------------------------------------------------------------------------------

def test_git_control_digest_sees_what_could_run_code_later(repo) -> None:
    base = nh.git_control_digest(repo)
    (repo / ".git/commondir").write_text(".\n", encoding="utf-8")   # Claude Code's own benign guards
    (repo / ".git/config.worktree").write_text("", encoding="utf-8")
    assert nh.git_control_digest(repo) == base
    for change in (lambda: (repo / ".git/config").open("a").write("[core]\n\tfsmonitor = /tmp/x\n"),
                   lambda: (repo / ".git/hooks/post-checkout").write_text("#!/bin/sh\n"),
                   lambda: (repo / ".git/info/attributes").write_text("* filter=x\n"),
                   lambda: (repo / ".git/commondir").write_text("/elsewhere\n")):
        before = nh.git_control_digest(repo)
        change()
        assert nh.git_control_digest(repo) != before


def test_claude_init_problems() -> None:
    tools = ["Read", "Glob", "Grep", "Edit", "Write"]
    assert not nh.claude_init_problems({"tools": tools, "mcp_servers": [], "permissionMode": "default"}, tools)
    assert nh.claude_init_problems({}, tools)  # no init event: the session cannot be confirmed
    assert nh.claude_init_problems({"tools": [*tools, "Bash"], "mcp_servers": [], "permissionMode": "acceptEdits"},
                                   tools)
    assert nh.claude_init_problems({"tools": tools, "mcp_servers": [{"name": "x"}], "permissionMode": "default"},
                                   tools)
    assert nh.claude_init_problems({"tools": tools, "mcp_servers": [], "permissionMode": "bypassPermissions"}, tools)


# -- the runner (fake agent as the CLI) -----------------------------------------------------------------

def steer(agent: Path, **control) -> None:
    Path(f"{agent}.json").write_text(json.dumps(control), encoding="utf-8")


def native_run(config, repo, role, actions, tmp, **checks):
    runner = NativeCliRunner(config.provider_for(role), config, **checks)  # type: ignore[arg-type]
    request = RunRequest(f"h.{role}", role, actions_prompt(actions), ScopedWorkspace(PathPolicy(repo, config), role),
                         context=run_context(tmp / "runs", f"h.{role}"))
    return runner.run(request)


@pytest.fixture
def agent_config(config, tmp_path) -> ProjectConfig:
    return hardened(config_with_agents(config, install_fake_agent(tmp_path / "agents")))


def test_a_hardened_run_completes_and_is_recorded(agent_config, repo, tmp_path, fake_home) -> None:
    del fake_home
    result = native_run(agent_config, repo, "backend", [{"do": "write", "path": "backend/app.py", "text": "x\n"}],
                        tmp_path, enforcement_check=lambda *a: pytest.fail("Claude Code has no self-test"))
    assert result.status == "completed", result.error
    assert result.details["hardening"]["agent_shell"] == "none (no Bash tool)"
    record = json.loads((Path(result.details["run_dir"]) / "native.json").read_text(encoding="utf-8"))
    assert record["hardening"]["enabled"] and "Bash" not in record["argv"][record["argv"].index("--tools") + 1]


def test_a_failed_preflight_never_starts_the_agent(agent_config, repo, tmp_path, fake_home) -> None:
    del fake_home
    result = native_run(agent_config, repo, "backend", [{"do": "write", "path": "backend/app.py"}], tmp_path,
                        hardening_check=lambda *a, **k: "the host cannot enforce it")
    assert result.status == "unavailable" and result.error == "native hardening: the host cannot enforce it"
    assert not (repo / "backend/app.py").exists()


def test_codex_runs_only_after_the_profile_self_test(agent_config, repo, tmp_path, fake_home) -> None:
    del fake_home
    seen: list = []

    def failing(executable, profile_args, workdir, env):
        seen.append((executable, profile_args, workdir, env.get("TMPDIR")))
        return "the Codex permission profile is not enforced: ['canary readable']"
    result = native_run(agent_config, repo, "frontend", [{"do": "write", "path": "frontend/x.ts"}], tmp_path,
                        enforcement_check=failing)
    assert result.status == "unavailable" and "canary readable" in result.error
    assert not (repo / "frontend/x.ts").exists()
    _exe, profile_args, workdir, tmpdir = seen[0]
    assert any(a.startswith("permissions.fc_worker.filesystem=") for a in profile_args)  # the run's own profile
    assert workdir.name == "selftest" and tmpdir == str(workdir.parent / "tmp")
    passed = native_run(agent_config, repo, "frontend", [{"do": "write", "path": "frontend/x.ts"}], tmp_path,
                        enforcement_check=lambda *a: "")
    assert passed.status == "completed", passed.error


@pytest.mark.parametrize("path,text", [(".git/config", "[core]\n\tfsmonitor = /tmp/evil\n"),
                                       (".git/hooks/pre-commit", "#!/bin/sh\n"),
                                       (".git/info/attributes", "* filter=evil\n")],
                         ids=["config", "hook", "attributes"])
def test_a_changed_git_control_file_rejects_the_run(agent_config, repo, tmp_path, fake_home, path, text) -> None:  # pylint: disable=too-many-arguments
    del fake_home
    result = native_run(agent_config, repo, "backend", [{"do": "append", "path": path, "text": text}], tmp_path)
    assert result.status == "rejected" and "Git control files" in result.error
    assert text in (repo / path).read_text(encoding="utf-8")  # left in place for the operator


def test_an_unexpected_claude_session_fails_the_run(agent_config, repo, tmp_path, fake_home) -> None:
    del fake_home
    agent = Path(agent_config.providers["claude"].executable)  # type: ignore[attr-defined]
    for control, problem in (({"report_tools": ["Read", "Bash"]}, "offered tools"),
                             ({"report_mcp": [{"name": "evil"}]}, "MCP servers")):
        steer(agent, **control)
        result = native_run(agent_config, repo, "backend", [], tmp_path)
        assert result.status == "failed" and problem in result.error


def test_a_dedicated_codex_home_is_used_for_the_login_check(agent_config, repo, tmp_path, fake_home) -> None:
    del fake_home
    worker = private_dir(tmp_path / "worker-codex")
    config = hardened(agent_config, codex_home=str(worker))
    seen: list = []

    def auth(kind, executable, env):
        del kind, executable
        seen.append(env.get("CODEX_HOME"))
        return SubscriptionCheck(True, "ChatGPT subscription")
    runner = NativeCliRunner(config.provider_for("frontend"), config, auth_check=auth,  # type: ignore[arg-type]
                             enforcement_check=lambda *a: "")
    request = RunRequest("h.f", "frontend", actions_prompt([]), ScopedWorkspace(PathPolicy(repo, config), "frontend"),
                         context=run_context(tmp_path / "runs", "h.f"))
    assert runner.run(request).status == "completed"
    assert seen == [str(worker)]
