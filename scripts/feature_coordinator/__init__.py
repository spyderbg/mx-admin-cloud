"""Feature coordinator — the v5 multi-agent feature-development workflow (Phase 2 skeleton).

One Linux host, one shared Git checkout and one `feature/<slug>` branch per feature,
parallel role writers with disjoint write scopes, every Git mutation serialized through
one coordinator, and exactly one final pull request per completed feature. See
docs_agents/coordination/feature-workflow.md for the design and
docs/multiple_agents/agents_prompt_v5.md for the governing assignment.

This package is deliberately independent of scripts/mxfix/ and scripts/mxissues/ (a
separate bug-finding/repair system) and of the backend: it imports neither, and a test
enforces that. Phase 2 ships mocks only — no agent runner, GitHub API or remote model
endpoint is ever contacted.
"""

__version__ = "0.2.0"
