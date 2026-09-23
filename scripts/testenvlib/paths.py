"""Path, port, and fixture constants shared by every test-environment script.

Single source of truth for where `.testenv/` lives and what it contains, so
testenv_seed.py, testenv_config.py, app.py, stub_server.py, and testenv.py
can never disagree about a path or a default port.
"""

from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------------------
# Repo layout
# ---------------------------------------------------------------------------

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = SCRIPTS_DIR.parent

BACKEND_DIR = REPO_ROOT / "backend"
FRONTEND_DIR = REPO_ROOT / "frontend"
DOCKER_DIR = REPO_ROOT / "docker"

REAL_CONTENT_DIR = REPO_ROOT / "content"
REAL_CONFIG_DIR = BACKEND_DIR / "config"

STUB_PROVIDER_DIR = REPO_ROOT / "test_environment" / "stub-provider"
STUB_SERVER_SCRIPT = STUB_PROVIDER_DIR / "stub_server.py"

E2E_DIR = REPO_ROOT / "test_environment" / "e2e"

# ---------------------------------------------------------------------------
# .testenv/ — throwaway, gitignored, rebuilt by `testenv.py seed` / `reset`
# ---------------------------------------------------------------------------

TESTENV_DIR = REPO_ROOT / ".testenv"

TEST_ENV_FILE = TESTENV_DIR / "test.env"
TEST_CONTENT_DIR = TESTENV_DIR / "content"
TEST_ARCHIVE_DIR = TESTENV_DIR / "archive"
TEST_CONFIG_DIR = TESTENV_DIR / "config"
# Not part of the plan's original directory sketch, added here so native mode never opens
# the real backend/data/users.db or writes logs into the repo's real output/ directory —
# both would either corrupt real dev state or defeat `reset`'s "byte-identical fixture"
# guarantee (see docs/test_environment/implementation_plan.md's Task order, note under A2).
TEST_DATA_DIR = TESTENV_DIR / "data"
TEST_OUTPUT_DIR = TESTENV_DIR / "output"
TEST_ARTIFACTS_DIR = TESTENV_DIR / "artifacts"
TEST_LOGS_DIR = TESTENV_DIR / "logs"  # native-mode process stdout/stderr, not app logs

SEED_MANIFEST_PATH = TEST_CONTENT_DIR / ".mx-seed.json"

# Directories that must exist (possibly empty) after a successful seed.
TESTENV_SUBDIRS = (
    TEST_CONTENT_DIR,
    TEST_ARCHIVE_DIR,
    TEST_DATA_DIR,
    TEST_OUTPUT_DIR,
    TEST_ARTIFACTS_DIR,
    TEST_LOGS_DIR,
)

PID_FILE = TESTENV_DIR / "native.pids.json"

# ---------------------------------------------------------------------------
# The curated content subset (Workstream 1, §3 of the implementation plan)
# ---------------------------------------------------------------------------

# (0-based row index into categories_ids.csv / categories_{lang}.csv /
#  descriptions_{lang}.csv, on-disk folder name). Order here is the order rows are
# written into the sliced root CSVs, i.e. the dashboard's card order.
KEPT_CATEGORY_ROWS: tuple[tuple[int, str], ...] = (
    (0, "at_school"),
    (1, "drinks"),
    (4, "at_home"),
    (5, "fruits"),
)

# 0-based row indices into a category's own items_ids.csv / items_{lang}.csv, kept in
# ascending order (the slice preserves original relative order; nothing downstream
# depends on a specific item order). A category absent from this map has no
# items_*.csv at all on disk (at_home) — nothing to slice, no item subfolders to filter.
KEPT_ITEM_ROWS: dict[str, tuple[int, ...]] = {
    "drinks": (0, 9),       # coffee, water
    "fruits": (0, 1),       # apricot, avocado
    "at_school": (1, 2),    # pencil, water_bottle
}

SUPPORTED_LANGUAGES: tuple[str, ...] = ("en", "fr", "es", "bg")

ROOT_CATEGORY_ID_FILE = "categories_ids.csv"
ROOT_CATEGORY_LANG_FILES = tuple(f"categories_{lang}.csv" for lang in SUPPORTED_LANGUAGES)
ROOT_DESCRIPTION_LANG_FILES = tuple(f"descriptions_{lang}.csv" for lang in SUPPORTED_LANGUAGES)
# Orphan file: no categories_it.csv, "it" not in SUPPORTED_LANGUAGES, never read by the
# app — kept and sliced anyway so the quirk (dead file, present but inert) survives into
# the fixture. See content-quirks.spec.ts.
ROOT_DESCRIPTION_IT_FILE = "descriptions_it.csv"

ROOT_SLICED_CSV_FILES: tuple[str, ...] = (
    (ROOT_CATEGORY_ID_FILE,)
    + ROOT_CATEGORY_LANG_FILES
    + ROOT_DESCRIPTION_LANG_FILES
    + (ROOT_DESCRIPTION_IT_FILE,)
)

ITEMS_IDS_FILE = "items_ids.csv"
ITEMS_LANG_FILES = tuple(f"items_{lang}.csv" for lang in SUPPORTED_LANGUAGES)
ITEM_SLICED_CSV_FILES: tuple[str, ...] = (ITEMS_IDS_FILE,) + ITEMS_LANG_FILES

# ---------------------------------------------------------------------------
# Ports
# ---------------------------------------------------------------------------
# Deliberately NOT the repo's native-dev defaults (backend :8000, frontend/vite :5173)
# or the dev docker stack's ports (9000/9001) or anything near them (9100/9101/9102) —
# early testing collided with a developer's own live dev session on 8000/5173 despite
# `doctor`'s port-free check (which caught it and refused to start, correctly, but the
# collision was still an avoidable annoyance). One clearly-distinct block per mode.

NATIVE_DEFAULT_BACKEND_PORT = 48000
NATIVE_DEFAULT_FRONTEND_PORT = 48001
NATIVE_DEFAULT_STUB_PORT = 48002

DOCKER_DEFAULT_BACKEND_PORT = 48100
DOCKER_DEFAULT_FRONTEND_PORT = 48101
DOCKER_DEFAULT_STUB_HOST_PORT = 48102
# Container-internal only (like the backend image's own internal :8000 or the frontend
# image's internal :80, neither of which this project controls) — never bound on the
# host, so it isn't part of the host-collision concern above and stays a normal port.
DOCKER_STUB_CONTAINER_PORT = 8080

DOCKER_PROJECT_NAME = "mxadmin-test"
DOCKER_BACKEND_CONTAINER = "mxtest-backend"
DOCKER_FRONTEND_CONTAINER = "mxtest-frontend"
DOCKER_STUB_CONTAINER = "mxtest-stub"

# ---------------------------------------------------------------------------
# Dummy credentials — never real, deliberately obvious if they ever leaked.
# Fixed (not randomized) so `reset` stays fully deterministic and auth.setup.ts
# (test_environment/e2e) can rely on a known admin password without re-reading
# test.env's generation logic — it still reads the value from test.env at run time
# rather than hardcoding it a second time, so the two can never drift.
# ---------------------------------------------------------------------------

TEST_OPENAI_API_KEY = "test-openai-key-not-real"
TEST_GEMINI_API_KEY = "test-gemini-key-not-real"
TEST_SECRET_KEY = "test-secret-key-do-not-use-in-production"
TEST_DEFAULT_ADMIN_PASSWORD = "TestAdmin123!"


def testenv_relative(path: Path) -> str:
    """Return *path* relative to the repo root for display, or the absolute path."""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)
