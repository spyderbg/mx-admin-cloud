# test_environment

An isolated, runnable sandbox for mx-admin-cloud: the real backend, the real frontend,
and a deterministic stub standing in for every external provider (OpenAI, Speaktor,
Gemini, ComfyUI, Seq), driven end to end by Playwright. Nothing here touches real
content, real credentials, or a real vendor API.

This is Workstream 1 of [docs/test_environment/implementation_plan.md](../docs/test_environment/implementation_plan.md)
— that document has the full design rationale (why a stub, why a curated content subset,
why two run modes, the Docker Compose merge semantics, and so on). This file is the
day-to-day operational reference: commands, configuration, and layout.

```
test_environment/
  e2e/                  Playwright suite — its own npm package, deliberately outside frontend/
    playwright.config.ts
    global-setup.ts      readiness probe, runs once before the suite
    auth.setup.ts         real UI login, saves storageState for every other spec
    fixtures/             selectors.ts, seed.ts, stub.ts, testEnv.ts, upload.png
    specs/                02-login.spec.ts … 10-category-create.spec.ts (numbered — see below)
  stub-provider/
    stub_server.py         stdlib-only HTTP stub for every redirected provider
```

Everything is driven through **`scripts/testenv.py`** (or its `scripts/testenv.sh`
wrapper — same thing, picks a working Python for you). You should not normally need to
invoke Docker, uvicorn, or Playwright directly.

## Quick start

```bash
./scripts/testenv.sh doctor   # mode, ports, secret scan — run this first, always
./scripts/testenv.sh up       # seeds .testenv/ if absent, starts the stack, waits for it
./scripts/testenv.sh e2e      # installs Playwright once, runs the full 22-test suite
./scripts/testenv.sh down     # stop
```

(`python scripts/testenv.py <command>` works identically — `testenv.sh` only adds
picking a Python interpreter and a stable `./scripts/testenv.sh` entry point matching
the rest of `scripts/*.sh`.)

## All available operations

| Command | What it does |
|---|---|
| `doctor` | Reports the resolved mode, Docker Compose version (if applicable), whether the stack's ports are free, and scans the environment that would be handed to child processes for anything that looks like a real credential. Safe to run any time; changes nothing. |
| `seed [--force] [--check-ts]` | (Re)builds `.testenv/{content,config,archive}` and `.testenv/test.env` from the real `content/` and `backend/config/`. `--force` wipes an existing `.testenv/content` first; without it, `seed` refuses if content is already there. `--check-ts` compares the real `content/` against the last seed's manifest and reports drift **without** reseeding — useful if a test failure might be a stale fixture rather than an app bug. |
| `up [--build] [--simple-confirm] [--backend-port N] [--frontend-port N] [--stub-port N]` | Seeds if `.testenv/content` is empty, starts stub → backend → frontend (or the three containers, in Docker mode), waits for each to answer, prints the URLs. `--build` forces a Docker image rebuild (native mode always rebuilds the frontend bundle; there's no image to skip). |
| `down [--purge]` | Stops everything. `--purge` also deletes `.testenv/` entirely (content, config, archive, the SQLite user DB, logs, Playwright artifacts) — the next `up`/`seed` starts from nothing. |
| `reset [--simple-confirm] [--backend-port N] [--frontend-port N] [--stub-port N]` | `down` + wipe `.testenv/` + `seed` + `up`, in one call. The workhorse — run this between full E2E runs so every spec starts from the identical, byte-for-byte fixture. |
| `e2e [--base-url URL] [--stub-url URL] [-- <playwright args>]` | Ensures the stack is up (starts it if not), installs `test_environment/e2e`'s npm deps and Playwright's Chromium on first use, then runs the suite. Anything after `--` is passed straight to `playwright test` — see [Running specs](#running-specs) below. |
| `logs [backend\|frontend\|stub] [--follow]` | Prints (or tails) that service's log. No argument prints all three. |
| `status` | Shows what's currently running (pid + port in native mode, `docker compose ps` in Docker mode). |
| `shell <backend\|frontend\|stub>` | Docker mode: opens a shell in that container. Native mode: there's no container — it tells you which directory the process runs from and how to tail its log instead. |

Every command accepts `--mode docker|native`, **placed before the command**
(`testenv.py --mode docker up`, not `testenv.py up --mode docker`) — a subparser-defaults
quirk in Python's argparse makes the reverse silently discard the flag if it were
allowed at all, so it isn't; you'll get a clean argument-parsing error instead of a
surprise. Omitted, it autodetects: Docker if the daemon answers, native otherwise.

Both modes are validated end to end (all 22 specs green in each) against this repo's
real backend and frontend.

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
static-file-plus-reverse-proxy server (`scripts/app.py`), mirroring `docker/nginx.conf`'s
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
(`scripts/testenvlib/envfile.py`'s `child_env()` — `PATH`/`HOME`/`LANG`/… plus whatever
`.testenv/test.env` supplies, nothing else from your shell), specifically so a real key
exported by having sourced `scripts/app.sh`/`scripts/deploy.sh` in the same shell can't
leak in. `doctor` scans for this on every run and refuses to report success on a hit.

### The seeded fixture

`seed` copies four real categories out of `content/` verbatim (never synthesised) and
slices only the row-aligned index CSVs — see
[implementation_plan.md §3](../docs/test_environment/implementation_plan.md#3-seed-fixture--a-curated-subset-copied-from-the-real-content)
for why. To change *which* categories/items are included, edit
`scripts/testenvlib/paths.py`'s `KEPT_CATEGORY_ROWS` (root CSV row index → folder name)
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

## Running specs

```bash
./scripts/testenv.sh e2e                                          # everything
./scripts/testenv.sh e2e -- specs/06-item-create.spec.ts            # one file
./scripts/testenv.sh e2e -- --headed specs/06-item-create.spec.ts   # watch it run
./scripts/testenv.sh e2e -- --debug                                 # Playwright inspector
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

## Known gaps

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
back to the well-known install locations automatically (`scripts/testenvlib/dockerutil.py`)
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
Run `./scripts/testenv.sh seed --check-ts` — if `content/` changed since the last seed,
this reports exactly what drifted instead of leaving you to debug what looks like an app
bug.
