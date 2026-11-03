"""Opt-in hardening of the `native-cli` backend (W1 decision A): `runtime.native_hardening`.

The native backend runs the installed Claude Code / Codex CLIs top-level with the operator's
subscription logins (native_backend.py). With `native_hardening.enabled: true` the TOOLS those
CLIs give the agent are confined; the trusted CLI processes themselves are not (they keep host
network and read their own login, exactly as without hardening). Disabled (the default), nothing
here changes the backend's behaviour.

Claude Code (`claude_shell`):
  * `disabled` (default, the mode verified on the operator's desktop): no Bash tool at all, so
    nothing the agent writes is executed by the CLI. The file tools are confined by `--restricted`,
    `permissions.block{Reads,Writes}OutsideWorkingDirectories` and deny rules for /proc, the
    workspace's untracked secret files and Git / agent configuration (`.git`, `.claude`,
    `.mcp.json`, `.codex`, `.agents`); hooks, claude.ai connectors and bundled skills are off,
    bypassPermissions is disabled; MCP stays off (`--strict-mcp-config`). After the run the init
    event must show exactly the expected tools and no MCP server.
  * `sandboxed` (UNVERIFIED, opt-in): the role's Bash allowlist runs in Claude Code's own strict
    sandbox (no network, no Unix sockets, home and runtime sockets unreadable, credential variables
    unset). Its Linux sandbox needs capabilities inside a NESTED user namespace (`apply-seccomp`),
    which Ubuntu's `bwrap-userns-restrict` AppArmor policy denies; the preflight probes exactly that
    and the runner is unavailable where it fails (fail closed — blocker B1). It is deliberately not
    combined with CLAUDE_CODE_SUBPROCESS_ENV_SCRUB: on Linux (2.1.317) that variable makes the
    sandbox bind /home, /tmp, /var, /run, ... writable (w1-progress.md).

Codex: `--sandbox` is NOT passed (it, or any `sandbox_mode`, silently replaces a permission
profile). Instead the `fc_worker` permission profile: `:minimal` system paths read-only, the Codex
release (it re-executes itself in the sandbox) and its PATH helpers read-only, the login file
denied, workspace roots writable except `.git`, `.codex`, `.agents`, `.claude`, `.mcp.json`
(read-only) and the untracked secret files (denied), a private per-run TMPDIR writable, no
network. Approval policy `never` (escalation requests are refused), `--ignore-rules`, no login
shell, a core-only command environment, and hooks, apps (connectors), plugins, subagents,
browser/computer use, skill MCP installs and shell snapshots disabled. `codex_home` optionally
points Codex at a Worker-dedicated ChatGPT login (private directory). Before every run a canary
self-test (`codex sandbox` with the same profile, no model) must show that the profile is enforced
on this host — denied read, no socket, no write outside — or the runner is unavailable.

Both: the verified minimum versions, no managed policy files (they could add hooks or replace
the sandbox policy), and the Git control files (`.git/config`, hooks, info) unchanged by the run.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from .errors import ConfigError
from .providers import ProviderKind

if TYPE_CHECKING:  # pragma: no cover
    from .config import RuntimeConfig

CLAUDE_SHELL_MODES = ("disabled", "sandboxed")
# The versions whose hardened behaviour was verified on the operator's desktop (2026-11-03): Claude
# Code's block*OutsideWorkingDirectories (>= 2.1.314) and Codex's permission profiles in `exec`.
HARDENED_MIN_VERSIONS: Mapping[ProviderKind, tuple[int, int, int]] = {
    ProviderKind.CLAUDE_CODE: (2, 1, 317),
    ProviderKind.CODEX: (0, 163, 0),
}
# Policy layers above the generated settings / -c overrides: their presence is refused.
MANAGED_POLICY_PATHS: Mapping[ProviderKind, tuple[str, ...]] = {
    ProviderKind.CLAUDE_CODE: ("/etc/claude-code/managed-settings.json", "/etc/claude-code/managed-settings.d"),
    ProviderKind.CODEX: ("/etc/codex/managed_config.toml", "/etc/codex/requirements.toml"),
}
PROTECTED_AGENT_CONFIG = (".git", ".claude", ".mcp.json", ".codex", ".agents")  # never written by an agent
CLAUDE_CREDENTIAL_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN")
CODEX_PROFILE = "fc_worker"
CODEX_DISABLED_FEATURES = ("hooks", "apps", "plugins", "remote_plugin", "multi_agent", "browser_use",
                           "browser_use_external", "computer_use", "in_app_browser", "skill_mcp_dependency_install",
                           "shell_snapshot", "use_legacy_landlock", "network_proxy")
SELF_TEST_PYTHON = "/usr/bin/python3"  # inside the profile only :minimal paths are readable
# Credential and session locations under HOME that a read path may never expose.
SENSITIVE_HOME = (".ssh", ".claude", ".claude.json", ".codex", ".config/gh", ".git-credentials", ".netrc",
                  ".gnupg", ".local/share/keyrings", ".aws", ".config/gcloud", ".docker", ".kube",
                  ".config/mx-admin-agents", ".local/state", ".mozilla", ".config/google-chrome", ".config/chromium")


@dataclass(frozen=True)
class NativeHardening:
    """`runtime.native_hardening` (W1 decision A). Holds no secret, only paths."""

    enabled: bool = False
    claude_shell: str = "disabled"          # disabled | sandboxed (unverified; needs nested user namespaces)
    codex_home: str = ""                    # "" = the CLI's own CODEX_HOME; else a Worker-dedicated login dir
    read_paths: tuple[str, ...] = field(default_factory=tuple)  # extra read-only paths for Codex commands


def hardening_from_mapping(data: Mapping[str, Any]) -> NativeHardening:
    """`runtime.native_hardening` from the project-config YAML (strict: unknown keys are refused)."""
    known = set(NativeHardening.__dataclass_fields__)  # pylint: disable=no-member
    unknown = set(data) - known
    if unknown:
        raise ConfigError(f"unknown runtime.native_hardening keys: {sorted(unknown)}")
    values: dict[str, Any] = {}
    if "enabled" in data:
        if not isinstance(data["enabled"], bool):
            raise ConfigError("runtime.native_hardening.enabled must be true or false")
        values["enabled"] = data["enabled"]
    for key in ("claude_shell", "codex_home"):
        if key in data:
            values[key] = str(data[key] or "")
    if "read_paths" in data:
        values["read_paths"] = tuple(str(p) for p in (data["read_paths"] or ()))
    return NativeHardening(**values)


def _absolute(path: str) -> bool:
    return path.startswith("/") and "\x00" not in path and ".." not in path.split("/") and path != "/"


def validate_hardening(runtime: "RuntimeConfig") -> None:
    """Raise ConfigError unless `runtime.native_hardening` is valid for *runtime*."""
    hardening = runtime.native_hardening
    if hardening.claude_shell not in CLAUDE_SHELL_MODES:
        raise ConfigError(f"runtime.native_hardening.claude_shell must be one of {CLAUDE_SHELL_MODES}")
    if hardening.codex_home and not _absolute(hardening.codex_home):
        raise ConfigError("runtime.native_hardening.codex_home must be an absolute path without '..'")
    for path in hardening.read_paths:
        if not _absolute(path):
            raise ConfigError(f"runtime.native_hardening.read_paths: {path!r} must be an absolute path "
                              "without '..' (and not '/')")
    if hardening.enabled and runtime.execution_backend != "native-cli":
        raise ConfigError("runtime.native_hardening applies to the native-cli backend only; the bwrap backend "
                          "has its own containment (W1)")


# ---------------------------------------------------------------------------
# Generated policy
# ---------------------------------------------------------------------------

def claude_shell_enabled(hardening: NativeHardening) -> bool:
    """True when hardened Claude Code keeps the Bash tool (inside its own sandbox)."""
    return hardening.claude_shell == "sandboxed"


def harden_claude_settings(settings: dict[str, Any], hardening: NativeHardening, *, workspace: Path,
                           secrets: Sequence[str], home: Path) -> dict[str, Any]:
    """Add the hardened rules to the native Claude Code settings (*settings* is changed and returned)."""
    shell = claude_shell_enabled(hardening)
    permissions = settings["permissions"]
    if not shell:
        permissions["allow"] = [rule for rule in permissions["allow"] if not rule.startswith("Bash")]
    permissions["deny"] = [*permissions["deny"], "Read(//proc/**)", "Read(//sys/**)",
                           *(f"Edit(./{p}/**)" if not p.endswith(".json") else f"Edit(./{p})"
                             for p in PROTECTED_AGENT_CONFIG),
                           *(f"{tool}(./{rel})" for rel in secrets for tool in ("Read", "Edit"))]
    permissions.update(disableBypassPermissionsMode="disable", blockReadsOutsideWorkingDirectories=True,
                       blockWritesOutsideWorkingDirectories=True)
    settings.update(disableAllHooks=True, enableAllProjectMcpServers=False, disableClaudeAiConnectors=True,
                    disableBundledSkills=True, disableSkillShellExecution=True)
    if shell:
        uid = os.getuid()
        settings["sandbox"] = {
            "enabled": True, "failIfUnavailable": True, "allowUnsandboxedCommands": False,
            "autoAllowBashIfSandboxed": False, "excludedCommands": [], "allowNestedUserNamespaces": False,
            "enableWeakerNestedSandbox": False,
            "network": {"allowedDomains": [], "strictAllowlist": True, "allowUnixSockets": [],
                        "allowAllUnixSockets": False, "allowLocalBinding": False},
            "filesystem": {"denyRead": ["~/", str(home), f"/run/user/{uid}", "/run/dbus", "/tmp/.X11-unix",
                                        *(str(workspace / rel) for rel in secrets)],
                           "allowRead": [str(workspace), *hardening.read_paths]},
            "credentials": {"envVars": [{"name": name, "mode": "deny"} for name in CLAUDE_CREDENTIAL_ENV]},
        }
    return settings


def codex_home(hardening: NativeHardening, env: Mapping[str, str]) -> Path:
    """The CODEX_HOME hardened Codex runs with: the configured Worker login, else the CLI's own."""
    if hardening.codex_home:
        return Path(hardening.codex_home)
    return Path(env.get("CODEX_HOME") or Path(env.get("HOME", str(Path.home()))) / ".codex")


