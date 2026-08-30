#!/usr/bin/env bash
# CI/CD deployment script for mx-admin.
# Builds Docker images, starts containers, validates services, installs kiddo-cli.
# Designed to run directly on the Ubuntu 24.04 host (shell executor).
#
# Required Ubuntu 24.04 packages:
#   - docker-ce: Docker engine for container management
#   - docker-compose-v2: Docker Compose for multi-container orchestration (integrated as 'docker compose')
#   - curl: HTTP client for service health checks
#   - bash: Shell interpreter (usually pre-installed)
#
# Installation command Ubuntu 24.04 LTS:
#   sudo install -m 0755 -d /etc/apt/keyrings
#   curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
#   sudo chmod a+r /etc/apt/keyrings/docker.gpg
#   echo \
#     "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
#      https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" | \
#   sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
#   sudo apt-get update && sudo apt-get install -y docker-ce docker-compose-plugin curl
# or
#   sudo apt remove docker docker-engine docker.io containerd runc
#   sudo apt update
#   sudo apt install docker-ce docker-ce-cli containerd.io docker-compose-plugin
#
# Usage: deploy.sh [command] [--compose-file <path>] [--backend-port <port>] [--frontend-port <port>] [--start-backend-cli]
#
# Commands:
#   build            - Step 1: Build Docker images
#   start-core       - Step 2: Start core containers (backend, frontend)
#   start-cli        - Step 3: Start optional kiddo-backend-cli container
#   validate         - Step 4: Validate services health
#   install-cli      - Step 5: Install kiddo-cli tool
#   all              - Run all steps (default)
#
# Environment variables:
#   START_BACKEND_CLI: If set to "true", starts the kiddo-backend-cli container.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

COMPOSE_FILE="$REPO_ROOT/docker/docker-compose.yml"
BACKEND_PORT="9000"
FRONTEND_PORT="9001"
START_BACKEND_CLI="$(echo "$START_BACKEND_CLI" | tr '[:upper:]' '[:lower:]' | tr -d ' ')"
SERVICE_HOST="localhost"
MAX_RETRIES=30
RETRY_INTERVAL=2
export DOCKER_API_VERSION="1.41"

COMMAND="all"
if [[ $# -gt 0 && ! "$1" =~ ^-- ]]; then
  COMMAND="$1"
  shift
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --compose-file) COMPOSE_FILE="$2"; shift 2 ;;
    --backend-port) BACKEND_PORT="$2"; shift 2 ;;
    --frontend-port) FRONTEND_PORT="$2"; shift 2 ;;
    --start-backend-cli) START_BACKEND_CLI="true"; shift ;;
    --help|-h|help) COMMAND="help"; shift ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

# ── Step 1: Build Docker images ──────────────────────────────────────────────
build() {
  echo "==> Building Docker images..."
  docker compose -f "$COMPOSE_FILE" build
  echo "    Done: Images built."
}

# ── Step 2: Create or update containers ──────────────────────────────────────
start-core() {
  echo "==> Starting core containers (backend, frontend)..."
  docker compose -f "$COMPOSE_FILE" up -d
  echo "    Done: Core containers started."
}

# ── Step 3: Start backend-cli container (optional) ───────────────────────────
start-cli() {
  echo "Start backend-cli: $START_BACKEND_CLI"
  if [[ "$START_BACKEND_CLI" == "true" ]]; then
    echo "==> Starting optional kiddo-backend-cli container..."
    docker compose -f "$COMPOSE_FILE" --profile cli up -d backend-cli
    echo "    Done: kiddo-backend-cli started."
  else
    echo "==> Skipping kiddo-backend-cli container (START_BACKEND_CLI != true)."
    if docker ps --format '{{.Names}}' | grep -q "^kiddo-backend-cli$"; then
      echo "==> Stopping existing kiddo-backend-cli container..."
      docker compose -f "$COMPOSE_FILE" --profile cli stop backend-cli
      echo "    Done: kiddo-backend-cli stopped."
    fi
  fi
}

