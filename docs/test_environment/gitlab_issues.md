# GitLab issue tracking (Workstream 2)

`scripts/issues.py` replaces the origin document's `tasks.sqlite` (§2) with the
project's real GitLab issue tracker — see
[implementation_plan.md](implementation_plan.md) Workstream 2 for the full design and
findings. This is the operational reference: label schema, the state machine, the
ownership rule, and the CLI.

## Setup

Three keys in the repo-root `.env` (gitignored):

```
GITLAB_HOST=http://82.103.77.45          # or http://127.0.0.1:8080 via an SSH tunnel
GITLAB_PROJECT=marxman/mx-admin
GITLAB_TOKEN=glpat-...                    # project access token, role Developer, scope api
```

The instance has no TLS — a token crosses the network in the clear unless you tunnel
(`ssh -L 8080:localhost:80 <user>@82.103.77.45`, then `GITLAB_HOST=http://127.0.0.1:8080`).
`doctor` warns on every run when `GITLAB_HOST` is plain HTTP and not loopback; it's a
warning, not a hard stop, since direct is a valid (if riskier) choice you can make
explicitly. See `.env`'s own comments for both forms.

Install `python-gitlab` via `scripts/requirements.txt` (already wired into
`scripts/setup_initial.py`), or `pip install -r scripts/requirements.txt` directly into
whichever venv you're running `issues.py` from.