def _toml(value: Any) -> str:
    """A TOML inline value (JSON strings are valid TOML basic strings)."""
    if isinstance(value, Mapping):
        return "{" + ", ".join(f"{json.dumps(str(k))}={_toml(v)}" for k, v in value.items()) + "}"
    return json.dumps(value)


def codex_profile_args(*, release: Path, home: Path, codex_dir: Path, run_tmp: Path, secrets: Sequence[str],
                       read_paths: Sequence[str]) -> list[str]:
    """The `-c` overrides that define the `fc_worker` permission profile (used by runs and the self-test)."""
    filesystem: dict[str, Any] = {":minimal": "read", str(release): "read", f"{codex_dir}/tmp/arg0-linux": "read"}
    filesystem.update({path: "read" for path in read_paths})
    filesystem.update({f"{codex_dir}/auth.json": "deny", f"{home}/.codex/auth.json": "deny",
                       f"{home}/.claude": "deny", f"{home}/.claude.json": "deny", str(run_tmp): "write"})
    roots: dict[str, str] = {".": "write", **{p: "read" for p in PROTECTED_AGENT_CONFIG}}
    roots.update({rel: "deny" for rel in secrets})
    filesystem[":workspace_roots"] = roots
    return ["-c", f"permissions.{CODEX_PROFILE}.filesystem={_toml(filesystem)}",
            "-c", f"permissions.{CODEX_PROFILE}.network.enabled=false"]