# ── Step 4: Validate services ────────────────────────────────────────────────
validate() {
  echo "==> Waiting for services on ${SERVICE_HOST} (backend:${BACKEND_PORT}, frontend:${FRONTEND_PORT})..."

  for i in $(seq 1 "$MAX_RETRIES"); do
    backend_ok=false
    frontend_ok=false
    cli_ok=true

    if curl -sf "http://${SERVICE_HOST}:${BACKEND_PORT}/" >/dev/null 2>&1; then
      backend_ok=true
    fi

    http_code=$(curl -sf -o /dev/null -w "%{http_code}" "http://${SERVICE_HOST}:${FRONTEND_PORT}/" 2>/dev/null || echo "000")
    if echo "$http_code" | grep -qE '^(200|301|302)$'; then
      frontend_ok=true
    fi

    if [[ "$START_BACKEND_CLI" == "true" ]]; then
      if ! docker ps --format '{{.Names}}' | grep -q "^kiddo-backend-cli$"; then
        cli_ok=false
      fi
    fi

    if $backend_ok && $frontend_ok && $cli_ok; then
      echo "    Services are up."
      break
    fi

    if [ "$i" -eq "$MAX_RETRIES" ]; then
      echo "    Timeout after $((MAX_RETRIES * RETRY_INTERVAL))s waiting for services." >&2
      docker compose -f "$COMPOSE_FILE" ps
      docker compose -f "$COMPOSE_FILE" logs --tail=50
      exit 1
    fi

    sleep "$RETRY_INTERVAL"
  done

  echo "    Backend response:"
  curl -s "http://${SERVICE_HOST}:${BACKEND_PORT}/"
  echo ""
  echo "    Frontend HTTP status: $(curl -s -o /dev/null -w '%{http_code}' "http://${SERVICE_HOST}:${FRONTEND_PORT}/")"
  if [[ "$START_BACKEND_CLI" == "true" ]]; then
    echo "    CLI container status: $(docker inspect -f '{{.State.Status}}' kiddo-backend-cli)"
  fi
}

# ── Step 5: Install kiddo-cli ────────────────────────────────────────────────
install-cli() {
  echo "==> Installing kiddo-cli..."
  bash "$SCRIPT_DIR/cli.sh" install
  echo "    Done: kiddo-cli installed."
  kiddo-cli --help
}

final-report() {
  echo "==> Deployment status check..."
  echo "    Containers running:"
  docker compose -f "$COMPOSE_FILE" ps
  echo "==> Done."
}

case "$COMMAND" in
  build)
    build
    ;;
  start-core)
    start-core
    ;;
  start-cli)
    start-cli
    ;;
  validate)
    validate
    ;;
  install-cli)
    install-cli
    ;;
  all)
    build
    start-core
    start-cli
    validate
    install-cli
    final-report
    ;;
  help)
    echo "Usage: $0 [command] [--compose-file <path>] [--backend-port <port>] [--frontend-port <port>] [--start-backend-cli]"
    echo ""
    echo "Commands:"
    echo "  build            - Step 1: Build Docker images"
    echo "  start-core       - Step 2: Start core containers (backend, frontend)"
    echo "  start-cli        - Step 3: Start optional kiddo-backend-cli container"
    echo "  validate         - Step 4: Validate services health"
    echo "  install-cli      - Step 5: Install kiddo-cli tool"
    echo "  all              - Run all steps (default)"
    echo ""
    echo "Options:"
    echo "  --compose-file      Path to docker-compose.yml"
    echo "  --backend-port      Backend port (default: 9000)"
    echo "  --frontend-port     Frontend port (default: 9001)"
    echo "  --start-backend-cli Force start of the backend CLI container"
    exit 0
    ;;
  *)
    echo "Unknown command: $COMMAND" >&2
    echo "Usage: $0 [build|start-core|start-cli|validate|install-cli|all]" >&2
    exit 1
    ;;
esac
