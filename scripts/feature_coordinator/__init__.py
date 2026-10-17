"""Feature coordinator — the v5 multi-agent feature-development workflow (Phases 2–4).

One Linux host, one shared Git checkout and one `feature/<slug>` branch per feature,
parallel role writers with disjoint write scopes, every Git mutation serialized through
one coordinator, and exactly one final pull request per completed feature. See
docs_agents/coordination/feature-workflow.md for the design and
docs/multiple_agents/agents_prompt_v5.md for the governing assignment.

This package is deliberately independent of scripts/mxfix/ and scripts/mxissues/ (a
separate bug-finding/repair system) and of the backend: it imports neither, and a test
enforces that.

Phase 3 adds real Claude Code and Codex launchers, bubblewrap containment per role
(sandbox.py), process supervision (supervisor.py), sandboxed gates with D-19 lint baselines
(gates.py, lint_baseline.py) and a loopback-only OpenAI-compatible transport for the remote
Qwen model (http_transport.py). No request is ever sent to the remote Qwen server: live
remote execution stays disabled.

Phase 4 adds GitHub Issue coordination: a real adapter over the authenticated `gh` CLI
(github_api.py), versioned ownership markers and the mock tracker (issues.py), idempotent
and recoverable synchronization with validated operator commands (issue_sync.py), human
escalation (escalation.py) and the live acceptance scenario (live_test.py). The local state
stays authoritative; GitHub writes are limited to test features, and no pull request is
ever created in this phase.
"""

__version__ = "0.4.0"
