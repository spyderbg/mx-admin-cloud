# W1 — Bounded Hardened Smoke Test (operator-run)

> Owner role: **coordination**. Prepared for operator approval; see [w1-progress.md](w1-progress.md) Stage 7 §7h
> and Stage 8. **Nothing in this plan has been executed.** No authentication was initiated and no model
> request was made while preparing it. The operator runs every step below and approves execution separately.

## Purpose and scope

Confirm, once, that the opt-in hardened `native-cli` backend ([native hardening](../../feature_coordinator/native_hardening.py))
runs real, subscription-authenticated agent sessions end to end on this Desktop: Claude Code with **no Bash
tool** and Codex under the credential-free, offline `fc_worker` permission profile, with the Git
command-execution defence ([gitsafe](../../feature_coordinator/gitsafe.py)) in force. It uses the existing
`subscription-smoke` harness (one disposable repository, local tracker, no GitHub, no gates, no PR, one
attempt per task), run with hardening explicitly enabled.

This is **not** a declaration that W1 is complete: D-21 for the trusted CLI processes remains unenforced on
this host (blocker B1).

## Hard limits (enforced by the harness, not by trust)

| Limit | Value | Where |
| --- | --- | --- |
| Model sessions | **3** — Backend (Claude Code), CLI (Claude Code), Frontend (Codex) | `native_smoke.smoke_spec()` |
| Attempts per task | 1 (no retries) | `native_smoke` |
| Per-run Claude budget | **$0.50** (`--max-budget-usd`) | `SMOKE_CLAUDE_BUDGET_USD` |
| Task timeout | **300 s** | `SMOKE_TASK_TIMEOUT_SECONDS` |
| Billing | **Subscription only** — a run fails closed if a CLI reports an API key, is not logged in, or substitutes a model | `native_backend.check_subscription`, `claude_session_problems` |
| Tracker / GitHub / gates / PR | local JSON tracker; none of GitHub, gates or a PR | `native_smoke.run_smoke` |
| Repository & state | a throwaway repository and state directory under `--workdir` | `--workdir` |

No API key is ever passed to a CLI (the environment is an allowlist; existing tests assert it), so there is
no pay-per-token path to disable.

## Preconditions (operator verifies; no model call)

1. Work in the isolated clone `~/worktrees/mx-admin-cloud-w1` on branch `security/worker-d20-d21`. **Do not**
   use the live Worker's checkout, state directory or service; the live Worker keeps running unchanged.
2. Claude Code is logged in with the **claude.ai subscription** (the operator's existing login — nothing to
   provision): `claude auth status` reports `claude.ai`, not an API key.
3. A **Worker-only `CODEX_HOME`** exists with its own ChatGPT login, provisioned by the operator as in the
   next section. It is never the operator's `~/.codex`, and no existing credential is copied into it.
4. CLI versions at or above the hardened floor: Claude Code ≥ 2.1.317, Codex ≥ 0.163.0.

## One-time provisioning of the Worker-only Codex login (operator-run)

Create a private directory and log in **interactively** with ChatGPT device auth. This is the only step that
authenticates anything, and the operator performs it by hand — this plan does not run it.

```sh
install -d -m 700 ~/.config/mx-admin-worker-codex          # a real dir, mode 0700, outside any workspace
CODEX_HOME=~/.config/mx-admin-worker-codex codex login --device-auth   # operator completes ChatGPT sign-in
CODEX_HOME=~/.config/mx-admin-worker-codex codex login status          # expect: "Logged in using ChatGPT"
```

Do **not** copy `~/.codex/auth.json` (or any other credential) into this directory — provisioning is a fresh
sign-in only. The preflight refuses this directory unless it is a real directory (not a symlink), owned by
the Worker user, mode 0700, and outside the workspace.

## Hardened project configuration (operator-run; no secrets in it)

Start from the documented example and enable hardening, pointing Codex at the Worker-only login:

```sh
cd ~/worktrees/mx-admin-cloud-w1
cp docs_agents/coordination/project-config.example.yaml /tmp/w1-smoke-config.yaml
```

Edit `/tmp/w1-smoke-config.yaml` so the `runtime` section contains:

```yaml
runtime:
  execution_backend: native-cli
  native_hardening:
    enabled: true
    claude_shell: disabled                 # no Bash tool (sandboxed Bash is unavailable on this host — B1)
    codex_home: /home/<user>/.config/mx-admin-worker-codex   # the Worker-only login; absolute path
    read_paths: []                         # add the node toolchain dir only if a Codex command needs it
```

The file holds only paths and flags — never a token. The smoke harness preserves these keys and overrides
only the execution backend, the budget and the timeout.

## Step 1 — Dry run (no model request, no approval needed)

```sh
cd ~/worktrees/mx-admin-cloud-w1
.venv/bin/python -m feature_coordinator --project-config /tmp/w1-smoke-config.yaml \
    subscription-smoke --workdir /tmp/w1-smoke
```

Expected: a report (no `--apply`) that runs only `--version`, `--help` and each CLI's **status** command, and
prints, per role, the agent, model, the exact hardened command line, and the authentication method. **Confirm
before going on:**

- Backend and CLI → `claude`, authentication `claude.ai subscription (...)`; the argv shows
  `--tools Read,Glob,Grep,Edit,Write` (no `Bash`) and `--restricted`.
- Frontend → `codex`, authentication `ChatGPT subscription`; the argv shows `default_permissions="fc_worker"`,
  `permissions.fc_worker.network.enabled=false`, `approval_policy="never"` and **no** `--sandbox`.
- If any role reports “not logged in”, an API key, or “undetermined”, **stop** and fix the login; do not
  continue to `--apply`.

## Step 2 — Bounded apply (operator approval REQUIRED)

Only after the dry run is clean and the operator approves this specific run:

```sh
.venv/bin/python -m feature_coordinator --project-config /tmp/w1-smoke-config.yaml \
    subscription-smoke --apply --workdir /tmp/w1-smoke
```

This runs the three sessions against the disposable repository in `/tmp/w1-smoke`.

### Expected result (pass criteria)

- Exit 0 with “subscription smoke test: PASSED”.
- The three files exist on the feature branch: `backend/smoke/backend.txt`, `cli/smoke/cli.txt`,
  `frontend/smoke/frontend.txt`; CLI and Frontend depend on Backend.
- Every run recorded `"hardening": {"enabled": true, ...}` and, for Claude, `agent_shell: "none (no Bash tool)"`,
  in `/tmp/w1-smoke/state/runs/*/native.json`; the Claude init event shows `apiKeySource: none`, only the five
  file tools, and no MCP server.
- No Git control change and no `rejected`/`failed` for a Git-config reason (the gitsafe gate stays quiet on a
  clean disposable repository).
- Usage: at most 3 short sessions; Claude spend bounded by $0.50/run.

### If it fails

- `unavailable: native hardening: …` before any session → a preflight refusal (version floor, managed policy,
  or the Worker `CODEX_HOME` not being a private 0700 directory outside the workspace). Fix and re-run the dry
  run; no model was called.
- A subscription/auth refusal → the login is not the subscription; fix per Step 1. No billing occurred.
- A timeout or a single failed session → inspect `/tmp/w1-smoke/state/runs/*/stdout.jsonl`; one attempt only,
  so the harness stops. Nothing outside `/tmp/w1-smoke` changed.

## Rollback / teardown

Nothing in this test touches the live Worker, `dev`, the operator's own logins, or anything outside
`/tmp/w1-smoke`. To tear down:

```sh
rm -rf /tmp/w1-smoke /tmp/w1-smoke-config.yaml
```

The Worker-only `CODEX_HOME` may be kept for a future hardened run or removed with
`rm -rf ~/.config/mx-admin-worker-codex` (and, to end that ChatGPT session,
`CODEX_HOME=~/.config/mx-admin-worker-codex codex logout` first). The live Worker is unaffected either way; no
service is restarted and no branch is pushed.

## After a successful smoke test

A clean pass demonstrates the hardened mode on real subscription sessions. Enabling hardening on the **live**
Worker is a separate operator decision (it changes the active Worker, and hardened Claude agents cannot run
commands — the gates validate instead). W1 remains incomplete until D-21 for the CLI processes is addressed
(blocker B1, option B or C).
