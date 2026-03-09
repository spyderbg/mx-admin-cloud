#!/usr/bin/env bash
# Build and start / stop backend and frontend via Docker Compose.
# Usage: app.sh [ start | stop | start backend | stop backend | start frontend | stop frontend ]
# Default (no args): start

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
export DOCKER_API_VERSION="1.41"
COMPOSE_FILE="$REPO_ROOT/docker/docker-compose.yml"

cd "$REPO_ROOT"

run_compose() {
  docker compose -f "$COMPOSE_FILE" "$@"
}

case "${1:-start}" in
  start)
    case "${2:-}" in
      backend)
        run_compose up -d --build backend
        echo "Backend started."
        ;;
      frontend)
        run_compose up -d --build frontend
        echo "Frontend started (backend started if needed)."
        ;;
      "")
        run_compose up -d --build
        echo "Backend and frontend started."
        ;;
      *)
        echo "Usage: $0 start [ backend | frontend ]" >&2
        exit 1
        ;;
    esac
    ;;
  stop)
    case "${2:-}" in
      backend)
        run_compose stop backend
        echo "Backend stopped."
        ;;
      frontend)
        run_compose stop frontend
        echo "Frontend stopped (backend left running if it was started)."
        ;;
      "")
        run_compose down
        echo "Backend and frontend stopped."
        ;;
      *)
        echo "Usage: $0 stop [ backend | frontend ]" >&2
        exit 1
        ;;
    esac
    ;;
  *)
    echo "Usage: $0 { start [ backend | frontend ] | stop [ backend | frontend ] }" >&2
    exit 1
    ;;
esac
