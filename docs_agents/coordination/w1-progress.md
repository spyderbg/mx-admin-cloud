# W1 Progress Report — Worker Security (D-20, D-21) with Subscription Authentication

> Owner role: **coordination**. Durable progress log for the W1 completion task (started 2026-11-03).
> Branch: `security/worker-d20-d21` (Draft PR #20 → `dev`). Design document: [worker-security.md](worker-security.md).
> Evidence tags as in worker-security.md: **[V]** verified by execution, **[S]** source/documentation read, **[U]** unverified.

## Current status

| Item | Status |
| --- | --- |
| Stage | 4 — reconciling W1 with `dev` (in progress) |
| Active Worker | `mx-admin-worker.service` running from the operator's checkout on `dev` (`068d0b9`), `execution_backend: native-cli`. **Not touched** by this work |
| Open blockers | See [Open blockers and operator decisions](#open-blockers-and-operator-decisions) |

## Constraints followed

- All development happens in an independent clone, `/home/spyder/worktrees/mx-admin-cloud-w1` (ext4), cloned from `origin` at `security/worker-d20-d21` (`19e9f6d`). Reason: the running Worker imports `feature_coordinator` straight from its checkout's working tree and commits there (`feature-coordinator:` reflog entries), so even `git worktree add` would have shared its `.git` (refs, locks, packed-refs). A separate clone writes nothing into the Worker's repository.
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

**Conclusion (design chosen for implementation):** keep the subscription credential with the **trusted CLI process**, isolate it from **agent-controlled tools**, and restrict the CLI's own egress:

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

- **Claude Code** (strict nested Bash sandbox, `failIfUnavailable`): the CLI cannot start its command sandbox inside ours, so every `bwrap`-backend Claude run fails closed. `dev`'s host-setup table ("bwrap backend, Claude Code: Works") describes `dev`'s bwrap backend, which has **no** nested sandbox and therefore mounts the login file readable to agent commands.
- **Codex**: its nested sandbox cannot start (already recorded on `dev`, [agent-ownership §5](agent-ownership.md#5-residual-risks-operator-decisions-in-phase-status) item 6). Its namespace-free fallback, legacy Landlock, is documented as *rejected for filesystem-restricted policies*, so it could not keep `auth.json` away from agent commands either.
- Therefore the subscription egress design above, which keeps W1's nesting, **cannot run on the production desktop** without changing the host's AppArmor policy — which this task must not do. Its implementation was started and then **parked**, not committed (`/home/spyder/worktrees/w1-parked/`).

### Incident note — one unauthenticated request to OpenAI

While checking that `codex exec --strict-config` accepts permission-profile keys (2026-11-03 ~08:45 UTC), Codex was started with an **empty** throwaway `CODEX_HOME` (not logged in). Instead of stopping, it opened `wss://api.openai.com/v1/responses` with **no credential**; OpenAI answered `401 Missing bearer or basic authentication` at the WebSocket handshake. No prompt was sent, nothing was billed, and no credential existed or was used. Every later CLI experiment ran inside `bwrap --unshare-net` (verified: `ENETUNREACH`, no DNS). Lesson for the Worker: Codex does not fail closed on a missing login by itself — the native backend's pre-run `codex login status` check matters.

## Stage 4 — reconcile W1 with `dev` (in progress)

Merge of `origin/dev` (`068d0b9`) into the W1 branch, no history rewrite. Resolution rules:

1. `dev` is the base for W2–W4.5 behaviour; W1 applies to the **`bwrap` backend only**. The `native-cli` backend's command lines, settings, environment and probe are unchanged (tests assert it).
2. Legacy keys stay loadable: `credential_files` and `runtime.agent_network: true` are accepted, have **no effect** (nothing is mounted, no sandbox shares the network) and are reported by `runners` and `worker-check` (`config.ignored_settings()`).
3. The version floor moved from `probe_runner()` to the bwrap runner's preflight (`version_floor_error()`).
4. `worker-check` reports `INELIGIBLE` while `runtime.execution_backend` is `native-cli` (agents are not isolated there).

## Files changed

To be completed per commit.

## Tests executed

To be completed.

## Open blockers and operator decisions

### Blocker B1 — W1's nested isolation is incompatible with this host's AppArmor policy

The requirements — (1) subscription authentication, (2) agent-controlled commands and code cannot read credentials, (3) **no** network for the runner sandbox except approved model endpoints (W1's D-21, which also covers the CLI process), (4) autonomous runs, (5) the desktop's `bwrap-userns-restrict` policy unchanged — **cannot all be met on this desktop at the same time**. (3) needs an outer namespace around the CLI; (2) with a subscription credential inside that namespace needs a second, nested namespace for commands; (5) forbids the nesting. This is a property of the host policy, not of the authentication method: W1's API-key broker hits the same wall here.

Narrowly scoped alternatives (each needs operator approval; none is implemented on this branch):

| Option | What changes | D-20 (agent tools) | D-21 (agent tools) | D-21 (CLI process) | Subscription | Runs on this desktop |
| --- | --- | --- | --- | --- | --- | --- |
| **A. CLI-native tool sandboxes, CLI top-level** (recommended first step) | Opt-in hardening of the `native-cli` backend: Claude Code `sandbox` settings (strict, `failIfUnavailable`, no unsandboxed retry, empty strict domain allowlist, `filesystem.denyRead` for home secrets, `credentials` deny) + `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1` + `permissions.blockReadsOutsideWorkingDirectories`; Codex permission profile (`:minimal` read, workspace write, deny `CODEX_HOME` and home secrets, no network) instead of `workspace-write` | **Yes** — Codex verified offline here **[V]**; Claude documented **[S]**, same settings verified nested by W1 on another host | **Yes** (CLIs' own sandboxes; top-level bubblewrap is allowed by the profile) | **No** — the trusted CLI keeps host network; its in-process tools (Read/Edit, WebFetch denied) are bounded by permission rules, not an OS sandbox | Yes — existing logins, or a Worker-dedicated `setup-token` / `CODEX_HOME` | Yes |
| **B. W1 as designed on a host without `bwrap-userns-restrict`** (e.g. a dedicated Worker VM) plus subscription egress | The parked CONNECT-only egress (end-to-end TLS to fixed hosts) + `CLAUDE_CODE_OAUTH_TOKEN` via the channel + Worker `CODEX_HOME` with the permission profile | Yes (design) | Yes | **Yes** | Yes | No — needs another host |
| **C. A narrowly scoped AppArmor rule** allowing nested user namespaces only for the Worker's outer sandbox | Host security policy (root, reviewed profile) | as B | as B | as B | Yes | Yes, after the policy change |
| D. W1's API-key broker | Per-token API billing | Yes | Yes | Yes | **No** | **No** (same nesting) |

Every option still needs (a) the operator-approved real authenticated smoke test before anything is called verified, and (b) for options using Worker-dedicated credentials, the operator's one-time provisioning (`claude setup-token` into a private file; `codex login --device-auth` into a Worker `CODEX_HOME`), which never touches the operator's own logins.

### Other decisions

1. **Real authenticated smoke test** (subscription usage) — required before W1 can be called verified, for whichever option is chosen.
2. **Switching the running Worker's configuration** (backend or hardening flag) — a change of the active Worker, which this task does not make.
3. **Live risk on `dev` today (unchanged by this branch):** the running `native-cli` Worker gives agent-run code the operator's files, logins and network. Verified here: with `dev`'s Codex flags (`--sandbox workspace-write`), an agent command **can read `CODEX_HOME/auth.json`** (fake file, offline) **[V]**; Claude Code's allowlisted commands such as `{python} -m pytest` run agent-written code unsandboxed (by design, [worker-functional](worker-functional.md)). Option A addresses exactly this.

## Next action

Run the regression suite on the merge, fix failures, commit the merge.
