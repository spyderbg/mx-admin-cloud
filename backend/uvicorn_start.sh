#!/bin/bash
# Start backend twice: public HTTP (0.0.0.0:8000) and CLI-only Unix socket (/tmp/kiddo-backend.sock).
# Writes backend/uvicorn.pid (one PID per line: HTTP, then UDS) for ./kill_uvicorn.sh.
# Uses python -m uvicorn so the same interpreter as your venv is used; nohup keeps both processes
# alive after this script exits (they would otherwise get SIGHUP from the closing shell).
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"
export PYTHONPATH="$SCRIPT_DIR/src:$PYTHONPATH"

PID_FILE="$SCRIPT_DIR/uvicorn.pid"
UDS_SOCKET="/tmp/kiddo-backend.sock"

if [[ -f "$PID_FILE" ]]; then
  echo "Refusing to start: $PID_FILE already exists. Run ./kill_uvicorn.sh first." >&2
  exit 1
fi

if [[ -n "${UVICORN_PYTHON:-}" ]]; then
  PY="$UVICORN_PYTHON"
elif [[ -x "$SCRIPT_DIR/.venv/bin/python" ]]; then
  PY="$SCRIPT_DIR/.venv/bin/python"
elif [[ -x "$SCRIPT_DIR/../.venv/bin/python" ]]; then
  PY="$SCRIPT_DIR/../.venv/bin/python"
else
  PY="$(command -v python3)"
fi
if [[ -z "$PY" || ! -x "$PY" ]]; then
  echo "Error: no Python interpreter found. Use .venv (see README) or set UVICORN_PYTHON." >&2
  exit 1
fi
if ! "$PY" -c "import uvicorn" 2>/dev/null; then
  echo "Error: $PY has no 'uvicorn' module installed. Create backend/.venv (see README) or set UVICORN_PYTHON to an interpreter with dependencies installed." >&2
  exit 1
fi

# Remove stale socket if present (e.g. from a previous run)
[[ -S "$UDS_SOCKET" ]] && rm -f "$UDS_SOCKET"

# 1) Public listener
nohup "$PY" -m uvicorn src.main:app --reload --host 0.0.0.0 --port 8000 \
  >> /tmp/backend_http.log 2>&1 &
HTTP_PID=$!

# 2) CLI listener (Unix domain socket only)
nohup "$PY" -m uvicorn src.main:app --reload --uds "$UDS_SOCKET" \
  >> /tmp/backend_uds.log 2>&1 &
UDS_PID=$!

{
  echo "$HTTP_PID"
  echo "$UDS_PID"
} > "$PID_FILE"

# Wait for the socket file to appear, then restrict permissions
for _ in 1 2 3 4 5 6 7 8 9 10; do
  sleep 1
  if [[ -S "$UDS_SOCKET" ]]; then
    chmod 600 "$UDS_SOCKET"
    echo "Backend started: HTTP on :8000 (PID $HTTP_PID), CLI socket at $UDS_SOCKET (PID $UDS_PID)."
    echo "PIDs recorded in $PID_FILE"
    exit 0
  fi
done

echo "Warning: Socket $UDS_SOCKET did not appear in time. HTTP backend may still be running (PID $HTTP_PID)." >&2
echo "PIDs recorded in $PID_FILE (run ./kill_uvicorn.sh to stop)."
exit 0
