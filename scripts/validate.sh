#!/usr/bin/env bash
# Validate backend Python code by setting up environment, compiling, and linting.
# Usage: validate.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKEND_DIR="$REPO_ROOT/backend"
if [ -d "$REPO_ROOT/.venv" ]; then
  VENV_DIR="$REPO_ROOT/.venv"
elif [ -d "$BACKEND_DIR/.venv" ]; then
  VENV_DIR="$BACKEND_DIR/.venv"
else
  VENV_DIR="$BACKEND_DIR/.venv"
fi

if command -v python3.10 >/dev/null 2>&1; then
  PYTHON_BIN="python3.10"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
else
  echo "Error: python3.10 or python3 is required." >&2
  exit 1
fi

if [ ! -d "$VENV_DIR" ]; then
  echo "Creating backend virtual environment at $VENV_DIR"
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

VENV_PYTHON="$VENV_DIR/bin/python"
if [ ! -x "$VENV_PYTHON" ]; then
  echo "Error: virtual environment python not found at $VENV_PYTHON" >&2
  exit 1
fi

cd "$BACKEND_DIR"

echo "Installing backend dependencies"
"$VENV_PYTHON" -m pip install -r requirements.txt

echo "Compiling backend Python sources"
"$VENV_PYTHON" -m compileall -q src

echo "Linting backend Python sources"
PYTHONPATH="$BACKEND_DIR/src" "$VENV_PYTHON" -m pylint src/

echo "Validation completed successfully."
