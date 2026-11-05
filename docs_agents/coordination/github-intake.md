# GitHub Issue Intake — Approved Issues as Worker Tasks (W4.3.1)

> Owner role: **coordination**. **W4.3.1–W4.3.4 VERIFIED WITH REAL GITHUB (2026-10-24, bounded supervised tests)** after the original offline implementation. Issue #25/PR #27 exercised manual PR adoption and verified closure; Issue #29/PR #30 exercised a real Claude Code Worker, automatic integration, branch push, Draft PR creation and post-merge closure. Implementation: PRs #26 and #28, merged into `dev`. `github.publish.enabled` defaults **false**, but was enabled in the operator's external test configuration. Shared GitHub identity/approval impersonation remains an accepted temporary risk (§9).
> Code: `feature_coordinator/intake.py` (records, approval grammar, digest, identity), `intake_poll.py` (the poll), `intake_commands.py`, `publish.py` and `publish_commands.py` (W4.3.3) · Tests: `feature_coordinator/tests/test_fc_intake.py`, `test_fc_publish.py` (W4.3.3) · Builds on: [github-integration.md](github-integration.md) (adapter, command authorization), [github-projects.md](github-projects.md) (Project #2), [worker-autonomy.md](worker-autonomy.md) (the Worker service).

A task can start as a GitHub Issue. Nobody writes a YAML file: an operator approves the Issue with a comment, and the approved text becomes one task for the existing coordinator and Worker. Before approval nothing happens. After approval the text is fixed.

W4.5 (§12, offline-tested, off by default) adds a label-driven path: `worker:plan` asks the Desktop Worker for a validated multi-task execution plan, published on the Issue, and `worker:approve` approves that exact Issue text and plan version without copying a digest.

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
   python -m feature_coordinator github intake show 25
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

The coordinator never replies to a rejected or ignored approval: before approval nothing is written to the Issue. `python -m feature_coordinator github intake poll` and the events (`intake-approval-rejected`, `intake-approval-ignored`) give the reason.

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

With `github.intake.enabled: true`, the W4 service (`python -m feature_coordinator --tracker github worker start`) polls at most every `poll_interval_seconds` (60 s), at the start of a cycle and never while an agent runs or while paused. You can also poll by hand: `python -m feature_coordinator github intake poll --apply`.

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
| While the agent runs (service, every poll interval) | fresh read | recorded; the run is stopped by the existing cancellation (as `python -m feature_coordinator stop`) | logged; the next boundaries check |
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
2. release the hold: `python -m feature_coordinator release issue-<N>.<role> --apply`.

A release while the Issue is still changed or closed is held again at the next boundary. To change the task, open a new Issue.
- **No duplicates.** There is one intake record per Issue number, created inside a state transaction. A second `/worker approve`, a repeated poll or a restarted coordinator changes nothing (`intake-duplicate-ignored`). Recovery after a crash at each step is tested (`test_a_restart_never_duplicates_the_task`).

## 7. Commands

| Command | Effect |
| --- | --- |
| `github intake show N [--json]` | Read-only: eligibility, digest, approval lines per role, intake status |
| `github intake poll [--apply] [--json]` | Discover approvals, plan and activate, verify approved texts. Without `--apply` it reads and reports only. `--apply` needs `github.intake.enabled` |
| `github intake status [--json]` | Read-only, local: every intake record with its feature and task state |

All of them are `python -m feature_coordinator github intake …`; `--tracker local` uses the local JSON tracker for rehearsals.

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
- **One task per `/worker approve`.** Multi-role work from one Issue needs a label-approved execution plan (W4.5, §12) or a planned feature (`python -m feature_coordinator plan`).
- **No `/feature hold|release` on approved Issues:** use the local commands. The coordinator closes the Issue only through W4.3.3 publication (§10), after the verified merge of its PR; with publication off, a person closes it.
- **Detection delay while an agent runs:** the service re-reads the Issue every `poll_interval_seconds`, and a run without the service (`python -m feature_coordinator launch`) is not re-read until it ends. An edit in that window can therefore be verified and then immediately withdrawn by the read after the claim is released. The verification never reaches the board or integration, but it does appear in the task's history (`verified → ready → needs-human`).
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

Until then, an operator can audit approvals with `python -m feature_coordinator github intake status` and the `intake-approved` events.

## 10. Automatic publication and completion (W4.3.3)

> Status (2026-10-24): **implemented; VERIFIED OFFLINE AND WITH REAL GITHUB**. Original disposable repositories, mock GitHub and bare-origin tests remain as regression evidence; a supervised live test exercised PR adoption (#27) and another exercised automatic push/Draft PR creation (#30), both followed by human merge/verified Issue closure. Off **by default** (`github.publish.enabled: false`), enabled only in the operator's local test configuration. Code: `publish.py`, `publish_commands.py`, `gitops.py`, `github_api.py`; publisher tests: 86 passed after PR #28 review fixes (reported test execution, not rerun here).

With `github.publish.enabled: true`, a feature made from an approved Issue goes the rest of the way by itself, up to the one step that stays human: the merge.

```text
approved Issue → task verified → integration barrier → push of feature/issue-<N> → ONE Draft PR → dev
   → a PERSON reviews, marks it ready and merges it (a merge commit)
   → merge verified → feature done → completion comment → Issue closed → Project #2 card Done
```

### 10.1 Lifecycle

The Worker service runs one publication pass at most every `poll_interval_seconds`, before its intake poll, between launches, never while paused. `python -m feature_coordinator github publish poll --apply` runs the same pass by hand. A pass handles **only features with an intake record**: nothing else is integrated, pushed or opened automatically. Each step first reads what is already true, so every step can be repeated:

| Feature state | The pass does | It refuses or waits when |
| --- | --- | --- |
| active | runs the existing **integration barrier** (`integrate`) once every task is verified | a task is not verified; a claim exists or a launch is still recorded in flight (waits); a withdrawn approval is known; the last integration failed and no task was verified since (an operator runs `python -m feature_coordinator integrate issue-<N> --apply` after fixing it). A failure publishes nothing |
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

**Stops:** doubtful evidence sets `attention` on the PR candidate. The stop is sticky: nothing about this feature runs automatically until an operator resolves the cause and runs `python -m feature_coordinator github publish reset issue-<N> --apply`. Causes: a wrong or rewritten remote; a moved branch; a mismatched or ambiguous PR; a rejected push; a withdrawn approval before publication; merge evidence that does not hold. Transient problems are reported and retried, not sticky: network, GitHub 5xx, a busy lock, an unclean checkout, a PR closed without merge.

### 10.6 Adopting a PR opened by hand (Issue #25 / PR #27)

A PR a person opened before W4.3.3 existed is adopted like any other once it matches §10.3. For Issue #25: feature `issue-25` is in review with its candidate at `8094a9c`; PR #27 (`feature/issue-25` → `dev`, by `spyderbg`, head `8094a9c`) was merged as `f631c57`; the Issue is still open. One pass:

1. finds PR #27 among the branch's PRs and verifies it against the candidate;
2. records it (`origin: adopted`);
3. reads it again and verifies the merge (it pushes nothing and creates nothing);
4. completes `issue-25`, posts the completion comment, closes Issue #25 and moves its card to Done.

No new Issue and no new PR are created. This is tested with a disposable repository whose numbers are #25 and #27 (`test_a_manually_opened_and_merged_pr_completes_issue_25`), and against the real PR #27 response shape (`test_the_real_pr_27_matches_the_issue_25_candidate`). **Historical note updated:** this was subsequently applied to real Issue #25 in the supervised W4.3.3 test; see §10.10. Publication was enabled only in the operator's external configuration.

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

- **Supervised live verification only.** Real adoption (PR #27), automatic integration/push/Draft PR creation (PR #30) and merged-Issue closure (#25, #29) succeeded in W4.3. W4.4 reran 4 transient API/network and 11 publisher recovery test cases, **offline with simulated GitHub failures**, not against a live outage. No unattended 24/7 soak was performed.
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

**Interpretation:** The W4.3 happy path was verified with real GitHub. W4.4 subsequently completed **bounded offline fault regressions** and a separate real-agent run **without GitHub calls**; live interruption of GitHub writes and 24/7 unattended operation are still **unverified** ([W4.4 record](w44-reliability-acceptance.md)). The Coordinator does **not** auto-merge; merge must be human-authorized and be a merge commit, not squash/rebase.


### 10.11 W4.4 publication reliability revalidation (2026-10-29)

The operator re-ran **4/4** selected Git/GitHub transient-error tests and **11/11** selected publisher-interruption tests on the W4.4 baseline. Verified cases include an unreachable Git remote, HTTP 503 on PR create, an uncertain PR-create response recovered by adoption, transient GitHub waits, an uncertain push, a crash after PR creation, **eight** crash points before/after push, create, completion, comment and Issue closure, and idempotent re-polling/restarts. The tests use **disposable local Git and mock GitHub**, checking exactly one PR and completion comment where applicable; **they do not reproduce a failure against the real GitHub service**.

W4.4's separate four-session real Claude/Codex smoke used a **no-remote** disposable repository and a GitHub shim that was never called. Its 69/69 success is evidence of agent and checkpoint recovery, **not** live GitHub outage handling. See [W4.4 acceptance](w44-reliability-acceptance.md). No new real GitHub Issue, PR or Project #2 item was created for W4.4 testing.

## 12. Label-driven planning and approval (W4.5)

> Status: **implemented and tested offline only** (mock GitHub, fake planner, the Action's real receipt code against the mock). Not activated: `github.planning.enabled` defaults **false**, and the workflow runs only once it is on the default branch (`main`). Code: `feature_coordinator/plan_digest.py` (canonical JSON, digests, markers; standard library only), `planning.py` (plan schema, deterministic validation, plan record, rendering), `planner.py` (the AI Feature Planner), `intake_plan.py` (the labels), `planning_commands.py`; `.github/workflows/worker-issue-labels.yml` and `.github/scripts/worker_issue_labels.py`. Tests: `feature_coordinator/tests/test_fc_issue_planning.py`.

A second way to approve an Issue, next to `/worker approve` (§2), which stays unchanged. The person never chooses a role or copies a digest: the Desktop Worker proposes an execution plan, the person reviews it on the Issue and approves it with a label.

### 12.1 The labels

| Label | Set by | Meaning |
| --- | --- | --- |
| `worker:plan` | a person | Plan this Issue (again). Each new label event is one request. |
| `worker:ready-for-review` | the coordinator | Plan version N is published on the Issue and waits for review. |
| `worker:approve` | a person | Approve exactly the current Issue text and the newest published plan version. |
| `worker:approved` | the coordinator | The approval was validated; the feature is planned (and activated when the checkout is free). |

`python -m feature_coordinator github planning labels --apply` creates the four labels once (existing labels are never edited).

### 12.2 What a person sees

1. Open and discuss the Issue as usual. Nothing happens.
2. Add **`worker:plan`**. Within a poll interval the Worker posts **one** comment, "Execution plan vN": the feature title and acceptance criteria, a table of tasks (ID, title, role, dependencies, gates), each task's instructions and intended paths, the integration gates, the plan digest, and how to approve or revise. `worker:plan` is replaced by `worker:ready-for-review`.
3. Review it. To change it, edit the Issue if needed, then remove and re-add `worker:plan`: a new version is published; any earlier approval no longer applies. Editing the Issue's title or body makes the published plan **stale** (one notice; `worker:ready-for-review` removed).
4. Add **`worker:approve`**. The workflow posts an *Approval receipt* (as `github-actions[bot]`); the Worker validates it, records the approval, replaces the labels with `worker:approved`, posts one acknowledgement and plans the feature with the plan's tasks. From here on it is the ordinary intake lifecycle (§3–§6, §10): recorded text, fail closed on any change, one feature per checkout, Draft PR, human merge.

A refused request or approval gets **one** notice saying why, and its label is removed. If the planner cannot assign the work within the role policies (a disabled role, a protected or approval-required path, unclear scope), it is **not** forced: the Issue gets a "Plan needs an operator" notice and nothing is published.

Example single-task plan (Issue "Export category items as CSV"):

| Task | Role | Depends on | Gates |
|---|---|---|---|
| `cli-export` — Add `content export-items` | cli | — | `cli-lint`, `docs-check` |

Example multi-role plan (Issue "Category export across the stack"):

| Task | Role | Depends on | Gates |
|---|---|---|---|
| `api` — CSV export endpoint | backend | — | `backend-tests`, `backend-lint`, `docs-check` |
| `ui` — Export button | frontend | `api` | `frontend-type-check`, `frontend-build`, `docs-check` |
| `cli` — `content export-items` | cli | `api` | `cli-lint`, `docs-check` |
| `docs` — Coordination notes | coordination | `api`, `cli`, `ui` | `docs-check` |

### 12.3 Planning (Desktop Worker only)

The intake poll (Worker service, or `github intake poll --apply`) runs at most **one** planner invocation per poll, after approvals are processed. The planner is the installed **Claude Code with the operator's subscription login**, under the native backend's rules: allowlisted environment (no API key, token or base URL is passed), `claude auth status` must report the claude.ai subscription before the request, and the session's `init` event must show `apiKeySource: none` afterwards. It gets **no tools** (`--tools ""`, `--permission-mode dontAsk`, `--restricted`, `--strict-mcp-config`, no session persistence) and runs in an empty directory under the state directory, so planning cannot read or change the checkout; the coordinator puts the Issue text, the available roles with their owned/excluded paths and required gates, the protected and approval-required paths, the gate list and an excerpt of `AGENTS.md`/`CLAUDE.md` into the prompt. The Issue text is marked as data. While it runs, the service record keeps heartbeating, and a stop signal, `worker stop` or `worker pause` ends the run (the request is retried later). No GitHub-hosted runner ever runs a model.

Every proposal is validated deterministically (`planning.canonical_plan`); nothing the model says is trusted:

- exactly the documented keys — `grants`, `steps`, `provides_contract` or any other key is refused (a plan cannot widen a permission);
- 1..`max_tasks` tasks, unique IDs, **at most one task per role** (no splitting for its own sake);
- every role enabled and with intake gates (the hand-approvable roles; `automation` is disabled);
- every intended path an exact path or `<dir>/**` inside the role's owned paths, outside its exclusions, never protected or approval-required;
- dependencies on known tasks, no self-dependency, no cycle;
- the role's intake gates always present (first, in configured order); a plan may only **add** configured gates. Integration gates are the coordinator's (`contract-check`, `docs-check`).

A refused proposal is fed back to the planner once (`max_attempts: 2`), then the request needs an operator. Attempts are counted **before** each run, so a crash loop cannot exceed the budget; an unavailable planner (not logged in, API-key login) defers without using an attempt.

### 12.4 The approval digest

`plan_digest.py` serializes canonically (UTF-8 JSON, sorted keys, no whitespace, no floats). The **plan digest** is the SHA-256 of the canonical plan, which carries its schema (`mx-feature-plan/v1`), **version**, repository, Issue identity (number, database ID, node ID), the Issue's spec digest at planning time, the feature and the tasks. The **approval digest** is the SHA-256 of the approval statement (`mx-worker-approval/v1`): repository name and ID, Issue identity, the exact normalized title and body with the legacy spec digest, the plan's schema, version and digest, every task's role, dependencies and gates, and the integration gates. Nobody types it; it is shown as `mx-worker-approval/v1:sha256:<hex>`.

### 12.5 What the coordinator validates before anything is planned

The receipt is **evidence only**. `github-actions[bot]` is **not** added to `command_authors`; only a well-formed receipt from `receipt_author` is read, and the approval is accepted only if all of these hold, re-read at validation time:

- exactly one receipt for the newest `worker:approve` label event; repository (name, ID) and Issue (number, database ID, node ID) match;
- GitHub's own Issue events show that label event (ID, actor, time), the label is still present, and no newer `worker:plan` request exists;
- the actor is a command author with at least `command_min_permission` **now**;
- the receipt names the latest version, its digest and its published comment; that comment still carries exactly the recorded plan; the plan was published before the label was added, is still `published`, and is still valid under the current configuration;
- the Issue's title and body are unchanged since planning; the locally recomputed approval digest equals the receipt's;
- the receipt comment was never edited (its `updated_at` equals its `created_at`: anyone with write access can edit a bot's comment, and GitHub keeps the bot as author);
- the Actions run named by the receipt exists, ran `.github/workflows/worker-issue-labels.yml` for an `issues` event on the default branch, triggered by the same actor, and was **created between the label event and the receipt** (5 s tolerance for GitHub's whole-second timestamps), so an older run of the workflow cannot be reused;
- **one run, one receipt:** no other receipt on the Issue names the same run, and no approval accepted before (any Issue, local intake records) was backed by it.

Anything else — forged, stale, malformed, mismatched, edited, a receipt from a person, two receipts, a reused or out-of-window run, no receipt after `receipt_wait_seconds` — refuses the approval with one notice; nothing is recorded or started. A valid approval becomes `intake/issue-<N>.json` with **schema 2** (`plan`, `task_ids`, `anchor`, `integration_gates`), written before anything else.

### 12.6 The GitHub Action

`.github/workflows/worker-issue-labels.yml` runs on `issues: labeled`, only for `worker:plan` and `worker:approve`, with `permissions: {}` at the top and `contents: read`, `issues: write` for its one job, a sparse, credential-less checkout pinned by commit SHA, and one step: `python3 -I .github/scripts/worker_issue_labels.py` (standard library plus `plan_digest.py`). Nothing from the event is placed on a command line. For `worker:plan` it only refuses (and removes) a request from an account below `write`. For `worker:approve` it checks the newest plan comment (by `WORKER_PLAN_AUTHORS`, default the repository owner — the Worker's `gh` account) against the Issue **as it was when the label was added** (the event payload) and posts one receipt, or one refusal and removes the label. Comments and labels written with the job token trigger no further workflow runs.

**Which label event.** The event payload carries no event ID, so the step answers the newest `labeled` event of its label. GitHub's events API can lag the webhook, so the newest *visible* event may be an earlier one that was already answered. An answered event (or a newer one by someone else) therefore never ends the wait: the step looks again after 5, 10, 15, 20 and 30 s for an **unanswered** event by the label's sender. If none appears it posts nothing and the job **fails** with the reason (re-run it once the event is visible; the coordinator refuses a label without a receipt after `receipt_wait_seconds`). Every receipt and refusal names its label event and its workflow run, so a re-run of the run that answered (same run ID, any attempt) recognizes its own answer and succeeds without posting; a second, different run for an already-answered event (a duplicate delivery) fails explicitly and posts nothing. No receipt or refusal is ever posted twice.

**Concurrency.** The `concurrency` group is set on the **job**, so only events that pass the job's condition (`worker:plan` or `worker:approve` on an Issue) join it; other labels, including the two the coordinator sets, never queue and cannot cancel approval work. The group is per Issue **and** label: runs that could answer the same event are serialized, a plan request never replaces a pending approval, and a pending approval run can only be replaced by a newer approval run on the same Issue, which answers the newest approval event — the only one the coordinator considers.

### 12.7 Compatibility, recovery, idempotency

- `/worker approve <role> <spec-sha256>` works exactly as before and keeps writing **schema 1** records with exactly the schema-1 fields, so the current Worker (or a rollback) reads them; schema-1 records written earlier load unchanged. Schema-2 records (label approvals) and `plan/` records are **not** readable by pre-W4.5 code: see §12.11 before any rollback. With `github.planning.enabled: false` (the default) the labels are ignored and nothing here runs.
- A planned feature uses the established lifecycle: `workflow.plan` (DAG validated), activation, claims, checkpoints, Git ownership checks, retries, the fail-closed Issue checks for **every** task, automatic Draft PR (integrated with the approved integration gates), Project #2. The approved Issue belongs to the **feature**: its tasks get no Issues of their own, the queued comments of the feature and all tasks go to the approved Issue, and its Project #2 card shows the feature's status (Blocked while a task needs a person).
- Write-ahead everywhere: the request and each planner attempt are recorded before the run; a plan version before its comment; the comment carries an operation marker and is found again after a crash; the approval record before the labels. Repeated polls, duplicate label events, repeated approvals and restarts create no second plan, comment, receipt, record or feature.

### 12.8 Configuration (`github.planning`)

`enabled` (false; needs `github.intake.enabled`), `provider` (`claude`, must be Claude Code), `model` (`opus`), `timeout_seconds` (900), `max_attempts` (2), `max_tasks` (4), `max_instructions_characters` (6000), `receipt_author` (`github-actions[bot]`, must be a bot login), `receipt_workflow`, `receipt_wait_seconds` (900), `conventions_files` (`AGENTS.md`, `CLAUDE.md`), `max_conventions_characters` (6000). See [project-config.example.yaml](project-config.example.yaml).

Commands: `github planning status`, `github planning show N` (read-only, local), `github planning labels [--apply]`; `github intake show N` also shows the plan state.

### 12.9 Limitations and actual guarantees

- **Shared identity (accepted risk, unchanged, §9).** The Worker and the operator share the `spyderbg` login. A `worker:approve` label added with that token is indistinguishable from the operator's, so the actor and permission checks do **not** prevent approval impersonation; nothing in W4.5 changes that, and no check below claims to. The coordinator itself never adds `worker:approve`. Agent sessions do not get the login while `runtime.native_hardening` is on (it hides `.config/gh`); with it **off — the production setting today** — an agent with shell access under the operator's HOME may reach the `gh` login and could add the label, exactly as it could post `/worker approve` (§9). Closing this needs a separate Worker identity; credentials, accounts and hardening are deliberately left as they are.
- **What the receipt checks guarantee.** `github-actions[bot]` is shared by every workflow of the repository, on any branch: any workflow with `issues: write` can post as that bot, and anyone with write access can edit a bot comment. The receipt is therefore never the authority. What the coordinator verifies itself (§12.5) — GitHub's own label event, the actor's command-author status and permission now, the recorded and published plan, the current Issue text, the recomputed approval digest — decides. The run checks add that an accepted receipt names a run of this workflow on the default branch, by the approver, **created for this approval** (between label and receipt), **unedited**, and backing **no other receipt or approval**. They cannot prove that this run posted the comment: a receipt forged by another workflow could still name the genuine run of the same approval while it has not posted yet. Forging needs write access (to push a workflow), which today only the shared identity has, and a forged receipt can at most corroborate facts the coordinator checks anyway; the one Action check it could skip is "the Issue text at the moment of labeling" (the coordinator checks the current text).
- **An edited receipt is refused** even when the edit changes nothing visible (GitHub reports a new `updated_at`). Re-add `worker:approve` to get a fresh receipt.
- **GitHub is trusted for facts** (label events, permissions, run metadata and timestamps); a compromised GitHub account defeats every check here.
- **The plan is model output.** Validation bounds roles, paths and gates, and a person approves it, but the instructions themselves are only as good as the review. Intended `paths` are guidance: a `<dir>/**` subtree may contain protected paths, which the role policy still refuses at run time.
- **Offline only.** No real Claude Code planning run and no real Actions run were made for W4.5.

### 12.10 Operational impact: the planner runs inside the Worker's loop

A planner invocation runs synchronously inside the Worker service's GitHub poll (`_poll_github`, before the active feature's step). While it runs, the service keeps heartbeating and honours stop, `worker stop` and `worker pause` (a paused service does not poll at all), but **the active feature does not advance**: no task is launched, settled or retried. One poll can take up to `max_attempts × timeout_seconds` — 2 × 900 s = **30 minutes** with the defaults — and at most one plan request is planned per poll. Agent runs are not affected mid-run (they are not polled concurrently).

Conservative settings for the first supervised trial:

```yaml
github:
  planning:
    enabled: true
    max_attempts: 1          # one subscription request per plan request; a refused plan goes to an operator
    timeout_seconds: 300     # a no-tools planning answer normally takes well under a minute
    max_tasks: 2             # start with single- and two-role plans
```

Trial with no feature active, so a long planner run delays nothing. Raise the values only after observing real planning times.

### 12.11 Rollback to pre-W4.5 code

`/worker approve` records (schema 1) stay readable by older code. Records written by label approvals are not: pre-W4.5 code refuses `intake/issue-<N>.json` with **schema 2** (strict loader) and does not know `plan/issue-<N>.json`. Before reverting the code:

1. Set `github.planning.enabled: false` and restart (labels are ignored from then on; this alone needs no code rollback).
2. Let every planned feature finish (Draft PR merged, Issue closed) or stop it (`python -m feature_coordinator stop <task>` for running tasks, then hold them).
3. With the service stopped, move every intake record with `"schema_version": 2` and the whole `plan/` directory out of the state directory (keep them as evidence; do not edit them), together with the local features they created if those must not be resumed by the old code.
4. Revert the code, start the service and check `github intake status`.

Re-applying W4.5 later reads the archived records again if they are moved back.

### 12.12 Before activation: compare the shared approval code on `main` and `dev` (read-only)

The Action runs `main`'s copy of `plan_digest.py`; the Worker runs its own checkout's (`dev`). The two must be byte-identical, or every approval is refused with a digest mismatch (fail closed, but confusing). Check, without changing anything:

```sh
git fetch origin
for f in feature_coordinator/plan_digest.py .github/scripts/worker_issue_labels.py \
         .github/workflows/worker-issue-labels.yml feature_coordinator/__init__.py; do
  printf '%-48s main=%s dev=%s\n' "$f" "$(git rev-parse --short origin/main:"$f" 2>/dev/null || echo MISSING)" \
         "$(git rev-parse --short origin/dev:"$f" 2>/dev/null || echo MISSING)"
done
git diff --stat origin/main origin/dev -- feature_coordinator/plan_digest.py .github/scripts/worker_issue_labels.py \
         .github/workflows/worker-issue-labels.yml      # must print nothing
git -C <worker checkout> rev-parse HEAD:feature_coordinator/plan_digest.py   # the Worker's copy: same blob
```

Every pair of blob IDs must match (until step 2 of §12.13, `main` shows `MISSING` for the three W4.5 files). Repeat after every release that touches `plan_digest.py` or the Action, and after every dev → main merge.

### 12.13 Production activation (operator steps; not done by W4.5)

1. Review and merge the W4.5 PR into `dev`; update the Worker's checkout as usual (pause → stop → fast-forward → start → resume). Planning stays disabled.
2. Bring `.github/workflows/worker-issue-labels.yml`, `.github/scripts/worker_issue_labels.py` and `feature_coordinator/plan_digest.py` to `main` **without deploying**: a commit reaching GitLab `main` deploys production, so use a dedicated `[skip ci]` commit that copies exactly those three files from `dev` (never fast-forward `main` to `dev` for this). Then run the read-only comparison of §12.12.
3. Create the labels: `python -m feature_coordinator github planning labels --apply`.
4. Optionally set the repository variable `WORKER_PLAN_AUTHORS` (default: the repository owner, which is the Worker's `gh` account).
5. Enable `github.planning` in the Worker's project configuration with the conservative settings of §12.10 (intake must be enabled) and restart the service.
6. Run one supervised test on a throwaway Issue, with no feature active: `worker:plan` → review → `worker:approve`; check the receipt and its run, `github planning status` and `github intake status`.
