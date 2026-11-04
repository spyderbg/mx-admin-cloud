"""Real Claude Code / Codex launchers: flag probe, command lines, sandboxed runs (fake agent + fake HOME;
brokered through a fake broker since W1 — no runner login is ever mounted)."""

# pylint: disable=missing-function-docstring,redefined-outer-name

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from feature_coordinator.config import ProjectConfig
from feature_coordinator.demo import init_demo_repo
from feature_coordinator.launchers import (
    CLAUDE_BROKER_ENV,
    FORBIDDEN_ARGS,
    LAUNCHERS as LAUNCHERS_BY_KIND,
    MIN_VERSIONS,
    REQUIRED_FLAGS,
    ClaudeCodeLauncher,
    CodexLauncher,
    RealCliRunner,
    _check_forbidden,
    parse_version,
    probe_runner,
    run_context,
    version_floor_error,
)
from feature_coordinator.operations import SELF_TEST_FILES
from feature_coordinator.paths import PathPolicy, ScopedWorkspace
from feature_coordinator.providers import CLAUDE_CODE, CODEX, ProviderKind
from feature_coordinator.runners import RunRequest
from feature_coordinator.sandbox import SandboxBuilder, SandboxPlan, install_prefix
from feature_coordinator.supervisor import supervise

from fc_helpers import FAKE_HOST_LOGIN, actions_prompt, fake_broker_factory


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
    assert probe.ok and probe.version == "fake-agent 9.9.9"


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


def test_versions_below_the_verified_minimum_are_unavailable(tmp_path: Path, agent_config: ProjectConfig) -> None:
    assert parse_version("2.1.303 (Claude Code)") == (2, 1, 303) and parse_version("codex-cli 0.163.0") == (0, 163, 0)
    assert MIN_VERSIONS[ProviderKind.CLAUDE_CODE] == (2, 1, 303) and MIN_VERSIONS[ProviderKind.CODEX] == (0, 163, 0)
    from fc_helpers import FAKE_AGENT  # pylint: disable=import-outside-toplevel
    old = tmp_path / "old-claude"
    old.write_text(FAKE_AGENT.read_text(encoding="utf-8").replace('print("fake-agent 9.9.9")', 'print("2.1.200")'),
                   encoding="utf-8")
    old.chmod(0o755)
    profile = type(CLAUDE_CODE)(name="claude", kind=ProviderKind.CLAUDE_CODE, executable=str(old))
    probe = probe_runner(profile)
    # The floor is the bwrap backend's (W1); the probe itself (shared with native-cli, W2) stays as before.
    floor = version_floor_error(probe)
    assert probe.ok and not probe.missing_flags and "older than the verified minimum 2.1.303" in floor
    result = RealCliRunner(profile, agent_config, broker_factory=fake_broker_factory()).run(
        RunRequest("t", "backend", "x", None))  # type: ignore[arg-type]
    assert result.status == "unavailable" and "2.1.303" in result.error



