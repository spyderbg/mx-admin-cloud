# Functional Multi-Agent Worker (W2)

> Owner role: **coordination**. Status: **IMPLEMENTED AND VERIFIED WITH REAL AGENTS (2026-10-22).** The bounded real subscription smoke test passed on the Ubuntu laptop: Backend on Claude Code `opus`, CLI on Claude Code `sonnet`, Frontend on Codex. See §10. Implementation commit `a0c88ac`; finalization (docs and commit subjects, §12) in the W2 draft PR to `dev`.
> Related: [feature-workflow.md](feature-workflow.md) (coordinator design) · [operations.md](operations.md) (commands) · [agent-ownership.md](agent-ownership.md) (write scopes, the `bwrap` backend) · [project-config.example.yaml](project-config.example.yaml) · [phase-status.md](phase-status.md)
>
> Status classes used below: **IMPLEMENTED** · **TESTED OFFLINE** (fake agents, disposable repositories) · **TESTED WITH SUBSCRIPTIONS** (a real model request) · **NOT IMPLEMENTED**. Only the bounded smoke test (§10) is tested with subscriptions; everything else is tested offline.

## 1. What W2 adds

One **Worker** (a local process) drives one feature through several specialized AI agents, using the operator's existing **Claude Code** and **ChatGPT (Codex)** subscriptions. No API key, gateway or pay-per-token billing is involved. W2 has **no AWS dependency**: the AWS coordinator, multiple Workers and durable checkpoints are not part of it.

```text
Worker (feature.py work FEATURE --apply)           ← one process, one feature, one checkout
  │  schedules READY tasks (dependencies, one writer per role, max_parallel)
  ├── feature.py launch backend-task  →  Claude Code, model opus     (Backend role)
  ├── feature.py launch cli-task      →  Claude Code, model sonnet   (CLI role)       ┐ concurrently, once
  ├── feature.py launch frontend-task →  Codex, CLI default model    (Frontend role)  ┘ Backend is verified
  └── automation tasks                →  remote Qwen: role DISABLED (never run)
        each launch: claim → agent (execution backend) → acceptance → serialized commit → gates → release
```

The coordinator itself is unchanged: plan, activate, `run_task`, the Git coordinator, gates and integration are the Phase 2–4 implementation. W2 adds the Worker loop, an execution-backend choice, role → agent → model routing, workspace verification and Python interpreter discovery.

## 2. Execution backends

`runtime.execution_backend` selects how real agents run. There is **never a fallback** from one backend to the other: an unusable backend makes the runner *unavailable* and the task returns to ready.

| Backend | What it is | Isolation | Status |
| --- | --- | --- | --- |
| `native-cli` (default) | The installed `claude` / `codex`, run directly in the workspace with the operator's subscription logins | **None at the OS level. NOT a security sandbox** (§3) | IMPLEMENTED, TESTED OFFLINE, TESTED WITH SUBSCRIPTIONS (§10) |
| `bwrap` | The Phase 3 launch: bubblewrap containment per role, the runner's login files bound read-only, network shared for the model API | OS write containment ([agent-ownership](agent-ownership.md)) | IMPLEMENTED, TESTED (Phase 3), unchanged |

Gates always run in the offline, read-only gate sandbox, whatever the backend (bubblewrap is still needed for gates). W1's credential and network isolation ([PR #20](https://github.com/spyderbg/mx-admin-cloud/pull/20), postponed) is not part of W2 and is not affected by it.

## 3. The native-cli backend

`native_backend.NativeCliRunner`. For each task:

1. **Preconditions** (else *unavailable*, nothing runs):
   - the CLI's `--version` and `--help` list every required flag;
   - the CLI is logged in with the subscription (§4);
   - the role's Python interpreter resolves (§6);
   - no merge, rebase, cherry-pick, revert or bisect is in progress.
