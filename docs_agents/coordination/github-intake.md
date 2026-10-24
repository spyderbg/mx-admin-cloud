# GitHub Issue Intake — Approved Issues as Worker Tasks (W4.3.1)

> Owner role: **coordination**. Status: **implemented and tested with mock GitHub responses** (2026-10-23). **Approval impersonation through the shared GitHub identity is a known risk, accepted temporarily (§9).** Against real GitHub only its read-only commands have run (`intake show 25` and a dry-run poll: 0 writes). No Issue has been approved, and the intake is off until an operator enables it (§8).
> Code: `scripts/feature_coordinator/intake.py` (records, approval grammar, digest, identity), `intake_poll.py` (the poll), `intake_commands.py` · Tests: `scripts/tests/feature_coordinator/test_fc_intake.py` · Builds on: [github-integration.md](github-integration.md) (adapter, command authorization), [github-projects.md](github-projects.md) (Project #2), [worker-autonomy.md](worker-autonomy.md) (the Worker service).

A task can start as a GitHub Issue. Nobody writes a YAML file: an operator approves the Issue with a comment, and the approved text becomes one task for the existing coordinator and Worker. Before approval nothing happens. After approval the text is fixed.

## 1. Create the Issue

Open an Issue by hand or with any tool. The **title** names the task. The **body** is the complete task specification: it is given to the agent as written. Before approval:

- the Issue can be edited freely;
- it is not added to Project #2;
- nothing is written to it;
- nothing runs.

The Issue does not choose the role, the writable paths or the gates. The operator chooses the role when approving; the configuration supplies the paths and gates.

Never eligible: pull requests, closed Issues, Issues of the bug workflow (`mx-auto`, `source::*`, `severity::*`), and coordinator-owned Issues (they carry an ownership marker and use `/feature` commands).

## 2. Approve it

1. Review the Issue, then print its digest and the exact approval line. This step is read-only:

   ```bash
   python scripts/feature.py github intake show 25
   ```

   It shows whether the Issue is eligible, the SHA-256 of its current title and body, and one `/worker approve` line per role with that role's gates.
2. Comment the line for the chosen role, as the first line of the comment:

   ```text
   /worker approve cli 3f1c…(64 hex characters)
   ```

The approval is accepted only if all of these hold:

- **The author is authorized.** The author is in `github.command_authors` and has at least `github.command_min_permission` (`write`) on the repository. These are the same rules as for `/feature` commands ([github-integration §7.2](github-integration.md#72-github--local-validated-commands-only)), and the permission is re-read on every poll. Other authors are recorded as ignored. This identifies a GitHub account, not a person: with the shared account, see the accepted risk in §9.
- **The digest matches.** It must match the Issue's title and body when the poll reads them. If anyone edited the Issue after the review, the approval is rejected, and the operator reviews again and approves the new digest.
- **The role is usable.** It is a configured, enabled role with intake gates.
- **The names are free.** No feature `issue-<N>` and no branch `feature/issue-<N>` exists yet.

The coordinator never replies to a rejected or ignored approval: before approval nothing is written to the Issue. `feature.py github intake poll` and the events (`intake-approval-rejected`, `intake-approval-ignored`) give the reason.

## 3. After approval

Each step is recorded locally before the next one, so an interrupted poll resumes without repeating anything (§6):

1. **Record.** The approved title and body, the digest, the role and gates, and the approver and comment go into `intake/issue-<N>.json` in the state directory, before anything else.
2. **Plan.** The existing `plan` creates feature `issue-<N>` with **one** task `issue-<N>.<role>`, using the existing records, state machine and gates. The task's instructions are the approved Issue text. **No new Issue is created.** The approved Issue is the task's Issue, and the feature has no parent Issue.
3. **Acknowledge.** One comment (with an operation marker) is posted on the Issue. It says who approved it, the digest, the task, the gates and the branch.
4. **Activate.** If no other feature is active on the checkout, the oldest planned intake feature is activated: its branch is created from `dev` and the task becomes **Ready**. Otherwise it waits in **Backlog**, because the checkout runs one feature at a time.
5. **Run.** The Worker service runs the Ready task as any other: claim, agent, checkpoint, gates, verification. Integration and the single pull request follow the existing procedures. The intake neither creates nor merges a pull request.

## 4. Project #2

The plan step authorizes the feature for the board, and the board sync adds **exactly the approved Issue** as the task's item on Project #2 (`mx-admin-cloud automation`; owner, number and node ID verified, Project #1 never written).

- **Status** follows the existing mapping: Backlog → Ready → In Progress → Review → Done, or Blocked ([github-projects §3](github-projects.md#3-status-mapping-from-the-existing-state-machines)). `Agent` is the role and `Feature ID` is `issue-<N>`.
- **Identity:** before a write, the Issue's number, database ID and node ID are checked against the approval record (in place of an ownership marker).
- **Write scope:** with `github.write_scope: test-features-only` (unchanged; `all-features` is not enabled), live writes stay limited to test features **plus the exact approved Issue**:
  - the only writes are comments on that Issue and that Issue's own card;
  - only the intake task may map to it: a record of the feature mapped to it, or to any other Issue, is refused (`protected`);
  - a task added to the feature later (for example a contract request) gets no Issue and no card;
  - every other Issue and card is never written (`test_the_live_write_scope_is_exactly_the_approved_issue_and_its_card`, `test_only_the_task_may_show_the_approved_issue`).
- **Cards are not input.** A card moved by a person is restored on the next sync. Putting an unapproved Issue on the board, or moving any card to Ready or In Progress, never plans, approves or starts anything.

## 5. How the Worker discovers the task

With `github.intake.enabled: true`, the W4 service (`feature.py --tracker github worker start`) polls at most every `poll_interval_seconds` (60 s), at the start of a cycle and never while an agent runs or while paused. You can also poll by hand: `feature.py github intake poll --apply`.

A poll is bounded:

- one paginated listing of open Issues;
- comments are read only for Issues whose `updated_at` changed since the last poll (`flags/intake-poll.json`), at most `max_issues_per_poll` (30) per poll, the rest in the next poll;
- one read of each approved, unfinished Issue.

Polls are serialized by `locks/github-intake.lock`. A GitHub failure defers the poll: the service records it and continues.

## 6. The approval holds only while the Issue is open and unchanged

**The task only ever runs the approved text.** It is copied into the local task record at approval and never re-read from GitHub.

**The approval is withdrawn** when:
- the title or body differs from the approved digest;
- the Issue is **closed**;
- it was deleted or transferred, became a pull request, or was labeled for the bug workflow.

The Issue is checked at every boundary where work could start or be accepted. GitHub is never contacted while a run holds its claim; the existing rule that no tracker request runs under a coordinator, project or role lock is unchanged.

| Boundary | Check | If withdrawn | If GitHub cannot be read |
| --- | --- | --- | --- |
| Approval (§2) | the digest in the comment | rejected | poll deferred |
| Every poll | fresh read | task held | poll deferred |
| Before a launch (service) | fresh read | held, no launch | transient wait, no launch |
| Start of every run (`launch`, `work`, service) | fresh read, before the claim | held, not started | not started (transient) |
| While the agent runs (service, every poll interval) | fresh read | recorded; the run is stopped by the existing cancellation (as `feature.py stop`) | logged; the next boundaries check |
| Before the result is accepted (claim held) | local: is a withdrawal known? | `needs-human`, not verified | — |
| Right after the claim is released | fresh read | a verification just made is withdrawn (re-opened, then held) before the board or the service sees it | re-opened for a later attempt (transient) |
| `integrate` (the single PR candidate) | fresh read, before the barrier | refused | refused |

**What fail closed means here:**
- the task is held (`needs-human`), and the board shows **Blocked** with the reason;
- one notice is posted on the Issue (none on an Issue that is no longer ours, such as a bug-workflow one);
- an `intake-spec-changed` event is recorded.

A running agent is stopped gracefully. Completed checkpoints and commits stay on the feature branch, uncommitted work stays in the role's paths, and nothing of it is accepted.

**Closing the approved Issue** stops its task in the same way. The coordinator never reopens, edits or closes the Issue.

**To continue,** an operator decides explicitly:
1. restore the approved title and body (GitHub keeps the edit history), and reopen the Issue if it was closed;
2. release the hold: `feature.py release issue-<N>.<role> --apply`.

A release while the Issue is still changed or closed is held again at the next boundary. To change the task, open a new Issue.
- **No duplicates.** There is one intake record per Issue number, created inside a state transaction. A second `/worker approve`, a repeated poll or a restarted coordinator changes nothing (`intake-duplicate-ignored`). Recovery after a crash at each step is tested (`test_a_restart_never_duplicates_the_task`).

## 7. Commands

| Command | Effect |
| --- | --- |
| `github intake show N [--json]` | Read-only: eligibility, digest, approval lines per role, intake status |
| `github intake poll [--apply] [--json]` | Discover approvals, plan and activate, verify approved texts. Without `--apply` it reads and reports only. `--apply` needs `github.intake.enabled` |
| `github intake status [--json]` | Read-only, local: every intake record with its feature and task state |

All of them are `feature.py github intake …`; `--tracker local` uses the local JSON tracker for rehearsals.

## 8. Configuration (`github.intake` in [project-config.example.yaml](project-config.example.yaml))

| Key | Default | Meaning |
| --- | --- | --- |
| `enabled` | `false` | The service polls and `poll --apply` writes only when `true` |
| `poll_interval_seconds` | `60` | How often the service polls at most (at least 10) |
| `max_issues_per_poll` | `30` | Changed Issues whose comments are read per poll (1–100) |
| `max_spec_characters` | `20000` | Longer bodies are rejected as task specifications |
| `role_gates` | backend: `backend-tests`, `backend-lint`, `docs-check` · cli: `cli-lint`, `docs-check` · frontend: `frontend-type-check`, `frontend-build`, `docs-check` · coordination: `docs-check` | The gates an approved task must pass; a role without gates cannot be approved |

## 9. Limitations

- **No real approval yet:** the tests use mock Issue and comment responses and the local board; against real GitHub only the read-only commands have run.
- **One task per Issue.** Multi-step and multi-role work still needs a planned feature (`feature.py plan`).
- **No `/feature hold|release` on approved Issues:** use the local commands. When the feature is done, the coordinator does not close the Issue either; a person does.
- **Detection delay while an agent runs:** the service re-reads the Issue every `poll_interval_seconds`, and a run without the service (`feature.py launch`) is not re-read until it ends. An edit in that window can therefore be verified and then immediately withdrawn by the read after the claim is released. The verification never reaches the board or integration, but it does appear in the task's history (`verified → ready → needs-human`).
- **The approved body is prompt text.** The role's path policy and the post-run checks still bound what an agent may change, but `native-cli` is not a filesystem sandbox ([worker-functional.md](worker-functional.md)).

### Accepted risk: shared GitHub identity and approval impersonation

> **Operator decision (2026-10-24):** Shared GitHub identity and approval impersonation are known risks accepted temporarily for development, real-task validation and 24/7 stability testing. Credential separation will be considered after the autonomous Worker has completed several real tasks and demonstrated reliable 24/7 operation.

**The intake is NOT protected against approval impersonation.** The operator and the Worker use the same `gh` login (`spyderbg`). Under the default `native-cli` backend, an agent runs as the operator's OS user, with `HOME`, `XDG_CONFIG_HOME` and `XDG_RUNTIME_DIR`, so it can reach that login and the keyring session. A Claude Code role with Bash rules (backend, cli, coordination) may run `python -m pytest`, which executes any test file the agent writes.

Such an agent can post `/worker approve <role> <digest>`, or `/feature release …`, as `spyderbg`, computing the digest itself. The coordinator cannot tell this from the operator, and the approval could create a new task for any role. The agent could also write the coordinator's state directory directly.

| Implemented safeguards (they do hold) | Accepted risks (not prevented) |
| --- | --- |
| Approval only by a comment from a `command_authors` account with at least `write` permission, re-read every poll | The check identifies a GitHub **account**, not a person; with one shared account, an agent's comment passes it |
| The approval is bound to the SHA-256 of the reviewed title and body | An agent can compute the digest of any Issue, including one it wrote |
| Nothing is written to an unapproved Issue; one intake record per Issue; no extra Issues | An agent can create an Issue and approve it as the operator |
| The approved text is fixed; a changed or closed Issue holds the task at every boundary (§6) | An agent can also change or close Issues, or write the coordinator's local state directly |
| Writes limited to the approved Issue and its card; Project #1 never written | Every approval is recorded only locally (`intake-approved` events, `intake status`) and acknowledged from the same account, so GitHub notifies nobody |
| Under the `bwrap` backend, agents see no `gh` login (private HOME, cleared environment) | `native-cli` (the default) is not a sandbox; Codex's own no-network sandbox is unverified with a real agent |

**Not done now, by decision:** no separate accounts, bots, GitHub Apps, tokens, credential brokers, W1 credential isolation or other new security infrastructure. Options to consider afterwards:
- a separate Worker identity;
- approvals only from a human account whose credentials are not on the Worker host;
- an "approver is not the coordinator" check;
- W1 isolation (D-20/D-21, draft PR #20).

Until then, an operator can audit approvals with `feature.py github intake status` and the `intake-approved` events.
