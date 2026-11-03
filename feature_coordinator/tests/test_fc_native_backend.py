"""W2 native-cli backend: role → agent → model routing, Python discovery, subscription-only
authentication, supervision and workspace-wide acceptance — with the FAKE agent as a real host
process in disposable repositories (no model, no credential, no bubblewrap needed)."""

# pylint: disable=missing-function-docstring,redefined-outer-name

from __future__ import annotations

import dataclasses
import json
import os
import time
from pathlib import Path

import pytest

from feature_coordinator.config import ProjectConfig, config_from_mapping
from feature_coordinator.demo import init_demo_repo
from feature_coordinator.errors import ConfigError
from feature_coordinator.launchers import ClaudeCodeLauncher, CodexLauncher, RealCliRunner, run_context
from feature_coordinator.native_backend import (
    NATIVE_ENV_KEEP,
    NativeCliRunner,
    _claude_status,
    _codex_status,
    check_subscription,
    native_environment,
)
from feature_coordinator.operations import SELF_TEST_FILES, build_runners
from feature_coordinator.paths import PathPolicy, ScopedWorkspace
from feature_coordinator.providers import ProviderKind
from feature_coordinator.python_env import InterpreterNotFoundError, discover_python, expand_command
from feature_coordinator.runners import DisabledRoleRunner, RunRequest

from fc_helpers import actions_prompt

HOST_SECRETS = {"ANTHROPIC_API_KEY": "FAKE-ANTHROPIC-KEY", "ANTHROPIC_AUTH_TOKEN": "FAKE-AUTH-TOKEN",
                "ANTHROPIC_BASE_URL": "http://127.0.0.1:9", "OPENAI_API_KEY": "FAKE-OPENAI-KEY",
                "CODEX_API_KEY": "FAKE-CODEX-KEY", "CLAUDE_CODE_OAUTH_TOKEN": "FAKE-OAUTH",
                "CLAUDECODE": "1", "CLAUDE_CODE_MESSAGING_TOKEN": "FAKE-SESSION", "GH_TOKEN": "FAKE-GH",
                "AGENT_QWEN_API_KEY": "FAKE-QWEN", "CLAUDE_CODE_USE_BEDROCK": "1"}


@pytest.fixture
def repo3(tmp_path: Path) -> Path:
    path = tmp_path / "repo3"
    init_demo_repo(path, SELF_TEST_FILES)
    return path