2. **Command lines.** These are the same launchers as the `bwrap` backend (`launchers.py`, `native=True`), with the role's model:
   - Claude Code: `claude -p --output-format stream-json --verbose --restricted --tools Read,Glob,Grep,Edit,Write[,Bash] --settings <run dir>/claude-settings.json --strict-mcp-config --permission-mode acceptEdits --permission-prompts none --no-session-persistence --disable-slash-commands --max-budget-usd N [--model M]`. The settings allow the file tools and only the role's Bash prefixes; everything else is denied.
   - Codex: `codex exec --json --strict-config --sandbox workspace-write --ephemeral --ignore-user-config -C <workspace> -o <run dir>/last-message.txt -c sandbox_workspace_write.network_access=false -c forced_login_method="chatgpt" [-m M] -`. Codex keeps its own command sandbox.
   - The run's files (prompt, settings, last message, `native.json`, output) live in `<state>/runs/<task>.<stamp>/`. Nothing is written to the operator's HOME.
3. **Supervision.** This is the existing supervisor: own process group, heartbeats, operator stop, task timeout, SIGTERM → grace → SIGKILL. There is no PID namespace, so each run carries a random `FC_RUN_TAG` in its environment, and processes still carrying it after the run (for example a daemon that called `setsid`) are killed. This is best effort: a process that clears its environment escapes.
4. **Acceptance**, checked workspace-wide after the run. The run is **rejected** if it changed any of these:
   - a path its role may not write: tracked, untracked, or a protected ignored file such as `.env`;
   - the Git state: a branch switch, other refs, the stash, staged content, a rewritten history, or a commit without the coordinator's `Feature-Task:` trailer.

   Rejected changes are **left in place** for the operator and never committed or discarded. Accepted runs are committed by the Git coordinator from the role's own paths only (unchanged). The commit message is generated by the coordinator, never by the agent (§12).

   Concurrent tasks share the checkout. Two rules keep one run from being blamed for another's work:
   - A change inside the paths of a role that held a claim during this run (`RunContext.concurrent_roles`) is left to that role's run.
   - The before/after snapshots are taken while holding the Git coordinator's `git.lock`. A commit moves the branch first and refreshes the shared index afterwards, so a snapshot in between would look like staged content; the offline workflow test found exactly this race, and taking the lock fixed it.
5. **Post-run authentication check (Claude Code).** The `init` event must show `apiKeySource: none` and a model of the requested family (`opus` → `claude-opus-…`). Otherwise the run **fails**, and a substituted model is never accepted.

**What native execution does NOT do.** The agent and every command it runs have the operator's full file-system and network access. Role write scopes are verified *after* the run, and Claude Code's permission rules apply while it runs, but neither prevents reading or writing outside the workspace. Nor do they stop an agent from reaching the network or other services on the machine. Use `bwrap` (or, later, W1-style isolation) when that matters.

## 4. Subscription authentication

| Rule | How |
| --- | --- |
| Only the subscription logins | The CLI's environment is an **allowlist** (`NATIVE_ENV_KEEP`: HOME, PATH, locale, XDG dirs, `CLAUDE_CONFIG_DIR`, `CODEX_HOME`, proxies, CA bundles). Never passed: `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`, `CLAUDE_CODE_OAUTH_TOKEN`, `CLAUDE_CODE_USE_BEDROCK/VERTEX`, `OPENAI_API_KEY`, `CODEX_API_KEY`, GitHub tokens, `AGENT_QWEN_*`, and the variables of an enclosing Claude Code session (`CLAUDECODE`, `CLAUDE_CODE_*`) |
| Verified before every run | The CLI's own status command, with that environment. `claude auth status --json` must show `loggedIn: true`, `authMethod: "claude.ai"`, `apiProvider: "firstParty"`. `codex login status` must say "Logged in using ChatGPT" |
| API keys refused | `authMethod` api_key/console, or "Logged in using an API key", makes the runner unavailable |
| Undetermined → stop | Any other answer means the billing method cannot be determined; the runner is unavailable until the operator confirms or fixes it |
| No settings override | Claude Code `--restricted` ignores user, project and local settings, including an `apiKeyHelper`. Codex `--ignore-user-config` + `forced_login_method="chatgpt"` (and `--strict-config`, so the key cannot be silently ignored) |
| No credential handling | The coordinator never reads, prints, copies or changes login files or tokens. Only the method is recorded (`claude.ai subscription (pro)`, `ChatGPT subscription`); the status output, which contains the account e-mail, is never stored |
| Never logs in | A missing login is reported (`run claude` / `codex login` by the operator); the Worker never starts a login flow |

