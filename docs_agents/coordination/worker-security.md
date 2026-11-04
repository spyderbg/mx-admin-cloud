# Worker Security — Credential and Network Isolation (W1: D-20, D-21)

> Owner role: **coordination**. Status: **W1 implemented and tested offline (2026-10-21); reconciled with `dev` (2026-11-03); BLOCKED for real execution on the desktop Worker host** (§0). Draft PR #20; progress log: [w1-progress.md](w1-progress.md).
> Related: [agent-ownership.md](agent-ownership.md) (write containment) · [operations.md](operations.md) (commands) · [feature-workflow.md](feature-workflow.md) §17 (runtime design) · [project-config.example.yaml](project-config.example.yaml) (`broker`, `runtime`) · [phase-status.md](phase-status.md) (decisions)
>
> Evidence tags: **[V]** verified by execution (the named test or command), **[S]** source-read, **[U]** unverified. Each protection below is also classed as **IMPLEMENTED**, **TESTED**, **NOT IMPLEMENTED** or **NOT VERIFIED**. Configuration alone never counts as a protection.

## 0. Status update (2026-11-03)

- **Scope.** W1 applies to `runtime.execution_backend: bwrap`. The default and currently running backend, `native-cli` (W2), is not a sandbox and is **unchanged** by W1: same command lines, settings, environment and probe (tests assert it). Commands below are now `python -m feature_coordinator …` (the package moved on `dev`).
- **Compatibility.** Existing Worker configurations keep loading: `providers.*.credential_files` and `runtime.agent_network: true` are accepted but have **no effect** (nothing is mounted; no sandbox shares the host network) and are reported by `runners` and `worker-check` (`config.ignored_settings()`). The version floor is enforced by the bwrap runner, not by the shared probe.
- **Subscriptions.** A subscription cannot be brokered (§4.4 stands), but it does not need to be: both CLIs document a subscription credential for unattended use that stays with the trusted CLI process (Claude Code `claude setup-token` → `CLAUDE_CODE_OAUTH_TOKEN`; Codex ChatGPT login in a dedicated `CODEX_HOME`), while their own sandboxes keep it away from agent-run commands (Claude Code `sandbox.credentials` deny + `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB`; Codex permission profiles). The CLI's own egress can be limited to a CONNECT-only tunnel to fixed provider hosts (end-to-end TLS; the proxy never sees the credential). Research and offline evidence: [w1-progress §Stage 3](w1-progress.md#stage-3--subscription-authentication-research-done).
- **Blocker.** W1 isolates agent commands by **nesting** the CLI's sandbox inside ours. The desktop Worker host has Ubuntu's `bwrap-userns-restrict` AppArmor profile ([worker-host-setup](worker-host-setup.md#nested-sandbox-limitation)), under which no bubblewrap can start inside a bubblewrap (verified 2026-11-03). Real `bwrap`-backend runs therefore fail closed on that host for **both** CLIs, whatever the credential source. Options and the decision needed: [w1-progress §Open blockers](w1-progress.md#open-blockers-and-operator-decisions).
- The verdicts in §1 and the evidence in §5/§8 were obtained on 2026-10-21 on a host **without** that profile; they stand for such hosts.

## 1. Verdict

| Decision | Status | Why |
| --- | --- | --- |
| **D-20** credential isolation | **PARTIALLY VERIFIED** | No reusable credential reaches a runner sandbox: no login file is mounted, no API key is forwarded, and the upstream credential stays in the host-side broker. Negative tests prove this for both runners **[V]**. Real provider authentication through the broker is **NOT VERIFIED**: the only documented brokered mode for both CLIs uses a per-token-billed API key, and choosing one is an operator decision (§4). Until then real runners are **unavailable** (fail closed) |
| **D-21** network isolation | **VERIFIED (offline)** | Every runner sandbox has its own empty network namespace. The CLI's only route is the broker endpoint inside that namespace, and the commands an agent runs are isolated again by the CLI's own sandbox. Verified on this host with the installed Claude Code 2.1.303 and Codex 0.163.0 against a fake provider **[V]** |

**This Worker is INELIGIBLE for real agent execution** (`python -m feature_coordinator worker-check --apply`) until the operator provisions a broker credential — and, on a host with `bwrap-userns-restrict`, until blocker B1 is decided (§0).

## 2. Vulnerabilities addressed

| Before W1 | After W1 |
| --- | --- |
| `~/.claude/.credentials.json` and `~/.codex/auth.json` were bound read-only into the runner sandbox. Agent-run code could read them; Phase 3 printed a real token prefix ([phase-status](phase-status.md#security-note-operator-attention)) | Nothing is mounted to authenticate. The `credential_files` key is gone and the loader rejects it |
| `PASSTHROUGH_ENV` forwarded `ANTHROPIC_API_KEY`/`OPENAI_API_KEY` and proxy variables into the sandbox | Removed. The sandbox environment is the fixed allowlist plus the broker URL and the per-run token |
| Claude Code's sandbox shared the host network (`--share-net`): the internet, the LAN, host localhost services and host **abstract** Unix sockets (the X11 display `@/tmp/.X11-unix/X0`) were reachable | No sandbox shares the host network; there is no code path that emits `--share-net`. `runtime.agent_network` is fixed to `false` |
| Agent Bash commands ran in the same domain as the runner | Claude Code's own Bash sandbox (strict) and Codex's own sandbox isolate every agent command again: own network and PID namespaces, no sockets, no token |

## 3. Trust boundaries

```text
 HOST (developer machine, Ubuntu 24.04)                    nothing below this line can see what is above it
 ┌──────────────────────────────────────────────────────────────────────────────────────────┐
 │ TRUSTED WORKER RUNTIME  feature.py launch (coordinator, Git coordinator, supervisor)       │
 │   ├─ TRUSTED AUTHENTICATION BROKER (broker.py, a thread of the worker; per run)            │
 │   │     holds the upstream credential · validates the per-run token · forwards approved    │
 │   │     operations to ONE approved HTTPS host · limits · redacted log                      │
 │   │            ▲ accepts on a listener that lives INSIDE the sandbox's namespace            │
 └───┼────────────┼─────────────────────────────────────────────────────────────────────────────┘
     │ bwrap      │ 127.0.0.1:47613 (sandbox namespace only)
 ┌───▼────────────┴─────────────────────────────────────────────────────────────────────────┐
 │ AI MODEL RUNNER  claude -p / codex exec   own net + PID + mount + user ns, private HOME,   │
 │   no credential; holds only the per-run token (env, delivered through the channel)        │
 │   Claude Code file tools (Read/Edit/Write) run here, limited by permission rules          │
 │ ┌──────────────────────────────────────────────────────────────────────────────────────┐ │
 │ │ AGENT-CONTROLLED TOOLS + AGENT-WRITTEN CODE  (Bash / exec_command and all children)    │ │
 │ │   nested sandbox of the CLI: own net + PID ns, sockets blocked (seccomp), token        │ │
 │ │   variables removed; cannot reach the broker endpoint, the network or the host         │ │
 │ └──────────────────────────────────────────────────────────────────────────────────────┘ │
 └──────────────────────────────────────────────────────────────────────────────────────────┘
```

| Boundary | May access | Must never access | Enforced by |
| --- | --- | --- | --- |
| Trusted Worker runtime | The checkout (Git coordinator), the state directory, the broker | — (trusted; runs as the operator's user) | — |
| Trusted authentication broker | The upstream credential (from `broker.credential_source`); the approved upstream host | Any other host; the sandbox's files | `ROUTES`, `Upstream.approved()` **[V]** |
| AI model runner | Its role roots (read-write), the checkout (read-only), a private HOME, the broker endpoint with its token | Login files, API keys, host network, host localhost, LAN, desktop sockets, other projects, the state directory | bubblewrap (sandbox.py), the channel (netns_channel.py) **[V]** |
| Agent-controlled tools / agent-written code | The same files as the runner, through the nested sandbox's rules | The broker endpoint, the runner's token, any network, any Unix socket | Claude Code `sandbox.*` settings (strict); Codex `workspace-write` with no network **[V]** |

## 4. Credential broker (D-20)

### 4.1 Flow

1. **Before any sandbox exists**, the worker builds a broker for the run: the upstream credential from `broker.credential_source`, the provider's approved route, a fresh **session** (32 random bytes; it expires at the task deadline plus grace plus 60 s). Without a credential the runner is `unavailable` and nothing starts.
2. The sandbox's first process (`sandbox_bootstrap.py`, system Python) binds `127.0.0.1:47613` **in the sandbox's namespace** and hands the listening socket to the worker over an inherited socketpair (no path, no file). The worker verifies the socket. Its network namespace must differ from the worker's (`SO_NETNS_COOKIE`), otherwise the run is refused.
3. The worker sends `{ANTHROPIC_BASE_URL, ANTHROPIC_AUTH_TOKEN}` (Claude Code) or `{FC_BROKER_TOKEN}` (Codex) over the socketpair. The bootstrap closes it and execs the CLI. **The token is never on a command line, in `sandbox.json`, in a file or in a log** **[V]**.
4. Each request is checked in this order: token (constant-time compare), revocation, expiry, request budget, then approved operation, size and framing. The broker drops the client's `Authorization`, adds the upstream credential and streams the answer back.
5. When the run ends, whatever the outcome (completed, failed, crash, cancelled, timeout, start-up failure), `stop()` **revokes** the session and closes the endpoint **[V]**.

### 4.2 Approved destinations and operations

| Runner | Upstream (fixed) | Operations | Client headers forwarded | Credential header added |
| --- | --- | --- | --- | --- |
| Claude Code | `https://api.anthropic.com` | `POST /v1/messages`, `POST /v1/messages/count_tokens`; query only `beta=true` | `content-type`, `accept`, `anthropic-version`, `anthropic-beta`, `user-agent`, `x-app`, `x-claude-code-session-id`, `x-stainless-*` | `x-api-key` |
| Codex | `https://api.openai.com` | `POST /v1/responses` | `content-type`, `accept`, `user-agent`, `originator`, `session-id`, `thread-id`, `x-client-request-id`, `openai-beta`, `x-codex-*` | `Authorization: Bearer` |

These operation lists are exactly what the installed CLIs sent when observed against the fake provider **[V]**. The client cannot change the host: `Host` is replaced, and absolute-form targets and `//` are refused. Certificates are verified with the system CA store.

### 4.3 Limits (`broker` section)

| Key | Default | On excess |
| --- | --- | --- |
| `max_request_bytes` | 32 MiB | 413; no `Content-Length` → 411; chunked request bodies → 411 |
| `max_response_bytes` | 64 MiB | the stream is cut off and the client sees a failed response |
| `upstream_idle_timeout_seconds` | 300 | 504 |
| `max_request_seconds` | 1800 | the stream is cut off |
| `max_requests_per_run` | 1000 | 429 |
| `max_concurrent_requests` | 8 | 503 after 30 s |
| `handshake_timeout_seconds` | 30 | the run fails: sandbox start-up failed |

`broker.log` in the run directory records one JSON line per request: time, method, path (`-` for a refused target), status, reason, sizes and duration. It never records headers or bodies **[V]**.

### 4.4 Credential sources — and why subscriptions cannot be brokered

| `credential_source` | Behavior | Status |
| --- | --- | --- |
| `none` (default) | Real runners unavailable | **IMPLEMENTED, TESTED** |
| `api-key-file` | One API key per CLI provider, read by the worker from `broker.api_key_files[<provider>]`. The path must be absolute and outside the checkout; the file must be regular (not a link), owned by the user, mode 0600 or stricter, and hold exactly one key. Errors never echo the value | **IMPLEMENTED, TESTED with a fake key file; NOT VERIFIED against a real provider** |
| Claude subscription OAuth / ChatGPT login | Not supported | **NOT IMPLEMENTED (by design)** |

The installed CLIs support brokering only in their documented gateway modes:

- **Claude Code** (`ANTHROPIC_BASE_URL` with `ANTHROPIC_AUTH_TOKEN` or `apiKeyHelper`). Anthropic's documentation (code.claude.com, "LLM gateways → Subscriptions and gateways") says: *"While a gateway credential variable or `apiKeyHelper` is active, requests carry that credential in place of a developer's claude.ai subscription login … That traffic is billed per token to whoever owns the credential the gateway forwards."* The only documented way to keep the subscription is for the saved claude.ai login to remain the client's credential. That puts it back in the sandbox, which D-20 forbids.
- **Codex** (a custom `model_providers` entry with `env_key`, passed through `-c`). It authenticates with an API key; ChatGPT sign-in through a gateway also keeps the ChatGPT token in the client.

Replaying an OAuth access token from the broker would rely on undocumented behavior (refresh flow, OAuth headers), so it is not done.

> **Potential alternative — not investigated, not implemented (noted 2026-10-21).** Official OpenAI documentation now describes ChatGPT plan usage through *Sign in with ChatGPT* and the *Codex app-server* (the [app-server page](https://learn.chatgpt.com/docs/app-server) has an authentication section on Sign in with ChatGPT; see also [Sign in with ChatGPT](https://developers.openai.com/siwc)). Whether this can keep reusable ChatGPT credentials outside the runner sandbox and still meet §4.1–§5 needs a separate security and compatibility investigation. Until then, the statements in this section stand for the mechanisms verified in W1. `apiKeyHelper` alone would only move a reusable key into the CLI process; here the CLI receives only the broker's per-run token.

**Operator decision D-20 (open):**
1. Provision Anthropic and OpenAI API keys for the broker (`api-key-file`; billed per token); or
2. keep real runners disabled (the current state); or
3. wait for a documented, brokerable subscription mechanism.

The Worker never changes the operator's authentication method or login files by itself.

## 5. Network isolation (D-21)

| Layer | Mechanism | Status |
| --- | --- | --- |
| Runner sandbox | `bwrap --unshare-all` without `--share-net`: only the namespace's own loopback. `/etc/resolv.conf` points to a resolver that does not exist there, so DNS fails | **IMPLEMENTED, TESTED [V]** |
| Model path | The broker endpoint on 127.0.0.1:47613 inside that namespace; no host port | **IMPLEMENTED, TESTED [V]** |
| Fail closed | The handshake refuses a listener in the worker's own namespace (`SO_NETNS_COOKIE`; Linux ≥ 5.14). A missing endpoint or bootstrap failure fails the run; no code path shares the network | **IMPLEMENTED, TESTED [V]** (`test_missing_network_isolation_blocks_execution` injects `--share-net` and the run never starts) |
| Claude Code commands | `sandbox.enabled`, `failIfUnavailable`, `allowUnsandboxedCommands: false`, `autoAllowBashIfSandboxed: false`, `network.strictAllowlist` with no domains, `credentials.envVars` deny for both broker variables | **IMPLEMENTED, TESTED [V]**: nested inside our bwrap, a command sees its own PID namespace, no token, no broker (ECONNREFUSED), no Unix socket (EPERM) and no DNS |
| Codex commands | `--sandbox workspace-write`, `network_access=false`, `shell_environment_policy.inherit="core"`, `web_search="disabled"`, `--strict-config` | **IMPLEMENTED, TESTED [V]**: `socket()` itself is refused (EPERM); no token; own PID namespace |
| Gates | Unchanged: own namespace with no endpoint | **TESTED [V]** (`test_offline_gates_stay_offline`) |

Observed on this host by `feature.py sandbox-check --apply` **[V]**:
- broker endpoint answered;
- host localhost ECONNREFUSED;
- host LAN address ENETUNREACH;
- TEST-NET-1 ENETUNREACH;
- DNS EAI_NONAME;
- host abstract socket ECONNREFUSED;
- host path socket absent;
- X11 `@/tmp/.X11-unix/X0` ECONNREFUSED;
- no credential or session path, no display, bus or agent variable;
- 2 visible PIDs;
- nothing next to the repository.

A Unix socket is never placed in the sandbox: the endpoint is a TCP listener owned by the worker inside the sandbox's namespace. Nested command sandboxes block Unix sockets and get namespaces of their own.

## 6. Sandbox behavior (changes in W1)

- **Removed:** credential binds, API-key and proxy pass-through, `--share-net`, the systemd-resolved bind.
- **Added:**
  - `/run/fc-broker/bootstrap.py`, bound read-only;
  - the inherited channel descriptor (only `pass_fds` survive; every other descriptor is closed);
  - `broker.log` in the run directory;
  - the `broker` and `network` entries in `sandbox.json` (no token).
- **Unchanged:**
  - the private HOME and `/tmp`;
  - the read-only shadow root with the role roots read-write;
  - hidden secrets, read-only `.git`;
  - `--die-with-parent`, `--new-session`, `--clearenv`;
  - supervision, heartbeats, stop, timeout and recovery;
  - the commit gate.

## 7. Supported versions and host dependencies

| Component | Verified | Requirement |
| --- | --- | --- |
| Claude Code | 2.1.303 **[V]** | ≥ 2.1.303 (`MIN_VERSIONS`); every flag in `REQUIRED_FLAGS`. Newer versions are accepted; re-run `test_fc_runner_compat.py` after an update |
| Codex | 0.163.0 **[V]** | ≥ 0.163.0; `--strict-config` also refuses any override key a newer version renames |
| Python | 3.12.3 (`.venv`, `/usr/bin/python3`) **[V]** | `/usr/bin/python3` inside the sandbox runs the bootstrap |
| bubblewrap | 0.9.0-1ubuntu0.3 **[V]** | unprivileged user namespaces (Ubuntu 24.04: `kernel.apparmor_restrict_unprivileged_userns=1` with bwrap permitted) |
| socat | 1.8.0.0 **[V]** | Claude Code's nested sandbox relay |
| Kernel | 7.0.0-38 **[V]** | ≥ 5.14 for `SO_NETNS_COOKIE` |
| OS | Ubuntu 24.04.5 LTS **[V]** | Other Linux distributions: **NOT VERIFIED**. Run `feature.py worker-check --apply` there |

A missing dependency makes the runner `unavailable` (`SANDBOX_DEPENDENCIES`); there is no unsandboxed fallback.

## 8. Security self-tests

| Command | What it proves |
| --- | --- |
| `feature.py sandbox-check --apply` | The containment matrix plus the isolation self-test (`isolation_check.py`) in a disposable repository. Expect `PASSED` twice |
| `feature.py worker-check --apply` | bwrap, CLI versions and flags, sandbox dependencies and a broker credential per CLI provider, then both self-tests: `Worker: ELIGIBLE` (exit 0) or `INELIGIBLE` with blockers (exit 1). Never reads or prints a credential value; makes no model call |
| `feature.py runners` | Per role: CLI probe and command line; then what blocks real runs |

Regression tests (all fake credentials, nothing leaves 127.0.0.1):

| File | Covers |
| --- | --- |
| `test_fc_broker.py` (39) | forwarding with the broker key only; open connections capped; invalid, expired and revoked authorization; unapproved operations, methods and targets; the fixed upstream; size, framing, budget, response-size, timeout and unreachable limits; a failing credential source; log redaction; credential sources and file checks; configuration validation |
| `test_fc_isolation.py` (18) | for both runners: no credential in mounts, environment, `/proc` (environ and cmdline), files, settings or logs; agent-written Python; broker authorization from inside the sandbox; no internet, DNS, LAN, host localhost, desktop or X11 sockets; the isolation self-test; a `--share-net` sandbox blocked; offline gates; no-credential, missing-dependency, start-up failure, broker crash, runner crash, cancellation and timeout; a control test showing the probes catch a re-introduced mount or key; `worker-check` |
| `test_fc_runner_compat.py` (3) | the **installed** Claude Code and Codex through the real command lines, the broker and a fake provider: model path, nested command isolation, allowlist denial, no web search, no secret in any run file |
| `test_fc_launchers.py`, `test_fc_sandbox.py` (updated) | no credential binds and no pass-through; strict Claude sandbox settings; the Codex broker provider; minimum versions; no `--share-net` for any profile |
| `test_real_login_is_not_visible_inside_the_sandbox` (opt-in, `FC_HOST_RUNNER_CHECKS=1`) | the installed CLIs inside the sandbox, with the operator's real HOME path, report `"loggedIn": false` / `Not logged in` (2026-10-21) **[V]**; no login file is read |

Final regression on 2026-10-21 **[V]**:
- `scripts/tests`: **517 passed, 18 skipped** (all opt-in: live GitHub, live Projects, host login checks, paid smoke), exit 0; baseline before W1 was 455 passed, 18 skipped;
- Pylint 10.00/10 for the package and for the tests;
- `check_docs_agents.py` 6/6;
- `feature.py config`: identical;
- `sandbox-check --apply`: both self-tests PASSED;
- `worker-check --apply`: INELIGIBLE (exit 1, no broker credential — expected).

## 9. Failure handling

| Situation | Result | Test |
| --- | --- | --- |
| No broker credential (`none`), unreadable or badly permissioned key file | `unavailable` before any sandbox | `test_no_broker_credential_means_unavailable_before_any_sandbox`, `test_api_key_file_is_refused_unless_private_and_outside_the_checkout` |
| Broker crashes or stops mid-run | Further requests are refused; the CLI fails; no other route exists | `test_broker_crash_mid_run_fails_closed` |
| The credential source fails mid-run | 503; nothing forwarded | `test_a_failing_credential_source_fails_closed` |
| Runner crashes | `failed`; session revoked | `test_runner_crash_is_reported_and_revokes_the_session` |
| Sandbox start-up fails (bwrap error, bootstrap missing, no hand-over) | `failed: sandbox start-up failed`; the sandbox is killed | `test_sandbox_start_up_failure_fails_the_run` |
| Sandbox not network-isolated | `failed`; the CLI never starts | `test_missing_network_isolation_blocks_execution` |
| CLI older than verified, or missing a flag | `unavailable` | `test_versions_below_the_verified_minimum_are_unavailable`, `test_probe_fails_closed_when_a_flag_is_missing` |
| Unsafe configuration (`agent_network: true`, `credential_files`, unknown or inline broker keys, unapproved upstream) | Configuration or broker refused | `test_runtime_ceilings`, `test_yaml_broker_section_rejects_inline_secrets`, `test_only_the_approved_upstream_is_accepted` |
| Missing `socat`/`bwrap`/`/usr/bin/python3` | `unavailable` | `test_missing_sandbox_dependency_means_unavailable` |
| Operator stop / worker timeout | `cancelled` / `timeout`; session revoked | `test_cancellation_and_timeout_revoke_the_session`, worker lifecycle tests |

## 10. Known limitations

1. **Real provider authentication is NOT VERIFIED.** The fake-provider tests prove the path and the isolation, not that Anthropic or OpenAI accept a brokered request. That is the separate, operator-approved smoke stage (`FC_LIVE_RUNNER_SMOKE=1` with `FC_LIVE_PROJECT_CONFIG`).
2. **The runner domain holds the per-run token.** Code running in the runner's own process tree outside the nested sandbox could use the broker during the run, within its limits. Today that is only the CLI itself: `--restricted` ignores repository hooks, and `--strict-mcp-config` loads no MCP servers. The token is useless after the run.
3. **Claude Code's file tools** (Read/Edit/Write) run in the runner process, not in the nested sandbox. They are bounded by permission rules and by our outer sandbox, which contains no secret.
4. **No TLS pinning.** The broker trusts the system CA store for the provider endpoint.
5. **No token or cost metering in the broker.** Usage limits count requests and bytes; Claude Code's `--max-budget-usd` remains client-side.
6. **Prompt content is not inspected.** Repository code is sent to the provider by design, as before W1.
7. **The isolation self-test** checks a fixed list of paths, variables and targets; it is a regression check, not an exhaustive audit.
8. **No version ceiling.** Claude Code updates itself on the host. A newer version is accepted; re-run `test_fc_runner_compat.py` after an update.
9. **Pre-existing, outside W1:** `/usr/bin/python` does not exist on this host, so the Backend and CLI Bash allowlists (`python -m pytest`, `python -m pylint`) cannot run inside the sandbox. W2 must fix the role commands or the sandbox's Python.
10. **The worker is trusted.** A compromised worker process has the upstream credential. W1 isolates agents from the worker, not the worker from the operator's account.

## 11. Worker / developer isolation requirements for W3/W4

W1 keeps the current Git workflow. When dedicated Worker workspaces are introduced, they must:

1. **Use their own checkout**, never the developer's active checkout or its uncommitted files. The sandbox already binds only the target repository; a Worker must target a Worker-owned clone.
2. Keep the coordinator state, logs and run directories under the Worker's own state directory. They are never visible to agents; the hidden state directory is enforced today.
3. Run the broker inside the Worker runtime only, with credentials provisioned for the Worker. **No developer login, SSH key, GitHub token, browser profile or keyring** may be read by the Worker or mounted into a sandbox; `isolation_check.SENSITIVE_HOME_PATHS` must stay absent.
4. Grant no access to the desktop session (display, bus, agents). Sandboxes inherit no such variable and see no runtime directory.
5. Grant no access to unrelated projects, production content or databases. Only the target repository is bound, and `content/**`, `data/**` and `backend/data/**` stay protected.
6. Keep checkpoints compatible: run directories carry no secret (tested), so they may be archived. The broker session is per run and never persisted.
7. Re-run `worker-check --apply` after every Worker bootstrap or maintenance, and refuse work while it reports `INELIGIBLE`.

## 12. Status summary

| Item | Status |
| --- | --- |
| Credential files not mounted; API keys not forwarded | IMPLEMENTED, TESTED |
| Host-side broker: per-run token, expiry, revocation, approved destinations and operations, limits, redacted log | IMPLEMENTED, TESTED |
| Fail closed without a broker credential; no fallback to mounts or keys | IMPLEMENTED, TESTED |
| `api-key-file` credential source | IMPLEMENTED, TESTED (fake key); NOT VERIFIED with a provider |
| Brokering a Claude or ChatGPT subscription login | NOT IMPLEMENTED (no documented safe mechanism) |
| Runner sandbox network namespace; the endpoint inside it; isolation verified per run | IMPLEMENTED, TESTED |
| Nested command isolation (Claude Code strict sandbox, Codex sandbox) | IMPLEMENTED, TESTED with the installed CLIs and a fake provider |
| Isolation self-test and `worker-check` | IMPLEMENTED, TESTED |
| Real authenticated smoke runs | NOT VERIFIED (separate, operator-approved stage) |
| Other distributions or kernels than Ubuntu 24.04.5 / Linux 7.0 | NOT VERIFIED |

## 13. Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `real runs: UNAVAILABLE — no broker credential is configured` | Expected default (D-20). Decide the credential source; see §4.4 |
| `the broker API key file … must be owned by this user with mode 0600` | `chmod 600 <file>`; keep it outside the checkout; no symlink |
| `missing sandbox dependencies ['socat']` | `sudo apt-get install socat` (Claude Code's nested sandbox relay) |
| `version … is older than the verified minimum` | Update the CLI, or verify a new minimum with `test_fc_runner_compat.py` before lowering `MIN_VERSIONS` |
| `sandbox start-up failed: … exited before handing over its broker endpoint` | See `stderr.log` in the run directory (bwrap or bootstrap message). Check `feature.py sandbox-check --apply` |
| `… shares the worker's network namespace` | The sandbox was not network-isolated (a modified bwrap call?). The run is refused by design |
| Claude Code exits at start-up mentioning the sandbox | `failIfUnavailable`: its nested bubblewrap or socat cannot start. Check AppArmor's user-namespace policy for `bwrap` |
| Codex: `unknown configuration field … in -c/--config override` | A Codex update renamed a key; `--strict-config` refused it. Update the launcher after verifying the new key |
| Broker log shows 403 `operation-not-allowed` from a new CLI version | The CLI calls an endpoint not in `ROUTES`. Verify it with the fake provider, then add it deliberately |
