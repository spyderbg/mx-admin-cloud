# W4.4 — Reliability & Recovery Testing: acceptance record (2026-10-29)

> **Migration note (2026-10-30):** this record is kept as written. Its commands use `python scripts/feature.py` and `scripts/tests/feature_coordinator/`; the CLI is now `python -m feature_coordinator`, run from the checkout root, and the tests are in `feature_coordinator/tests/` ([operations §12](operations.md#12-feature-coordinator-relocation-updating-a-worker-host-2026-10-30)).

> Owner role: **coordination**. **CLOSED — bounded supervised acceptance VERIFIED.**
> This is a verification/documentation milestone over existing W4 implementation, **not** a claim of proven 24/7 unattended reliability and **not** a new production-code implementation. Baseline: `dev` after documentation PR #31 (`06070029`).
> Source of the observations: operator-supplied pytest output (2026-10-29), `soak-report.json` and the successful `fc_worker_smoke.py` JSON `report.json`. Historical W4.1–W4.3 evidence is preserved in [phase-status.md](phase-status.md).

## Scope and outcome

| Stage | Verification | Observed result |
| --- | --- | --- |
| W4.4.1 — Worker Crash & Recovery | One interrupted checkpoint recovery; three bookkeeping crash cases (`before-spawn`, `after-spawn`, `before-outcome`) | **4/4 passed** (pytest; fake agent, real subprocesses, disposable Git) |
| W4.4.2 — GitHub API & Network Failure Recovery | Unreachable Git remote, failed PR create, lost PR-create response, temporary GitHub API failure | **4/4 passed** (simulated GitHub responses, local Git remote) |
| W4.4.3 — Validation & Retry Policy | Transient backoff; bounded retries; validation attention; bounded optional auto-fix; persisted retry schedule; unknown failure; blocked gate environment | **7/7 passed** (offline/in-process scenarios, simulated clock as appropriate) |
| W4.4.4 — Publication Recovery | Interrupted push, crash after PR creation, eight deterministic crash points, idempotent completion over restarts | **11/11 passed** (disposable Git remote and mock GitHub; no live GitHub faults) |
| W4.4.5-A — Extended Offline Soak | Real Worker process with 48 fake-agent tasks and a 180-second idle period | **10/10 checks passed**, exit 0 |
| W4.4.5-B — Real-Agent Acceptance | Controlled stop after Backend step 1; restart; real Claude Code Opus/Sonnet and Codex; checkpoint/dependency/gate/process assertions | **69/69 checks passed**; verdict `VERIFIED` |

**Regression subtotal: 26/26 selected pytest cases passed** (4 + 4 + 7 + 11). These are selected existing tests, **not** a fresh full-suite run, and are **not** 26 new tests. All outcomes were reported by the operator on 2026-10-29. No project source-code change was required to obtain these results.

## Reproduction of selected regression cases

The following pytest invocations select exactly the cases used by the operator. They use disposable local Git repositories, fake agents and/or mock GitHub, **not live remote writes**. Keep `-p no:cacheprovider` for a read-only main checkout.

```bash
# W4.4.1 — 4 cases (1 + 3 parametrizations)
python -m pytest -p no:cacheprovider -v \
  scripts/tests/feature_coordinator/test_fc_service_process.py::test_c_restart_continues_from_the_last_checkpoint \
  scripts/tests/feature_coordinator/test_fc_service_process.py::test_q_crash_during_bookkeeping

# W4.4.2 — 4 cases
python -m pytest -p no:cacheprovider -v \
  scripts/tests/feature_coordinator/test_fc_publish.py::test_an_unreachable_remote_defers_and_recovers \
  scripts/tests/feature_coordinator/test_fc_publish.py::test_a_failed_pr_creation_is_retried_without_duplicates \
  scripts/tests/feature_coordinator/test_fc_publish.py::test_a_lost_create_response_is_adopted_not_repeated \
  scripts/tests/feature_coordinator/test_fc_publish.py::test_github_failures_while_waiting_are_transient

# W4.4.3 — 7 cases
python -m pytest -p no:cacheprovider -v \
  scripts/tests/feature_coordinator/test_fc_autonomy.py::test_d_transient_failure_is_retried_with_bounded_backoff \
  scripts/tests/feature_coordinator/test_fc_autonomy.py::test_d_transient_retries_are_bounded \
  scripts/tests/feature_coordinator/test_fc_autonomy.py::test_f_validation_failure_needs_an_operator_by_default \
  scripts/tests/feature_coordinator/test_fc_autonomy.py::test_f_validation_auto_fix_is_bounded \
  scripts/tests/feature_coordinator/test_fc_autonomy.py::test_l_retry_schedule_survives_a_restart \
  scripts/tests/feature_coordinator/test_fc_autonomy.py::test_m_unknown_error_is_not_retried \
  scripts/tests/feature_coordinator/test_fc_autonomy.py::test_a_gate_that_could_not_run_is_an_environment_failure_with_its_cause

# W4.4.4 — 11 cases (1 + 1 + 8 parametrizations + 1)
python -m pytest -p no:cacheprovider -v \
  scripts/tests/feature_coordinator/test_fc_publish.py::test_repeated_and_interrupted_pushes_are_safe \
  scripts/tests/feature_coordinator/test_fc_publish.py::test_a_crash_right_after_github_created_the_pr_never_duplicates_it \
  scripts/tests/feature_coordinator/test_fc_publish.py::test_every_interruption_is_recovered_without_duplicates \
  scripts/tests/feature_coordinator/test_fc_publish.py::test_repeated_completion_and_restarts_write_nothing
```

## Offline soak evidence (W4.4.5-A)

Command used (the harness creates a disposable repository and a fake HOME):

```bash
WORKDIR="$(mktemp -d /tmp/mx-w44-soak.XXXXXXXX)"
python scripts/tests/feature_coordinator/fc_service_soak.py \\
  --workdir "$WORKDIR" --tasks 48 --failure-every 4 --idle-seconds 180
```

The operator's `soak-report.json` recorded: **48/48 verified tasks**; **60 agent runs** (12 expected transient failures, then recovery); **one Git commit per task**, none duplicated; at most **one** claim; **zero** leftover claims or persistent child processes while idle; service ended `OFFLINE` with exit code **0**. The measured duration was **513 seconds** (approximately 332 seconds to verification and 180 seconds idle). The instrument collected **504 samples** and **931 events**. Peak RSS was **30,064 KiB** (~29.4 MiB); file descriptors ranged **3–11**; maximum threads **1**. Every one of the harness's **10 checks was true**.

These bounded observations **do not prove memory stability over days**, and the agents were fake; they do exercise the real service process and Git/checkpoint paths.

## Real-agent acceptance evidence (W4.4.5-B)

A dry-run `python scripts/tests/feature_coordinator/fc_worker_smoke.py --agent real` reported a plan without model requests. After explicit operator approval, the operator ran:

```bash
python scripts/tests/feature_coordinator/fc_worker_smoke.py --agent real --apply
```

The harness still prints the historical label `W4.2 real-agent smoke test`; this **new 2026-10-29 execution** is accepted as the W4.4.5-B revalidation, not a claim of a newly built harness. The provided successful JSON report had `verdict: VERIFIED`, `passed: true`, **69/69 checks** (**8 preflight, 28 outcome, 33 safety**), no primary failure, no other finding, and no safety violation.

| Evidence | Observation |
| --- | --- |
| Agents and routing | **Four sessions**: Backend Claude `opus` twice, CLI Claude `sonnet` once, Frontend Codex **CLI default** once; all exited 0. Claude sessions reported Opus/Sonnet families and subscription authentication, no API-key substitution. The actual Codex model was not independently established. |
| Service 1 | Completed and committed Backend `module` checkpoint, then stopped **gracefully at the boundary** (`OPERATOR_STOP`); no live model call was interrupted. |
| Service 2 | Adopted the **unchanged** Backend checkpoint, executed only `validation-test`, CLI and Frontend; exited `NATURAL_IDLE_EXIT` after work was verified. |
| Isolation and correctness | One active agent at a time; no overlapping run-tagged process; 4 distinct committed checkpoint commits; all tasks verified; all three validation gates passed; dependency order correct; no repeated step. |
| Health and cleanup | Healthy session heartbeats; final `OFFLINE`; no remaining claims, locks, agent/launch processes, or pending checkpoint transactions. |
| GitHub and scope | Disposable repository **without a remote**, local tracker; GitHub shim was never called. The test did **not** test live GitHub API failures. |

The test-only harness uses actual Claude Code and Codex CLIs in a **bounded foreground** run. This is neither a hard OS credential boundary nor an unattended overnight/24-hour soak. The user-supplied raw report remains **outside the tracked repository**, since it includes host/process details; this summary records the relevant, non-sensitive evidence.

## Accepted exit gates and remaining risk

**Accepted for closure:** the specified crash/recovery, backoff, validation, publisher idempotency, bounded soak, and supervised real-agent scenarios passed. W4.4 is **closed as bounded supervised verification**; no code changes or new GitHub Issue/PR were needed for the tests themselves. This document is intended for a separate documentation PR and does not itself change running services.

**Not verified; do not advertise 24/7 readiness:**

- Actual GitHub API downtime, lost response or abrupt process termination during a **real** live push/PR creation/Issue closure; these failures were injected **only** in offline publisher tests.
- Days of unattended Worker operation, host reboot and automatic recovery via an **installed** systemd user unit, power loss or suspend/resume; the template does not establish that the unit is deployed.
- Real subscription exhaustion/error texts and long-running model latency; Codex's effective underlying model is not proven by the CLI default setting.
- Full credential/network isolation (W1 security work remains separate, Draft PR #20); the shared GitHub identity/approval-impersonation risk is accepted temporarily, **not fixed**.
- Squash/rebase merge completion, remote CI as a general acceptance gate, and any multi-machine/distributed recovery.

**Next proposed milestone (not authorized or started):** W4.5 — unattended operation and 24/7 stability: explicit operator approval, systemd/host-reboot checks, measurable multi-day soak, monitoring/alerts, bounded live GitHub fault scenarios with disposable Issues and clear cleanup, and security-risk review. Keep Project #1 and protected branches untouched, and do not start a live worker or make GitHub writes as part of closing these documents.