def codex_exec_args(profile_args: Sequence[str]) -> list[str]:
    """Everything hardened `codex exec` gets besides the base command line (no `--sandbox`)."""
    args = ["--ignore-rules", "-c", f'default_permissions="{CODEX_PROFILE}"', *profile_args,
            "-c", 'approval_policy="never"', "-c", 'web_search="disabled"', "-c", "allow_login_shell=false",
            "-c", 'shell_environment_policy.inherit="core"',
            "-c", "shell_environment_policy.ignore_default_excludes=false"]
    for feature in CODEX_DISABLED_FEATURES:
        args += ["-c", f"features.{feature}=false"]
    return args


def summary(kind: ProviderKind, hardening: NativeHardening) -> dict[str, Any]:
    """What the run record says about the hardening (no secret, no path content)."""
    if not hardening.enabled:
        return {"enabled": False}
    if kind is ProviderKind.CLAUDE_CODE:
        return {"enabled": True, "agent_shell": "claude-sandbox (unverified)" if claude_shell_enabled(hardening)
                else "none (no Bash tool)", "cli_process": "unconfined (host network, own login)"}
    return {"enabled": True, "agent_shell": f"codex permission profile {CODEX_PROFILE} (no network)",
            "codex_home": "worker-dedicated" if hardening.codex_home else "the CLI's own",
            "cli_process": "unconfined (host network, own login)"}


