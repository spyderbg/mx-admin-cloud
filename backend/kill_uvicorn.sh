#!/bin/bash
# Stop uvicorn processes started by uvicorn_start.sh (reads backend/uvicorn.pid).
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$SCRIPT_DIR/uvicorn.pid"
UDS_SOCKET="/tmp/kiddo-backend.sock"

if [[ ! -f "$PID_FILE" ]]; then
  echo "No PID file at $PID_FILE. Is the server running?" >&2
  exit 1
fi

stopped=0
while read -r pid; do
  [[ -z "${pid// }" ]] && continue
  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null || true
    echo "Stopped process $pid"
    stopped=$((stopped + 1))
  else
    echo "Stale PID $pid (not running)"
  fi
done < "$PID_FILE"

rm -f "$PID_FILE"
if [[ -S "$UDS_SOCKET" ]]; then
  rm -f "$UDS_SOCKET"
  echo "Removed CLI socket $UDS_SOCKET"
fi

if [[ "$stopped" -eq 0 ]]; then
  echo "Removed PID file (no running processes matched)."
else
  echo "Uvicorn stopped ($stopped process(es))."
fi
