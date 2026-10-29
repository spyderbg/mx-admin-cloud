# GitHub Issue Intake — Approved Issues as Worker Tasks (W4.3.1)

> Owner role: **coordination**. **W4.3.1–W4.3.4 VERIFIED WITH REAL GITHUB (2026-10-24, bounded supervised tests)** after the original offline implementation. Issue #25/PR #27 exercised manual PR adoption and verified closure; Issue #29/PR #30 exercised a real Claude Code Worker, automatic integration, branch push, Draft PR creation and post-merge closure. Implementation: PRs #26 and #28, merged into `dev`. `github.publish.enabled` defaults **false**, but was enabled in the operator's external test configuration. Shared GitHub identity/approval impersonation remains an accepted temporary risk (§9).
> Code: `scripts/feature_coordinator/intake.py` (records, approval grammar, digest, identity), `intake_poll.py` (the poll), `intake_commands.py`, `publish.py` and `publish_commands.py` (W4.3.3) · Tests: `scripts/tests/feature_coordinator/test_fc_intake.py`, `test_fc_publish.py` (W4.3.3) · Builds on: [github-integration.md](github-integration.md) (adapter, command authorization), [github-projects.md](github-projects.md) (Project #2), [worker-autonomy.md](worker-autonomy.md) (the Worker service).

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
5. **Run.** The Worker service runs the Ready task as any other: claim, agent, checkpoint, gates, verification.
6. **Publish (W4.3.3, only with `github.publish.enabled`; §10).** Once every task is verified: integration, push of the feature branch, ONE Draft PR to `dev`. A person reviews and merges it; the verified merge completes the feature, closes this Issue and moves its card to Done. Nothing ever merges automatically. With publication off, integration and the PR stay manual, as before.

## 4. Project #2

The plan step authorizes the feature for the board, and the board sync adds **exactly the approved Issue** as the task's item on Project #2 (`mx-admin-cloud automation`; owner, number and node ID verified, Project #1 never written).

- **Status** follows the existing mapping: Backlog → Ready → In Progress → Review → Done, or Blocked ([github-projects §3](github-projects.md#3-status-mapping-from-the-existing-state-machines)), with one W4.3.3 rule: a verified intake task stays **Review** until its feature is done with merge evidence, its PR merged by a person. Verification alone never shows Done. `Agent` is the role and `Feature ID` is `issue-<N>`.
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

- **Real approvals tested, bounded:** the original W4.3.1 tests used mock Issues, but real approvals for Issues #25 and #29 were later accepted by intake and executed. This is supervised happy-path evidence, not evidence that shared credentials prevent approval impersonation or that live failures recover safely.
- **One task per Issue.** Multi-step and multi-role work still needs a planned feature (`feature.py plan`).
- **No `/feature hold|release` on approved Issues:** use the local commands. The coordinator closes the Issue only through W4.3.3 publication (§10), after the verified merge of its PR; with publication off, a person closes it.
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

## 10. Automatic publication and completion (W4.3.3)

> Status (2026-10-24): **implemented; VERIFIED OFFLINE AND WITH REAL GITHUB**. Original disposable repositories, mock GitHub and bare-origin tests remain as regression evidence; a supervised live test exercised PR adoption (#27) and another exercised automatic push/Draft PR creation (#30), both followed by human merge/verified Issue closure. Off **by default** (`github.publish.enabled: false`), enabled only in the operator's local test configuration. Code: `publish.py`, `publish_commands.py`, `gitops.py`, `github_api.py`; publisher tests: 86 passed after PR #28 review fixes (reported test execution, not rerun here).

With `github.publish.enabled: true`, a feature made from an approved Issue goes the rest of the way by itself, up to the one step that stays human: the merge.

```text
approved Issue → task verified → integration barrier → push of feature/issue-<N> → ONE Draft PR → dev
   → a PERSON reviews, marks it ready and merges it (a merge commit)
   → merge verified → feature done → completion comment → Issue closed → Project #2 card Done
```

### 10.1 Lifecycle

The Worker service runs one publication pass at most every `poll_interval_seconds`, before its intake poll, between launches, never while paused. `feature.py github publish poll --apply` runs the same pass by hand. A pass handles **only features with an intake record**: nothing else is integrated, pushed or opened automatically. Each step first reads what is already true, so every step can be repeated:

| Feature state | The pass does | It refuses or waits when |
| --- | --- | --- |
| active | runs the existing **integration barrier** (`integrate`) once every task is verified | a task is not verified; a claim exists or a launch is still recorded in flight (waits); a withdrawn approval is known; the last integration failed and no task was verified since (an operator runs `feature.py integrate issue-<N> --apply` after fixing it). A failure publishes nothing |
| review, no PR yet | re-reads the approved Issue, **pushes** the feature's own branch, records `pending_create`, opens **one Draft PR** to the feature's base branch, records its number, posts one PR link comment on the Issue | the approval no longer holds; the remote or the branch is not what it must be (§10.2); a PR already exists on the branch but does not match (§10.3) |
| review, PR recorded | reads the PR again by number | a draft or open PR: **waits** for a person; closed without merge: reports it and waits (never completes, never creates another PR; reopening resumes); a changed or mismatched PR: stops |
| review, PR merged | verifies the merge (§10.4), then `complete()` records the evidence and moves the feature to **done** in one transaction | the evidence does not hold: stops |
| done | posts one completion comment on the Issue, **closes the Issue** (state reason `completed`), checks that Project #2 shows **Done**, marks the intake record `done` | the Issue is no longer the approved one (it is not closed; reported); GitHub or the board fails (retried) |

What the Worker never does: merge, mark a PR ready, approve a review, push `dev`, `main` or any protected branch, force a push, open a second PR for a feature, or touch Project #1. The GitHub adapter has no merge request at all.

### 10.2 The push

`GitCoordinator.push_branch` pushes **exactly the validated commit** to `refs/heads/feature/issue-<N>`:

- the branch must match `feature/…` and is never a protected branch;
- the checkout must be clean (no tracked or untracked change, no unfinished Git operation), on that branch, at that commit; the local branch must still be at the PR candidate's head;
- `origin` must be the feature's repository: the root commits and the `origin` URL recorded at activation, a plain `github.com/<github.repository>` URL, configured once, and its **only** effective push URL. `git push origin` publishes to every push URL (each `pushurl`, else each `url`, after `insteadOf`/`pushInsteadOf` rewriting), so several push URLs, or one that differs from the configured URL, stop publication and nothing is pushed anywhere. `push_branch` checks this again under its locks, before it reads the remote and right before the push, against the destination the publisher approved;
- project.lock EXCLUSIVE + git.lock are held (no writer can start meanwhile); hooks are disabled (`core.hooksPath=/dev/null`, `--no-verify`); every network call is bounded by `git_timeout_seconds`, and on timeout the whole process group is killed;
- the remote branch is read first: already at the commit → nothing is pushed (a repeat); absent, or an ancestor → a normal push; anything else → **refused, never forced**. A remote rejection (`!` in the porcelain output) is refused too. After the push the remote branch is read again.

### 10.3 Exactly one PR per feature

Before anything is created, the pass looks for the PR: the recorded number, else **every** PR of the branch, open AND closed. A PR is adopted only when it is exactly the candidate:

- its head is `feature/issue-<N>` of `github.repository`;
- its base is the feature's base branch (`dev`) in the same repository;
- its head commit is the verified head;
- it was opened by the coordinator account or a `command_authors` login.

A branch name alone is never enough. Anything else stops publication, and no PR is created: a PR whose head commit, base, repository or author differ; several matching PRs; a closed foreign PR on the branch. The PR the pass has just created is checked the same way **before** it is recorded: a mismatch (for example, the branch moved on GitHub after the push) is never recorded and gets no link comment on the Issue. Publication stops, and later passes find that PR and refuse it instead of creating a second.

PR creation is narrowly scoped. `GitHubClient.create_pull_request` refuses before any request unless `github.publish.enabled` is true AND the publisher authorized exactly this head and base for one creation. It creates Draft PRs only; the operator-approved Phase 2 `open_pull_request` is still refused against real GitHub. The PR body names the Issue and its link, the feature, the task, the approval (approver, time, comment, spec digest), the changed files, the task gates and the integration gates. It has no closing keyword: the coordinator closes the Issue itself, only after the verified merge.

### 10.4 The human merge, verified

A merge completes the feature only if all of these hold:

- **The exact PR.** It is read again by number, and its identity still matches §10.3.
- **The verified head.** The PR's head is the verified head commit.
- **GitHub's evidence.** GitHub reports `merged` with a merge commit and a time.
- **Independent Git evidence.** The base branch is fetched from `origin` (FETCH_HEAD and the remote-tracking ref only; no local branch or working-tree file changes). The fetched branch must contain the merge commit, and the merge commit must contain the verified head.

A merge commit passes. A **squash or rebase merge** does not contain the verified head, so it stops for an operator, as does a merge commit that is not on `dev` or a PR whose head changed before the merge. The evidence is stored on the candidate (PR number and URL, merge commit, merged by and at, head, base, the fetched base tip), together with `merged_sha`, in the same transaction that moves the feature to done.

A closed approved Issue no longer looks like a withdrawn approval once its feature is done: the W4.3.1 check never looks at a done feature. The coordinator's own close therefore triggers nothing (`test_completion_never_triggers_the_approval_withdrawal`). The service runs publication before the intake poll, so a person who closes the Issue right after merging does not trigger the withdrawal either (`test_an_issue_closed_by_a_person_after_the_merge_still_completes`). Before the PR exists, the W4.3.1 rules apply unchanged: a changed or closed Issue stops publication, and nothing is pushed.

### 10.5 Recovery after interruptions

| Interrupted | On the next pass |
| --- | --- |
| before or during the push | the remote branch is read first: pushed again only if it is not there yet |
| after the push, before it was recorded | the remote already has the commit: nothing is pushed twice (`pushed: false`) |
| before the create request (`pending_create` recorded) | the PRs are listed first; none → created once |
| after GitHub created the PR, before it was recorded; a lost response | the PRs are listed first: the coordinator's own PR is adopted (`origin: created`), never duplicated. GitHub also refuses a second open PR per branch |
| a failed create request (5xx, permission) | deferred; the next pass lists, then creates once |
| while waiting for the merge (network, GitHub 5xx) | deferred, retried by the next pass; nothing is stopped |
| after the merge was verified, before done | verified again, then completed |
| between done and the Issue closure (comment, close, board) | a fixed outbox ID (`done-issue-<N>`) posts the comment once; the Issue is closed once (already closed: left as is); the board converges |

Each crash point is tested with a restart on new coordinator objects over the same files, and further passes write nothing (`test_every_interruption_is_recovered_without_duplicates`, `test_repeated_completion_and_restarts_write_nothing`).

**Stops:** doubtful evidence sets `attention` on the PR candidate. The stop is sticky: nothing about this feature runs automatically until an operator resolves the cause and runs `feature.py github publish reset issue-<N> --apply`. Causes: a wrong or rewritten remote; a moved branch; a mismatched or ambiguous PR; a rejected push; a withdrawn approval before publication; merge evidence that does not hold. Transient problems are reported and retried, not sticky: network, GitHub 5xx, a busy lock, an unclean checkout, a PR closed without merge.

### 10.6 Adopting a PR opened by hand (Issue #25 / PR #27)

A PR a person opened before W4.3.3 existed is adopted like any other once it matches §10.3. For Issue #25: feature `issue-25` is in review with its candidate at `8094a9c`; PR #27 (`feature/issue-25` → `dev`, by `spyderbg`, head `8094a9c`) was merged as `f631c57`; the Issue is still open. One pass:

1. finds PR #27 among the branch's PRs and verifies it against the candidate;
2. records it (`origin: adopted`);
3. reads it again and verifies the merge (it pushes nothing and creates nothing);
4. completes `issue-25`, posts the completion comment, closes Issue #25 and moves its card to Done.

No new Issue and no new PR are created. This is tested with a disposable repository whose numbers are #25 and #27 (`test_a_manually_opened_and_merged_pr_completes_issue_25`), and against the real PR #27 response shape (`test_the_real_pr_27_matches_the_issue_25_candidate`). **It has not been applied to the real Issue #25.** That needs the supervised live test, with a local configuration outside the repository that sets `github.publish.enabled: true`.

### 10.7 Configuration (`github.publish`)

| Key | Default | Meaning |
| --- | --- | --- |
| `enabled` | `false` | The service runs publication passes, and `publish poll --apply` writes, only when `true` |
| `poll_interval_seconds` | `60` | How often the service checks at most (at least 10) |
| `git_timeout_seconds` | `120` | Bound for one `git push`, `ls-remote` or `fetch` (10–1800) |

Fixed, not configurable: Draft PRs only, the feature's base branch as the target, `origin` as the remote, no merge, no forced push. Unchanged: `github.write_scope: test-features-only` (an approved Issue's feature is inside it, as in W4.3.1), the authorization rules, Project #2 and the Project #1 protection. Enable it only in a local configuration outside the repository.

### 10.8 Commands

| Command | Effect |
| --- | --- |
| `github publish status [--json]` | Read-only, local: every approved Issue's feature with its candidate head, PR (number, origin, draft), push, merge evidence and stop |
| `github publish poll [FEATURE…] [--apply] [--json]` | One pass. Without `--apply` it reads GitHub (and nothing else) and lists what it would do. `--apply` needs `github.publish.enabled`. Exit 1 when something was deferred or needs an operator |
| `github publish reset FEATURE [--apply]` | Clears a stop after the operator resolved it; the next pass checks everything again |

### 10.9 Limitations

- **Supervised live verification only.** Real adoption (PR #27), automatic integration/push/Draft PR creation (PR #30) and merged-Issue closure (#25, #29) succeeded. Simulated crashes/network loss are verified in isolated tests, **not** against live GitHub. No unattended 24/7 soak was performed.
- **Merge commits only.** A squash or rebase merge stops for an operator. There is no command for a manual completion yet, so such a feature stays in review; the operator closes the Issue by hand.
- **The next feature starts from the local `dev`.** The coordinator fetches `dev` into FETCH_HEAD and the remote-tracking ref only, and never updates the local `dev` branch. Update it before the next feature is activated, or the next branch starts from an older base.
- **Detection delay:** a merge is seen at the next pass (up to `poll_interval_seconds`; the service may be idle longer between cycles).
- **One shared GitHub identity.** The coordinator pushes, opens the PR and closes the Issue as the same `spyderbg` login an agent can reach under `native-cli`. An agent could therefore also push to the feature branch or edit or merge the PR. A changed head stops completion, and a merge is accepted only with the verified head on `dev`, but nothing stops a merge itself. This is part of the accepted risk in §9.
- **No CI.** Remote CI is not configured; the PR states it.

### 10.10 Live verification record (2026-10-24)

| Scenario | Evidence and result |
| --- | --- |
| W4.3.2 / manual PR | Issue [#25](https://github.com/spyderbg/mx-admin-cloud/issues/25) approved as `issue-25.cli`, real agent verified head `8094a9c`; PR [#27](https://github.com/spyderbg/mx-admin-cloud/pull/27) was opened/merged **by a person**, merge `f631c5711803`. |
| W4.3.3 / adoption | After PR #28 (`0b62949`) was merged, `github publish poll issue-25` dry-run found PR #27 with 3 reads/0 writes. `--apply` adopted it, verified merge ancestry, recorded `done`, posted one completion comment, closed #25 (`completed`), and Project #2 showed `Done`. |
| W4.3.4 / first auto Draft PR | Operator approved Issue [#29](https://github.com/spyderbg/mx-admin-cloud/issues/29) using its exact spec SHA-256; Worker (real Claude Sonnet, foreground) verified `issue-29.cli` and automatically integrated/pushed `feature/issue-29`, then opened **Draft** PR [#30](https://github.com/spyderbg/mx-admin-cloud/pull/30) at head `932a9643f4d4`. The only changed file was `docs_agents/cli/w434-auto-pr-smoke.md`; required gates passed, GitGuardian passed. |
| W4.3.4 / human merge and closure | PR #30 was reviewed, marked ready and **merge-committed** to `dev` as `9861975d0d37`. The Worker had already stopped normally. One manual `github publish poll issue-29 --apply` later verified the merge, closed Issue #29 (`completed`), posted one completion comment and put its Project #2 item in `Done`. |

**Interpretation:** The full happy path is now **verified with real GitHub**, while interrupted live PR creation, GitHub API outages, 24/7 unattended operation, network and process failures remain W4.4 test candidates. Do not present the isolated recovery tests as live-failure evidence. The Coordinator does **not** auto-merge; merge must be human-authorized and be a merge commit, not squash/rebase.
