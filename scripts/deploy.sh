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
# Usage: deploy.sh [--compose-file <path>] [--backend-port <port>] [--frontend-port <port>] [--start-backend-cli]
#
# Environment variables:
#   START_BACKEND_CLI: If set to "true", starts the kiddo-backend-cli container.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

COMPOSE_FILE="$REPO_ROOT/docker/docker-compose.yml"
BACKEND_PORT="9000"
FRONTEND_PORT="9001"
START_BACKEND_CLI="${START_BACKEND_CLI:-false}"
SERVICE_HOST="localhost"
MAX_RETRIES=30
RETRY_INTERVAL=2
export DOCKER_API_VERSION="1.41"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --compose-file) COMPOSE_FILE="$2"; shift 2 ;;
    --backend-port) BACKEND_PORT="$2"; shift 2 ;;
    --frontend-port) FRONTEND_PORT="$2"; shift 2 ;;
    --start-backend-cli) START_BACKEND_CLI="true"; shift ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

# ── Step 1: Build Docker images ──────────────────────────────────────────────

echo "==> Building Docker images..."
docker compose -f "$COMPOSE_FILE" build

# ── Step 2: Create or update containers ──────────────────────────────────────

echo "==> Starting containers..."
docker compose -f "$COMPOSE_FILE" up -d

# ── Step 3: Validate services ────────────────────────────────────────────────

echo "==> Waiting for services on ${SERVICE_HOST} (backend:${BACKEND_PORT}, frontend:${FRONTEND_PORT})..."

for i in $(seq 1 "$MAX_RETRIES"); do
  backend_ok=false
  frontend_ok=false

  if curl -sf "http://${SERVICE_HOST}:${BACKEND_PORT}/" >/dev/null 2>&1; then
    backend_ok=true
  fi

  http_code=$(curl -sf -o /dev/null -w "%{http_code}" "http://${SERVICE_HOST}:${FRONTEND_PORT}/" 2>/dev/null || echo "000")
  if echo "$http_code" | grep -qE '^(200|301|302)$'; then
    frontend_ok=true
  fi

  if $backend_ok && $frontend_ok; then
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

# ── Step 4: Install kiddo-cli ────────────────────────────────────────────────

echo "==> Installing kiddo-cli..."
bash "$SCRIPT_DIR/cli.sh" install
kiddo-cli --help
echo "==> Deployment complete."

# ── Step 5: Start backend-cli container (optional) ───────────────────────────

if [[ "$START_BACKEND_CLI" == "true" ]]; then
  echo "==> Starting kiddo-backend-cli container..."
  docker compose -f "$COMPOSE_FILE" --profile cli up -d backend-cli
  echo "    kiddo-backend-cli is running."
fi
