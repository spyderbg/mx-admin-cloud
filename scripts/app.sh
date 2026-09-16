#!/usr/bin/env bash
# Build and start / stop backend and frontend via Docker Compose.
# Usage: app.sh [ start | stop | start backend | stop backend | start frontend | stop frontend | start cli | stop cli ]
# Default (no args): start

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
export DOCKER_API_VERSION="1.41"
COMPOSE_FILE="$REPO_ROOT/docker/docker-compose.yml"

cd "$REPO_ROOT"

load_env_file() {
  local env_file="$1"
  local key val line

  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"
    [[ -z "$line" || "$line" == \#* ]] && continue
    key="${line%%=*}"
    val="${line#*=}"
    [[ -z "$key" ]] && continue
    val="${val%\'}"
    val="${val#\'}"
    val="${val%\"}"
    val="${val#\"}"
    if [[ -z "${!key:-}" ]]; then
      export "$key=$val"
    fi
  done < "$env_file"
}

ENV_FILE="$REPO_ROOT/.env"
if [[ ! -f "$ENV_FILE" && -f "$REPO_ROOT/docker/.env" ]]; then
  ENV_FILE="$REPO_ROOT/docker/.env"
fi
if [[ -f "$ENV_FILE" ]]; then
  load_env_file "$ENV_FILE"
fi

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
      cli)
        run_compose --profile cli up -d --build backend-cli
        echo "CLI backend started."
        ;;
      "")
        run_compose up -d --build
        echo "Backend and frontend started."
        ;;
      *)
        echo "Usage: $0 start [ backend | frontend | cli ]" >&2
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
      cli)
        run_compose stop backend-cli
        echo "CLI backend stopped."
        ;;
      "")
        run_compose down
        echo "Backend and frontend stopped."
        ;;
      *)
        echo "Usage: $0 stop [ backend | frontend | cli ]" >&2
        exit 1
        ;;
    esac
    ;;
  *)
    echo "Usage: $0 { start [ backend | frontend | cli ] | stop [ backend | frontend | cli ] }" >&2
    exit 1
    ;;
esac
