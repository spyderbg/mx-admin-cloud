# Agent Ownership and Write Enforcement (Phase 3)

> Owner role: **coordination**. Status: **Phase 3 deliverable, approved** (2026-10-17; committed as `b3f3691`).
> Design: [feature-workflow.md](feature-workflow.md) · Operations: [operations.md](operations.md) · Configuration: [project-config.example.yaml](project-config.example.yaml) · Status: [phase-status.md](phase-status.md) · Credentials and network (W1): [worker-security.md](worker-security.md)
>
> Evidence tags: **[V]** verified by execution (the named test or command), **[S]** source-read, **[U]** unverified.

## 1. Who may write what

| Role | Runner | Writable roots | Read-only inside them | Command allowlist (Claude Code Bash) |
| --- | --- | --- | --- | --- |
| backend | Claude Code | `backend/`, `docs_agents/backend/` | `backend/data/` | `python -m pytest`, `python -m pylint`, `python -m compileall` |
| cli | Claude Code | `cli/`, `docs_agents/cli/` | — | `python -m pytest`, `python -m pylint`, `kiddo-cli --help` |
| frontend | Codex | `frontend/`, `docs_agents/frontend/` | `frontend/.env.development.local` (D-5: debug defaults unchanged) | Codex sandbox: commands run with **no network** |
| automation | remote Qwen (local tool loop) | `scripts/`, `test_environment/`, `docs_agents/automation/` | `scripts/mxfix/`, `scripts/mxissues/`, `scripts/deploy.sh` | none; `find-replace` and `multi-find-replace` only, executed locally |
| coordination | Claude Code | `docs_agents/coordination/` | — | `python scripts/check_docs_agents.py` |

- **Never writable by anyone:** `.git/**` and everything else outside the roots above. That includes:
  - the repository root files and the other roles' trees;
  - protected paths (`.env*` at any depth, `content/`, `data/`, `backend/data/`, the bug workflow);
  - approval-required paths without a task grant.
- **Generated outputs follow destination ownership:** Frontend may write `frontend/src/types/*Schemas.ts`; Automation, which owns the generator, may not.
- The live checkout's roles are listed by `python scripts/feature.py sandbox-check` **[V]**.

## 2. Enforcement layers

| Layer | Mechanism | Status |
| --- | --- | --- |
| 1. OS containment | bubblewrap per run (`sandbox.py`): new namespaces **including the network namespace (W1, D-21)**, a read-only shadow root with the role roots bound read-write, read-only overlays, `.git` read-only, private HOME and `/tmp`, untracked secrets hidden, **no runner credential mounted (W1, D-20)** | **Enforced and tested [V]**. bubblewrap 0.9.0, unprivileged. The model is reached only through the host-side broker ([worker-security](worker-security.md)) |
| 2. Runner permissions | Claude Code: `--restricted`, `--tools`, generated `--settings` allow/deny list, `--permission-prompts none`, `--strict-mcp-config`; since W1 also its own Bash sandbox in strict mode (nested command isolation). Codex: `--sandbox workspace-write` (nested bubblewrap), `--ignore-user-config`, `network_access=false`; since W1 also `--strict-config`, the broker provider, `web_search="disabled"` and a core-only command environment | **Enforced [V]**: flags probed on the installed CLIs; live smoke runs observed (§4); W1 nested isolation verified offline with the installed CLIs ([worker-security §5](worker-security.md#5-network-isolation-d-21)) |
| 3. Commit gate | `RealCliRunner` rejects a run that created a path the role may not commit or wrote outside its roots. The Git coordinator re-validates every manifest path (`PathPolicy`) and refuses foreign staged content | **Enforced [V]** (Phase 2 and Phase 3 tests) |
| 4. PR/CI gate | Changed-path re-verification across the whole feature diff | Phase 6 |

`.git` is read-only in every sandbox, so agents can run `git status`, `log` and `diff`, but not `commit`, `branch`, `update-ref` or `config` (`test_git_metadata_is_read_only_but_readable` **[V]**). Only the Git coordinator mutates Git, outside any agent sandbox.

## 3. Containment matrix (exit-gate evidence)

`test_containment_matrix_for_every_role` and `python scripts/feature.py sandbox-check --apply` run a probe **inside each role's real sandbox profile** in a disposable repository. The probe:

- writes to 19 targets: every role's trees, a generated output, `.git`, protected and approval-required paths, the root, `scripts/mxfix`, `scripts/deploy.sh`;
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

> The login checks and live smoke runs in this table used the Phase 3 launch, which bound the runner's login read-only. Since W1 no login is mounted: `test_real_login_is_not_visible_inside_the_sandbox` (opt-in) asserts the opposite, and real runs need a broker credential ([worker-security §4.4](worker-security.md#44-credential-sources--and-why-subscriptions-cannot-be-brokered)). The W1 offline compatibility of Claude Code 2.1.303 and Codex 0.163.0 is recorded in [worker-security §8](worker-security.md#8-security-self-tests).

| Check | Claude Code 2.1.302 | Codex 0.163.0 |
| --- | --- | --- |
| Required flags present (`--help`) | yes **[V]** | yes **[V]** |
| Starts inside the sandbox (`--version`) | yes **[V]** | yes **[V]** |
| Login visible with read-only credentials (`auth status` / `login status`, opt-in test) | logged in **[V]** | logged in **[V]** |
| Own sandbox works nested inside ours | n/a | yes: Frontend write ok, Backend and `.git` denied, no network for commands **[V]** |
| **Live smoke run** (operator-approved, disposable repository) | created `backend/hello.txt`. Exactly 6 tools offered (`Bash, Edit, Glob, Grep, Read, Write`), **no MCP servers loaded**, `acceptEdits`, no permission denials, 2 turns, 5 s, $0.0225 **[V]** | created `frontend/hello.txt` through one command in its nested sandbox; 29,355 input tokens (24,576 cached), 90 output **[V]** |

## 5. Residual risks (operator decisions in [phase-status](phase-status.md))

1. ~~**The runner's own credentials are readable inside its sandbox.**~~ **Resolved in W1 (D-20; [worker-security](worker-security.md)):** no login file is mounted and no API key is forwarded; the host-side broker holds the upstream credential, and the runner receives only a per-run token that is revoked when the run ends **[V]**. Real runs now need an operator-chosen broker credential; a Claude or ChatGPT subscription login cannot be brokered (open part of D-20).
2. ~~**Runner network.**~~ **Resolved in W1 (D-21):** every runner sandbox has its own network namespace whose only route is the broker endpoint, and agent commands run in the CLI's own nested sandbox without network, sockets or token **[V]**.
3. **New protected-named files inside a role's own root** cannot be prevented by mounts, since nothing exists to mount over. The run is rejected and the commit gate refuses them (layer 3).
4. **Codex needs a writable shadow root** for its nested sandbox. Root-level writes are discarded and reject the run; empty mount-point residue is ignored.
5. ~~**OAuth token refresh** with read-only credentials~~ — no longer applicable since W1: the sandbox holds no OAuth token.