Detected on 2026-10-22 (status commands; only the method is recorded):

| Host | Claude Code | Codex |
| --- | --- | --- |
| Development workstation | 2.1.303 → **claude.ai subscription (pro)** | 0.163.0 → **ChatGPT subscription** |
| Ubuntu laptop | 2.1.296 → **claude.ai subscription (pro)** | 0.162.1 → **ChatGPT subscription** |

On the laptop, both checks ran through `check_subscription` with exactly the allowlisted run environment. No API-key or auth-token variable was set in the operator's shell.

What this verifies is the **authentication method** each CLI reports, plus, for Claude Code, `apiKeySource: none` in every run's `init` event. It does **not** audit billing: no account usage or billing records were inspected. No API key, gateway or other paid API access was configured for W2.

## 5. Role → agent → model

| Role | Agent (`provider`) | Model | Enabled |
| --- | --- | --- | --- |
| backend | claude (Claude Code) | `opus` | yes |
| cli | claude (Claude Code) | `sonnet` | yes |
| frontend | codex | `default` (Codex's own default) | yes |
| automation | remote_qwen | (profile) | **no** (`enabled: false`) |
| coordination | claude | `sonnet` | yes |

- A role names its agent (`roles.<r>.provider`) and optionally its model (`roles.<r>.model`, overriding the provider's `model`; `default` explicitly selects the CLI's default; omitted inherits). **Changing either is configuration only.**
- Write scopes belong to the role and do not change with the agent. Any role may use either CLI, for example `cli: {provider: codex, model: gpt-5.5-codex}`.
- `providers.check_model()` rejects unsupported combinations at load time, and nothing is substituted:
  - Claude Code accepts the aliases `fable`, `opus`, `sonnet`, `haiku`, `opusplan`, or a full `claude-…` id;
  - Codex accepts `gpt-…`, `o<N>…` or `codex-…`;
  - a role-level model for the OpenAI-compatible (Qwen) provider is refused.
- A disabled role's tasks are never launched. The runner refuses, and the Worker reports them.

```yaml
roles:
  backend:  {provider: claude, model: opus,    owned_paths: ["backend/**", "docs_agents/backend/**"], excluded_paths: ["backend/data/**"], max_writers: 1}
  cli:      {provider: claude, model: sonnet,  owned_paths: ["cli/**", "docs_agents/cli/**"], max_writers: 1}
  frontend: {provider: codex,  model: default, owned_paths: ["frontend/**", "docs_agents/frontend/**"], max_writers: 1}
  automation: {provider: remote_qwen, enabled: false, ...}
runtime:
  execution_backend: native-cli     # or bwrap
  python_interpreter: auto          # or an absolute path
```

## 6. Python interpreter compatibility

Ubuntu 24.04 has `python3` but no `python`, so the old allowlist entries `python -m pytest` / `python -m pylint` could not run. Role commands now use the placeholder `{python}` (`python_env.py`):

- `runtime.python_interpreter: auto` resolves, in order:
  1. `<workspace>/.venv/bin/python`;
  2. `<workspace>/venv/bin/python`;
  3. `python3` on the search path (the operator's PATH for `native-cli`, the sandbox PATH for `bwrap`);
  4. `python` on the search path.

  An absolute path is used as given, if it is executable.
- `{python} -m pytest:*` becomes the Claude Code rules `Bash(<abs interpreter> -m pytest:*)` and, for an interpreter inside the workspace, `Bash(.venv/bin/python -m pytest:*)`. The allowlist stays exactly as narrow as before.
- The configuration loader rejects bare `python …` or `python3 …` commands, and any other placeholder.
- The agent's prompt names the interpreter. For `native-cli`, a virtual environment's `bin/` is put first on PATH, so `kiddo-cli` and `pytest` resolve too.
- No global `python` symlink is needed, and nothing is installed.

## 7. Local workspace and lifecycle

The Worker operates on the checkout given by `--repo`. It never switches branches, resets, stashes or cleans.

| Check | Where |
| --- | --- |
| Repository identity: root commit(s) and `origin` recorded at `activate` | Every `run_task` and Worker start (`workspace.py`) |
| The path is the checkout's top level; the feature branch is checked out; HEAD contains the recorded base revision | Same |
| No unfinished merge, rebase, cherry-pick, revert or bisect | Same, and before every native run |
| Clean starting state | `activate` and `integrate` (unchanged) |
| No conflicting execution | One Worker per feature (`locks/worker-<feature>.lock`); one writer per role (existing claims and role locks); branch operations need quiescence (unchanged) |

Lifecycle:

```bash
feature.py plan spec.yaml --apply          # tasks with dependencies (DAG)
feature.py activate <feature> --apply      # creates feature/<slug> (requires a clean checkout; operator-authorized)
feature.py work <feature>                  # dry run: order, agent, model, backend, workspace check
feature.py work <feature> --apply          # the Worker: dependencies first, independent roles concurrently
feature.py integrate <feature> --apply     # or `work --integrate`: integration gates → PR candidate (not opened)
```

The Worker launches each READY task as its own `feature.py launch` process, which is the existing single-task path. It starts at most one task per role and at most `--max-parallel` at once (default: one per enabled role in the feature). After each finish it re-reads the state, and dependents of a verified task become READY.

Each task is launched at most `--max-attempts` times per run (default **1**). A task deferred because its agent was unavailable, it was stopped, or its commit was deferred is reported, not retried indefinitely. SIGTERM or Ctrl-C to the Worker stops every running task gracefully. A task that failed keeps its dependents blocked.

## 8. Supported versions

Two environments are verified. **No other versions are claimed to be compatible.** A different version must pass the flag probe (`feature.py runners`), the status checks and, before real use, the smoke test.

| | Development workstation | Ubuntu laptop |
| --- | --- | --- |
| Verification | **Offline only** (flag probe, status commands, offline tests; no model request) | Offline tests **and the real subscription smoke test (PASSED, §10)** |
| Claude Code | 2.1.303 | 2.1.296 |
| Codex | 0.163.0 | 0.162.1 |
| Python | 3.12.3 (`.venv`, `/usr/bin/python3`) | 3.12.3 (`.venv`, `/usr/bin/python3`) |
| bubblewrap | 0.9.0 (gates and the `bwrap` backend only) | 0.9.0 |
| OS | Ubuntu 24.04.5, Linux 7.0 | Ubuntu 24.04.5, Linux 6.8 |
| Checkout filesystem | fuseblk | ext4 |

Neither host has a `/usr/bin/python`.

On the laptop, every required flag was found in `--help`, including the values `--permission-prompts none` and `--permission-mode acceptEdits`. Codex 0.162.1 was checked with the backend's exact command line against an empty `CODEX_HOME`, which holds no credentials, so no model request was possible:

- `sandbox_workspace_write.network_access` and `forced_login_method` are accepted under `--strict-config`;
- an unknown `-c` key is rejected ("unknown configuration field"), so the configuration still fails closed.

## 9. Tests

Offline, with fake agents and disposable repositories (no model, no GitHub, no AWS, no W1 parts):

| File | Covers |
| --- | --- |
| `test_fc_native_backend.py` (42) | default routing; routing changes by configuration only (Backend Opus or Sonnet, CLI on Claude Code or Codex, Frontend on Codex or Claude); rejected combinations; native command lines; Python discovery; the environment allowlist; subscription status classification; the CLIs' status commands; successful native run; CLI role on Codex; missing executable; missing, API-key and undetermined authentication; agent failure; timeout and cancellation (including a detached daemon); changes outside the role (rejected, left in place); protected ignored files; an agent's own commit; model substitution; API-key use; unfinished Git operation; backend selection without fallback; disabled role |
| `test_fc_worker_loop.py` (10) | dependencies first, then independent tasks concurrently (barrier); one writer per role and `max_parallel`; a failed task blocks its dependents; deferred tasks are not retried; disabled roles never run; one Worker per feature; workspace identity; stop forwarding; the **complete local workflow** with real processes (`plan`, `activate`, `work --integrate`, the native backend, Claude-Code and Codex stand-ins, a sandboxed gate, three serialized single-role commits, review); Worker cancellation by SIGTERM; `subscription-smoke --apply` end to end with the fake agent (including the short commit subjects) and its refusal without a subscription |
| `test_fc_launchers.py` (updated) | role commands with `{python}`; the `bwrap` backend unchanged otherwise |
| `test_fc_gitops.py` (+10), `test_fc_workflow.py` (+1) | Commit subjects (§12): a short title is used, and prompt, long, multi-line, control-character and empty titles fall back to `<role>: task <id>`; a trailer-like title cannot change the trailers; through `run_task`, the exact messages of three commits (including a second commit of a reopened task), with trailers and author unchanged |

```bash
.venv/bin/python -m pytest -p no:cacheprovider scripts/tests                     # everything (offline)
.venv/bin/python -m pytest -p no:cacheprovider scripts/tests/feature_coordinator/test_fc_native_backend.py \
    scripts/tests/feature_coordinator/test_fc_worker_loop.py                    # W2 only
```

## 10. Real subscription smoke test (operator approval required)

```bash
feature.py subscription-smoke              # dry run: commands, agents, models, detected authentication — no model request
feature.py subscription-smoke --apply      # REAL requests: 3 small sessions in a new disposable repository
```

- **Tasks:**
  - Backend (Claude Code `opus`) creates `backend/smoke/backend.txt`.
  - Then, concurrently, CLI (Claude Code `sonnet`) creates `cli/smoke/cli.txt` and Frontend (Codex, default model) creates `frontend/smoke/frontend.txt`.
- **Bounds:**
  - one launch per task, 300 s per task, Claude Code `--max-budget-usd 0.5` per run;
  - local tracker; no GitHub, no gates, no PR.
- **Checks:**
  - subscription authentication before each run, and `apiKeySource`/model after each Claude Code run;
  - role scopes, and the files on the feature branch;
  - the integration candidate is rendered, never opened.

Status: **TESTED WITH SUBSCRIPTIONS — PASSED** (attempt 2, Ubuntu laptop, 2026-10-22). Any further run needs a new operator approval.

### Attempt 2 — PASSED (2026-10-22T03:59Z, Ubuntu laptop)

Run at `a0c88ac`, with Claude Code 2.1.296 and Codex 0.162.1, after the offline suite passed on that host (506 passed, 19 skipped). The dry run matched the approved design. One `--apply` run exited 0 with "subscription smoke test: PASSED". Evidence is kept in `~/.local/state/mx-admin-agents/subscription-smoke/20261022T035925/` on the laptop (`state/runs/*/native.json`, `stdout.jsonl`, `state/tasks/*.json`, the disposable `repo/`).

| Task | Agent | Model requested | Model observed | Authentication | Agent action | Commit |
| --- | --- | --- | --- | --- | --- | --- |
| Backend | Claude Code | `opus` | **`claude-opus-5-5`** (`init` event and every assistant message) | claude.ai subscription (pro); `apiKeySource: none` | one `Write` of `backend/smoke/backend.txt`, 2 turns, 4.4 s | `b07e19d` |
| CLI | Claude Code | `sonnet` | **`claude-sonnet-5-5`** (same) | claude.ai subscription (pro); `apiKeySource: none` | one `Write` of `cli/smoke/cli.txt`, 2 turns, 4.0 s | `4fcdf4c` |
| Frontend | Codex | CLI default (no `-m`) | **not reported**: Codex's `--json` events carry no model name | ChatGPT subscription | one `file_change` (add) of `frontend/smoke/frontend.txt`, ~9 s | `dcdc604` |

**Opus is available to Claude Code on this Pro subscription.** This was observed in the Backend run, not assumed.

**The Codex model is NOT independently verified.** The run used whatever Codex 0.162.1 selects by default; only the requested setting (the CLI default) is known (limitation E, §11).

- **Results.**
  - Each file is exactly `<text>\n` (`backend ok`, `cli ok`, `frontend ok`).
  - stderr was empty for all three runs. No permission denials, no retries, no leftover processes.
- **Dependencies.**
  - Backend was claimed at 03:59:35.168Z and verified at 03:59:40.079Z.
  - CLI and Frontend were unblocked at 03:59:40.098Z, so only after Backend was verified.
  - **Note:** the Worker log prints `start …cli` before `done …backend`. That is not an ordering problem: `done` is printed when the Backend launch *process* exits, after its bookkeeping, and the task itself was already verified.
- **Parallelism.**
  - CLI was in progress 03:59:40.396–44.899Z and Frontend 03:59:40.998–49.474Z. They overlapped for about 3.9 s; `max_concurrency` was 2.
  - Frontend started 0.6 s after CLI. The still-exiting Backend launch process held one of the smoke test's two slots.
- **Git.**
  - Three commits on `feature/subscription-smoke`, linear (seed ← backend ← cli ← frontend), made one at a time by the Git coordinator.
  - Each commit adds only its role's file and carries `Feature-Task` / `Feature-Role` trailers.
  - The disposable repository was left clean: no stash, no remote, only `dev` and the feature branch.
- **Isolation of the test.**
  - The real checkout was unchanged (same HEAD, clean).
  - The tracker and board were the local projections. GitHub Issues and PRs were identical before and after.
  - No AWS. No API key or other paid API access configured.
  - Claude Code's own `total_cost_usd` estimates ($0.025 Opus, $0.012 Sonnet) are notional. Codex reported 39,547 input tokens (31,872 cached) and 76 output tokens. **Billing records were not inspected.**
- **Finding.** The commit subjects were the complete task prompts, because the smoke tasks' titles are their instructions. This is fixed in the finalization (§12).

### Attempt 1 — FAILED in the harness (2026-10-22T00:31:37Z, development workstation)

- Exit 1 after ~1 s, before any agent started: `[Errno 2] No such file or directory: …/state/project-board.tmp`. No model request, no subscription usage, no GitHub write.
- Cause: `run_smoke` did not create its state directory, and the offline tests covered only the dry run.
- Fix:
  - `run_smoke` now creates the state directory with `ensure_state_dir`.
  - `test_fc_worker_loop.py` runs `subscription-smoke --apply` end to end with the fake agent, which carries out the smoke prompt itself (`follow_prompt`). It checks the three role commits, the models and the rendered candidate, and that `--apply` is refused without a subscription.
  - Without the fix, that test fails with the same error.

## 11. Known limitations

These remain open after W2. None of them is addressed by an architectural change in W2.

- **A. No OS-level sandbox isolation in `native-cli`.** See §3. The agent and every command it runs have the operator's full file-system and network access. Write scopes are checked *after* the run, and Claude Code's permission rules apply while it runs. Use `bwrap` (or, later, W1-style isolation) when containment matters.
- **B. Changes outside the workspace cannot be fully detected.** Acceptance compares the checkout before and after a run. A write elsewhere on the machine (for example in HOME) is not seen. Changes inside ignored directories (`node_modules`, `.venv`, `__pycache__`) are reported, not judged.
- **C. Concurrent agents share one checkout.** A change inside the paths of another role that is running at the same time is left to that role's run. So a write by one agent into another running role's paths would be committed, and attributed, as that role's work. A separate workspace per role belongs to W3.
- **D. Process cleanup is best effort.**
  - Without a PID namespace, a process that clears its environment (and with it `FC_RUN_TAG`) can outlive a stopped run.
  - If a `launch` process (an agent's supervisor) is SIGKILLed, its native agent keeps running until it exits. `recover` reclaims the task but does not kill that process.
  - If the Worker itself is SIGKILLed, its running `launch` processes are separate sessions: they finish their tasks normally, and no new task is started.
- **E. Codex does not report its selected model** in the observed `codex exec --json` output. For Claude Code, the model family is checked after every run from the `init` event, and a substitution fails the run. For Codex, the requested `-m`, or the CLI default, is trusted, not verified.
- **F. The installed AI tools may update independently of the Worker.** Neither the npm-installed Claude Code nor the Codex standalone package is pinned by W2, and a Codex update daemon (`codex-update-manager`) was seen running on the laptop. The flag probe and the status checks run before every run and fail closed on a missing flag or an unrecognized login. A behaviour change behind unchanged flags is not detected. Only the versions in §8 are verified. Controlled updates and Worker maintenance belong to a later stage.
- **G. No durable task checkpoints or recovery.** Work in progress stays uncommitted in the role's paths between runs, and there is no resumable Worker session (§12). This belongs to W3.

Other constraints:
- **Gates still need bubblewrap.** The Worker runs the integration gates the operator names; `contract-check`/`docs-check` are the defaults.
- **`StateStore` is not thread-safe.** Concurrency is by process (`feature.py launch`), never threads sharing a store.
- **Opus on the Pro plan is verified** (§10). If a plan cannot use the requested model, the Backend run fails ("model substitution" or an error) rather than silently using another model. Changing the Backend model is a configuration change.

## 12. Commits, checkpoints and W3

- **Current commit model:**
  - one feature, one branch (`feature/<slug>`), one final PR (unchanged);
  - each **run** of a task produces at most one role-scoped commit, so a task that runs several times (after a stop, a deferral or a reopen) gets several commits;
  - `task.commits` lists them all;
  - nothing in W2 limits commits per task.
- **Commit messages** are generated by the Git coordinator from trusted task data. Agent output and agent-issued Git commands are never used.
  - Subject (`gitops.commit_subject`): `<role>: <task title>` when the title is one printable line and the subject fits 72 characters (whitespace collapsed). Otherwise `<role>: task <task id>`. A multi-line, long, empty or control-character title (typically the task's whole instruction, as in the smoke test) is never copied into the history.
  - Trailers (unchanged): `Feature:`, `Feature-Task:`, `Feature-Role:`. They are the last paragraph, so a trailer-like title cannot change them. The native backend's acceptance check still requires `Feature-Task:` on every new commit.
  - Author, role-scoped manifests, `git.lock` serialization and compare-and-swap `update-ref` are unchanged. Every commit of a task, including a second run's, gets the same subject and trailers.
  - Example: the smoke Backend task now commits as `backend: task subscription-smoke.backend`. Before the fix, its subject was the full three-sentence prompt.
- **For W3 to address:**
  - durable checkpoints of in-progress work: today it stays uncommitted in the role's paths between runs;
  - commits *within* a run (an agent's intermediate milestones);
  - recovery of orphaned native agent processes after a Worker crash;
  - checkpoint storage outside the checkout (S3 is not implemented);
  - resumable Worker sessions (`work` restarts from the recorded task states but keeps no session of its own);
  - attribution of concurrent native changes (a separate workspace per role, or the `bwrap` backend).