# ---------------------------------------------------------------------------
# Fail closed: preflight, enforcement self-test, post-run checks
# ---------------------------------------------------------------------------

def version_error(kind: ProviderKind, version: str) -> str:
    """'' when *version* is at least the hardened floor, else why not."""
    found = re.search(r"(\d+)\.(\d+)\.(\d+)", version or "")
    parsed = tuple(int(found.group(i)) for i in (1, 2, 3)) if found else None
    minimum = HARDENED_MIN_VERSIONS[kind]
    if parsed and parsed >= minimum:
        return ""
    return (f"native hardening needs {kind.value} >= {'.'.join(map(str, minimum))} (the verified version), "
            f"found {version or '(none)'!r}")


def nested_userns_error(bwrap: str | None = None) -> str:
    """'' when a process inside a top-level bubblewrap may use a NESTED user namespace (what
    Claude Code's Linux sandbox needs for its seccomp step), else why not."""
    bwrap = bwrap or shutil.which("bwrap")
    if not bwrap or not shutil.which("socat"):
        return "Claude Code's Bash sandbox needs bubblewrap and socat"
    code = ("import os\nos.unshare(os.CLONE_NEWUSER)\n"
            "open('/proc/self/setgroups', 'w').write('deny')\n")
    argv = [bwrap, "--die-with-parent", "--unshare-user", "--unshare-pid", "--unshare-net", "--ro-bind", "/", "/",
            "--dev", "/dev", "--proc", "/proc", "--", SELF_TEST_PYTHON, "-I", "-c", code]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"the nested user namespace probe failed: {type(exc).__name__}"
    if proc.returncode == 0:
        return ""
    return ("Claude Code's Bash sandbox cannot start here: a nested user namespace has no capabilities "
            f"(host AppArmor policy, e.g. bwrap-userns-restrict; {proc.stderr.strip()[-160:]!r}); use "
            "runtime.native_hardening.claude_shell: disabled (blocker B1)")


def _private_dir_error(path: Path, workspace: Path) -> str:
    try:
        info = path.lstat()
    except OSError:
        return f"runtime.native_hardening.codex_home {path} does not exist (log in once with " \
               "`CODEX_HOME=<dir> codex login --device-auth`; not done automatically)"
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        return f"runtime.native_hardening.codex_home {path} must be a real directory (not a symlink)"
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        return f"runtime.native_hardening.codex_home {path} must be owned by this user with mode 0700"
    real, root = path.resolve(), workspace.resolve()
    if real == root or root in real.parents:
        return f"runtime.native_hardening.codex_home {path} must not be inside the workspace"
    return ""


def read_path_error(path: str, home: Path, codex_dir: Path) -> str:
    """'' when *path* may be readable by agent commands, else why not."""
    candidate = Path(path)
    sensitive = [home, *(home / rel for rel in SENSITIVE_HOME), codex_dir]
    for target in sensitive:
        if candidate == target or candidate in target.parents or (target != home and target in candidate.parents):
            return (f"runtime.native_hardening.read_paths: {path} would expose {target} (credentials or the "
                    "home directory) to agent commands")
    return ""


def preflight(kind: ProviderKind, version: str, hardening: NativeHardening, *, env: Mapping[str, str],
              workspace: Path) -> str:
    """'' when hardened runs of *kind* may start on this host, else the reason (fail closed)."""
    problem = version_error(kind, version)
    if problem:
        return problem
    present = [p for p in MANAGED_POLICY_PATHS[kind] if os.path.lexists(p)]
    if present:
        return (f"managed {kind.value} policy {present} is present; it could add hooks or replace the sandbox "
                "policy, so native hardening refuses to run (remove it or review and adapt this check)")
    home = Path(env.get("HOME", str(Path.home())))
    if kind is ProviderKind.CLAUDE_CODE:
        return nested_userns_error() if claude_shell_enabled(hardening) else ""
    codex_dir = codex_home(hardening, env)
    if hardening.codex_home:
        problem = _private_dir_error(codex_dir, workspace)
        if problem:
            return problem
    for path in hardening.read_paths:
        problem = read_path_error(path, home, codex_dir)
        if problem:
            return problem
    return ""


