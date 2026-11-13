# test_environment

The single operational reference for mx-admin-cloud's sandbox test environment, issue
tracking (GitLab or GitHub), and selectable AI-fixer execution strategies: an isolated, runnable
sandbox (the real backend, the real frontend, and a deterministic stub standing in for
every external provider — OpenAI, Qwen, Speaktor, Gemini, ComfyUI, Seq — driven end to end by
Playwright), the issues its checks get filed as, and the strategies that can act
on those issues. Nothing here touches real content or real credentials, and nothing
pushes code or opens a PR unless an execution strategy is deliberately configured and
run to do so.

This file corresponds to Workstreams 1–3 of
[docs/test_environment/implementation_plan.md](../docs/test_environment/implementation_plan.md)
(and [docs/test_environment/initial_spec.md](../docs/test_environment/initial_spec.md)),
which hold the original design rationale and findings — kept only as implementation
history, not maintained as day-to-day reference. **This file is authoritative for
day-to-day use**; if the two ever disagree, trust this one.

Jump to: [Quick start](#quick-start) · [All available operations](#all-available-operations)
· [Configuration](#configuration) · [Running specs](#running-specs) ·
[Issue tracking](#issue-tracking) ·
[Execution strategies](#execution-strategies) ·
[Known gaps](#known-gaps-sandbox) · [Troubleshooting](#troubleshooting)

> **Moved from `scripts/` (2026-10-30).** `scripts/testenv.py`, `testenv.sh`, `testenv_config.py`,
> `testenv_seed.py`, `app.py`, `issues.py`, `requirements.txt`, `testenvlib/`, `mxissues/` and
> `mxfix/`, and the root `testenv.toml`, now live in this directory: run
> `./test_environment/testenv.sh` instead of `./scripts/testenv.sh`. Move a root
> `testenv.local.toml` here too: while the old file exists, `testenv.py run` and `issues.py`
> refuse to run rather than silently ignore it. Runtime directories
> (`.testenv/`, `.mxworktrees/`, `.mxissues/`, `.mxfix*`) stay at the repository root.

Everything the test environment needs lives in this one directory — the sandbox, the
issue tracking, and the execution strategies. It shares no code with the feature Worker
(`feature_coordinator/`, run as `python -m feature_coordinator`):

```
test_environment/
  testenv.py             entry point for all three workstreams (every command below)
  testenv.sh             its bash wrapper
  testenv_seed.py        builds .testenv/ from the real content/ and backend/config/
  testenv_config.py      rewrites the copied provider config to point at the stub
  app.py                 native-mode runner: stub + uvicorn + built frontend/dist/ behind a proxy
  testenvlib/            shared paths, ports, env-file allowlist, process and venv helpers
  issues.py              issue tracking CLI, GitLab or GitHub (Workstream 2)
  mxissues/              its implementation — tracker.py (interface), trackers/{gitlab,github}.py,
                           config, sync, fingerprinting, sources/
  mxfix/                 execution strategies implementation (Workstream 3) —
                           fixer.py, codex_fixer.py, claude_fixer.py, worktree.py,
                           git_target.py, strategy/
  testenv.toml           execution strategies + issue tracker selection (tracked)
  testenv.local.toml     your own overrides (gitignored, optional)
  requirements.txt       python-gitlab + httpx for issues.py / mxfix (installed by scripts/setup_initial.py)
  e2e/                   Playwright suite — its own npm package, deliberately outside frontend/
    playwright.config.ts
    global-setup.ts        readiness probe, runs once before the suite
    auth.setup.ts          real UI login, saves storageState for every other spec
    fixtures/              selectors.ts, seed.ts, stub.ts, testEnv.ts, upload.png
    specs/                 02-login.spec.ts … 10-category-create.spec.ts (numbered — see below)
  stub-provider/
    stub_server.py         stdlib-only HTTP stub for every redirected provider
```

What it uses from the rest of the repo, all read-only: the app itself (`backend/`,
`frontend/`, `cli/requirements.txt`, `content/` as the seed source), the Docker stack
(`docker/docker-compose.yml` plus its test overlay `docker/docker-compose.test.yml`), and
the project's generic validators (`scripts/validate_build.py`, `validate_tests.py`,
`validate_frontend_build.py`), which `run`'s check suite calls as subprocesses. Its
runtime state stays at the repo root, all gitignored: `.testenv/`, `.mxworktrees/`,
`.mxissues/`, `.mxfix*`.

Everything is driven through **`test_environment/testenv.py`** (or its `test_environment/testenv.sh`
wrapper — same thing, picks a working Python for you). You should not normally need to
invoke Docker, uvicorn, or Playwright directly.

## Quick start

```bash
./test_environment/testenv.sh doctor   # mode, ports, secret scan — run this first, always
./test_environment/testenv.sh up       # seeds .testenv/ if absent, starts the stack, waits for it
./test_environment/testenv.sh e2e      # installs Playwright, runs the 23-test suite
./test_environment/testenv.sh down     # stop
```

(`python test_environment/testenv.py <command>` works identically — `testenv.sh` only adds
picking a Python interpreter and a stable `./test_environment/testenv.sh` entry point.)

## All available operations

| Command | What it does |
|---|---|
| `doctor` | Reports the resolved mode, Docker Compose version (if applicable), whether the stack's ports are free, and scans the environment that would be handed to child processes for anything that looks like a real credential. Safe to run any time; changes nothing. |
| `seed [--force] [--check-ts]` | (Re)builds `.testenv/{content,config,archive}` and `.testenv/test.env` from the real `content/` and `backend/config/`. `--force` wipes an existing `.testenv/content` first; without it, `seed` refuses if content is already there. `--check-ts` compares the real `content/` against the last seed's manifest and reports drift **without** reseeding — useful if a test failure might be a stale fixture rather than an app bug. |
| `up [--build] [--simple-confirm] [--backend-port N] [--frontend-port N] [--stub-port N]` | Seeds if `.testenv/content` is empty, starts stub → backend → frontend (or the three containers, in Docker mode), waits for each to answer, prints the URLs. `--build` forces a Docker image rebuild (native mode always rebuilds the frontend bundle; there's no image to skip). |
| `down [--purge]` | Stops everything. `--purge` also deletes `.testenv/` entirely (content, config, archive, the SQLite user DB, logs, Playwright artifacts) — the next `up`/`seed` starts from nothing. |
| `reset [--simple-confirm] [--backend-port N] [--frontend-port N] [--stub-port N]` | `down` + wipe `.testenv/` + `seed` + `up`, in one call. The workhorse — run this between full E2E runs so every spec starts from the identical, byte-for-byte fixture. |
| `e2e [--base-url URL] [--stub-url URL] [-- <playwright args>]` | Ensures the stack is up (starts it if not), installs `test_environment/e2e`'s npm deps on first use, runs `npx playwright install chromium --with-deps` on **every** invocation, then runs the suite. On Linux, `--with-deps` installs OS packages through `sudo apt-get`, so it can stop at a sudo password prompt; to skip it, run `npx playwright test` from `test_environment/e2e` with `E2E_BASE_URL`/`E2E_STUB_URL` set yourself. Anything after `--` is passed straight to `playwright test` — see [Running specs](#running-specs) below. |
| `logs [backend\|frontend\|stub] [--follow]` | Prints (or tails) that service's log. No argument prints all three. |
| `status` | Shows what's currently running (pid + port in native mode, `docker compose ps` in Docker mode). |
| `shell <backend\|frontend\|stub>` | Docker mode: opens a shell in that container. Native mode: there's no container — it tells you which directory the process runs from and how to tail its log instead. |
| `run [--strategy report\|heal\|batch\|work] [--max-issues N] [--dry-run] [--print-config]` | Workstream 3: check → sync → select → (heal/work/batch) fix, driven by `test_environment/testenv.toml`. `report` only files issues; `heal`/`work`/`batch` write code in `.mxworktrees/`, push branches, and open PRs — `work` skips the check/file step and goes straight to whatever's already filed. Full reference: [Execution strategies](#execution-strategies) below — read it, and run `--dry-run` first, before ever using `heal`/`work`/`batch` for real. |

Every command accepts `--mode docker|native`, **placed before the command**
(`testenv.py --mode docker up`, not `testenv.py up --mode docker`) — a subparser-defaults
quirk in Python's argparse makes the reverse silently discard the flag if it were
allowed at all, so it isn't; you'll get a clean argument-parsing error instead of a
surprise. Omitted, it autodetects: Docker if the daemon answers, native otherwise.

Both modes are validated end to end against this repo's real backend and frontend: all
23 tests green in each, last measured 2026-11-13 after a fresh `reset`.

## Configuration

### Ports

Deliberately **not** this repo's native-dev defaults (backend `:8000`, frontend/vite
`:5173`) or the dev Docker stack's `:9000`/`:9001` or anything near them — running the
test stack must never fight a dev server you already have open.

| | Native mode | Docker mode |
|---|---|---|
| Backend  | `48000` | `48100` |
| Frontend | `48001` | `48101` |
| Stub     | `48002` | `48102` |

Override per invocation with `--backend-port` / `--frontend-port` / `--stub-port` on
`up`/`reset`; they are resolved fresh at every `up`, never written into `.testenv/`, so
switching ports never requires a reseed. `e2e` reads whatever's actually running via
`--base-url`/`--stub-url` (defaulting to the mode's standard ports above) — if you
started `up` with custom ports, pass the matching `--base-url`/`--stub-url` to `e2e`, or
just don't customize either and let both default together.

### Run mode

`--mode docker` or `--mode native`, or omit it to autodetect (Docker if the daemon
answers). Native mode needs no Docker at all — it runs the stub and `uvicorn` as plain
subprocesses and serves a once-built `frontend/dist/` through a small stdlib
static-file-plus-reverse-proxy server (`test_environment/app.py`), mirroring `docker/nginx.conf`'s
`/api/`, `/internal/`, `/content/` routing exactly. It deliberately never runs `npm run
dev`: `frontend/vite.config.ts` serves `/content` from a **hardcoded `../content`** via
`sirv`, which would silently expose the real content directory regardless of
`CONTENT_DIR`.

### Confirmation mode

`--simple-confirm` on `up`/`reset` builds the frontend with `VITE_ARCHIVE_CONFIRMATION_MODE`
and `VITE_DELETE_ITEM_CONFIRMATION_MODE` set to `simple` instead of the default
`complex` (typed-id) confirmation. These are Vite **build-time** values, so changing this
costs a frontend rebuild, not something togglable per-request.

### Credentials

`.testenv/test.env` (regenerated by every `seed`) holds dummy values only —
`OPENAI_API_KEY=test-openai-key-not-real`, a fixed `DEFAULT_ADMIN_PASSWORD`, etc. Real
values are never read: child processes get an **allowlist**-built environment
(`test_environment/testenvlib/envfile.py`'s `child_env()` — `PATH`/`HOME`/`LANG`/… plus whatever
`.testenv/test.env` supplies, nothing else from your shell), specifically so a real key
exported by having sourced `scripts/app.sh`/`scripts/deploy.sh` in the same shell can't
leak in. `doctor` scans for this on every run and refuses to report success on a hit.

Every provider key the backend reads is listed in `test.env`, as a dummy or as an empty
value, never left out: the backend's `load_dotenv()` fills any unset variable from a real
`backend/.env` or repo-root `.env`. A new provider key therefore needs an entry in
`test_environment/testenv_seed.py` (`_write_test_env`), with any dummy value defined in
`test_environment/testenvlib/paths.py`. `up` reuses an existing `test.env` rather than regenerating
it, so after such a change run `reset` (or `seed --force`) before the next `up`.

### The seeded fixture

`seed` copies four real categories out of `content/` verbatim (never synthesised) and
slices only the row-aligned index CSVs — see
[implementation_plan.md §3](../docs/test_environment/implementation_plan.md#3-seed-fixture--a-curated-subset-copied-from-the-real-content)
for why. To change *which* categories/items are included, edit
`test_environment/testenvlib/paths.py`'s `KEPT_CATEGORY_ROWS` (root CSV row index → folder name)
and `KEPT_ITEM_ROWS` (per-category item row indices) — then update
`test_environment/e2e/fixtures/seed.ts` to match, since that's what the specs assert
against, and re-run `reset`. That file's header comment documents two behaviors that are
easy to get wrong when picking new items: dashboard
item counts are on-disk directory counts, not CSV row counts, and `item_images()` treats
an item's first (sorted) image variant as its main image whenever no dedicated
`{item}.{ext}` file exists — neither is obvious from the CSVs alone.

```
.testenv/                # gitignored, entirely disposable — rebuilt by seed/reset
  test.env                 absolute paths + dummy credentials, generated
  content/                 the 4-category subset, ~8MB
  config/                  copy of backend/config/, providers redirected to the stub
  archive/                 empty, pre-created (archive/restore specs use this)
  data/                    users.db — isolated from the real backend/data/users.db
  output/                  backend logs, pipeline output
  artifacts/                Playwright html-report/, results.json, .auth/admin.json
  logs/                    native mode only: stub.log, backend.log, frontend.log
  native.pids.json          native mode only: what app.py needs to stop things
```

Nothing under `.testenv/` is ever a symlink into a tracked path — asserted after every
seed. The suite includes destructive specs (archive a category, delete an item, delete
an image); a symlink there would follow straight through to real, tracked content.

### Issue tracker and execution-strategy configuration

These have their own configuration — `test_environment/testenv.toml`'s `[issues]` section
(tracker choice, GitLab host/project, GitHub repo/project) plus the tracker's token in
`.env`, and the rest of `testenv.toml` for execution strategies — documented where they're used rather than duplicated here: see
[Issue tracking](#issue-tracking)'s [Choosing a tracker](#choosing-a-tracker) and
[Execution strategies](#execution-strategies)'s [`testenv.toml`](#testenvtoml) below.

## Running specs

```bash
./test_environment/testenv.sh e2e                                          # everything
./test_environment/testenv.sh e2e -- specs/06-item-create.spec.ts            # one file
./test_environment/testenv.sh e2e -- --headed specs/06-item-create.spec.ts   # watch it run
./test_environment/testenv.sh e2e -- --debug                                 # Playwright inspector
```

Spec files are numbered (`02-login.spec.ts` … `10-category-create.spec.ts`; `01` is
`auth.setup.ts`, a separate Playwright *project* that always runs first) because
**Playwright's default file order is alphabetical**, which stops matching the intended
run order the moment a spec mutates state — e.g. `10-category-create.spec.ts` adding a
category before `03-dashboard.spec.ts`'s "exactly 4 cards" assertion runs would fail a
correct app for the wrong reason. This was caught by running the full suite together,
not by each spec file in isolation, and is exactly why `playwright.config.ts` pins
`fullyParallel: false` / `workers: 1` / `retries: 0` — every root/category CSV is
row-index aligned, so a concurrent write (or a retry re-running against state the first
attempt already mutated) corrupts a *different* spec's assertions, not just its own.

`auth.setup.ts` logs in through the real UI (not a `localStorage` shortcut) and reads the
admin password from `.testenv/test.env` at run time rather than hardcoding it a second
time. `fixtures/stub.ts` talks directly to the stub's `/__calls`/`/__reset` — it isn't
behind the frontend's `/api`/`/internal`/`/content` proxy, so it needs `E2E_STUB_URL`
(set by `testenv.py e2e` alongside `E2E_BASE_URL`) rather than a relative path.

## Issue tracking

*(Workstream 2.)* `test_environment/issues.py` replaces the origin document's `tasks.sqlite` (§2)
with a real issue tracker — the self-hosted GitLab project or GitHub Issues — see
[implementation_plan.md](../docs/test_environment/implementation_plan.md) Workstream 2
for the original (GitLab-only) design and findings kept there for implementation
history. This section is the day-to-day operational reference: choosing a tracker, label
schema, the state machine, the ownership rule, and the CLI.

### Choosing a tracker

`testenv.toml`'s `[issues] tracker` selects the backend for both `issues.py` and
`testenv.py run`:

```toml
[issues]
tracker = "gitlab"   # or "github"
```

Override it per developer in `testenv.local.toml`, or per invocation with
`testenv.py run --tracker github` / `issues.py --tracker github <command>` (the flag goes
**before** the subcommand). `testenv.py run --print-config` shows the resolved value.

Both backends share the same workflow statuses (`open`, `in-progress`, `done`,
`needs-human`), the same metadata labels, fingerprint dedup, ownership rule and CLI.
They differ only in **where the workflow status lives**:

| | GitLab | GitHub |
|---|---|---|
| Status | exclusive `status::*` label | single-select field on a GitHub Project — **no status labels** |
| `done` | `status::done` + issue closed | field = `Done` + issue closed (`state_reason: completed`) |
| Config | `testenv.toml` `[issues.gitlab]` + `.env`: `GITLAB_TOKEN` | `testenv.toml` `[issues.github]` + `.env`: `GITHUB_PAT` |
| Fix commit closes via | `Closes <full GitLab issue URL>` | `Closes owner/repo#N` |

Code-wise, everything goes through `mxissues/tracker.py`'s `IssueTracker` interface;
`mxissues/trackers/gitlab.py` and `trackers/github.py` implement it. Adding another
tracker means implementing that interface and registering it in `trackers/__init__.py`
and `mxissues/config.py`'s `TRACKERS`.

Both trackers split their configuration the same way: everything non-secret lives in
`testenv.toml` (tracked, so the whole team shares it — override per developer in
`testenv.local.toml`), and only the token lives in `.env` (gitignored). A token never
goes in either TOML file.

Switching trackers doesn't migrate anything — each tracker keeps its own backlog, and
its own fingerprint cache (`.mxissues/index-<tracker>.json`).

### GitLab setup

Where the project is, in `testenv.toml` (already filled in for this project):

```toml
[issues.gitlab]
host = "http://82.103.77.45"      # or http://127.0.0.1:8080 via an SSH tunnel
project = "marxman/mx-admin"      # namespace/path, or the numeric project id
```

The token, in the repo-root `.env` (gitignored):

```
GITLAB_TOKEN=glpat-...            # project access token, role Developer, scope api
```

The instance has no TLS — a token crosses the network in the clear unless you tunnel:
run `ssh -L 8080:localhost:80 <user>@82.103.77.45`, then set
`host = "http://127.0.0.1:8080"` under `[issues.gitlab]` in your own
`testenv.local.toml` (not the shared `testenv.toml`). `issues.py doctor` warns on every
run when `host` is plain HTTP and not loopback; it's a warning, not a hard stop, since
direct is a valid (if riskier) choice you can make explicitly.

**Moving from the old `.env` setup:** `GITLAB_HOST`/`GITLAB_PROJECT` in `.env` are no
longer read. If `[issues.gitlab]` is ever empty while your environment still sets them,
`issues.py`/`testenv.py run` say so and point here instead of silently using them —
move the values into `testenv.toml`/`testenv.local.toml` and delete them from `.env`.
`GITLAB_TOKEN` stays in `.env`.

Install `python-gitlab` via `test_environment/requirements.txt` (already wired into
`scripts/setup_initial.py`), or `pip install -r test_environment/requirements.txt` directly into
whichever venv you're running `issues.py` from.

**`issues.py doctor` runs first, always** (either tracker) — it's the only thing
standing between a typo'd project path and a pile of issues filed into the wrong
repository. On GitLab it also reports the GitLab version and edition (this instance: 18.9.1, **Enterprise**, confirmed
via `/version`'s `enterprise` field) — Community vs Enterprise/Premium decides whether
GitLab enforces scoped-label (`status::*`) exclusivity server-side, but this tool
enforces it client-side regardless (see [State machine](#state-machine) below), so it
behaves identically either way.

### GitHub setup

GitHub Issues has only two native states (open/closed), so the four-state workflow is
kept in a **single-select field on a GitHub Project** (Projects v2 — free on every GitHub
plan, personal accounts and organizations alike) instead of in labels. Every issue this
tool files or transitions is added to that project, and its status is the item's field
value — visible as a board column if you group the project by that field.

1. **Create an empty GitHub Project** in the UI (your profile or org → Projects → New
   project) and note its number from the URL: `github.com/users/<owner>/projects/<N>` or
   `github.com/orgs/<owner>/projects/<N>`.
2. **Configure `testenv.toml`** (or `testenv.local.toml`):

   ```toml
   [issues]
   tracker = "github"

   [issues.github]
   repo = ""               # "owner/repo"; empty = the repo the origin remote points at
   project_owner = ""      # user/org owning the project; empty = the repo's owner
   project_number = 7      # the <N> from step 1
   status_field = "mx-status"
   ```

   `[issues.github.status_options]` maps each status to an option name (defaults:
   `Open`, `In progress`, `Needs human`, `Done`); override any subset to reuse an
   existing field's option names.
3. **Token** — `.env`'s `GITHUB_PAT`, the same one PRs are opened with
   ([Landing the fix](#landing-the-fix)). It needs to read/write issues and labels on the
   repo **and** read/write the project: a classic PAT with the `repo` and `project`
   scopes, or a fine-grained PAT with repository *Issues: read and write* plus Projects
   read/write access for the project's owner (fine-grained tokens' Projects support
   depends on whether the owner is a user or an organization — if `doctor` can't read
   the project with one, use a classic token). Write (or triage) access to the repo also
   matters: GitHub silently drops labels on issues created without it, and `doctor`
   warns about that.

   Set the token in the repo-root `.env`:

   ```dotenv
   GITHUB_PAT=your-github-personal-access-token
   ```

   Matching single or double quotes around the value are also accepted. Process
   environment variables take precedence over `.env`, so unset an old exported
   `GITHUB_PAT` if you want to use a replacement from the file.

4. `issues.py --tracker github doctor`, then `issues.py --tracker github init` — creates
   the labels below (minus the `status::*` ones) and the `mx-status` field with its four
   options.

**`init` never edits an existing field's options.** Replacing a single-select field's
option list through the API can reset every item's value, so if `status_field` already
exists but lacks an option, `doctor`/`init` report exactly which ones to add in the
project's UI instead.

The default is a **dedicated** `mx-status` field rather than the project's built-in
`Status`, because GitHub's built-in project workflows (*Item closed → Done*, *Item
reopened → Todo*, …) only ever act on `Status` and run asynchronously — against a
dedicated field they can't race this tool's read-back assertion. Pointing
`status_field = "Status"` at the built-in field works too, provided those workflows are
disabled or agree with `status_options`.

### Label schema

`issues.py init` creates these idempotently:

| Label | Purpose |
|---|---|
| `status::open` / `status::in-progress` / `status::done` / `status::needs-human` | the workflow — **GitLab only**; on GitHub it's the project field (see [GitHub setup](#github-setup)) |
| `source::detector` / `::e2e` / `::pytest` / `::pylint` / `::manual` | provenance |
| `severity::high` / `::medium` / `::low` | from the detector schema / sync's severity mapping |
| `mx-auto` | ownership marker — see [Ownership rule](#ownership-rule) |

`status::needs-human` is the origin document's `MAX_FIX_ATTEMPTS` escape valve — without
it a stuck task has nowhere to go but silently back to `open`. `heal`/`work` (see
[Execution strategies](#execution-strategies) — both share the same retry loop) set it
automatically once a fixer gives up after its retry budget; `issues.py needs-human <number>`
sets it by hand.

### State machine

The same four statuses and transitions on both trackers — the diagram below uses
GitLab's label names. **GitLab:** exactly one `status::*` label at a time, enforced **client-side**: every transition
computes the full label set and sends it as one replacement (`issue.labels = [...]`),
never `add_labels`/`remove_labels` (separate calls that can race and leave two
`status::` labels set). Every transition re-fetches the issue by `iid` afterward and
asserts exactly the target status label survived — `mxissues/tracker.py`'s
`TransitionError` if it didn't. That read-back is the verification GitLab Free/Premium
licensing doesn't give you client-side, and it costs one cheap GET.

```
status::open <-> status::in-progress -> status::done (closes the issue)
       ^                                      |
       +---------- reopened as a regression --+
                 (sync finds a recurrence on a done/closed issue)
       \-> status::needs-human (a fixer gave up — see Execution strategies)
```

`done` sets `state_event: close`; transitioning *off* `status::done` on a closed issue
sets `state_event: reopen` — so `reopen`/a regression on a closed issue both reopen it
in GitLab, not just relabel it.

**The mirror race:** the GitHub→GitLab mirror is one-way and push-only (`main` + tags).
When it pushes to GitLab's default branch, GitLab parses those commit messages for a
closing reference and closes the issue *itself* — useful, but it races with
`issues.py done`, and either way it never touches the `status::` label. `issues.py
reconcile` sweeps every tool-owned issue whose GitLab state is `closed` but whose label
isn't `status::done` and corrects it — run it after a merge, or on a schedule.

**GitHub:** a transition sets the project field (adding the issue to the project first
if needed — `addProjectV2ItemById` is idempotent), then closes the issue for `done` or
reopens it for anything else on a closed issue, then re-fetches it and asserts both the
field value and the open/closed state — `TransitionError` if either is off. The same
race exists in a different shape: a fix commit's `Closes owner/repo#N` closes the issue
once it reaches the repository's default branch, and humans close issues by hand;
neither touches the project field. `issues.py reconcile` corrects those exactly as it
does on GitLab.

### Ownership rule

So manual issues (and the 6 that already existed in this project before this tool ever
ran) are never clobbered:

- This tool **creates** issues only with the `mx-auto` label — including
  `issues.py open`, which is still tool-mediated even for a human-typed title.
- It **rewrites descriptions, auto-reopens, or auto-closes** only issues carrying
  `mx-auto` (`sync`'s dedup logic skips — never comments on, never relabels — any match
  that isn't tool-owned; see `Issue.owned` in `mxissues/tracker.py`).
- It **transitions state** on *any* issue when named explicitly (`issues.py start 42`),
  manual ones included — a human said so by number, that's enough.
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

### Deduplication

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

Lookup: a local cache at `.mxissues/index-<tracker>.json` is a fast path (fingerprint →
issue number); a cache miss, or a cached number that no longer exists or whose stored
fingerprint doesn't match, **always** falls through to a real lookup against the
tracker — never straight to "create":

- **GitLab:** a search per finding
  (`project.issues.list(search=f"mx-fp:{fp}", in_="description", state="all")`).
- **GitHub:** one paginated GraphQL listing of every issue in the repo (any state, owned
  or not), parsed once per process into a fingerprint index. Not the search API: its
  index lags writes by seconds to minutes and it's limited to 30 requests/minute, either
  of which could turn a sync into duplicates or a 403 halfway through.

The API is the source of truth; the cache is gitignored and safe to delete any time.

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

### Feeding results in

Optional flags, so the default `/verify` human tables are unchanged:

```bash
python scripts/validate_tests.py --junit-xml .testenv/artifacts/pytest.xml
python scripts/validate_build.py --json .testenv/artifacts/pylint.json
# Playwright: already wired — playwright.config.ts's `json` reporter writes
# .testenv/artifacts/results.json on every run, test_environment/e2e or not.
```

Then:

```bash
python test_environment/issues.py sync --pytest .testenv/artifacts/pytest.xml            # dry run
python test_environment/issues.py sync --pytest .testenv/artifacts/pytest.xml --apply    # for real
python test_environment/issues.py sync --pylint .testenv/artifacts/pylint.json --apply
python test_environment/issues.py sync --e2e .testenv/artifacts/results.json --apply
```

**`sync` defaults to a dry run** — creating issues in a shared tracker is outward-facing
and awkward to undo (see [Ownership rule](#ownership-rule) on why deletion isn't a
reliable undo — GitHub only lets repository admins delete issues at all), so it always prints the create/update/reopen plan first.

A full `sync --pytest --apply` against this repo's own (real, pre-existing, unrelated to
this workstream) 20 failing auth-related backend tests was deliberately **not** run
against the live project during development — that would leave 20 real issues in a
shared tracker with no way to delete them back out. The dry-run path was verified
against all 20 at once; the create/update/reopen path was verified against a single
synthetic failure instead, exercising the identical code.

### `issues.py` CLI reference

```
issues.py [--tracker gitlab|github] <command>   (default: testenv.toml [issues] tracker)

issues.py doctor                          connection/token/project/labels (+ GitLab version, GitHub status field)
issues.py init                            create missing labels (+ GitHub's project status field), idempotent
issues.py list [--status S] [--source S] [--all]
issues.py show <number>
issues.py open "<title>" [--source manual] [--severity S] [--file F ...]
issues.py start <number>                  -> in-progress
issues.py done <number>                   -> done + close
issues.py reopen <number>                 -> open (reopens if closed)
issues.py needs-human <number>            -> needs-human
issues.py sync --e2e <json> | --pytest <xml> | --pylint <json> | --detector <json>  [--apply]
issues.py reconcile                       set done on tool-owned issues something else closed
```

### Known gaps

- **No detector or critic exists yet** — `sources/detector.py` (B10) defines the
  hand-off shape (the origin document's §1 JSON schema) so an LLM detector can plug in
  unchanged later; nothing calls it today. Its confidence floor (0.7) is a crude stand-in
  for the origin document's §7 critic, not a replacement for one.
- **Scoped-label enforcement** is a Premium/Ultimate licensing feature, independent of
  Community vs Enterprise edition — this instance is Enterprise-edition but its license
  tier isn't visible to a project-scoped token, so whether it's actually enforced
  server-side is unconfirmed. Irrelevant in practice: the client-side read-back
  assertion in [State machine](#state-machine) is the safe path either way.
- **The GitHub backend hasn't run against a live repository/project yet.** It's been
  exercised against an in-memory fake of the REST + GraphQL API (create → recurrence →
  done → regression reopen, the `max_new_issues` cap, the ownership guard, a paginated
  fingerprint index, the transition read-back assertion) and a real 401 from
  `api.github.com`, but not against a real project. Run `doctor`, `init`, then one
  `issues.py --tracker github open "test"` + `start`/`done` by hand first.
- **Rate limits** weren't a factor in testing (self-managed instances commonly leave
  them off), but `sync`'s dry run does one live search per finding with no batching —
  expect it to take a while (tens of seconds to minutes) against a few dozen findings
  over a direct, non-tunneled connection.

## Execution strategies

*(Workstream 3.)* `test_environment/testenv.py run` is the origin document's executor (§3): it
takes whatever `test_environment/issues.py` has filed in the configured tracker
([Issue tracking](#issue-tracking), above) and, depending on the selected
**strategy**, either just reports on it or hands it to an AI coding agent running inside
a throwaway git worktree. See
[implementation_plan.md](../docs/test_environment/implementation_plan.md) §14–18 for the
full design and findings kept there for implementation history; this section is the
operational reference — the four strategies, the `Fixer` interface, `testenv.toml`,
landing a fix, and the safety rails around all of it. `work` is a later addition beyond
what the plan itself specifies (§16 draws three) — see its own entry below for why it
exists and how it differs from `heal`.

**Status: built, reviewed, not yet run for real.** Every piece below has been exercised
up to the point that would invoke a real AI fixer, push a real branch, or open a real
PR — `--print-config`, the dirty-tree gate, config loading/validation, and all the
git/worktree/issue-tracker machinery `heal`/`work`/`batch` share with the already-validated
Workstream 2 pieces. Nothing has actually run `heal`, `work`, or `batch` against the live
GitLab project yet. Do that deliberately, starting with `--dry-run` — see
[Trying it for the first time](#trying-it-for-the-first-time) at the bottom.

### The four strategies

All four share the machinery in `strategy/common.py`, but not all of the same pipeline:
`report`/`heal`/`batch` open with **check → sync → select**; `work` skips straight to
**select** — see its entry below for why.

| | `report` | `heal` | `work` | `batch` |
|---|---|---|---|---|
| Runs check → sync first | yes | yes | **no** | yes |
| Modifies code | never | yes, in `.mxworktrees/` | yes, in `.mxworktrees/` | yes, in `.mxworktrees/` |
| Pushes branches / opens PRs | never | yes | yes | yes |
| Issues per invocation | all of them (files/updates issues, no cap on *how many checks* run) | 1 at a time, sequential, up to `--max-issues` | 1 at a time, sequential, up to `--max-issues` | up to `[limits] concurrency` at once, in parallel |
| Verification | the one check run report/heal/batch start with | full check suite, reset fresh, **per attempt** | full check suite, reset fresh, **per attempt** | full check suite, reset fresh, **once**, bisected on red |
| Safe unattended | yes — the only one | no | no | no |

**`report`** — run every check, `sync --apply`, stop. Nothing is modified anywhere.
The only strategy safe to run on a schedule.

**`heal`** — check → sync first (same as `report`), then select the single
highest-severity `open` issue, work on it alone in `.mxworktrees/<iid>`, verify
with a full reset + check run, and either land it or retry with the failure fed back as
feedback, up to `[limits] max_fix_attempts`. Then the next issue. Slow (every attempt
fully resets the sandbox fixture and, in Docker mode, rebuilds images — see
[Landing the fix](#landing-the-fix)'s note on risk #13) but each fix is verified against
a byte-identical starting point, and a failed attempt can't contaminate the next issue's.

**`work`** — exactly `heal`'s per-issue loop (same worktree/fixer/review_diff/verify/land
machinery, `strategy/common.py`'s `work_one_issue()` — both strategies call the same
function), but **skips the check → sync step entirely** and goes straight to selecting
whatever is already `open` in the issue tracker. It exists for working down a backlog
`report`/`heal`/`batch` already filed across several separate `run` invocations: `heal`
repeats the *entire* check suite (pytest/pylint/frontend build/e2e — several minutes, see
[Verifying a fix](#verifying-a-fix-reset-not-just-up)) before it ever looks at an issue,
every single time, even when nothing about the underlying findings has changed since the
last invocation. `work` trusts the tracker's current `open` state as-is and skips
straight to the part that actually does something new. It never files anything itself —
run `report`/`heal`/`batch` (or `issues.py open` by hand) at least once first, or `work`
just reports nothing to do and exits.

**`batch`** — select up to `concurrency` issues, fix all of them in parallel worktrees
(one fixer call each, no per-candidate retry — batch's retry granularity is a whole
invocation, not an attempt loop), merge every successful candidate onto one throwaway
integration branch, and verify **once**. Faster, but a red verify can't say which
candidate broke it — see [The bisect fallback](#the-bisect-fallback-for-batch) below.

### `testenv.toml`

Tracked, next to this README (deliberately not under `.testenv/`, which `reset --purge` deletes).
Loaded via `tomllib` (stdlib), merged with a gitignored `test_environment/testenv.local.toml` for
per-developer overrides (lower concurrency on a laptop, `push = false` while trusting a
new fixer adapter, ...), then CLI flags, which always win:

```
testenv.toml  ->  testenv.local.toml  ->  --<setting> CLI flags on `testenv.py run`
```

Every setting has its own `--<setting>` flag (`--max-fix-attempts`, `--concurrency`,
`--adapter`, `--push`/`--no-push`, ...) — see
[`testenv.py run` CLI reference](#testenvpy-run-cli-reference) below for the full list,
or `testenv.toml`'s own comments, which note each one right next to the setting it
overrides.

`testenv.py run --print-config` prints the fully-merged result and exits — a strategy
that writes code should never execute against settings you haven't actually seen. The
file's own comments document every setting in depth (especially the four strategy
modes and why `max_new_issues` exists); this section doesn't repeat them.

Unknown keys anywhere in the file are a hard error (`test_environment/mxfix/config.py`'s
`_from_dict`) — a typo'd setting fails loudly at load time instead of silently being
ignored.

### The `Fixer` interface

`test_environment/mxfix/fixer.py` defines `FixTask`/`FixResult`/the `Fixer` Protocol so none of
`strategy/heal.py`, `strategy/work.py`, or `strategy/batch.py` hardcodes which CLI
actually writes the fix — `[fixer] adapter` in `testenv.toml` picks `CodexFixer` or
`ClaudeFixer`, and both
implement the identical interface:

```python
class Fixer(Protocol):
    name: str
    def preflight(self) -> None: ...     # binary present? authenticated? raises with a fix hint
    def run(self, task: FixTask) -> FixResult: ...
```

- **`CodexFixer`** (`test_environment/mxfix/codex_fixer.py`) — `codex exec --cd <worktree>
  --sandbox workspace-write --json --output-schema fix_result_schema.json
  --output-last-message <file> -` with the prompt piped via stdin. `preflight()` runs
  `codex doctor --json` and checks `overallStatus` and `checks["auth.credentials"]`.
- **`ClaudeFixer`** (`test_environment/mxfix/claude_fixer.py`) — `claude -p --output-format json
  --permission-mode bypassPermissions <prompt>`, cwd pinned to the worktree. Claude Code
  has no `--output-schema` equivalent, so the prompt itself asks for a fenced ` ```json `
  block in the final reply, parsed out the same way `mxissues/fingerprint.py` parses the
  ` ```mx-meta ` block embedded in an issue description.

Both were verified against the real installed CLIs (`codex doctor --json`,
`claude doctor`, and — for Claude specifically, since its output envelope isn't
documented in the same structured way codex's is — one live minimal probe call in an
isolated scratch repo, confirming the `type`/`subtype`/`is_error`/`result` fields
`claude_fixer.py` actually parses). Neither has been used to attempt a real fix yet.

`preflight()` is cheap and side-effect-free (a version/auth check, nothing that touches
the issue tracker or a worktree), so `run` calls it even in `--dry-run` — a dry run should be a
genuine preview of whether the real thing would work, not just of the plan.

The prompt (`fixer.build_prompt()`) follows the origin document's §3 constraints
verbatim: *do not modify unrelated code, do not change public API unless necessary, do
not disable tests, do not weaken lint rules.* Those constraints are necessary but not
sufficient — see [The diff-review safety check](#the-diff-review-safety-check) below for
the enforcement layer that doesn't just trust the prompt.

### Worktrees

`test_environment/mxfix/worktree.py` creates every worktree under `.mxworktrees/`, branched off
`[git] base_branch` (fetched fresh from `origin` first, never off a possibly-stale local
branch of the same name). `.kilo/worktrees/` is a **different tool's** worktree
directory, already in active use — `worktree.py` never lists, prunes, or touches
anything outside `.mxworktrees/`, and deliberately never calls a bare
`git worktree prune` at all (it has no path-scoping option; the only fully safe
operation is removing the one specific path this tool itself created). Verified live:
created a real worktree, confirmed the safety guard refuses to touch
`.kilo/worktrees/*`, removed the probe cleanly.

`run` refuses to start any strategy at all — including `report` and `work` — while the
**main** working tree has uncommitted changes (`worktree.repo_is_clean()`): `report`'s
checks would otherwise run against a mix of committed and uncommitted code, and
heal/work/batch's worktrees are always branched fresh off `base_branch`, so local edits
sitting uncommitted in the main tree would silently never be reflected in what's
actually verified.

### Verifying a fix: reset, not just up

Every heal/work attempt and every batch round's single verify runs `testenv.py reset`
(not `up`) **inside the candidate's own worktree** — `test_environment/mxfix/strategy/common.py`'s
`run_all_checks(..., reset_stack=True)`. Two reasons, both load-bearing, not just one:

1. **The fixture must be byte-identical every time.** Earlier E2E specs delete items and
   archive categories; without a fresh reset, a later attempt's "fix" would be verified
   against a fixture an earlier attempt already mutated, not a clean one.
2. **Risk #13 — backend source is `COPY`ed into the Docker image at build time.**
   `testenv.py reset`'s own `up` step already always passes `--build` in Docker mode
   (`cmd_reset` hardcodes it — this was already true for Workstream 1, not something
   Workstream 3 added), so a fixer's backend edit is never verified against a stale
   image. In native mode there's no image to go stale in the first place. Reusing
   `testenv.py reset` rather than reimplementing "bring the stack up" was a deliberate
   choice specifically to inherit this for free.

The stack is torn down (`down --purge`) again immediately after every reset-backed
verify, success or failure, so an attempt never leaks a running process or held port
into the next one.

This is also the single biggest cost of `heal`/`work`/`batch`: since a worktree shares nothing
with the main tree but `.git`'s object store (not `.venv`, not `node_modules`, not
Playwright's browser install — all gitignored, so a fresh worktree starts without any of
them), every attempt's first check run pays for a from-scratch `python -m venv` + `pip
install` and `npm install` + `playwright install chromium`. Correct, not fast — a
one-to-few-minute fixed cost on top of the check suite itself, before a fixer has
produced anything. Sharing/caching those across worktrees safely (a naive directory copy
of a venv is not guaranteed relocatable — its shebangs and activation scripts can embed
absolute paths) is a real possible follow-up, not attempted here.

### The diff-review safety check

Prompts are not enforcement (risk #15) — a fixer can make a check pass by weakening it
instead of fixing the underlying problem: deleting an assertion, disabling a test,
relaxing a pylint rule. `strategy/common.py`'s `review_diff()` runs on every "fixed"
result, **before** the expensive reset+full-check-suite verify (heal/work) or before the
diff is even committed (batch — which has no per-candidate check run to catch it later),
and rejects two shapes outright:

- Every changed file looks like a test file (`test_*.py`, `*_test.py`, `*.spec.ts`, or
  anything under a `tests`/`specs` directory), but the issue's `source::` label is
  `pytest` or `e2e` — a real fix for a failing check changes the code under test, not
  just the test.
- Any change at all to `pylintrc`/`.pylintrc`/`pytest.ini`/`pyproject.toml` — always
  flagged, regardless of the issue's source, since loosening one of these can silence
  unrelated future failures too, not just this issue's.

A rejection in `heal`/`work` becomes feedback for the next attempt (the fixer gets told
*why*, which is often enough to redirect it to a real fix — both share the exact same
retry loop, `strategy/common.py`'s `work_one_issue()`); in `batch` it's a dead end
for that round — the candidate returns to `open` with the reason as a comment,
same as any other failed candidate (see [batch's scope boundary](#batch-has-no-cross-invocation-retry-counter)
below).

This is deliberately narrow — exactly the two shapes implementation_plan.md risk #15
names, not a general "is this diff reasonable" reviewer. Subtler check-weakening is
still only defended against by the prompt constraints and by the full check suite
(heal/work) / bisect (batch) actually running.

### Landing the fix

**The commit's closing reference comes from the tracker.** On GitHub it's
`Closes owner/repo#<number>` — unambiguous whether or not the issues live in the same
repository as the PR, and acted on by GitHub once the commit reaches the repository's
default branch (not on merge into `[git] base_branch` when that's a different branch).
The PR body carries `GitHub issue: <url>`, never a closing keyword. The rest of this
subsection's GitLab specifics (mirror, `reconcile`) have the GitHub equivalents
described under [State machine](#state-machine).

**On GitLab it's a full GitLab issue URL, not a bare `#<iid>`** —
this is a deliberate correction of the origin plan's literal example, not an oversight.
The plan's pseudocode shows `Closes #<iid>`; `test_environment/mxfix/git_target.py` uses
`Closes http://82.103.77.45/marxman/mx-admin/-/issues/<iid>` instead. Reason: the commit
is pushed to **GitHub** (`spyderbg/mx-admin-cloud`) before it ever reaches GitLab, and
GitHub parses that exact same `Closes #<number>` syntax against its **own** issue/PR
numbering in that repository — completely unrelated to the GitLab issue's `iid`. A bare
`Closes #7` in a commit destined for GitHub could silently auto-close an unrelated
GitHub issue or PR numbered 7 on merge. GitLab's own closing-pattern parser accepts a
full issue URL just as well as a bare `#iid`, and a non-`github.com` URL means nothing
special to GitHub's parser — so the URL form satisfies GitLab's auto-close while being
completely inert on GitHub. The PR body's back-link goes one step further and avoids
GitHub closing keywords entirely (`GitLab issue: <url>`, never `Closes`), so it can't
even attempt to act on it.

Sequence, once a candidate's checks are green:

1. `git add -A && git commit` in the worktree — message ends with `Closes <tracker reference>`.
2. `git push` the branch to `origin` (GitHub) — **only if `[git] push = true`**.
3. Open a GitHub PR via the REST API (`httpx` + `GITHUB_PAT` from `.env` — no `gh` CLI
   dependency) — **only if `[git] push = true` and `[git] open_pr = true`**. PR base is
   `[git] base_branch`; body cross-links the tracked issue.
4. An issue comment links back to the PR. **The issue is not closed here** — it stays
   `in-progress` until the PR actually merges. Closing on "PR opened" would mark
   unreviewed work as done.
5. On merge (GitLab), the GitHub→GitLab mirror pushes those commits to GitLab's default branch,
   GitLab parses the `Closes <url>` reference and closes the issue itself.
   `issues.py reconcile` ([Issue tracking](#issue-tracking), above) sweeps
   issues whose GitLab state is closed but whose label isn't `status::done` and corrects
   the label — the same mechanism that already resolves the mirror/`issues.py done` race
   documented in that section's [State machine](#state-machine).

The branch itself never reaches GitLab — the mirror is one-way and push-only (`main` +
tags) — so the GitLab issue ends up linking to a GitHub PR across that one-way boundary.
Inherent to the current topology, not something `run` can fix on its own; see
implementation_plan.md's "Out of scope" section on native GitLab MRs.

`run` refuses to start `heal`/`work`/`batch` for real (not under `--dry-run`) when
`[git] open_pr = true` but `GITHUB_PAT` is empty or unset in `.env`. `GITHUB_PAT` is
configured and verified working (a live, read-only `GET /repos/{owner_repo}` call
confirmed the token authenticates with `push`/`admin` permissions on the repo) — but the
PR-opening path itself still hasn't been exercised end-to-end by an actual fixer run.
`git push` itself doesn't need `GITHUB_PAT` at all (it uses whatever git credential you
already push with); the PAT is only for the REST API call that opens the PR.

### The bisect fallback for batch

A single merged-and-verified check run can't say which of `concurrency` candidates broke
it. Rather than send the whole batch to human review, `strategy/batch.py`'s `_bisect()`
recurses: verify the first half of the merged set on a fresh scratch integration
worktree, then the second, recursing into whichever half is red. `log2(N)` extra verify
runs instead of one per candidate — at the default `concurrency = 3`, at most two extra.
A subset that verifies green lands (pushed, PR'd) on the strength of *that* verify, not a
final combined re-verify of the whole exonerated set — a deliberate cost tradeoff, not an
oversight (a merge conflict at the real final re-merge is still caught, just not a test
failure introduced only by the full combination).

This is a heuristic, stated plainly, not a guarantee: a fault caused by an *interaction*
between two individually-fine candidates can in principle survive bisection undetected,
same as it would survive any test suite that doesn't specifically test that interaction.
That's the plan's own accepted tradeoff (risk #14), not something this implementation
adds on top.

A candidate that doesn't merge cleanly onto the integration branch at all is detected
before any verification happens and returned to `open` with a `merge-conflict`
comment, for a later run to retry alone.

#### batch has no cross-invocation retry counter

`heal`/`work`'s retry loop (`attempt` up to `[limits] max_fix_attempts`, feedback fed
back each time) lives entirely within one `run` call. `batch` gives each selected issue
exactly one fixer attempt per invocation — there's no per-round retry before the
merge+verify step, matching the plan's own pseudocode, which draws no inner retry loop
for batch. A candidate `batch` isolates as the culprit (via bisection), or whose fixer
attempt fails outright, or whose diff is rejected, or that won't merge cleanly, always
goes back to plain `open` with the failure recorded as an issue comment —
**never** `needs-human`; only `heal`/`work`'s counted retry loop sets that. The
next `run` (another `batch` round, or a `heal`/`work` run for a focused, attempt-counted
retry) picks it up from there. Persisting a retry count for `batch` specifically across separate process
invocations would need a new mechanism (there's nowhere in the current status/fingerprint
schema to put it) — a known, named gap, not a silent one.

### `testenv.py run` CLI reference

```
testenv.py run [--strategy report|heal|batch|work] [--max-issues N]
                [--max-fix-attempts N] [--concurrency N] [--severity-threshold high|medium|low]
                [--max-new-issues N] [--adapter codex|claude] [--timeout-seconds N]
                [--base-branch NAME] [--branch-prefix PREFIX] [--push | --no-push]
                [--open-pr | --no-open-pr] [--tracker gitlab|github] [--dry-run] [--print-config]
```

Every `testenv.toml` setting except `[issues.github]` (per-repository setup, overridden in
`testenv.local.toml` only) has a matching `--<setting>` flag — `testenv.py run --help`
is the exact, always-current list; `testenv.toml`'s own comments note each one right next
to the setting it overrides. Only the flags you actually pass take effect (an unset flag
never clobbers `testenv.toml`/`testenv.local.toml` with some CLI-side default), and a
flag always wins over both files for that one invocation — see `testenv.toml`'s
"Resolution order" comment at the bottom of the file.

| Flag | Effect |
|---|---|
| `--strategy` | Overrides `testenv.toml`'s top-level `[strategy]` for this invocation only. |
| `--max-issues N` | Caps how many issues this invocation works on. `heal`/`work`: default unlimited (works through every eligible issue) — no `testenv.toml` key of its own. `batch`: default `[limits] concurrency`; passing a smaller N shrinks that round's selection below `--concurrency`, never raises it above. |
| `--max-fix-attempts` / `--concurrency` / `--severity-threshold` / `--max-new-issues` | Override the matching `[limits]` key. |
| `--adapter` / `--timeout-seconds` | Override the matching `[fixer]` key. |
| `--base-branch` / `--branch-prefix` / `--push`/`--no-push` / `--open-pr`/`--no-open-pr` | Override the matching `[git]` key. The push/open-pr pairs are the tri-state pattern (unset = whatever `testenv.toml` says; the flag always wins either direction). |
| `--tracker` | Overrides `[issues] tracker` — see [Choosing a tracker](#choosing-a-tracker). |
| `--dry-run` | Checks still run and `sync` still previews for `report`/`heal`/`batch` (same as omitting `--apply` on `issues.py sync` — `work` has no check/sync step to preview), but no fixer runs, no worktree is created, and nothing is written to the issue tracker or GitHub. Prints the plan instead: which issue(s), which worktree/branch names, which fixer, and whether it would push/open a PR. |
| `--print-config` | Prints the fully-merged config — including any other flags passed alongside it — and exits. Runs nothing else: no issue-tracker connection, no check suite, no dirty-tree check. |

Preflight `run` always does before touching anything (beyond `--print-config`, which
skips all of it):

1. Load and validate `testenv.toml` (+ `testenv.local.toml` + CLI overrides).
2. Refuse if the **main** working tree has uncommitted changes.
3. Connect to the `[issues]` tracker — the same connection `issues.py doctor` checks
   ([Issue tracking](#issue-tracking)); a GitHub project without its status field fails
   here, before anything is filed.
4. `report` stops here (after its own check→sync). `heal`/`work`/`batch` additionally:
   run the selected fixer's `preflight()`, and — only if `[git] open_pr = true` —
   confirm `GITHUB_PAT` is set and the `origin` remote looks like a `github.com` remote.

### Trying it for the first time

In order, each step strictly safer than the next:

1. `testenv.py run --print-config` — confirms `testenv.toml` loads and validates; no
   network calls at all.
2. `testenv.py run --strategy report` (commit or stash first) — runs the full check
   suite and `sync --apply`s real findings into the issue tracker. No code is touched. This is the
   strategy meant to run unattended, so this is also the safe way to see current signal
   before ever selecting `heal`/`work`/`batch`.
3. `testenv.py run --strategy heal --dry-run`,
   `testenv.py run --strategy batch --dry-run`, and — once step 2 has actually filed
   something — `testenv.py run --strategy work --dry-run` — exercises fixer
   `preflight()` for real and prints exactly what each would do, still without touching
   a worktree or the issue tracker.
4. Only once 1–3 look right: `testenv.py run --strategy heal --max-issues 1` against a
   single, low-severity issue, with `[git] push = false` in `testenv.local.toml` first
   (land locally, inspect the commit and worktree by hand, decide about pushing
   separately) before ever trusting `push`/`open_pr = true` unattended. `work
   --max-issues 1` behaves identically once the tracker already has something `open`
   — the only difference from `heal` is skipping the check/file step first.

Nothing in this repository has run step 4 yet.

## Known gaps (sandbox)

- **The specs ride on CSS classes, so a frontend refactor breaks them.** The frontend
  has almost no `data-testid`s; every selector lives in `e2e/fixtures/selectors.ts`, so a
  renamed class is a one-file fix there. Viewport matters too: the item page's action
  buttons only render inline when its container is at least 64rem wide, and at Desktop Chrome's
  1280px they sit in the "⋯" menu instead. Use `itemDetail.clickItemAction()`, which handles both.
- **Only `speaktor` TTS is stub-covered**, so `seed` enables it and disables every
  other `text_to_speech/*.json`, whatever the real `backend/config` enables
  (`testenv_config.py`'s `isolate_tts_to_speaktor`). Voice generation on item create is
  opt-in (`84bc38e`), so `06-item-create` ticks "Generate the voices".
- **Four providers can't be redirected without a source change**: `elevenlabs_tts`,
  `static_tts`, `openai_tts` (hardcoded `API_BASE`/`ENDPOINT` module constants), and the
  `genai` SDK provider (`genai.Client(http_options=...)` never takes a base URL). Each is
  a redundant implementation of a capability the stub already covers elsewhere (TTS via
  `speaktor`, Gemini imagery via `genai_http`), so no *capability* goes untested — see
  implementation plan §4.
- **`genai_http` and `comfyui` are enabled and stub-covered, but no named spec reaches
  them** — `TextToImageService.generate()` always picks the single highest-priority
  active provider (`openai_http`) with no fallback chain (unlike translation, which does
  chain). Verified directly via `POST /internal/image-generators/{provider}/generate`;
  nothing wires that into an automated spec yet.
- **A checkout with no repo-root `.env`** will see ~20 pre-existing backend pytest
  failures asserting `require_auth` behavior (`test_content_service_integration.py` and
  others) — they need `AUTH_DISABLED=false` to hold, which nothing here sets (the test
  environment deliberately never reads the real `.env`). Confirmed unrelated to this
  workstream by rerunning one in isolation with `AUTH_DISABLED=false` explicitly set.

## Troubleshooting

**`docker: command not found` right after installing/starting Docker Desktop.** Normal —
Desktop adds `~/.docker/bin` to `PATH` via a shell-profile change that only takes effect
in a *new* shell, even though the daemon is already up. `testenv.py`/`testenv.sh` fall
back to the well-known install locations automatically (`test_environment/testenvlib/dockerutil.py`)
and `doctor` prints a note when it had to. If you invoke `docker`/`docker compose`
yourself outside these scripts, either open a new terminal or
`export PATH="$HOME/.docker/bin:$PATH"` first.

**`docker compose up` fails with `error getting credentials: exec:
"docker-credential-desktop": executable file not found in $PATH`.** Same root cause as
above — the credential helper lives right next to the `docker` binary itself, so a
`docker` found only via the fallback needs its directory added to the *child process's*
`PATH` too, not just resolved for the parent call. `testenv.py` does this for you; if
you're running `docker compose` by hand, add `~/.docker/bin` to your own `PATH`.

**Port already in use.** `doctor`/`up` check before doing anything, and `up` refuses
cleanly rather than stealing the port — but if something else on your machine happens to
be using one of the ports above, pass `--backend-port`/`--frontend-port`/`--stub-port`.

**A spec fails with counts or content that don't match this file's expectations.**
Run `./test_environment/testenv.sh seed --check-ts` — if `content/` changed since the last seed,
this reports exactly what drifted instead of leaving you to debug what looks like an app
bug.
