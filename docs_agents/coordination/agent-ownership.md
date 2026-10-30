# Agent Ownership and Write Enforcement (Phase 3)

> Owner role: **coordination**. Status: **Phase 3 deliverable, approved** (2026-10-17; committed as `b3f3691`).
> Design: [feature-workflow.md](feature-workflow.md) · Operations: [operations.md](operations.md) · Configuration: [project-config.example.yaml](project-config.example.yaml) · Status: [phase-status.md](phase-status.md)
>
> Evidence tags: **[V]** verified by execution (the named test or command), **[S]** source-read, **[U]** unverified.

## 1. Who may write what

| Role | Runner | Writable roots | Read-only inside them | Command allowlist (Claude Code Bash) |
| --- | --- | --- | --- | --- |
| backend | Claude Code | `backend/`, `docs_agents/backend/` | `backend/data/` | `python -m pytest`, `python -m pylint`, `python -m compileall` |
| cli | Claude Code | `cli/`, `docs_agents/cli/` | — | `python -m pytest`, `python -m pylint`, `kiddo-cli --help` |
| frontend | Codex | `frontend/`, `docs_agents/frontend/` | `frontend/.env.development.local` (D-5: debug defaults unchanged) | Codex sandbox: commands run with **no network** |
| automation | remote Qwen (local tool loop) | `scripts/`, `test_environment/`, `docs_agents/automation/` | `test_environment/mxfix/`, `test_environment/mxissues/`, `test_environment/testenv.toml`, `test_environment/testenv.local.toml`, `scripts/deploy.sh` | none; `find-replace` and `multi-find-replace` only, executed locally |
| coordination | Claude Code | `docs_agents/coordination/` | — | `python scripts/check_docs_agents.py` |

- **Never writable by anyone:** `.git/**` and everything else outside the roots above. That includes:
  - the repository root files and the other roles' trees;
  - protected paths (`.env*` at any depth, `content/`, `data/`, `backend/data/`, the bug workflow);
  - approval-required paths without a task grant.
- **Generated outputs follow destination ownership:** Frontend may write `frontend/src/types/*Schemas.ts`; Automation, which owns the generator, may not.
- The live checkout's roles are listed by `python scripts/feature.py sandbox-check` **[V]**.

## 2. Enforcement layers

> **W2 (2026-10-22): which layers apply depends on `runtime.execution_backend`.** With `bwrap`, all four layers below apply as described. With the default **`native-cli`** ([worker-functional.md](worker-functional.md)), layer 1 does **not** apply: the agent runs with the operator's full file-system and network access. Layer 2 applies to the CLIs' own permissions (Claude Code's allowlist, Codex's command sandbox). Layer 3 applies **workspace-wide**: any change outside the role, any protected ignored file and any Git-state change rejects the run, and the changes are left in place for review. Role write scopes are then verified *after* the run, not enforced while it runs.

| Layer | Mechanism | Status |
| --- | --- | --- |
| 1. OS containment | bubblewrap per run (`sandbox.py`): new namespaces, a read-only shadow root with the role roots bound read-write, read-only overlays, `.git` read-only, private HOME and `/tmp`, untracked secrets hidden | **Enforced and tested [V]** for the `bwrap` backend. bubblewrap 0.9.0, unprivileged. **Not applied** by the `native-cli` backend (W2) |
| 2. Runner permissions | Claude Code: `--restricted`, `--tools`, generated `--settings` allow/deny list, `--permission-prompts none`, `--strict-mcp-config`. Codex: `--sandbox workspace-write` (nested bubblewrap), `--ignore-user-config`, `network_access=false` | **Enforced [V]**: flags probed on the installed CLIs; live smoke runs observed (§4) |
| 3. Commit gate | `RealCliRunner` rejects a run that created a path the role may not commit or wrote outside its roots. The Git coordinator re-validates every manifest path (`PathPolicy`) and refuses foreign staged content | **Enforced [V]** (Phase 2 and Phase 3 tests) |
| 4. PR/CI gate | Changed-path re-verification across the whole feature diff | Phase 6 |

`.git` is read-only in every sandbox, so agents can run `git status`, `log` and `diff`, but not `commit`, `branch`, `update-ref` or `config` (`test_git_metadata_is_read_only_but_readable` **[V]**). Only the Git coordinator mutates Git, outside any agent sandbox.

## 3. Containment matrix (exit-gate evidence)

`test_containment_matrix_for_every_role` and `python scripts/feature.py sandbox-check --apply` run a probe **inside each role's real sandbox profile** in a disposable repository. The probe:

- writes to 19 targets: every role's trees, a generated output, `.git`, protected and approval-required paths, the root, `test_environment/mxfix`, `scripts/deploy.sh`;
- reads the hidden `.env`;
- writes outside the repository;
- writes through a symlink into another role's tree;
- runs `git commit`.

Every observed result must equal the `PathPolicy` decision. **Result: 0 mismatches for all 5 roles, in about 0.6 s [V].**