SELF_TEST = r"""
import json, os, socket, sys
canary, outside_write = sys.argv[1], sys.argv[2]
out = {}
try:
    open(canary, "rb").read()
    out["canary"] = "READ"
except OSError as exc:
    out["canary"] = exc.errno
try:
    socket.socket(socket.AF_INET, socket.SOCK_STREAM).close()
    out["socket"] = "OPEN"
except OSError as exc:
    out["socket"] = exc.errno
try:
    open(outside_write, "w").write("x")
    out["write"] = "WRITTEN" if os.path.exists(outside_write) else "lost"
except OSError as exc:
    out["write"] = exc.errno
open("inside.txt", "w").write("ok")
out["inside"] = open("inside.txt").read()
print("FC-SELFTEST" + json.dumps(out))
"""


def codex_enforcement_error(executable: str, profile_args: Sequence[str], workdir: Path,
                            env: Mapping[str, str]) -> str:
    """Run a canary under `codex sandbox` with the hardened profile (no model, no network): a file
    outside every granted path must be unreadable, `socket()` must fail, a write outside the
    workspace must not reach the host, a write inside must work. '' when all hold, else why not.
    `codex sandbox` has no --ignore-user-config, so it runs with its own empty CODEX_HOME: neither
    the operator's user configuration nor any login takes part."""
    workspace, outside, own_home = workdir / "ws", workdir / "outside", workdir / "codex-home"
    for path in (workspace, outside, own_home):
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    env = {**env, "CODEX_HOME": str(own_home)}
    canary = outside / "canary"
    canary.write_text("FC-CANARY\n", encoding="utf-8")
    target = outside / "written"
    argv = [executable, "sandbox", "-P", CODEX_PROFILE, "-C", str(workspace), *profile_args, "--",
            SELF_TEST_PYTHON, "-I", "-c", SELF_TEST, str(canary), str(target)]
    try:
        proc = subprocess.run(argv, cwd=workspace, capture_output=True, text=True, env=dict(env), timeout=60,
                              check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"the Codex permission-profile self-test could not run: {type(exc).__name__}"
    line = next((ln for ln in proc.stdout.splitlines() if ln.startswith("FC-SELFTEST")), "")
    if not line:
        return (f"the Codex permission profile cannot be enforced here (self-test exit {proc.returncode}: "
                f"{(proc.stderr or proc.stdout).strip()[-200:]!r})")
    result = json.loads(line[len("FC-SELFTEST"):])
    failures = [name for name, bad in (("canary readable", result.get("canary") == "READ"),
                                       ("socket allowed", result.get("socket") == "OPEN"),
                                       ("outside write reached the host", target.exists()),
                                       ("workspace not writable", result.get("inside") != "ok")) if bad]
    return f"the Codex permission profile is not enforced: {failures}" if failures else ""


def git_control_digest(root: Path) -> str:
    """A digest of the Git control files an agent could use to run code in a later, unsandboxed Git
    command (config, worktree config, commondir, hooks, info): names and contents."""
    git = root / ".git"
    entries: list[str] = []
    for rel in ("config", "config.worktree", "commondir"):
        path = git / rel
        if path.is_file() and not path.is_symlink():
            data = path.read_bytes()
            if (rel, data.strip()) not in (("config.worktree", b""), ("commondir", b".")):
                entries.append(f"{rel}:{hashlib.sha256(data).hexdigest()}")
        elif os.path.lexists(path):
            entries.append(f"{rel}:special")
    for sub in ("hooks", "info"):
        base = git / sub
        for path in sorted(base.rglob("*")) if base.is_dir() else ():
            rel = path.relative_to(git)
            if path.is_symlink() or not (path.is_file() or path.is_dir()):
                entries.append(f"{rel}:special")
            elif path.is_file() and path.name != ".claude-sandbox-noreplace":
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                entries.append(f"{rel}:{oct(path.stat().st_mode & 0o777)}:{digest}")
    return hashlib.sha256("\n".join(entries).encode()).hexdigest()


def claude_init_problems(init: Mapping[str, Any], expected_tools: Sequence[str]) -> list[str]:
    """Hardened Claude Code: the init event must show exactly the expected tools and no MCP server."""
    if not init:
        return ["Claude Code reported no init event; the hardened session cannot be confirmed"]
    problems = []
    offered = sorted(init.get("tools") or ())
    if offered != sorted(expected_tools):
        problems.append(f"Claude Code offered tools {offered}, expected {sorted(expected_tools)}")
    if init.get("mcp_servers"):
        problems.append("Claude Code loaded MCP servers in a hardened run")
    if init.get("permissionMode") not in ("acceptEdits", "default"):
        problems.append(f"Claude Code ran in permission mode {init.get('permissionMode')!r}")
    return problems