def test_legacy_worker_configuration_still_loads_but_mounts_nothing(repo3: Path, tmp_path: Path) -> None:
    """A W2-W4 Worker configuration names `credential_files` and `runtime.agent_network: true`. It must keep
    loading (the running Worker's config is never edited by W1), but neither key has any effect on the bwrap
    backend: no login file is bound and the sandbox never shares the host network (D-20, D-21)."""
    import yaml  # pylint: disable=import-outside-toplevel
    from feature_coordinator.cli import EXAMPLE_CONFIG  # pylint: disable=import-outside-toplevel
    from feature_coordinator.broker_config import ignored_settings  # pylint: disable=import-outside-toplevel
    from feature_coordinator.config import config_from_mapping  # pylint: disable=import-outside-toplevel
    data = yaml.safe_load(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
    data["providers"]["claude"]["credential_files"] = [".claude/.credentials.json"]
    data["providers"]["codex"]["credential_files"] = [".codex/auth.json"]
    data["runtime"]["agent_network"] = True
    legacy = config_from_mapping(data)
    notes = ignored_settings(legacy)
    assert any(n.startswith("runtime.agent_network") for n in notes)
    assert {n.split(" ")[0] for n in notes if n.startswith("providers.")} == {
        "providers.claude.credential_files", "providers.codex.credential_files"}
    for role, name in (("backend", "claude"), ("frontend", "codex")):
        launcher = LAUNCHERS_BY_KIND[legacy.providers[name].kind](legacy.providers[name], legacy, f"/opt/{name}")  # type: ignore[union-attr]
        spec = launcher.prepare(request_for(repo3, legacy, role, [], tmp_path), tmp_path / f"run-{name}", [role])
        assert all(".credentials" not in b.source and "auth.json" not in b.source for b in spec.binds)
        plan = SandboxBuilder(repo3, legacy).plan(run_dir=tmp_path / f"plan-{name}", role=role, env=spec.env,
                                                   extra=spec.binds)
        argv = plan.argv(spec.argv)
        assert "--share-net" not in argv and not any(".credentials" in a or "auth.json" in a for a in argv)
    assert not ignored_settings(config_from_mapping(yaml.safe_load(EXAMPLE_CONFIG.read_text(encoding="utf-8"))))

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
    # W1: every Bash command runs in Claude Code's own sandbox, strictly (D-21 nested isolation).
    sandbox = launcher.settings("backend", tmp_path)["sandbox"]
    assert sandbox["enabled"] and sandbox["failIfUnavailable"] and sandbox["allowUnsandboxedCommands"] is False
    assert sandbox["autoAllowBashIfSandboxed"] is False and "enableWeakerNestedSandbox" not in json.dumps(sandbox)
    assert sandbox["network"] == {"allowedDomains": [], "strictAllowlist": True}
    assert [e["name"] for e in sandbox["credentials"]["envVars"]] == list(CLAUDE_BROKER_ENV)
    assert {e["mode"] for e in sandbox["credentials"]["envVars"]} == {"deny"}
    # The native-cli backend (W2) is not changed by W1: no nested sandbox settings.
    native = ClaudeCodeLauncher(agent_config.providers["claude"], agent_config, "claude",  # type: ignore[arg-type]
                                native=True)
    assert "sandbox" not in native.settings("backend", tmp_path)


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
    # W1 (D-20): nothing is mounted to authenticate, no key or proxy variable is forwarded.
    assert [b.dest for b in spec.binds] == [str(install_prefix("/opt/claude"))]
    assert set(spec.env) == {"DISABLE_AUTOUPDATER", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"}
    assert ClaudeCodeLauncher.broker_environment("T") == {"ANTHROPIC_BASE_URL": "http://127.0.0.1:47613",
                                                          "ANTHROPIC_AUTH_TOKEN": "T"}


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
    assert spec.shadow_writable and spec.env == {"CODEX_HOME": str(fake_home / ".codex")}
    # W1: the broker is a custom Responses provider; unknown keys fail (--strict-config); no hosted web search.
    for override in ('model_provider="fc_broker"', 'model_providers.fc_broker.base_url="http://127.0.0.1:47613/v1"',
                     'model_providers.fc_broker.env_key="FC_BROKER_TOKEN"',
                     'model_providers.fc_broker.wire_api="responses"',
                     'web_search="disabled"', 'shell_environment_policy.inherit="core"'):
        assert override in argv, override
    assert "--strict-config" in argv and [b.dest for b in spec.binds] == [str(install_prefix("/opt/codex"))]
    assert CodexLauncher.broker_environment("T") == {"FC_BROKER_TOKEN": "T"}


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
    runner = RealCliRunner(agent_config.providers["claude"], agent_config,  # type: ignore[arg-type]
                           broker_factory=fake_broker_factory())
    result = runner.run(request_for(repo3, agent_config, "backend", actions, tmp_path))
    assert result.status == "completed", result.error
    assert result.changed_paths == ["backend/src/feature.py"]
    outcomes = {r["action"]: r for r in report_of(result)["report"]}
    for denied in ("try_write frontend/src/view.ts", "try_write .git/HEAD", "try_write backend/data/users.db"):
        assert not outcomes[denied]["ok"] and "Errno 30" in outcomes[denied]["error"], denied
    assert not outcomes["read .env"]["ok"]
    # W1 (D-20): the runner's login file is not in the sandbox at all (the CLI uses the broker).
    login = outcomes[f"read {fake_home}/.claude/.credentials.json"]
    assert not login["ok"] and "FileNotFoundError" in login["error"] and FAKE_HOST_LOGIN not in json.dumps(outcomes)
    # A write to that path lands in the private HOME (the run directory); the host file is untouched.
    assert outcomes[f"try_write {fake_home}/.claude/.credentials.json"]["ok"]
    assert FAKE_HOST_LOGIN in (fake_home / ".claude/.credentials.json").read_text(encoding="utf-8")
    assert (Path(result.details["run_dir"]) / "home/.claude/.credentials.json").read_text(encoding="utf-8") == "x\n"
    assert not outcomes["git commit --allow-empty -m x"]["ok"]
    assert (repo3 / "frontend/src/view.ts").read_text(encoding="utf-8") == SELF_TEST_FILES["frontend/src/view.ts"]
    env_keys = report_of(result)["env_keys"]
    assert "XDG_STATE_HOME" not in env_keys and "AGENT_QWEN_API_KEY" not in env_keys
    sandbox = json.loads((Path(result.details["run_dir"]) / "sandbox.json").read_text(encoding="utf-8"))
    assert sandbox["writable"] == ["backend", "docs_agents/backend"] and ".env" in sandbox["hidden"]
    assert "--share-net" not in sandbox["argv"] and not any(".credentials.json" in a for a in sandbox["argv"])


@pytest.mark.usefixtures("needs_bwrap")
def test_codex_style_run_stray_root_write_is_rejected(repo3: Path, agent_config: ProjectConfig,
                                                       tmp_path: Path, fake_home: Path) -> None:
    del fake_home
    actions = [{"do": "write", "path": "frontend/src/view.ts", "text": "export const views = ['x']\n"},
               {"do": "write", "path": "NOTES.md", "text": "agent notes at the root\n"}]
    runner = RealCliRunner(agent_config.providers["codex"], agent_config,  # type: ignore[arg-type]
                           broker_factory=fake_broker_factory())
    result = runner.run(request_for(repo3, agent_config, "frontend", actions, tmp_path))
    assert result.status == "rejected" and result.stray_writes == ["NOTES.md"]
    assert not (repo3 / "NOTES.md").exists()  # it only ever reached the discarded shadow
    assert report_of(result)["argv"][0] == "exec"


@pytest.mark.usefixtures("needs_bwrap")
def test_new_protected_or_symlink_entries_are_rejected_but_preexisting_secrets_are_not(
        repo3: Path, agent_config: ProjectConfig, tmp_path: Path, fake_home: Path) -> None:
    del fake_home
    (repo3 / "backend/.env.local").write_text("S=1\n", encoding="utf-8")  # pre-existing, untracked
    runner = RealCliRunner(agent_config.providers["claude"], agent_config,  # type: ignore[arg-type]
                           broker_factory=fake_broker_factory())
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
    runner = RealCliRunner(agent_config.providers["claude"], agent_config,  # type: ignore[arg-type]
                           broker_factory=fake_broker_factory())
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


# -- nested sandboxes (Codex's own bubblewrap inside ours) -------------------------------------

# Ubuntu's `bwrap-userns-restrict` AppArmor profile runs everything bubblewrap starts as
# `unpriv_bwrap`, with every capability denied, so a bubblewrap started inside ours cannot create
# namespaces. The Codex integration test is skipped only when the probe demonstrates exactly that:
# the `unpriv_bwrap` label inside our sandbox AND a namespace-permission refusal from the nested bwrap.
RESTRICTED_PROFILE = "unpriv_bwrap"
NAMESPACE_REFUSALS = ("No permissions to create",  # bwrap's EPERM message (Debian/Ubuntu and upstream wording)
                      "Creating new namespace failed: Operation not permitted",
                      "setting up uid map: Permission denied")


@dataclass(frozen=True)
class NestedSandbox:
    """The probe's decision: `restricted` (skip, with `reason`) only for the known host restriction."""
    supported: bool
    restricted: bool
    reason: str


def nested_sandbox_decision(label: str, returncode: int | None, stderr: str) -> NestedSandbox:
    """Decide from the AppArmor label seen inside our sandbox and the nested bwrap's outcome."""
    if returncode == 0:
        return NestedSandbox(True, False, "nested bubblewrap works")
    profiles = set(re.split(r"//&?", label.split(" (", 1)[0].strip()))  # "bwrap//&unpriv_bwrap (enforce)"
    refusal = next((line.strip() for line in stderr.splitlines()
                    if any(text in line for text in NAMESPACE_REFUSALS)), "")
    if RESTRICTED_PROFILE in profiles and refusal:
        return NestedSandbox(False, True, (
            f"nested bubblewrap is denied on this host: inside the coordinator's sandbox processes run as AppArmor "
            f"{label.strip()!r} (Ubuntu's bwrap-userns-restrict profile) and the nested bwrap failed: "
            f"{refusal[:160]!r}. "
            "Codex's own sandbox — and, since W1, Claude Code's nested Bash sandbox — cannot start inside ours; this "
            "affects runtime.execution_backend: bwrap (native-cli and the gates are unaffected). Run this test on a "
            "host without that profile; do not disable AppArmor or the user-namespace restriction to make it pass "
            "(agent-ownership.md §5, w1-progress.md blocker B1)"))
    return NestedSandbox(False, False, f"nested bubblewrap failed (exit {returncode}, AppArmor label "
                                       f"{label.strip() or 'unreadable'!r}), not the known restriction: "
                                       f"{stderr.strip()[:300]}")


def probe_nested_sandbox(plan: SandboxPlan, cwd: Path) -> NestedSandbox:
    """Start a minimal bubblewrap inside `plan` and decide. Writes nothing; discards the plan's shadow."""
    script = ("label=$(cat /proc/self/attr/apparmor/current 2>/dev/null || cat /proc/self/attr/current 2>/dev/null);"
              ' printf "label=%s\\n" "$label";'
              f" exec {shlex.quote(plan.bwrap)} --ro-bind / / --unshare-user /usr/bin/true")
    try:
        proc = subprocess.run(plan.argv(["/bin/sh", "-c", script]), cwd=cwd, env={"PATH": os.environ["PATH"]},
                              capture_output=True, text=True, timeout=30, check=False)
    except subprocess.TimeoutExpired:
        return nested_sandbox_decision("", None, "the nested-sandbox probe timed out")
    except OSError as exc:  # our own sandbox could not even start
        return nested_sandbox_decision("", None, f"the nested-sandbox probe could not start: {exc}")
    finally:
        plan.discard_shadow()
    label = next((line[len("label="):] for line in proc.stdout.splitlines() if line.startswith("label=")), "")
    return nested_sandbox_decision(label, proc.returncode, proc.stderr)


RESTRICTED_LABEL = "bwrap//&unpriv_bwrap (enforce)"
DEBIAN_REFUSAL = ("bwrap: No permissions to create new namespace, likely because the kernel does not allow "
                  "non-privileged user namespaces. See <https://deb.li/bubblewrap> or "
                  "<file:///usr/share/doc/bubblewrap/README.Debian.gz>.")
UPSTREAM_REFUSAL = ("bwrap: No permissions to create a new namespace, likely because the kernel does not allow "
                    "non-privileged user namespaces.")


@pytest.mark.parametrize("label, returncode, stderr, supported, restricted", [
    (RESTRICTED_LABEL, 0, "", True, False),                    # nested bubblewrap works: the test runs
    ("unconfined", 0, "", True, False),
    (RESTRICTED_LABEL, 1, DEBIAN_REFUSAL, False, True),        # the known restriction, demonstrated: skip
    ("unpriv_bwrap (enforce)", 1, UPSTREAM_REFUSAL, False, True),
    (RESTRICTED_LABEL, 1, "bwrap: setting up uid map: Permission denied", False, True),
    ("unconfined", 1, DEBIAN_REFUSAL, False, False),           # refused, but not under that profile: no skip
    ("", 1, DEBIAN_REFUSAL, False, False),                     # label unreadable: no skip
    ("unprivileged_userns (enforce)", 1, DEBIAN_REFUSAL, False, False),
    ("bwrap//&unpriv_bwrap_custom (enforce)", 1, DEBIAN_REFUSAL, False, False),
    (RESTRICTED_LABEL, 1, "bwrap: execvp /usr/bin/true: No such file or directory", False, False),  # other failures
    (RESTRICTED_LABEL, 127, "sh: 1: exec: /opt/bwrap: not found", False, False),
    (RESTRICTED_LABEL, -9, "", False, False),                  # killed
    (RESTRICTED_LABEL, None, "the nested-sandbox probe timed out", False, False),
], ids=["works-restricted-label", "works-unconfined", "debian-refusal", "upstream-refusal", "uid-map-refusal",
        "refusal-unconfined", "refusal-no-label", "refusal-userns-profile", "refusal-other-profile",
        "exec-failure", "bwrap-not-found", "killed", "timeout"])
def test_nested_sandbox_decision_skips_only_for_the_known_restriction(label, returncode, stderr, supported,
                                                                       restricted) -> None:
    decision = nested_sandbox_decision(label, returncode, stderr)
    assert (decision.supported, decision.restricted) == (supported, restricted), decision.reason
    if restricted:  # actionable: what is denied, by what, what it affects, what not to do
        for text in ("unpriv_bwrap", "bwrap-userns-restrict", stderr[:40], "execution_backend: bwrap", "native-cli",
                     "do not disable AppArmor"):
            assert text in decision.reason
    elif not supported:
        assert "not the known restriction" in decision.reason and str(returncode) in decision.reason


def frontend_codex_plan(repo3: Path, config: ProjectConfig, run_dir: Path, real: str | None) -> SandboxPlan:
    """The Frontend sandbox the Codex integration test uses (`real`: the codex binary, bound read-only)."""
    from feature_coordinator.sandbox import Bind  # pylint: disable=import-outside-toplevel
    return SandboxBuilder(repo3, config).plan(
        run_dir=run_dir, role="frontend", shadow_writable=True,
        env={"CODEX_HOME": f"{os.environ['HOME']}/.codex"},
        extra=[Bind(str(install_prefix(real)), str(install_prefix(real)), "ro")] if real else [])


@pytest.mark.usefixtures("needs_bwrap")
def test_nested_sandbox_probe_reaches_a_justified_decision(repo3: Path, config: ProjectConfig, tmp_path: Path) -> None:
    """In a real Frontend sandbox the probe finds nested bubblewrap working, or demonstrates the
    known restriction; any other failure is reported here, never turned into a skip."""
    def checkout() -> str:
        return subprocess.run(["git", "status", "--porcelain", "--ignored"], cwd=repo3, capture_output=True,
                              text=True, check=True).stdout
    before = checkout()
    probe = probe_nested_sandbox(frontend_codex_plan(repo3, config, tmp_path / "probe", None), repo3)
    assert probe.supported or probe.restricted, probe.reason
    assert checkout() == before and not (tmp_path / "probe" / "shadow").exists()


@pytest.mark.usefixtures("needs_bwrap")
@pytest.mark.parametrize("outer", ["missing", "failing"])
def test_nested_sandbox_probe_never_skips_for_an_unrelated_failure(outer: str, repo3: Path, config: ProjectConfig,
                                                                   tmp_path: Path) -> None:
    """A sandbox that cannot start, or fails for its own reasons, is reported, never a skip."""
    bwrap = tmp_path / "bwrap"
    if outer == "failing":
        bwrap.write_text('#!/bin/sh\necho "bwrap: Can\'t mount proc on /newroot/proc: Device or resource busy" >&2\n'
                         "exit 1\n", encoding="utf-8")
        bwrap.chmod(0o755)
    plan = replace(frontend_codex_plan(repo3, config, tmp_path / "probe", None), bwrap=str(bwrap))
    probe = probe_nested_sandbox(plan, repo3)
    assert not probe.supported and not probe.restricted and "not the known restriction" in probe.reason
    assert ("could not start" if outer == "missing" else "Can't mount proc") in probe.reason


@pytest.mark.usefixtures("needs_bwrap")
def test_codex_own_sandbox_works_nested_inside_ours(repo3: Path, config: ProjectConfig, tmp_path: Path) -> None:
    """Codex keeps its own (bubblewrap) sandbox inside the Frontend sandbox: writes stay in the
    Frontend roots and model-generated commands get no network. Uses `codex sandbox`, no model.
    Skipped only where the probe demonstrates the host's AppArmor restriction on nested bubblewrap."""
    if not shutil.which("codex"):
        pytest.skip("codex is not installed")
    real = str(Path(shutil.which("codex")).resolve())
    probe = probe_nested_sandbox(frontend_codex_plan(repo3, config, tmp_path / "probe", real), repo3)
    if probe.restricted:
        pytest.skip(probe.reason)
    home = tmp_path / "run" / "home"
    plan = frontend_codex_plan(repo3, config, tmp_path / "run", real)
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
                                 reason="set FC_HOST_RUNNER_CHECKS=1 to check that real runner logins stay outside "
                                        "the sandbox (no model call)")
LIVE_SMOKE = pytest.mark.skipif(os.environ.get("FC_LIVE_RUNNER_SMOKE") != "1" or
                                not os.environ.get("FC_LIVE_PROJECT_CONFIG"),
                                reason="set FC_LIVE_RUNNER_SMOKE=1 and FC_LIVE_PROJECT_CONFIG (a configuration with an "
                                       "operator-provisioned broker credential) for one tiny PAID run per runner "
                                       "(needs approval)")


def _real_home() -> Path:
    import pwd  # pylint: disable=import-outside-toplevel
    return Path(pwd.getpwuid(os.getuid()).pw_dir)


@HOST_CHECKS
@pytest.mark.usefixtures("needs_bwrap")
@pytest.mark.parametrize("profile,argv,logged_in,logged_out", [
    (CLAUDE_CODE, ["auth", "status"], '"loggedIn": true', '"loggedIn": false'),
    (CODEX, ["login", "status"], "Logged in", "Not logged in")], ids=["claude", "codex"])
def test_real_login_is_not_visible_inside_the_sandbox(profile, argv, logged_in, logged_out,  # pylint: disable=too-many-arguments
                                                      repo3: Path, config: ProjectConfig, tmp_path: Path,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    """W1 (D-20): with the REAL HOME path, the CLI inside the sandbox sees no login (the operator's
    files are not mounted and are never read by this test); only the absence is asserted."""
    monkeypatch.setenv("HOME", str(_real_home()))
    real = str(Path(shutil.which(profile.executable)).resolve())
    launcher = LAUNCHERS_BY_KIND[profile.kind](profile, config, real)
    spec = launcher.prepare(request_for(repo3, config, "backend", [], tmp_path), tmp_path / "run", ["backend"])
    plan = SandboxBuilder(repo3, config).plan(run_dir=tmp_path / "run", role="backend", env=spec.env, extra=spec.binds)
    supervise(plan.argv([real, *argv]), cwd=repo3, env={"PATH": os.environ["PATH"]},
              stdout_path=tmp_path / "s.out", stderr_path=tmp_path / "s.err", timeout_seconds=60)
    output = (tmp_path / "s.out").read_text(encoding="utf-8") + (tmp_path / "s.err").read_text(encoding="utf-8")
    assert logged_in not in output and logged_out in output


@LIVE_SMOKE
@pytest.mark.usefixtures("needs_bwrap")
@pytest.mark.parametrize("profile,role,path", [(CLAUDE_CODE, "backend", "backend/hello.txt"),
                                               (CODEX, "frontend", "frontend/hello.txt")], ids=["claude", "codex"])
def test_live_runner_smoke(profile, role: str, path: str, repo3: Path, config: ProjectConfig, tmp_path: Path,
                           monkeypatch: pytest.MonkeyPatch) -> None:
    """ONE tiny real model run per runner in a disposable repository, through the broker with the
    operator's provisioned credential (operator approval required; separate stage after W1)."""
    import dataclasses  # pylint: disable=import-outside-toplevel
    from feature_coordinator.config import load_config  # pylint: disable=import-outside-toplevel
    monkeypatch.setenv("HOME", str(_real_home()))
    config = load_config(Path(os.environ["FC_LIVE_PROJECT_CONFIG"]))
    config = dataclasses.replace(config, runtime=dataclasses.replace(config.runtime, claude_max_budget_usd=0.5,
                                                                     task_timeout_seconds=300))
    runner = RealCliRunner(profile, config)
    request = RunRequest(f"smoke.{role}", role, f"Create the file {path} containing exactly the word hello. "
                         "Do nothing else.", ScopedWorkspace(PathPolicy(repo3, config), role),
                         context=run_context(tmp_path / "runs", f"smoke.{role}", timeout_seconds=300))
    result = runner.run(request)
    assert result.status == "completed", (result.error, result.details)
    assert result.changed_paths == [path] and (repo3 / path).read_text(encoding="utf-8").strip() == "hello"
