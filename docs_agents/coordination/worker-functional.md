# Functional Multi-Agent Worker (W2)

> Owner role: **coordination**. Status: **W2 implemented and tested offline (2026-10-22); real subscription smoke test attempt 1 failed in the harness before any model request (fixed, see §10); a second run awaits operator approval.** Not committed.
> Related: [feature-workflow.md](feature-workflow.md) (coordinator design) · [operations.md](operations.md) (commands) · [agent-ownership.md](agent-ownership.md) (write scopes, the `bwrap` backend) · [project-config.example.yaml](project-config.example.yaml) · [phase-status.md](phase-status.md)
>
> Status classes used below: **IMPLEMENTED** · **TESTED OFFLINE** (fake agents, disposable repositories) · **TESTED WITH SUBSCRIPTIONS** (a real model request) · **NOT IMPLEMENTED**. Nothing here is tested with subscriptions yet.

## 1. What W2 adds

One **Worker** (a local process) drives one feature through several specialized AI agents, using the operator's existing **Claude Code** and **ChatGPT (Codex)** subscriptions. No API key, gateway or pay-per-token billing is involved. The AWS coordinator, multiple Workers and durable checkpoints are not part of W2.

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
| `native-cli` (default) | The installed `claude` / `codex`, run directly in the workspace with the operator's subscription logins | **None at the OS level. NOT a security sandbox** (§3) | IMPLEMENTED, TESTED OFFLINE |
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

   Rejected changes are **left in place** for the operator and never committed or discarded. Accepted runs are committed by the Git coordinator from the role's own paths only (unchanged).

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

Detected on this host on 2026-10-22 (status commands only): Claude Code 2.1.303 → **claude.ai subscription (pro)**; Codex 0.163.0 → **ChatGPT subscription**.

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

| Component | Verified | Notes |
| --- | --- | --- |
| Claude Code | 2.1.303 | Flags probed; `auth status --json` reports the subscription (TESTED OFFLINE with the status command; no model request) |
| Codex | 0.163.0 | Flags probed; `login status`; `forced_login_method` accepted under `--strict-config` (checked offline) |
| Python | 3.12.3 (`.venv`, `/usr/bin/python3`) | No `/usr/bin/python` on this host |
| bubblewrap | 0.9.0 | Gates and the `bwrap` backend only |
| OS | Ubuntu 24.04.5, Linux 7.0 | Others not verified |

## 9. Tests

Offline, with fake agents and disposable repositories (no model, no GitHub, no AWS, no W1 parts):

| File | Covers |
| --- | --- |
| `test_fc_native_backend.py` (42) | default routing; routing changes by configuration only (Backend Opus or Sonnet, CLI on Claude Code or Codex, Frontend on Codex or Claude); rejected combinations; native command lines; Python discovery; the environment allowlist; subscription status classification; the CLIs' status commands; successful native run; CLI role on Codex; missing executable; missing, API-key and undetermined authentication; agent failure; timeout and cancellation (including a detached daemon); changes outside the role (rejected, left in place); protected ignored files; an agent's own commit; model substitution; API-key use; unfinished Git operation; backend selection without fallback; disabled role |
| `test_fc_worker_loop.py` (8) | dependencies first, then independent tasks concurrently (barrier); one writer per role and `max_parallel`; a failed task blocks its dependents; deferred tasks are not retried; disabled roles never run; one Worker per feature; workspace identity; stop forwarding; the **complete local workflow** with real processes (`plan`, `activate`, `work --integrate`, the native backend, Claude-Code and Codex stand-ins, a sandboxed gate, three serialized single-role commits, review); Worker cancellation by SIGTERM |
| `test_fc_launchers.py` (updated) | role commands with `{python}`; the `bwrap` backend unchanged otherwise |

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

Status: **NOT TESTED WITH SUBSCRIPTIONS.**

- **Attempt 1 (approved, 2026-10-22T00:31:37Z): FAILED before any agent started.** Exit 1 after ~1 s:
  `[Errno 2] No such file or directory: …/state/project-board.tmp`.
  - No model request, no subscription usage, no GitHub write.
  - Cause: `run_smoke` did not create its state directory. Offline tests covered only the dry run.
- **Fix:**
  - `run_smoke` now creates the state directory with `ensure_state_dir`.
  - `test_fc_worker_loop.py` runs `subscription-smoke --apply` end to end with the fake agent. With `follow_prompt` set, the fake agent carries out the smoke prompt itself.
  - The tests check the three role commits, the models and the rendered candidate, and that `--apply` is refused without a subscription.
  - Without the fix, the new test fails with the same error.
- A second run needs a new operator approval.

## 11. Known limitations

1. **No OS isolation in `native-cli`.** See §3. Write scopes are checked after the run, and an agent can read or change files outside the workspace and use the network. Changes outside the workspace cannot be detected.
2. **Concurrent agents share one checkout.** A change inside the paths of another role that is running at the same time is left to that role's run, so a write by one agent into another running role's paths would be committed as that role's work. Changes inside ignored directories (`node_modules`, `.venv`, `__pycache__`) are reported, not judged.
3. **Process cleanup is best effort.** Without a PID namespace, a process that clears its environment can outlive a stopped run. If a `launch` process (an agent's supervisor) is SIGKILLed, its native agent keeps running until it exits; `recover` reclaims the task but does not kill that process. If the Worker itself is SIGKILLed, its running `launch` processes are separate sessions: they finish their tasks normally, and only no new task is started.
4. **Model verification.** For Claude Code, the model family is checked from the `init` event. For Codex no model is reported, so the requested `-m` (or the CLI default) is trusted.
5. **Opus on the Pro plan.** This is unverified. If Claude Code cannot use Opus on the operator's plan, the Backend run fails ("model substitution" or an error) instead of silently using another model. Switching Backend to `sonnet` is a configuration change.
6. **Gates still need bubblewrap**, and the Worker runs the integration gates the operator names. `contract-check`/`docs-check` are the defaults.
7. **`StateStore` is not thread-safe.** Concurrency is by process (`feature.py launch`), never threads sharing a store.

## 12. Commits, checkpoints and W3

- **Current commit model:**
  - one feature, one branch (`feature/<slug>`), one final PR (unchanged);
  - each **run** of a task produces at most one role-scoped commit, so a task that runs several times (after a stop, a deferral or a reopen) gets several commits;
  - `task.commits` lists them all;
  - nothing in W2 limits commits per task.
- **For W3 to address:**
  - durable checkpoints of in-progress work: today it stays uncommitted in the role's paths between runs;
  - commits *within* a run (an agent's intermediate milestones);
  - recovery of orphaned native agent processes after a Worker crash;
  - checkpoint storage outside the checkout (S3 is not implemented);
  - resumable Worker sessions (`work` restarts from the recorded task states but keeps no session of its own);
  - attribution of concurrent native changes (a separate workspace per role, or the `bwrap` backend).