**`doctor` runs first, always** — it's the only thing standing between a typo'd project
path and a pile of issues filed into the wrong repository. It also reports the GitLab
version and edition (this instance: 18.9.1, **Enterprise**, confirmed via `/version`'s
`enterprise` field) — Community vs Enterprise/Premium decides whether GitLab enforces
scoped-label (`status::*`) exclusivity server-side, but this tool enforces it
client-side regardless (see [State machine](#state-machine) below), so it behaves
identically either way.

## Label schema

`issues.py init` creates these idempotently:

| Label | Purpose |
|---|---|
| `status::open` / `status::in-progress` / `status::done` / `status::needs-human` | the workflow |
| `source::detector` / `::e2e` / `::pytest` / `::pylint` / `::manual` | provenance |
| `severity::high` / `::medium` / `::low` | from the detector schema / sync's severity mapping |
| `mx-auto` | ownership marker — see [Ownership rule](#ownership-rule) |

`status::needs-human` is the origin document's `MAX_FIX_ATTEMPTS` escape valve — without
it a stuck task has nowhere to go but silently back to `open`. Nothing in this
workstream sets it automatically yet (that's Workstream 3's job, once a fixer gives up
after its retry budget); `issues.py needs-human <iid>` sets it by hand today.

## State machine

Exactly one `status::*` label at a time, enforced **client-side**: every transition
computes the full label set and sends it as one replacement (`issue.labels = [...]`),
never `add_labels`/`remove_labels` (separate calls that can race and leave two
`status::` labels set). Every transition re-fetches the issue by `iid` afterward and
asserts exactly the target status label survived — `mxissues/state.py`'s
`TransitionError` if it didn't. That read-back is the verification GitLab Free/Premium
licensing doesn't give you client-side, and it costs one cheap GET.

```
status::open <-> status::in-progress -> status::done (closes the issue)
       ^                                      |
       +---------- reopened as a regression --+
                 (sync finds a recurrence on a done/closed issue)
       \-> status::needs-human (a fixer gave up — Workstream 3)
```

`done` sets `state_event: close`; transitioning *off* `status::done` on a closed issue
sets `state_event: reopen` — so `reopen`/a regression on a closed issue both reopen it
in GitLab, not just relabel it.

**The mirror race:** the GitHub→GitLab mirror is one-way and push-only (`main` + tags).
When it pushes to GitLab's default branch, GitLab parses those commit messages for
`Closes #N` and closes the issue *itself* — useful, but it races with `issues.py done`,
and either way it never touches the `status::` label. `issues.py reconcile` sweeps every
tool-owned issue whose GitLab state is `closed` but whose label isn't `status::done` and
corrects it — run it after a merge, or on a schedule.

## Ownership rule

So manual issues (and the 6 that already existed in this project before this tool ever
ran) are never clobbered:

- This tool **creates** issues only with the `mx-auto` label — including
  `issues.py open`, which is still tool-mediated even for a human-typed title.
- It **rewrites descriptions, auto-reopens, or auto-closes** only issues carrying
  `mx-auto` (`sync`'s dedup logic skips — never comments on, never relabels — any match
  that isn't tool-owned; see `mxissues/client.py`'s `is_owned()`).
- It **transitions state** on *any* issue when named explicitly (`issues.py start 42`),
  manual ones included — a human said so by iid, that's enough.
- `sync` never touches the body of an issue it doesn't own.

Verified directly against the real project: `issues.py list` (no `--all`) shows only
tool-owned issues; `issues.py list --all` shows every issue and marks the ones this tool
doesn't own.

**A token scoped to Developer cannot delete issues** (GitLab requires Owner/Maintainer)
— confirmed against the real instance (`403 Forbidden`). This tool never tries to; a
mis-filed or no-longer-needed issue gets relabeled/closed, not deleted. If you want
programmatic deletion available, the project access token needs a higher role — weigh
that against the ownership rule above being the only thing standing between this tool
and someone's hand-written issue.

## Deduplication

Every finding gets a **fingerprint**: `sha1(f"{source}:{key}")`, truncated to 12 hex
chars, where `key` is source-specific (`mxissues/sources/*.py`):

| Source | Fingerprint key | Why |
|---|---|---|
| `e2e` | spec path + test title | stable across reruns and refactors |
| `pytest` | node id (`src/.../test_x.py::test_y`), reconstructed from JUnit XML's `classname`+`name` (no `file` attribute is present in this repo's `--junit-xml` output) | canonical and stable |
| `pylint` | file path + `symbol` | **not** the line number — lines shift on every edit |
| `detector` | normalised title + sorted affected files | LLM ids aren't stable; the weakest key, and the reason a confidence floor (`MIN_CONFIDENCE = 0.7` in `sources/detector.py`) stands in for the origin document's not-yet-built critic (§7) |

Stored in the issue description as a fenced block:

````
```mx-meta
{"fp": "a1b2c3d4e5f6", "source": "pytest", "key": "...", "first_seen": "2026-09-21", "occurrences": 3}
```
````

Lookup: a local cache at `.mxissues/index.json` is a fast path (fingerprint → iid); a
cache miss, or a cached iid that no longer exists or whose stored fingerprint doesn't
match, **always** falls through to a real search
(`project.issues.list(search=f"mx-fp:{fp}", in_="description", state="all")`) — never
straight to "create". The API is the source of truth; the cache is gitignored and safe
to delete any time.

On a match:
- Not tool-owned → skip entirely, report it, touch nothing.
- Tool-owned, still open → comment "Recurred (occurrence N)", bump the counter.
- Tool-owned, `status::done`/closed → comment "**Regression**", bump the counter,
  **reopen** it (`status::open`) — this is the *"search for regressions"* step the
  origin document's reviewer (§4) was meant to perform.

Verified end to end against the real project with a synthetic finding: create → sync
again (0 created, 1 updated, occurrences 1→2) → mark done → sync again (0 created, 1
**reopened**, occurrences 2→3, a "Regression" comment posted) — the exact sequence
`implementation_plan.md`'s Checkpoint calls for, confirmed against real
issues (not against 20 real pre-existing pytest failures, to keep the footprint on a
shared project small — see the dry-run note below instead).

## Feeding results in

Optional flags, so the default `/verify` human tables are unchanged:

```bash
python scripts/validate_tests.py --junit-xml .testenv/artifacts/pytest.xml
python scripts/validate_build.py --json .testenv/artifacts/pylint.json
# Playwright: already wired — playwright.config.ts's `json` reporter writes
# .testenv/artifacts/results.json on every run, test_environment/e2e or not.
```

Then:

```bash
python scripts/issues.py sync --pytest .testenv/artifacts/pytest.xml            # dry run
python scripts/issues.py sync --pytest .testenv/artifacts/pytest.xml --apply    # for real
python scripts/issues.py sync --pylint .testenv/artifacts/pylint.json --apply
python scripts/issues.py sync --e2e .testenv/artifacts/results.json --apply
```

**`sync` defaults to a dry run** — creating issues in a shared tracker is outward-facing
and awkward to undo (see [Ownership rule](#ownership-rule) on why deletion isn't a
reliable undo), so it always prints the create/update/reopen plan first.

A full `sync --pytest --apply` against this repo's own (real, pre-existing, unrelated to
this workstream) 20 failing auth-related backend tests was deliberately **not** run
against the live project during development — that would leave 20 real issues in a
shared tracker with no way to delete them back out. The dry-run path was verified
against all 20 at once; the create/update/reopen path was verified against a single
synthetic failure instead, exercising the identical code.

## CLI reference

```
issues.py doctor                          host/project/token/version/edition/labels
issues.py init                            create missing labels (idempotent)
issues.py list [--status S] [--source S] [--all]
issues.py show <iid>
issues.py open "<title>" [--source manual] [--severity S] [--file F ...]
issues.py start <iid>                     -> status::in-progress
issues.py done <iid>                      -> status::done + close
issues.py reopen <iid>                    -> status::open (reopens if closed)
issues.py needs-human <iid>               -> status::needs-human
issues.py sync --e2e <json> | --pytest <xml> | --pylint <json> | --detector <json>  [--apply]
issues.py reconcile                       fix labels the mirror's auto-close missed
```

## Known gaps

- **No detector or critic exists yet** — `sources/detector.py` (B10) defines the
  hand-off shape (the origin document's §1 JSON schema) so an LLM detector can plug in
  unchanged later; nothing calls it today. Its confidence floor (0.7) is a crude stand-in
  for the origin document's §7 critic, not a replacement for one.
- **Scoped-label enforcement** is a Premium/Ultimate licensing feature, independent of
  Community vs Enterprise edition — this instance is Enterprise-edition but its license
  tier isn't visible to a project-scoped token, so whether it's actually enforced
  server-side is unconfirmed. Irrelevant in practice: the client-side read-back
  assertion in [State machine](#state-machine) is the safe path either way.
- **Rate limits** weren't a factor in testing (self-managed instances commonly leave
  them off), but `sync`'s dry run does one live search per finding with no batching —
  expect it to take a while (tens of seconds to minutes) against a few dozen findings
  over a direct, non-tunneled connection.
