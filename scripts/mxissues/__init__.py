"""Issue tracking (Workstream 2) — replaces the origin document's tasks.sqlite with a real
issue tracker: GitLab or GitHub, selected by testenv.toml's [issues] tracker (see
mxissues/tracker.py for the interface, mxissues/trackers/ for the backends). See
scripts/issues.py for the CLI and docs/test_environment/implementation_plan.md
Workstream 2 for the original design.
"""