@pytest.fixture
def host(fake_home: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for name, value in HOST_SECRETS.items():
        monkeypatch.setenv(name, value)
    return fake_home


def steer(agent: Path, **control) -> None:
    Path(f"{agent}.json").write_text(json.dumps(control), encoding="utf-8")


def run_native(repo: Path, config: ProjectConfig, role: str, actions: list, tmp: Path, **ctx):
    runner = NativeCliRunner(config.provider_for(role), config)  # type: ignore[arg-type]
    request = RunRequest(f"w2.{role}", role, actions_prompt(actions), ScopedWorkspace(PathPolicy(repo, config), role),
                         context=run_context(tmp / "runs", f"w2.{role}", **ctx))
    return runner.run(request)


def report_of(result) -> dict:
    return json.loads(result.transcript[0])


def yaml_config(change) -> ProjectConfig:
    import yaml  # pylint: disable=import-outside-toplevel
    from feature_coordinator.cli import EXAMPLE_CONFIG  # pylint: disable=import-outside-toplevel
    data = yaml.safe_load(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
    change(data)
    return config_from_mapping(data)


# -- role → agent → model -------------------------------------------------------------------------

def test_default_routing(config: ProjectConfig) -> None:
    routes = {r: (config.provider_for(r).name, config.model_for(r), config.role(r).enabled) for r in config.roles}
    assert routes == {"backend": ("claude", "opus", True), "cli": ("claude", "sonnet", True),
                      "frontend": ("codex", "", True), "automation": ("remote_qwen", "", False),
                      "coordination": ("claude", "sonnet", True)}
    assert config.runtime.execution_backend == "native-cli" and config.runtime.python_interpreter == "auto"


@pytest.mark.parametrize("change,role,provider,model", [
    (lambda d: d["roles"]["backend"].update(model="sonnet"), "backend", "claude", "sonnet"),
    (lambda d: d["roles"]["backend"].update(model="claude-opus-5-5"), "backend", "claude", "claude-opus-5-5"),
    (lambda d: d["roles"]["cli"].update(provider="codex", model="default"), "cli", "codex", ""),
    (lambda d: d["roles"]["cli"].update(provider="codex", model="gpt-5.5-codex"), "cli", "codex", "gpt-5.5-codex"),
    (lambda d: d["roles"]["frontend"].update(provider="claude", model="haiku"), "frontend", "claude", "haiku"),
    (lambda d: d["roles"]["cli"].pop("model"), "cli", "claude", ""),  # inherits the provider's (CLI default)
], ids=["backend-sonnet", "backend-full-id", "cli-codex-default", "cli-codex-model", "frontend-claude", "inherit"])
def test_routing_is_configuration_only(change, role: str, provider: str, model: str) -> None:
    config = yaml_config(change)
    assert (config.provider_for(role).name, config.model_for(role)) == (provider, model)
    owned = {"backend": ("backend/**", "docs_agents/backend/**"), "cli": ("cli/**", "docs_agents/cli/**"),
             "frontend": ("frontend/**", "docs_agents/frontend/**")}[role]
    assert config.role(role).owned == owned  # the write scope belongs to the role, not the agent


@pytest.mark.parametrize("change,match", [
    (lambda d: d["roles"]["cli"].update(provider="codex", model="sonnet"), "not a codex model"),
    (lambda d: d["roles"]["backend"].update(model="gpt-5.5"), "not a claude-code model"),
    (lambda d: d["roles"]["backend"].update(model="opus-ultra"), "not a claude-code model"),
    (lambda d: d["roles"]["automation"].update(model="opus"), "role-level model is not supported"),
    (lambda d: d["roles"]["cli"].update(provider="gemini"), "unknown provider"),
    (lambda d: d["roles"]["automation"].update(enabled="no"), "true or false"),
    (lambda d: d["runtime"].update(execution_backend="docker"), "execution_backend"),
    (lambda d: d["runtime"].update(python_interpreter="python3"), "python_interpreter"),
    (lambda d: d["runtime"]["role_commands"].update(backend=["python -m pytest:*"]), "bare interpreter"),
    (lambda d: d["runtime"]["role_commands"].update(backend=["python3 -m pytest:*"]), "bare interpreter"),
    (lambda d: d["runtime"]["role_commands"].update(backend=["{shell} -c x:*"]), "leading {python}"),
], ids=["claude-model-on-codex", "openai-model-on-claude", "unknown-alias", "qwen-model", "unknown-agent",
        "enabled-not-bool", "unknown-backend", "relative-interpreter", "bare-python", "bare-python3", "placeholder"])
def test_unsupported_configurations_are_rejected(change, match: str) -> None:
    with pytest.raises(ConfigError, match=match):
        yaml_config(change)


def test_native_command_lines_follow_the_role(repo3: Path, config: ProjectConfig, tmp_path: Path,
                                              host: Path) -> None:
    def spec(launcher, role):
        request = RunRequest(f"t.{role}", role, "x", ScopedWorkspace(PathPolicy(repo3, config), role))
        run_dir = tmp_path / f"run-{role}"
        run_dir.mkdir()
        return launcher.prepare(request, run_dir, [role]), run_dir
    backend, run_dir = spec(ClaudeCodeLauncher(config.providers["claude"], config, "/opt/claude", native=True),
                            "backend")  # type: ignore[arg-type]
    assert backend.argv[backend.argv.index("--model") + 1] == "opus" and not backend.binds
    assert "--restricted" in backend.argv
    assert backend.argv[backend.argv.index("--settings") + 1] == str(run_dir / "claude-settings.json")
    assert not (host / ".fc").exists()  # nothing is written into the operator's HOME
    cli, _ = spec(ClaudeCodeLauncher(config.providers["claude"], config, "/opt/claude", native=True),  # type: ignore[arg-type]
                  "cli")
    assert cli.argv[cli.argv.index("--model") + 1] == "sonnet"
    frontend, run_dir = spec(CodexLauncher(config.providers["codex"], config, "/opt/codex", native=True),
                             "frontend")  # type: ignore[arg-type]
    assert "-m" not in frontend.argv and "--strict-config" in frontend.argv  # CLI default model, unknown keys fail
    assert 'forced_login_method="chatgpt"' in frontend.argv and frontend.argv[-1] == "-"
    assert frontend.argv[frontend.argv.index("-o") + 1] == str(run_dir / "last-message.txt") and not frontend.env
    prompt = (tmp_path / "run-backend/prompt.txt").read_text(encoding="utf-8")
    assert "reject this whole run" in prompt and "Run Python as" in prompt


# -- Python interpreter discovery ---------------------------------------------------------------------

def test_python_discovery(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    (workspace / ".venv/bin").mkdir(parents=True)
    with pytest.raises(InterpreterNotFoundError):
        discover_python(workspace, search_path=str(tmp_path / "nothing"))  # no venv python, nothing on PATH
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "python3").symlink_to("/usr/bin/python3")
    found = discover_python(workspace, search_path=str(bindir))
    assert found.path == str(bindir / "python3") and found.source == "search-path"  # Ubuntu 24.04: no `python`
    (workspace / ".venv/bin/python").symlink_to("/usr/bin/python3")
    (workspace / ".venv/pyvenv.cfg").write_text("home = /usr/bin\n", encoding="utf-8")
    venv = discover_python(workspace, search_path=str(bindir))
    assert venv.source == "workspace-venv" and venv.venv_bin == str(workspace / ".venv/bin")
    assert expand_command("{python} -m pytest:*", venv, workspace) == [
        f"{workspace}/.venv/bin/python -m pytest:*", ".venv/bin/python -m pytest:*"]
    assert expand_command("kiddo-cli --help:*", venv, workspace) == ["kiddo-cli --help:*"]
    assert discover_python(workspace, "/usr/bin/python3").source == "configured"
    with pytest.raises(InterpreterNotFoundError, match="not an executable"):
        discover_python(workspace, "/nonexistent/python")
    spaced = tmp_path / "with space"
    (spaced / ".venv/bin").mkdir(parents=True)
    (spaced / ".venv/bin/python").symlink_to("/usr/bin/python3")
    with pytest.raises(InterpreterNotFoundError, match="whitespace"):
        expand_command("{python} -m pytest:*", discover_python(spaced), spaced)


# -- subscription authentication -----------------------------------------------------------------------

def test_the_environment_is_an_allowlist(host: Path) -> None:
    env = native_environment(tag="T")
    assert not set(env) & set(HOST_SECRETS)          # no key, token, base URL, cloud switch or host session
    assert env["HOME"] == str(host) and env["FC_RUN_TAG"] == "T"
    assert set(env) <= set(NATIVE_ENV_KEEP) | {"TERM", "NO_COLOR", "GIT_TERMINAL_PROMPT", "PYTHONDONTWRITEBYTECODE",
                                               "FC_RUN_TAG"}


def test_status_classification_never_keeps_the_raw_output() -> None:
    ok = _claude_status({"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty",
                         "subscriptionType": "max", "email": "person@example.invalid", "orgName": "Org"})
    assert ok.ok and ok.method == "claude.ai subscription (max)" and "example" not in ok.method + ok.detail
    for data, method in (({"loggedIn": True, "authMethod": "api_key", "apiProvider": "firstParty"}, "API key"),
                         ({"loggedIn": False}, "not logged in"),
                         ({"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "bedrock"}, "undetermined"),
                         ({"loggedIn": True, "authMethod": "oauth_token", "apiProvider": "firstParty"},
                          "undetermined")):
        assert not _claude_status(data).ok and _claude_status(data).method.startswith(method)
    assert _codex_status("Logged in using ChatGPT").ok
    refused = _codex_status("Logged in using an API key - sk-proj-***ABCD")
    assert not refused.ok and "ABCD" not in refused.method + refused.detail
    assert not _codex_status("Not logged in").ok and "confirmation" in _codex_status("??").detail


@pytest.mark.parametrize("kind", [ProviderKind.CLAUDE_CODE, ProviderKind.CODEX])
@pytest.mark.parametrize("auth,ok", [("subscription", True), ("api-key", False), ("none", False),
                                     ("unknown", False)])
def test_status_commands_of_the_cli(kind, auth: str, ok: bool, fake_agent: Path, host: Path) -> None:
    steer(fake_agent, auth=auth)
    check = check_subscription(kind, str(fake_agent), native_environment())
    assert check.ok is ok and "example.invalid" not in check.method + check.detail
    assert host.exists()


# -- native runs (the fake agent as a real host process) ----------------------------------------------

def test_native_run_with_subscription(repo3: Path, agent_config: ProjectConfig, tmp_path: Path, host: Path,
                                      fake_agent: Path) -> None:
    result = run_native(repo3, agent_config, "backend", [{"do": "write", "path": "backend/src/feature.py",
                                                          "text": "X = 1\n"}], tmp_path)
    assert result.status == "completed", result.error
    assert result.changed_paths == ["backend/src/feature.py"]
    details = result.details
    assert details["backend"] == "native-cli" and details["security_sandbox"] is False
    assert details["model_requested"] == "opus" and details["reported_model"] == "claude-opus-fake-1"
    assert details["authentication"] == "claude.ai subscription (pro)" and details["api_key_source"] == "none"
    report = report_of(result)
    assert report["home"] == str(host) and report["cwd"] == str(repo3)
    assert not set(report["env_keys"]) & set(HOST_SECRETS) and "FC_RUN_TAG" in report["env_keys"]
    assert report["argv"][report["argv"].index("--model") + 1] == "opus"
    native = json.loads((Path(details["run_dir"]) / "native.json").read_text(encoding="utf-8"))
    assert native["security_sandbox"] is False and not set(native["environment_names"]) & set(HOST_SECRETS)
    assert not (host / ".fc").exists() and fake_agent.exists()


def test_cli_role_on_codex(repo3: Path, agent_config: ProjectConfig, tmp_path: Path, host: Path) -> None:
    del host
    config = dataclasses.replace(agent_config, roles={**agent_config.roles, "cli": dataclasses.replace(
        agent_config.roles["cli"], provider="codex", model="gpt-5.5-codex")})
    runners = build_runners(config)
    assert isinstance(runners["cli"], NativeCliRunner) and runners["cli"].kind is ProviderKind.CODEX
    result = run_native(repo3, config, "cli", [{"do": "write", "path": "cli/src/new.py", "text": "N = 1\n"}],
                        tmp_path)
    assert result.status == "completed" and result.changed_paths == ["cli/src/new.py"]
    argv = report_of(result)["argv"]
    assert argv[0] == "exec" and argv[argv.index("-m") + 1] == "gpt-5.5-codex"
    assert 'forced_login_method="chatgpt"' in argv and result.details["authentication"] == "ChatGPT subscription"


def test_missing_executable_and_missing_authentication(repo3: Path, agent_config: ProjectConfig, tmp_path: Path,
                                                       host: Path, fake_agent: Path) -> None:
    del host
    missing = dataclasses.replace(agent_config, providers={**agent_config.providers, "claude": dataclasses.replace(
        agent_config.providers["claude"], executable=str(tmp_path / "no-such-claude"))})
    assert run_native(repo3, missing, "backend", [], tmp_path).status == "unavailable"
    for auth, words in (("none", "not logged in"), ("api-key", "API-key authentication"),
                        ("unknown", "operator confirmation")):
        steer(fake_agent, auth=auth)
        result = run_native(repo3, agent_config, "backend", [{"do": "write", "path": "backend/x.py"}], tmp_path)
        assert result.status == "unavailable" and words in result.error, auth
        assert not (repo3 / "backend/x.py").exists()  # nothing ran


def test_agent_failure_is_reported(repo3: Path, agent_config: ProjectConfig, tmp_path: Path, host: Path) -> None:
    del host
    result = run_native(repo3, agent_config, "backend", [{"do": "exit", "code": 3}], tmp_path)
    assert result.status == "failed" and "exit 3" in result.error


@pytest.mark.parametrize("outcome", ["timeout", "cancelled"])
def test_timeout_and_cancellation_stop_every_process(outcome: str, repo3: Path, agent_config: ProjectConfig,
                                                    tmp_path: Path, host: Path) -> None:
    del host
    config = dataclasses.replace(agent_config, runtime=dataclasses.replace(agent_config.runtime,
                                                                           terminate_grace_seconds=1))
    beat = repo3 / "backend/beat.log"
    started = time.monotonic()
    ctx = {"timeout_seconds": 2} if outcome == "timeout" else {"cancelled": lambda: time.monotonic() - started > 2}
    result = run_native(repo3, config, "backend", [{"do": "daemon", "path": "backend/beat.log", "interval": 0.05},
                                                   {"do": "sleep", "seconds": 60}], tmp_path, **ctx)
    assert result.status == outcome and time.monotonic() - started < 30
    assert result.details["leftover_processes_killed"] >= 1  # the setsid daemon left the process group
    size = beat.stat().st_size
    time.sleep(0.5)
    assert beat.stat().st_size == size


def test_changes_outside_the_role_reject_the_run_and_stay(repo3: Path, agent_config: ProjectConfig, tmp_path: Path,
                                                          host: Path) -> None:
    del host
    result = run_native(repo3, agent_config, "backend", [
        {"do": "write", "path": "backend/src/ok.py", "text": "OK = 1\n"},
        {"do": "write", "path": "frontend/src/view.ts", "text": "// not yours\n"},
        {"do": "write", "path": "NOTES.md", "text": "root\n"}], tmp_path)
    assert result.status == "rejected" and "frontend/src/view.ts" in result.error and "NOTES.md" in result.error
    assert (repo3 / "frontend/src/view.ts").read_text(encoding="utf-8") == "// not yours\n"  # left for review
    assert (repo3 / "NOTES.md").exists()


def test_protected_ignored_files_and_git_state_are_watched(repo3: Path, agent_config: ProjectConfig,
                                                           tmp_path: Path, host: Path) -> None:
    del host
    (repo3 / ".gitignore").write_text(".env\n", encoding="utf-8")
    from fc_helpers import git  # pylint: disable=import-outside-toplevel
    git(repo3, "add", ".gitignore")
    git(repo3, "commit", "-q", "-m", "ignore .env")
    (repo3 / ".env").write_text("SECRET=1\n", encoding="utf-8")
    env_run = run_native(repo3, agent_config, "backend", [{"do": "write", "path": ".env", "text": "SECRET=2\n"}],
                         tmp_path)
    assert env_run.status == "rejected" and ".env" in env_run.error
    head = git(repo3, "rev-parse", "HEAD")
    git_run = run_native(repo3, agent_config, "backend", [{"do": "write", "path": "backend/a.py", "text": "A = 1\n"},
                                                          {"do": "git", "args": ["add", "backend/a.py"]},
                                                          {"do": "git", "args": ["commit", "-q", "-m", "agent"]}],
                         tmp_path)
    assert git_run.status == "rejected" and "Git state" in git_run.error
    assert git(repo3, "rev-parse", "HEAD") != head  # reported, not undone: the operator decides


def test_a_new_ignored_testenv_local_toml_rejects_the_run(repo3: Path, agent_config: ProjectConfig,
                                                          tmp_path: Path, host: Path) -> None:
    del host
    (repo3 / ".gitignore").write_text("testenv.local.toml\n", encoding="utf-8")
    from fc_helpers import git  # pylint: disable=import-outside-toplevel
    git(repo3, "add", ".gitignore")
    git(repo3, "commit", "-q", "-m", "ignore testenv.local.toml")
    local = "test_environment/testenv.local.toml"  # Git-ignored, so only the protected-file watch sees it
    result = run_native(repo3, agent_config, "backend",
                        [{"do": "write", "path": local, "text": "[git]\npush = true\n"}], tmp_path)
    assert result.status == "rejected" and local in result.error


@pytest.mark.parametrize("control,words", [({"report_model": "claude-sonnet-x"}, "model substitution"),
                                           ({"api_key_source": "ANTHROPIC_API_KEY"}, "used an API key")])
def test_substitution_or_api_key_use_fails_the_run(control, words: str, repo3: Path, agent_config: ProjectConfig,
                                                   tmp_path: Path, host: Path, fake_agent: Path) -> None:
    del host
    steer(fake_agent, **control)
    result = run_native(repo3, agent_config, "backend", [{"do": "write", "path": "backend/b.py", "text": "B\n"}],
                        tmp_path)
    assert result.status == "failed" and words in result.error


def test_an_unfinished_git_operation_blocks_the_run(repo3: Path, agent_config: ProjectConfig, tmp_path: Path,
                                                    host: Path) -> None:
    del host
    (repo3 / ".git/MERGE_HEAD").write_text("0" * 40 + "\n", encoding="utf-8")
    result = run_native(repo3, agent_config, "backend", [{"do": "write", "path": "backend/c.py"}], tmp_path)
    assert result.status == "failed" and "MERGE_HEAD" in result.error and not (repo3 / "backend/c.py").exists()


def test_backend_selection_never_falls_back(agent_config: ProjectConfig) -> None:
    native = build_runners(agent_config)
    contained = build_runners(dataclasses.replace(
        agent_config, runtime=dataclasses.replace(agent_config.runtime, execution_backend="bwrap")))
    assert {type(native[r]).__name__ for r in ("backend", "cli", "frontend", "coordination")} == {"NativeCliRunner"}
    assert {type(contained[r]).__name__ for r in ("backend", "cli", "frontend")} == {"RealCliRunner"}
    assert isinstance(native["automation"], DisabledRoleRunner)
    assert native["automation"].run(None).status == "unavailable"  # type: ignore[arg-type]
    assert not isinstance(native["backend"], RealCliRunner) and os.environ.get("HOME")
