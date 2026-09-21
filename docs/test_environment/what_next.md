# What's Next / Known Gaps

## Known Gaps

### Batch Cross-Invocation Retry Counter
A named, deliberate gap: batch has no cross-invocation retry counter. A candidate that batch isolates as the culprit (via its bisection fallback) just goes back to `status::open` with a comment — there's no persistent attempt-count mechanism across separate batch invocations the way heal's in-process retry loop has. This is documented as a known gap, not silently missing.

---

## Explicitly Out of Scope

Per the plan itself (not Workstream 3 tasks, called out as follow-ups):

- **No LLM detector/critic** — `sources/detector.py` defines the hand-off shape, but findings only ever come from deterministic checks (pytest/pylint/e2e), never an LLM-driven detector.
- **No native GitLab merge requests** — fixes land as GitHub PRs only, since the GitHub→GitLab mirror is push-only; the GitLab issue links across to a GitHub PR rather than a native MR.
- **Four provider implementations** (`elevenlabs_tts`, `static_tts`, `openai_tts`, the genai SDK) can't be redirected to the stub without a source change — flagged for separate approval.
- **No auth/RBAC test suite** — the sandbox stays permissive by default.
- **No cli/ / backend-cli UDS profile test harness.**
- **Docker mode has no backend hot-reload** — needs `--build` after every backend change.
- **Worktree `.venv`/`node_modules` aren't cached/shared** across heal/batch worktrees — each attempt pays a from-scratch install cost; flagged as a possible follow-up.

---

> **Note:** I can't re-verify current file state from here since this is a side question with no tool access — if you want a fresh check against the actual repo, that'd need to happen in the main conversation.