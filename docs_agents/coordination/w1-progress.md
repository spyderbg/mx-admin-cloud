# W1 Progress Report — Worker Security (D-20, D-21) with Subscription Authentication

> Owner role: **coordination**. Durable progress log for the W1 completion task (started 2026-11-03).
> Branch: `security/worker-d20-d21` (Draft PR #20 → `dev`). Design document: [worker-security.md](worker-security.md).
> Evidence tags as in worker-security.md: **[V]** verified by execution, **[S]** source/documentation read, **[U]** unverified;
> since Stage 7 also **[C]** set by configuration only (accepted by the CLI, effect not demonstrated).

## Current status

| Item | Status |
| --- | --- |
| Stage | 8 — **Git command-execution vulnerability fixed** (`feature_coordinator/gitsafe.py`, [Stage 8](#stage-8--git-command-execution-defence)): the `.git/config` → `core.fsmonitor` class can no longer run a program during a Coordinator Git operation, on both backends. Stage 7 (opt-in `runtime.native_hardening`) remains. **W1 is NOT complete**: D-21 for the CLI processes stays unenforced (blocker B1); the autonomy demonstration (Priority 3) and the real-smoke-test plan (Priority 4) are **not yet done** |
| Branch head | Stages 6–7 plus the Stage 8 commits `65e609d` (fix) and `aa1ce03` (tests) — **local only, not pushed** (see [Delivery](#delivery)) |
| Active Worker | `mx-admin-worker.service` running from the operator's checkout on `dev` (`068d0b9`), `execution_backend: native-cli`, hardening not configured. **Not touched** by this work |
| Ready for | An operator-approved, bounded, real authenticated smoke test of the hardened mode ([requirements](#7h-requirements-for-a-real-authenticated-smoke-test)) |
| Open blockers | See [Open blockers and operator decisions](#open-blockers-and-operator-decisions) |

## Constraints followed

- All development happens in an independent clone outside the operator's checkout (on ext4), cloned from `origin` at `security/worker-d20-d21` (`19e9f6d`). Reason: the running Worker imports `feature_coordinator` straight from its checkout's working tree and commits there (`feature-coordinator:` reflog entries), so even `git worktree add` would have shared its `.git` (refs, locks, packed-refs). A separate clone writes nothing into the Worker's repository.
- The Worker's checkout, `.venv`, state directory, service, credentials and configuration were only **read** (unit `ExecStart`, `git status`, configuration key names). No branch switch, no service change, no `dev` change.
- No authentication token was printed, copied, read or committed. CLI checks used `--version`/`--help` only; Codex sandbox experiments used a throwaway `CODEX_HOME` with a fake `auth.json`.
- No paid API request and no real authenticated smoke test.

## Stage 1 — isolated workspace (done)

| Check | Result |
| --- | --- |
| Operator checkout | `dev` = `origin/dev` = `068d0b9`, clean; one worktree; Worker PID started 2026-11-02 21:37 with `--repo` = that checkout **[V]** |
| Remote | `security/worker-d20-d21` = `19e9f6d` on `origin` (PR #20 head), Draft, `CONFLICTING` with `dev` **[V]** |
| Workspace | Clone on ext4 (the operator checkout's disk is FUSE, where every file reads as 0777 and permission tests cannot run); own `.venv` (pytest, PyYAML, pylint, backend requirements) **[V]** |

## Stage 2 — W1 analysis and divergence (done)

- W1 (3 commits on `bfd55a7`) predates **59** `dev` commits: W2 (`native-cli` backend), W3 (checkpoints), W4–W4.5 (autonomous service, GitHub intake/publish, systemd), and the move of `scripts/feature_coordinator/` to the root `feature_coordinator/` package.
- Merge conflicts: 9 code/test files (39 hunks with docs), 7 documentation files, one modify/delete (`fc_fake_agent.py`). W1's new modules moved to the new package path automatically.
- **Compatibility hazards found** (each would have broken the running Worker after a merge and restart):
  1. W1 *rejects* `providers.*.credential_files`; the live Worker configuration contains it (key names checked only).
  2. W1 *rejects* `runtime.agent_network: true`; the live configuration sets it (it was `dev`'s default).
  3. The auto-merged Codex command line would have given the **native** backend W1's broker provider flags (pointing the live Worker's Codex at a broker that does not exist there).
  4. W1's version floor lived in `probe_runner()`, which the native backend shares.

| W1 component | State before this task |
| --- | --- |
| No credential mount, no API-key/proxy pass-through (bwrap backend) | Implemented, tested offline |
| Own network namespace per runner sandbox; endpoint handed over from inside it (`SO_NETNS_COOKIE`) | Implemented, tested offline |
| Nested command isolation (Claude Code strict Bash sandbox; Codex sandbox, no network) | Implemented, tested with the installed CLIs against a fake provider |
| Host-side broker, `credential_source: api-key-file` | Implemented, tested with a fake key; **per-token API billing** |
| Real agent execution under W1 | **Blocked**: no subscription-compatible credential path |

## Stage 3 — subscription authentication research (done)

Sources are the vendors' current documentation (fetched 2026-11-03) and offline experiments with the installed CLIs (Claude Code 2.1.317, Codex 0.163.0).

**Claude Code** **[S]** (code.claude.com: *Authentication*, *Sandboxing*, *Enterprise network configuration*, *Environment variables*):

- `claude setup-token` produces a one-year OAuth token for "CI pipelines, scripts, or other environments where interactive browser login isn't available"; it "authenticates with your Claude subscription" and "can only make model requests". The CLI reads it from `CLAUDE_CODE_OAUTH_TOKEN` (authentication precedence 5, below API keys, so the run environment must contain no API key).
- Gateways (`ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN`/`apiKeyHelper`) replace the subscription and bill per token — this is why W1's broker could not carry a subscription.
- Standard `HTTPS_PROXY` is supported for direct Anthropic API traffic ("including through an HTTPS proxy"). A CONNECT tunnel carries end-to-end TLS: the proxy never sees the credential. Required hosts: `api.anthropic.com` (API), `platform.claude.com` (OAuth exchange/refresh/revocation), `claude.ai` (account login); `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` removes telemetry/update hosts.
- Agent-executed commands: the sandbox's `credentials.envVars` `deny` entries are "unset before each sandboxed command runs"; `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1` strips credentials from **all** subprocesses (Bash, hooks, MCP) and, on Linux, runs Bash in an isolated PID namespace "so they can't read host process environments through `/proc`". "The Claude Code process keeps the credentials for its own API calls."
- The sandbox covers shell commands only; Read/Edit/Write/WebFetch follow permission rules. Anthropic recommends "a boundary around the whole process … with a network allowlist limited to the hosts you approve".

**Codex** **[S]** (learn.chatgpt.com: *Authentication*, *App server*, *Permissions*):

- ChatGPT sign-in stores refreshable tokens in `CODEX_HOME/auth.json` (or keyring / `ephemeral`); Codex refreshes them during use. "API keys are still the recommended default for automation", but ChatGPT login on headless machines is documented (device-code login, or a copied `auth.json`).
- App-server `chatgptAuthTokens` mode is **experimental** and "intended for host apps that already own the user's ChatGPT auth lifecycle" — the Worker does not, so it is **not** used. Codex access tokens (`CODEX_ACCESS_TOKEN`) are a workspace-admin feature.
- **Permission profiles** (beta): `[permissions.<name>.filesystem]` with `":minimal" = "read"`, workspace-root writes and `deny` entries, enforced by bubblewrap and seccomp on Linux.

**Offline experiments** (throwaway `CODEX_HOME` with a fake `auth.json`, no model call) **[V]**:

| Codex sandbox mode (installed 0.163.0, `codex sandbox`) | Agent command reads `CODEX_HOME/auth.json` |
| --- | --- |
| `sandbox_mode="workspace-write"` (what `dev` uses today, both backends) | **Yes — content printed** |
| Profile: `":minimal"="read"` + Codex release dir `read` + `":workspace_roots"={"."="write"}` | No (path absent); workspace read/write OK; `socket()` EPERM; own PID namespace; writes outside the workspace land in a throwaway tmpfs |
| Same + `"<CODEX_HOME>"="deny"` | No (`Permission denied`) |

**Design for subscriptions (it keeps W1's nesting — see the blocker below):** keep the subscription credential with the **trusted CLI process**, isolate it from **agent-controlled tools**, and restrict the CLI's own egress:

| Layer | Claude Code | Codex |
| --- | --- | --- |
| Credential (subscription) | `CLAUDE_CODE_OAUTH_TOKEN` from an operator-run `claude setup-token`, read by the worker from a private file and delivered only through W1's in-namespace channel (environment of the CLI process) | A Worker-dedicated `CODEX_HOME` signed in once by the operator with ChatGPT (device-code login); bound read-write into the sandbox so Codex can refresh it |
| Agent commands cannot read it | `credentials.envVars` deny + `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1` (own PID namespace); file tools denied `/proc` | Permission profile: minimal reads, workspace writes, `CODEX_HOME` denied, no network |
| Network (D-21) | CLI: W1's own network namespace; its only route is a host-side **CONNECT-only** egress proxy (end-to-end TLS, approved hosts only). Commands: none | same |

What this does **not** protect: the CLI process itself necessarily holds the credential (documented behaviour of both CLIs). A compromised CLI binary, or a tool that runs inside the CLI process, could misuse it — but only towards the approved provider hosts.

### Stage 3 finding — the W1 architecture cannot run on this host (BLOCKER)

W1 isolates agent commands by **nesting**: the CLI runs inside the Worker's bubblewrap sandbox (own network namespace), and every command the agent runs goes into the CLI's own bubblewrap sandbox *inside* it. This desktop has Ubuntu's `bwrap-userns-restrict` AppArmor profile installed (`/etc/apparmor.d/bwrap-userns-restrict`, `kernel.apparmor_restrict_unprivileged_userns = 1`), as required by `dev`'s [host setup](worker-host-setup.md), which forbids weakening it. Under that profile everything bubblewrap starts runs as `unpriv_bwrap` with every capability denied, so **no bubblewrap can start inside a bubblewrap**:

| Experiment (2026-11-03, this host) | Result |
| --- | --- |
| `codex sandbox` with the W1/permission-profile settings, top-level | Works: fake `auth.json` unreadable, `socket()` EPERM **[V]** |
| The same command inside an outer `bwrap --unshare-net` | `bwrap: No permissions to create new namespace` — the command never runs **[V]** |

Consequences on this host:

- **Claude Code** (strict nested Bash sandbox, no unsandboxed retry): its Linux sandbox is bubblewrap (documented **[S]**), so it cannot start inside ours either and its commands are refused — fail closed. This is inferred from the generic denial above; the end-to-end test with the real CLI is skipped on this host by design. Observed: with W1's strict settings inside an outer no-network sandbox, the CLI did **not** exit at start-up (a sandbox failure after start-up refuses commands instead, per the docs). `dev`'s host-setup table ("bwrap backend, Claude Code: Works") describes `dev`'s bwrap backend, which has **no** nested sandbox and therefore mounts the login file readable to agent commands.
- **Codex**: its nested sandbox cannot start (already recorded on `dev`, [agent-ownership §5](agent-ownership.md#5-residual-risks-operator-decisions-in-phase-status) item 6). Its namespace-free fallback, legacy Landlock, is documented as *rejected for filesystem-restricted policies*, so it could not keep `auth.json` away from agent commands either.
- Therefore the subscription egress design above, which keeps W1's nesting, **cannot run on the production desktop** without changing the host's AppArmor policy — which this task must not do. Its implementation (a CONNECT-only egress module and private-file checks for a `setup-token` file and a Worker `CODEX_HOME`) was started and then **parked outside the repository**, not committed; it can be resumed if option B or C below is chosen.

### Incident note — one unauthenticated request to OpenAI

While checking that `codex exec --strict-config` accepts permission-profile keys (2026-11-03 ~08:45 UTC), Codex was started with an **empty** throwaway `CODEX_HOME` (not logged in). Instead of stopping, it opened `wss://api.openai.com/v1/responses` with **no credential**; OpenAI answered `401 Missing bearer or basic authentication` at the WebSocket handshake. No prompt was sent, nothing was billed, and no credential existed or was used. Every later CLI experiment ran inside `bwrap --unshare-net` (verified: `ENETUNREACH`, no DNS). Lesson for the Worker: Codex does not fail closed on a missing login by itself — the native backend's pre-run `codex login status` check matters.

## Stage 4 — reconcile W1 with `dev` (done, `9af9621`)

Merge of `origin/dev` (`068d0b9`) into the W1 branch, no history rewrite. Resolution rules:

1. `dev` is the base for W2–W4.5 behaviour; W1 applies to the **`bwrap` backend only**. The `native-cli` backend's command lines, settings, environment and probe are unchanged (tests assert it).
2. Legacy keys stay loadable: `credential_files` and `runtime.agent_network: true` are accepted, have **no effect** (nothing is mounted, no sandbox shares the network) and are reported by `runners` and `worker-check` (`broker_config.ignored_settings()`).
3. The version floor moved from `probe_runner()` to the bwrap runner's preflight (`version_floor_error()`).
4. `worker-check` reports `INELIGIBLE` while `runtime.execution_backend` is `native-cli` (agents are not isolated there).

Additional fixes found while testing the merge:

5. `worker-check` would have reported **ELIGIBLE** on this desktop with a credential configured, although every `bwrap` run fails closed here. It now probes the nested command sandbox (`isolation_check.nested_sandbox_check`) and reports blocker B1. The installed-CLI compatibility tests skip **only** where that AppArmor policy is demonstrated (label `bwrap//&unpriv_bwrap` plus bubblewrap's namespace refusal); any other nested failure still fails.
6. `dev`'s W4 soak harness imports `FAKE_CREDENTIAL`, which W1 had removed from the test helpers (not collected by pytest; found by pylint) — restored.
7. W1's broker configuration moved to `broker_config.py`, because the merged `config.py` exceeded the 1000-line module limit.

## Files changed (Stage 4)

Merge commit `9af9621` against `dev` (`068d0b9`): 37 files, +3558/−217.

| Area | Files |
| --- | --- |
| New (W1, moved to the package) | `broker.py`, `netns_channel.py`, `sandbox_bootstrap.py`, `isolation_check.py` (+ `nested_sandbox_check`), tests `test_fc_broker.py`, `test_fc_isolation.py`, `test_fc_runner_compat.py`, `fc_fake_provider.py`, `worker-security.md` |
| New (this round) | `broker_config.py`, `w1-progress.md` |
| Resolved / adapted | `launchers.py` (W1 for bwrap only; `version_floor_error`), `providers.py` (`ignored_keys`), `config.py`, `commands.py` (`runners`, `worker-check`), `operations.py`, `cli.py`, tests `fc_helpers.py`, `fc_fake_agent.py`, `test_fc_launchers.py`, `test_fc_sandbox.py`, `test_fc_parallel.py`, `test_fc_worker_lifecycle.py`, `test_fc_worker_smoke.py` (fake-agent version string) |
| Docs | `README.md`, `architecture/README.md`, `agent-ownership.md`, `feature-workflow.md` (W1 is now §24), `operations.md`, `phase-status.md`, `project-config.example.yaml` |

Unchanged by W1: `native_backend.py`, the service, checkpoints, GitHub intake/publish, the backend, CLI and frontend.

## Tests executed (Stage 4)

All with fake credentials and fake providers; the W1 isolation and compat tests run in their own network namespaces. Outbound contact to a model provider: only the incident above. Caveat: the first three `codex sandbox` experiments (top-level, no login, no model call) ran with the host network available; their output showed no network activity, but it was not monitored.

| Check | Result |
| --- | --- |
| Coordinator regression `feature_coordinator/tests` + `scripts/tests`, on the committed merge | **982 passed, 23 skipped, 0 failed** (14 min), exit 0 **[V]** |
| — skips | 14 live GitHub / Projects (opt-in), 2 host login checks (opt-in), 2 paid smoke (opt-in), 1 FUSE-only state-directory test (this clone is on ext4), **4 nested-sandbox tests: denied by this host's AppArmor policy** (blocker B1) |
| First run on the uncommitted merge | 979 passed, 7 failed → fixed: 2 stale W1 tests (`python3` role commands vs W2's `{python}`), 2 expected after the compatibility changes, 1 `worker-check` verdict (fix 5), 2 "live checkout untouched" checks that only pass on a committed tree |
| W1 suites alone | `test_fc_broker.py`, `test_fc_isolation.py` (18), `test_fc_launchers.py`, `test_fc_sandbox.py`: passed; `test_fc_runner_compat.py`: 3 skipped (B1) **[V]** |
| Native backend unchanged | `test_fc_native_backend.py` and the W2–W4 suites pass unmodified; new assertions: native Claude settings have no `sandbox` block; legacy config keys are accepted and mount nothing **[V]** |
| Pylint | package 10.00/10, no messages; tests 10.00/10 apart from `wrong-import-order` notes that depend on `PYTHONPATH` and also occur in untouched `dev` files **[V]** |
| `scripts/check_docs_agents.py` | 6/6 (links, services, providers, openapi, readmes, drift) **[V]** |
| Codex permission profile, installed 0.163.0, top-level `codex sandbox` | fake `auth.json` hidden/denied; workspace read/write; `socket()` EPERM; own PID namespace **[V]** |
| Same, nested in bubblewrap | `No permissions to create new namespace` **[V]** |
| Not run | Real authenticated smoke tests (need operator approval); backend/CLI/frontend suites (not touched by W1) |

## Stage 7 — Decision A: hardened native-cli backend

Operator decision (2026-11-03): option A — opt-in hardening of the existing `native-cli` backend, an
incremental improvement that keeps the working Worker, its task lifecycle and GitHub integration. All
work in the isolated clone; the active Worker, its checkout, state, service, AppArmor and the logins were
not touched; `dev` unchanged; nothing pushed. The parked W1 experiments (`~/worktrees/w1-parked`: the
CONNECT egress proxy and broker secret-file checks) belong to option B and were **not** used.

Installed versions on the desktop: **Claude Code 2.1.317**, **Codex 0.163.0**, bubblewrap 0.9.0, kernel
7.0 (Landlock ABI 8), `kernel.apparmor_restrict_unprivileged_userns = 1`. No managed policy files
(`/etc/claude-code`, `/etc/codex` absent).

### 7a. Investigation results (what decided the design)

All experiments used fake credentials in a fake HOME and a local fake model provider; the CLIs ran
top-level under a **Landlock** domain that allowed TCP connects only to the fake provider's port
(`feature_coordinator/tests/fc_landlock.py`; Landlock needs no namespace, so the CLIs' own sandboxes
still start). Every claim below is **[V]** on this host unless tagged.

| # | Finding | Consequence |
| --- | --- | --- |
| F1 | **Today's native mode (dev, live Worker)**: an allowlisted Claude Code command (`{python} …`) read the fake Claude login, `~/.claude.json`, Codex `auth.json`, the SSH key and files outside the workspace (also via a planted symlink and `..`), saw 742 host PIDs and the CLI's environment, **connected to the D-Bus session bus**, and wrote `.git/config`, `.git/hooks/pre-commit` and `.mcp.json`. With dev's Codex flags (`:workspace` ≙ `workspace-write`) a command read every fake login and inherited the host variables | The risk option A addresses; unchanged while hardening is off |
| F2 | A `core.fsmonitor` in `.git/config` **runs during exactly the `git status` the native backend makes**; `core.hooksPath=/dev/null` (gitops) does not stop it. With F1, agent code can make the Worker run code unsandboxed after the run | Hardened mode blocks the write (both CLIs) and rejects any run that changes Git control files |
| F3 | **Claude Code's own Linux sandbox cannot run a command on this desktop, even with the CLI top-level.** Its `apply-seccomp` step needs a capability inside a nested user namespace; the kernel log shows `apparmor="DENIED" operation="capable" profile="unpriv_bwrap" capname="sys_admin"` for every attempt, with or without Landlock (a standalone `bwrap … unshare -U` reproduces it). The command never runs (fail closed) | Blocker B1 extends to option A for Claude Code: its Bash tool **cannot be sandboxed here**, so hardened mode removes it (`claude_shell: disabled`, the default) |
| F4 | `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1` (2.1.317, Linux) changes the sandbox's bwrap mounts: `/home`, `/root`, `/tmp`, `/var`, `/opt`, `/run`, `/mnt` become **writable** binds (without it: only the workspace and `/tmp/claude-<uid>`). With the scrub the sandbox also failed earlier (`Can't create file at .git/config.lock: Read-only file system`); it forces the permission mode to `default` | Not used with the sandbox. Not needed without Bash: the native environment is an allowlist without credential variables (existing tests) |
| F5 | Even without the scrub, Claude's sandbox shares the writable `/tmp/claude-<uid>` (the temp area of **every** Claude session of the user, including the operator's interactive ones) and pre-creates guard files in the host's `.git` (`commondir` = `.`, empty `config.worktree`, `glab-cli/`, `modules/`, `worktrees/`, `remotes/`, `branches/`; harmless to Git) | Recorded for `claude_shell: sandboxed` (unverified, unavailable here) |
| F6 | Claude Code's built-in file tools are already confined by `--restricted` (outside the working directory, `/proc`, settings/git/tool-config writes refused); the one gap was **untracked secrets inside the workspace** (`.env` read OK) | Hardened deny rules for the workspace's secret files; `block{Reads,Writes}OutsideWorkingDirectories` as a version-independent second layer |
| F7 | Codex: passing `--sandbox` (or any `sandbox_mode`) **replaces** a permission profile (docs [S]); `codex exec` honours `default_permissions` (commands see `CODEX_PERMISSION_PROFILE=fc_worker`) | Hardened Codex drops `--sandbox workspace-write` and the legacy `sandbox_workspace_write.*` key |
| F8 | Codex re-executes its own binary inside its sandbox, and its release lives **inside** the default `CODEX_HOME` (`~/.codex/packages/…`) next to `auth.json`. A profile that denies `CODEX_HOME` and re-opens a sub-path does **not** re-open it (EACCES); granting only the release and `CODEX_HOME/tmp/arg0-linux` (the `apply_patch` PATH helper) and denying the `auth.json` *file* works with the production layout | Profile built from narrow grants on `:minimal`, file-level denies |
| F9 | Codex with `forced_login_method="chatgpt"` and an API key in its environment **deletes `CODEX_HOME/auth.json`** ("Logging out") | The allowlisted native environment never passes API keys (existing tests); a Worker-dedicated `CODEX_HOME` limits the blast radius to the Worker's own login |
| F10 | Codex's `apply_patch` and `view_image` tools run under the profile (writes outside rejected; a file outside the grants is "No such file"); an escalation request is refused with `approval_policy = "never"`; a hostile workspace `.codex/config.toml` (`sandbox_mode = "danger-full-access"`, a hook, an MCP server) is ignored (untrusted project, user config ignored) | Covered by tests |
| F11 | Codex has execution and data paths outside the command sandbox that are on by default: `hooks`, `apps` (ChatGPT connectors), `plugins`, `remote_plugin`, `multi_agent`, `browser_use*`, `computer_use`, `in_app_browser`, `skill_mcp_dependency_install`, `shell_snapshot` | Disabled with `features.*=false` (accepted by `--strict-config`) |

### 7b. Exact implementation changes

| File | Change |
| --- | --- |
| `feature_coordinator/native_hardening.py` (new) | `NativeHardening` config (`enabled`, `claude_shell`, `codex_home`, `read_paths`) with strict parsing/validation; the generated policy (`harden_claude_settings`, `codex_profile_args`, `codex_exec_args`); fail-closed checks (`preflight`: version floors 2.1.317 / 0.163.0, managed policy files, nested-userns probe for `sandboxed`, private Worker `CODEX_HOME`, `read_paths` that would expose HOME or credentials; `codex_enforcement_error`: canary self-test under `codex sandbox`); post-run checks (`git_control_digest`, `claude_init_problems`) |
| `config.py` | `runtime.native_hardening` (field, parse, validate); refused on the `bwrap` backend. Docstring tightened to stay at the 1000-line limit |
| `launchers.py` | Hardened Claude Code: `--tools Read,Glob,Grep,Edit,Write` (no Bash unless `sandboxed`), confining settings, a prompt that says shell commands are unavailable. Hardened Codex: no `--sandbox`, `--ignore-rules`, the `fc_worker` profile, private per-run `TMPDIR` (0700), optional `CODEX_HOME`. Unhardened command lines unchanged (tests) |
| `native_backend.py` | Hardened runs: preflight → `unavailable` (never a fallback); Codex self-test before the agent starts; Git control files fingerprinted before/after (changed → `rejected`, left in place); Claude init event must show exactly the hardened tools and no MCP server (else `failed`); the dedicated `CODEX_HOME` is also used for the subscription check; `native.json`/details record the hardening |
| `commands.py`, `native_smoke.py` | `runners` / `worker-check` show the hardening scope (still `INELIGIBLE` for W1); login checks use the hardened environment |
| `project-config.example.yaml` | Documents `runtime.native_hardening` (disabled) |
| Tests | `test_fc_native_hardening.py` (fast, fake agent), `test_fc_native_hardening_cli.py` (installed CLIs), `fc_landlock.py`; `fc_fake_provider.py` gains multi-step scripts (`FAKE-STEP:`, incl. Codex `apply_patch`/`view_image`, escalation); `fc_fake_agent.py` reports tools/MCP/permission mode in its init event |

The generated policy, as built for this desktop:

* **Claude Code** — `--restricted --tools Read,Glob,Grep,Edit,Write --strict-mcp-config --permission-prompts none …`;
  settings add deny rules `Read(//proc/**)`, `Read(//sys/**)`, `Edit(./.git/**)`, `Edit(./.claude/**)`,
  `Edit(./.mcp.json)`, `Edit(./.codex/**)`, `Edit(./.agents/**)`, `Read/Edit(./<untracked secret>)`;
  `blockReadsOutsideWorkingDirectories`, `blockWritesOutsideWorkingDirectories`,
  `disableBypassPermissionsMode: disable`, `disableAllHooks`, `disableClaudeAiConnectors`,
  `disableBundledSkills`, `disableSkillShellExecution`, `enableAllProjectMcpServers: false`.
* **Codex** — `codex exec --json --strict-config --ephemeral --ignore-user-config --ignore-rules -C <repo> -o <run>/last-message.txt
  -c forced_login_method="chatgpt" -c default_permissions="fc_worker"
  -c permissions.fc_worker.filesystem={":minimal"="read", "<release>"="read", "<CODEX_HOME>/tmp/arg0-linux"="read",
  <read_paths>="read", "<CODEX_HOME>/auth.json"="deny", "~/.codex/auth.json"="deny", "~/.claude"="deny", "~/.claude.json"="deny",
  "<run>/tmp"="write", ":workspace_roots"={"."="write", ".git"="read", ".claude"="read", ".mcp.json"="read", ".codex"="read",
  ".agents"="read", "<untracked secret>"="deny"}} -c permissions.fc_worker.network.enabled=false -c approval_policy="never"
  -c web_search="disabled" -c allow_login_shell=false -c shell_environment_policy.inherit="core"
  -c shell_environment_policy.ignore_default_excludes=false -c features.<F11 list>=false [-m <model>] -`,
  environment `TMPDIR=<run>/tmp` (+ `CODEX_HOME` when configured).

### 7c. Security boundaries — enforced and tested

Tests: `test_fc_native_hardening_cli.py` (the installed CLIs, the production hardened command lines,
fake HOME/provider, Landlock) and `test_fc_native_hardening.py` (fake agent). Each negative test asserts
that the access is **denied by the CLI's enforcement** and, in addition, that no fake secret appears in
any request the model provider received.

| Agent path | Claude Code 2.1.317 (`claude_shell: disabled`) | Codex 0.163.0 (`fc_worker`) |
| --- | --- | --- |
| Shell commands, subprocesses | **No Bash tool** ("No such tool available: Bash"), also with `dangerouslyDisableSandbox` **[V]** | Run under the profile: fake Claude login, `~/.claude.json`, `auth.json`, SSH key, GitHub login, files outside the workspace, the untracked `.env`, a planted symlink and a `..` path all unreadable (EACCES/ENOENT); `cat auth.json` → Permission denied **[V]** |
| Writes | Write/Edit outside the workspace, to `.git/config`, `.git/hooks`, `.claude/settings.json`, `.mcp.json`, HOME refused; workspace write OK **[V]** | `.git`, `.codex` read-only (EROFS); writes outside never reach the host; only the workspace and the private TMPDIR persist **[V]** |
| Built-in file tools | Read of the login, the symlink, `..`, `/proc/self/environ`, the workspace `.env` refused; Grep of the workspace or of HOME returns no secret; Glob of HOME refused **[V]** | `apply_patch` outside / into `.git/hooks` / HOME refused, inside OK; `view_image` of a login or an outside file refused **[V]** |
| Network | No command path at all **[V]** (the CLI process itself: not confined, 7d) | `socket()` EPERM, also to the one port the host allowed (so the sandbox, not Landlock, denied it); DNS fails **[V]** |
| Unix sockets (D-Bus, agents, keyrings) | No command path **[V]** | EPERM for a socket in HOME and for the D-Bus session bus **[V]** |
| Environment / `/proc` | No command path **[V]** | No fake key, token or provider key in a command's environment; no marker in any visible `/proc/*/environ`; ≤ 4 PIDs visible **[V]** |
| Escalation | `bypassPermissions` refused (`--restricted`, setting) **[C]**; no prompt can be answered (`--permission-prompts none`) **[V]** | `require_escalated` request refused ("approval policy is Never") **[V]**; a hostile project config (full access, hook, MCP) ignored **[V]** |
| Subagents | `Agent` tool unavailable **[V]** | No subagent tool offered (`multi_agent` off) **[V]** |
| MCP / web | No MCP server (init event checked after every run) **[V]**; WebFetch unavailable **[V]** | No MCP tool and no `web_search` offered **[V]** |
| Hooks | No hook source: `--restricted` ignores user/project/local settings **[S]** (CLI help), managed policy refused by the preflight **[V]**; `disableAllHooks` **[C]** | Project hook not run **[V]**; `features.hooks=false` **[C]** |
| Git control files | Run **rejected** when `.git/config`, hooks or info change (fake agent) **[V]** | same **[V]** |
| Fail closed | Version floor, managed policy, init-event mismatch (Bash/MCP present → run failed) **[V]** (fakes) | Version floor, managed policy, Worker `CODEX_HOME` not private / a symlink / inside the workspace, `read_paths` exposing HOME or credentials, **self-test failure → runner unavailable, agent never started** **[V]**; the self-test itself runs with no TCP at all and detects a weakened profile **[V]** |
| `claude_shell: sandboxed` on this host | Preflight refuses (nested user namespace probe) **[V]**; started anyway, Claude Code's own sandbox refuses the command and it never runs **[V]** | — |

Mutation checks (not committed): with the hardening switched off, or with a weakened policy (Codex
profile granting `:root` read; Claude without the added rules), the negative tests **fail** — the fake
login (Codex) and the workspace secret (Claude) then reach the model provider.

### 7d. Boundaries that remain unenforced

1. **The trusted CLI processes** (both) keep the host network and read their own login — D-21 for the CLI
   process is not met (B1). A compromised CLI binary, or a defect in its in-process tool checks, could
   reach any host and use the credential.
2. **Exfiltration through the model**: anything the agent may read — the whole workspace except the
   untracked secret files, and for Codex the `:minimal` system paths such as `/etc` — can be sent to the
   provider. Untracked secrets whose names do not match `runtime.secret_patterns` are readable.
3. **Agent-written code still runs later**: in the gates (Phase 3 bwrap containment: own network
   namespace, private HOME, no logins — unchanged), and when people review or use the branch.
4. **Claude Code agents cannot run tests** in hardened mode (no Bash); the gates verify afterwards. A
   sandboxed Bash needs a host where Claude Code's sandbox can start (not this desktop) and is unverified.
5. **Not enforced while hardening is off** (the default, and the live Worker today): F1 and F2 apply.
6. Codex commands get no `/tmp` (a private TMPDIR instead): tools that hard-code `/tmp` fail (functional).
7. Server-managed Claude settings (claude.ai organisations) cannot be checked locally (not used by this
   personal subscription).

### 7e. Claude Code and Codex compatibility

* **Claude Code** — verified with 2.1.317 (floor in hardened mode; `blockWritesOutsideWorkingDirectories`
  needs ≥ 2.1.314). Subscription login unchanged: the CLI reads its own login as before; the hardening
  touches only the generated `--settings` file and the tool list. `apiKeySource: none`, the init event and
  model checks of W2 still apply. Permission mode is `acceptEdits` as before.
* **Codex** — verified with 0.163.0 (floor). `--strict-config` accepts every key used, so an unknown key in
  a future version fails the run instead of being ignored. ChatGPT login unchanged (`forced_login_method`);
  optional Worker-dedicated `CODEX_HOME` (must exist, be owned by the Worker user, mode 0700, outside the
  workspace; provisioned by the operator with `CODEX_HOME=<dir> codex login --device-auth`, never
  automatically). The per-run self-test uses its own empty `CODEX_HOME` (`codex sandbox` has no
  `--ignore-user-config`), so neither the operator's configuration nor any login takes part.
* Hardening off: both command lines are byte-for-byte the W2 ones (tests).

### 7f. Test results

| Check | Result |
| --- | --- |
| New tests `test_fc_native_hardening.py` (38, fakes) + `test_fc_native_hardening_cli.py` (5, installed CLIs) | **43 passed**, none skipped on this desktop (the real-CLI tests take ~7 s: the fake provider answers at once) **[V]** |
| Coordinator regression `feature_coordinator/tests` + `scripts/tests` on `bfaae1c` | **1025 passed, 23 skipped, 0 failed** (14 min), exit 0 **[V]** — 982 before + 43 new; the 23 skips are unchanged: 14 live GitHub/Projects (opt-in), 2 host login checks (opt-in), 2 paid smoke (opt-in), 1 FUSE-only, 4 nested-sandbox (B1) |
| Mutation checks (7c) | 4/4 mutants detected |
| Pylint | package 10.00/10; new tests 10.00/10 |
| `scripts/check_docs_agents.py` | 6/6 |
| `runners` with hardening on (fake HOME, Landlock, no TCP) | Hardened command lines and the hardening line shown; logins reported "not logged in" (fake HOME), exit 1 as expected |
| Not run | Any real authenticated session (needs approval, 7h); backend/CLI/frontend suites (not touched) |

### 7g. Remaining AppArmor limitations

* Ubuntu's `bwrap-userns-restrict` (`kernel.apparmor_restrict_unprivileged_userns = 1`) confines every
  process started by bubblewrap as `unpriv_bwrap`: **no capability inside a nested user namespace**.
* Consequences: (1) W1's outer network namespace around the CLI cannot be combined with any inner
  sandbox (B1); (2) **Claude Code's own Bash sandbox cannot start a command even top-level** (its
  `apply-seccomp` step) — so hardened Claude Code has no shell here; (3) Codex's sandbox is unaffected
  (bwrap + seccomp without a nested namespace), which is why its commands can be hardened.
* Anthropic's documented remedy (an AppArmor profile granting `bwrap` unconfined `userns`) is a host
  policy change and was not made.

### 7h. Requirements for a real authenticated smoke test

1. Explicit operator approval, bounded: the existing `subscription-smoke` harness (one disposable repository,
   local tracker, no GitHub, no gates, no PR, one attempt per task, Claude budget cap, short timeout; 3
   model sessions: Backend and CLI on Claude Code, Frontend on Codex) run with a project config that sets
   `runtime.native_hardening.enabled: true`; dry run first (`subscription-smoke` without `--apply`).
2. Run from this clone, with its own state directory — **not** the live Worker's checkout, state or
   service; the live Worker keeps running unchanged.
3. Operator choice for Codex: the existing login (`~/.codex`) or a Worker-dedicated `CODEX_HOME` provisioned
   by the operator beforehand (device-code login, mode 0700). For Claude Code the existing claude.ai login.
4. Preconditions checked by the harness: both status commands report the subscription; no API key;
   versions at the floors; the Codex self-test passes.
5. Pass criteria: the three files on the feature branch; every run `completed` with the hardening recorded
   in `native.json`; the Claude init event shows only the five file tools and no MCP server; no Git control
   change; no unexpected network access attempts in the transcripts.
6. Optional second step (separate approval): enable the hardening in the live Worker's configuration — a
   change of the active Worker, not made by this work.

### 7i. Incidents and deviations

* During the unsandboxed **baseline** of today's native mode (F1), the probe's DNS lookup of `example.com`
  resolved through the host resolver (Landlock does not cover UDP). No TCP connection was possible (Landlock),
  no provider was contacted, nothing was sent. The probe uses `fc-probe.invalid` since.
* `codex features list` was run once to list the feature flags; it reads the operator's Codex configuration
  (no login, no model call; its network use was not monitored).
* The first manual Codex self-test ran with the operator's `~/.codex` as `CODEX_HOME` (`codex sandbox`, no
  model, no TCP possible); it left no file there. The self-test now always uses its own empty `CODEX_HOME`.
* Experiment directories (`~/worktrees/.fcx`, `/tmp/claude-1000/fcx`) were deleted after the work.

## Stage 8 — Git command-execution defence

Priority 1 of the operator's follow-up: the repository's own Git configuration/attributes must not
turn a Coordinator Git operation into an execution vector (the `.git/config` → `core.fsmonitor`
attack found in [7a F2](#7a-investigation-results-what-decided-the-design), and its whole class:
hooks, filters, `core.sshCommand`, `diff.external`, `credential.helper`, …). Verified on this host:
a planted repo-local `core.fsmonitor=<program>` runs on a plain `git status`; a `clean` filter runs
on `git add`; `-c core.fsmonitor=` stops the fsmonitor and a `-c` value beats an injected
`GIT_CONFIG_PARAMETERS` **[V]**.

**Implemented protection** (`feature_coordinator/gitsafe.py`, used by both execution backends; does
not change the Git architecture):

1. **Prevention on every Coordinator Git call** — `git_argv()` + `hardened_env()`. Command-line `-c`
   overrides at the highest configuration precedence (above repo-local `.git/config`, its
   `[include]`/`[includeIf]`, and global/system) force `core.fsmonitor=`, `core.hooksPath=/dev/null`
   and `protocol.ext.allow=never` — keys the Coordinator never needs from any scope. The environment
   is stripped of the Git variables that inject configuration (`GIT_CONFIG*`,
   `GIT_CONFIG_PARAMETERS`, `GIT_CONFIG_COUNT`/`_KEY_*`/`_VALUE_*`) or name a program (`GIT_SSH*`,
   `GIT_PROXY_COMMAND`, `GIT_EXTERNAL_DIFF`, `GIT_PAGER`, `GIT_EDITOR`, `GIT_SEQUENCE_EDITOR`,
   `GIT_ASKPASS`, `GIT_ATTR_SOURCE`); trusted global/system config (identity, credential helper) is
   kept, so pushes keep working. Routed through: `gitops` (every call, via `_gitc()` + the extended
   `_env` scrub), `native_backend._git` and its post-run `merge-base`, `launchers.role_dirty_paths`,
   `workspace`, `checkpoints`, `sandbox` ls-files, `commands`, `lint_baseline` (`_git` + `archive`).
2. **Fail-closed gate** — `execution_findings()` / `assert_no_execution_config()` for the vectors a
   single `-c` cannot force off: filters are keyed by an arbitrary name, and `sshCommand`,
   `credential.helper`, `diff.external`, `*.textconv`, `gitProxy`, pager/editor are legitimately set
   in the operator's **global** config. They are a boundary violation only in the **untrusted**
   repository scope — `local`/`worktree`, with `[include]`s resolved through `git config
   --show-scope` (which runs no program). `NativeCliRunner.run` calls it before it launches an agent
   (returns `rejected`, so the agent never starts — **both** hardened and non-hardened); and
   `GitCoordinator.commit` calls it before it builds or lands a commit (raises
   `GitConfigExecutionError` → task `FAILED`, branch unmoved). It is not after-the-fact detection:
   the agent is refused before it runs and the commit before it happens, and prevention (layer 1)
   does not depend on it.

Precedence, env overrides and inheritance are accounted for (layer 1 beats repo-local, includes and
injected env; the scan resolves includes). git-lfs in the global scope is never flagged; a
repo-local `filter.lfs.*` that shadows it is. The two after-the-fact checks already present for the
hardened native backend (`git_control_digest` before/after the run) remain as defence in depth.

**Priority 2 — security regression tests** (`test_fc_gitsafe.py`, 16 tests, offline, disposable
repositories, harmless marker programs): a hostile `core.fsmonitor` runs on a raw `git status` but
not through the Coordinator's helpers; a planted hook runs on a raw commit but not a hardened one; a
`clean` filter runs on a raw `add` and the scan refuses the checkout; env-injected execution cannot
survive `hardened_env`; `GitCoordinator.commit` refuses a tampered checkout (fsmonitor/filter/
hooksPath) without moving the branch or running a program; the native backend refuses a tampered
checkout before running, hardened and non-hardened; normal status/add/commit and a clean coordinator
commit still work; `[include]`s are resolved and inert/builtin values are left alone. Each prevention
test asserts the attack precondition (the marker IS created on the raw call), so it cannot silently
pass; a mutation run that empties `SAFE_CONFIG_ARGS` and stubs the scan makes all 10 prevention/
refusal/scan tests fail while the clean-workflow tests still pass **[V]**.

**Results:** `test_fc_gitsafe.py` 16 passed; focused regression over every gitsafe-touched module
(`gitops`, `workflow`, `checkpoints`, `native_backend`, `native_hardening`, `launchers`, `sandbox`,
`autonomy`, plus the new file) **264 passed, 5 skipped** **[V]**; pylint 10.00/10 on the changed
modules and the test. The **full** `feature_coordinator/tests` + `scripts/tests` sweep on the Stage 8
commits is **not yet run** in this round (Stage 7 ran it green at 1025/23; the gitsafe change is
additive and the focused subset is green). Commits: `65e609d` (fix), `aa1ce03` (tests), local only.

### Not done this round (operator follow-up Priorities 3 and 4)

- **Priority 3 — autonomy demonstration.** A representative task run end-to-end with hardened Claude
  Code (no Bash tool): start, obtain code changes via file tools only, the Coordinator runs the
  validation gates, a gate failure is reported, a correction is requested through the existing
  validation retry, and a verified result is reached with no operator intervention. The pieces exist
  (the `Rig` autonomy harness in `test_fc_autonomy.py` already proves gate-fail → auto-fix retry →
  `VERIFIED` with mock runners; the hardened launcher gives Claude no Bash and routes gates
  Coordinator-side), but a test that drives the **hardened native runner** through the full
  `FeatureCoordinator` with a fail-once gate was not written before the session's usage limit.
- **Priority 4 — bounded real-smoke-test plan.** Instructions, expected results, limits and rollback
  for a ~3-session run with the existing Claude subscription and a separate Worker-only `CODEX_HOME`
  (ChatGPT), hardened mode on, no API-key billing, no credential copying, no real calls initiated.
  Stage 7 §7h sketches the shape; the full written plan is still owed.
- **Full-suite sweep** on the Stage 8 commits.

## Open blockers and operator decisions

### Blocker B1 — W1's nested isolation is incompatible with this host's AppArmor policy

The requirements — (1) subscription authentication, (2) agent-controlled commands and code cannot read credentials, (3) **no** network for the runner sandbox except approved model endpoints (W1's D-21, which also covers the CLI process), (4) autonomous runs, (5) the desktop's `bwrap-userns-restrict` policy unchanged — **cannot all be met on this desktop at the same time**. (3) needs an outer namespace around the CLI; (2) with a subscription credential inside that namespace needs a second, nested namespace for commands; (5) forbids the nesting. This is a property of the host policy, not of the authentication method: W1's API-key broker hits the same wall here.

**Status after Stage 7:** the operator chose option A; it is implemented as `runtime.native_hardening`
(opt-in). The row for A below is the Stage 3 plan; what was actually built and verified differs for
Claude Code — its Bash sandbox cannot start on this desktop (F3) and the env scrub weakens it (F4), so
hardened Claude Code has **no** Bash tool here (see [7a](#7a-investigation-results-what-decided-the-design)).
B1 itself is unchanged: D-21 for the CLI process still needs option B or C.

Narrowly scoped alternatives (each needs operator approval; Stage 7 implemented A):

| Option | What changes | D-20 (agent tools) | D-21 (agent tools) | D-21 (CLI process) | Subscription | Runs on this desktop |
| --- | --- | --- | --- | --- | --- | --- |
| **A. CLI-native tool sandboxes, CLI top-level** (recommended first step) | Opt-in hardening of the `native-cli` backend: Claude Code `sandbox` settings (strict, `failIfUnavailable`, no unsandboxed retry, empty strict domain allowlist, `filesystem.denyRead` for home secrets, `credentials` deny) + `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1` + `permissions.blockReadsOutsideWorkingDirectories`; Codex permission profile (`:minimal` read, workspace write, deny `CODEX_HOME` and home secrets, no network) instead of `workspace-write` | **Yes** — Codex verified offline here **[V]**; Claude: strict mode, empty network allowlist and env-variable deny verified nested by W1 on another host (2.1.303) **[V]**, `denyRead`/credential-file deny documented only **[S]** | **Yes** (CLIs' own sandboxes; top-level bubblewrap is allowed by the profile) | **No** — the trusted CLI keeps host network; its in-process tools (Read/Edit, WebFetch denied) are bounded by permission rules, not an OS sandbox | Yes — existing logins, or a Worker-dedicated `setup-token` / `CODEX_HOME` | Yes |
| **B. W1 as designed on a host without `bwrap-userns-restrict`** (e.g. a dedicated Worker VM) plus subscription egress | The parked CONNECT-only egress (end-to-end TLS to fixed hosts) + `CLAUDE_CODE_OAUTH_TOKEN` via the channel + Worker `CODEX_HOME` with the permission profile | Yes (design) | Yes | **Yes** | Yes | No — needs another host |
| **C. A narrowly scoped AppArmor rule** allowing nested user namespaces only for the Worker's outer sandbox | Host security policy (root, reviewed profile) | as B | as B | as B | Yes | Yes, after the policy change |
| D. W1's API-key broker | Per-token API billing | Yes | Yes | Yes | **No** | **No** (same nesting) |

Every option still needs (a) the operator-approved real authenticated smoke test before anything is called verified, and (b) for options using Worker-dedicated credentials, the operator's one-time provisioning (`claude setup-token` into a private file; `codex login --device-auth` into a Worker `CODEX_HOME`), which never touches the operator's own logins.

### Other decisions

1. **Real authenticated smoke test** (subscription usage) of the hardened mode — bounded, as in [7h](#7h-requirements-for-a-real-authenticated-smoke-test); required before the hardened mode is used for real work.
2. **Switching the running Worker's configuration** (enabling `runtime.native_hardening`) — a change of the active Worker, which this task does not make. Note the functional cost: hardened Claude Code agents cannot run commands (tests), only the gates do.
3. **Codex login for the Worker**: keep the operator's `~/.codex` or provision a Worker-dedicated `CODEX_HOME` (recommended, F9).
4. **Live risk on `dev` today (unchanged by this branch, hardening off):** the running `native-cli` Worker gives agent-run code the operator's files, logins, D-Bus session and network, and lets it write `.git/config`, whose `core.fsmonitor` the Worker then executes during `git status` (F1, F2) **[V]**. The hardened mode closes these paths; with hardening off a separate fix (e.g. `-c core.fsmonitor=false` and related keys on the coordinator's Git calls) would still be worth an operator decision.

## Delivery

- Commits are on the local branch `security/worker-d20-d21` in the isolated clone, **not pushed**: the delivery rule allows updating PR #20 only when the implementation is complete, and W1 is not complete (B1). The update is a plain fast-forward of `origin/security/worker-d20-d21` (`19e9f6d`) whenever the operator wants it published: `git push origin security/worker-d20-d21`.
- Stage 7 adds focused commits on top of Stage 6 (see `git log 439b159..`).
- PR #20 is unchanged (Draft). Nothing was merged; `dev` and the running Worker were not touched.

## Next action

Operator approval of the bounded hardened smoke test ([7h](#7h-requirements-for-a-real-authenticated-smoke-test)); after it passes, the operator decides whether the live Worker enables `runtime.native_hardening`. Full W1 (D-21 for the CLI process) still needs option B or C.
