#!/bin/bash
# Start backend twice: public HTTP (0.0.0.0:8000) and CLI-only Unix socket (/tmp/kiddo-backend.sock).
# After the socket is created, restrict permissions with chmod 600.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"
export PYTHONPATH="$SCRIPT_DIR/src:$PYTHONPATH"

UDS_SOCKET="/tmp/kiddo-backend.sock"

# Remove stale socket if present (e.g. from a previous run)
[[ -S "$UDS_SOCKET" ]] && rm -f "$UDS_SOCKET"

# 1) Public listener
nohup uvicorn src.main:app --reload --host 0.0.0.0 --port 8000 \
  >> /tmp/backend_http.log 2>&1 &
HTTP_PID=$!

# 2) CLI listener (Unix domain socket only)
nohup uvicorn src.main:app --reload --uds "$UDS_SOCKET" \
  >> /tmp/backend_uds.log 2>&1 &
UDS_PID=$!

# Wait for the socket file to appear, then restrict permissions
for i in 1 2 3 4 5 6 7 8 9 10; do
  sleep 1
  if [[ -S "$UDS_SOCKET" ]]; then
    chmod 600 "$UDS_SOCKET"
    echo "Backend started: HTTP on :8000 (PID $HTTP_PID), CLI socket at $UDS_SOCKET (PID $UDS_PID)."
    exit 0
  fi
done

echo "Warning: Socket $UDS_SOCKET did not appear in time. HTTP backend may still be running (PID $HTTP_PID)." >&2
exit 0