| Role | Written (all others refused with `EROFS`) |
| --- | --- |
| backend | `backend/src/probe.py`, `docs_agents/backend/probe.md` |
| cli | `cli/src/probe.py`, `docs_agents/cli/probe.md` |
| frontend | `frontend/src/probe.ts`, `frontend/src/types/probeSchemas.ts`, `docs_agents/frontend/probe.md`; a root write lands only in the discarded shadow and is reported as a stray |
| automation | `scripts/probe.py`, `test_environment/probe.md`, `docs_agents/automation/probe.md` |
| coordination | `docs_agents/coordination/probe.md` |

For every role:

- `.env` is not found;
- the symlink escape is denied (`EROFS`);
- `git commit` is refused;
- the outside write never reaches the host;
- protected files are byte-identical afterwards.

Further negative tests **[V]**:

- `test_traversal_and_symlink_escapes_never_reach_the_host`: `..` traversal, an absolute `/etc` write and a symlink to `/etc`;
- `test_private_home_tmp_and_no_state_dir`: the coordinator state and the real HOME are invisible;
- `test_gate_profile_has_no_network_and_agent_profile_does`;
- `test_codex_style_run_stray_root_write_is_rejected`;
- `test_new_protected_or_symlink_entries_are_rejected_but_preexisting_secrets_are_not`.

## 4. Real runner verification (2026-10-17)

| Check | Claude Code 2.1.302 | Codex 0.163.0 |
| --- | --- | --- |
| Required flags present (`--help`) | yes **[V]** | yes **[V]** |
| Starts inside the sandbox (`--version`) | yes **[V]** | yes **[V]** |
| Login visible with read-only credentials (`auth status` / `login status`, opt-in test) | logged in **[V]** | logged in **[V]** |
| Own sandbox works nested inside ours | n/a | yes: Frontend write ok, Backend and `.git` denied, no network for commands **[V]** |
| **Live smoke run** (operator-approved, disposable repository) | created `backend/hello.txt`. Exactly 6 tools offered (`Bash, Edit, Glob, Grep, Read, Write`), **no MCP servers loaded**, `acceptEdits`, no permission denials, 2 turns, 5 s, $0.0225 **[V]** | created `frontend/hello.txt` through one command in its nested sandbox; 29,355 input tokens (24,576 cached), 90 output **[V]** |

## 5. Residual risks (operator decisions in [phase-status](phase-status.md))

The risks below concern the `bwrap` backend. The `native-cli` backend (W2) gives agents full access by design; its limitations are listed in [worker-functional §11](worker-functional.md#11-known-limitations). W1's credential and network isolation work (D-20, D-21) is postponed in [PR #20](https://github.com/spyderbg/mx-admin-cloud/pull/20).

1. **The runner's own credentials are readable inside its sandbox.** The CLI needs them, and they are bound read-only. Commands an agent runs could read them; a Phase 3 smoke probe demonstrated this.
   - Claude Code's file tools are denied (`Read(~/.claude/**)`, `Read(~/.codex/**)`).
   - Claude's Bash is limited to the role's command prefixes; Codex's commands have no network.
   - A test written by an agent and run via `python -m pytest` could still read and print the file, and the model would see its output. Full mitigation needs credential brokering, for example an API-key helper outside the sandbox (decision D-20).
2. **Runner network.** Claude Code needs its API, so its sandbox shares the host network, and Bash commands allowed by the role allowlist could reach the network. Codex commands are offline (nested sandbox). Options: a network namespace with an allowlisting proxy, or Claude Code's own sandboxed Bash (D-21).
3. **New protected-named files inside a role's own root** cannot be prevented by mounts, since nothing exists to mount over. The run is rejected and the commit gate refuses them (layer 3).
4. **Codex needs a writable shadow root** for its nested sandbox. Root-level writes are discarded and reject the run; empty mount-point residue is ignored.
5. **OAuth token refresh** with read-only credentials is unverified **[U]**. A long run whose token expires would fail authentication, and the operator re-logs in on the host.
6. **Codex's nested sandbox cannot start on hosts with Ubuntu's `bwrap-userns-restrict` AppArmor profile** (found 2026-10-23).
   - **Why:** the profile runs everything bubblewrap starts as `unpriv_bwrap`, with every capability denied, so the bubblewrap that Codex starts inside the Frontend sandbox cannot create namespaces ("No permissions to create new namespace"; kernel audit `profile="unpriv_bwrap" capname="sys_admin"`). With `codex sandbox` the command did not run at all.
   - **Affected:** only `runtime.execution_backend: bwrap` with Codex.
   - **Not affected:** the default `native-cli` backend, which runs agents outside the coordinator's sandbox, and the gates, which are top-level sandboxes.
   - **The test:** `test_fc_launchers.py::test_codex_own_sandbox_works_nested_inside_ours` is skipped, with that reason, only where a probe in the same Frontend sandbox shows both the `unpriv_bwrap` label and bubblewrap's namespace refusal. Any other failure still fails the test.
   - **The fix is not to weaken the host:** never disable AppArmor or `kernel.apparmor_restrict_unprivileged_userns` for this. Use `native-cli`, or a host without that profile, until the `bwrap` backend has a Codex mode that does not nest bubblewrap (an open decision).
