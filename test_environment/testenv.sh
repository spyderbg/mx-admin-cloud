#!/usr/bin/env bash
# Manage the sandbox test environment (Workstream 1) — the same job app.sh does for the
# real dev stack, but for the throwaway one: test_environment/testenv.py.
# Usage: testenv.sh [--mode docker|native] { doctor | seed | up | down | reset |
#                                             logs | status | shell | e2e } [args...]
# Default (no args): up
#
# A thin launcher only — every actual behavior lives in testenv.py (and the
# test_environment/testenvlib/ package it shares with app.py); this just finds a working Python
# interpreter and hands off everything unchanged, the same shape as app.sh wrapping
# docker compose for the dev stack.
#
# Unlike app.sh, this deliberately does NOT load the repo's real .env — the test
# environment is designed to never read it (see test_environment/testenvlib/envfile.py's
# allowlist-based child_env()); sourcing real provider keys into this process before
# handing off to testenv.py would work against that on purpose, not by accident.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TESTENV_PY="$SCRIPT_DIR/testenv.py"

cd "$REPO_ROOT"

# Prefer a newer, well-tested interpreter over whatever "python3" happens to resolve
# to — mirrors test_environment/testenvlib/pyenv.py's own preference order for the backend venv
# (the app Dockerfile pins python:3.11-slim; several backend deps lag a same-day Python
# release by months).
find_python() {
  local candidate
  for candidate in python3.12 python3.11 python3.10 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}

PYTHON="$(find_python)" || {
  echo "No python3.10+ interpreter found on PATH." >&2
  exit 1
}

if [[ $# -eq 0 ]]; then
  exec "$PYTHON" "$TESTENV_PY" up
fi

exec "$PYTHON" "$TESTENV_PY" "$@"
