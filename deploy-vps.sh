#!/usr/bin/env bash
# deploy-vps.sh — local GHCR pull-based VPS deploy helper.
# Required env:
#   GHCR_PULL_USERNAME
#   GHCR_PULL_TOKEN
# Optional env:
#   VPS_HOST
#   VPS_SSH_USER
#   VPS_SSH_PORT
#   VPS_APP_DIR
#   GHCR_REGISTRY
#   GHCR_OWNER
#   GHCR_IMAGE_PREFIX
#   IMAGE_TAG
set -euo pipefail

VPS_HOST="${VPS_HOST:-217.216.66.68}"
VPS_SSH_USER="${VPS_SSH_USER:-root}"
VPS_SSH_PORT="${VPS_SSH_PORT:-22}"
VPS_APP_DIR="${VPS_APP_DIR:-/srv/banking_agent}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.vps.yml}"
GHCR_REGISTRY="${GHCR_REGISTRY:-ghcr.io}"
GHCR_OWNER="${GHCR_OWNER:-stepheng323}"
GHCR_IMAGE_PREFIX="${GHCR_IMAGE_PREFIX:-banking-agent-vps}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
LOCAL_ENV_FILE="${LOCAL_ENV_FILE:-.env}"
REMOTE_ENV_FILE="$VPS_APP_DIR/.env"

: "${GHCR_PULL_USERNAME:?set GHCR_PULL_USERNAME}"
: "${GHCR_PULL_TOKEN:?set GHCR_PULL_TOKEN}"

VPS_GATEWAY_IMAGE="${VPS_GATEWAY_IMAGE:-${GHCR_REGISTRY}/${GHCR_OWNER}/${GHCR_IMAGE_PREFIX}-gateway:${IMAGE_TAG}}"
VPS_CORE_IMAGE="${VPS_CORE_IMAGE:-${GHCR_REGISTRY}/${GHCR_OWNER}/${GHCR_IMAGE_PREFIX}-core:${IMAGE_TAG}}"
VPS_CORE_CHAT_WORKER_IMAGE="${VPS_CORE_CHAT_WORKER_IMAGE:-${GHCR_REGISTRY}/${GHCR_OWNER}/${GHCR_IMAGE_PREFIX}-core-chat-worker:${IMAGE_TAG}}"
VPS_TRANSACTION_WORKER_IMAGE="${VPS_TRANSACTION_WORKER_IMAGE:-${GHCR_REGISTRY}/${GHCR_OWNER}/${GHCR_IMAGE_PREFIX}-transaction-worker:${IMAGE_TAG}}"
VPS_RECEIPT_WORKER_IMAGE="${VPS_RECEIPT_WORKER_IMAGE:-${GHCR_REGISTRY}/${GHCR_OWNER}/${GHCR_IMAGE_PREFIX}-receipt-worker:${IMAGE_TAG}}"

SSH_OPTS=(
  -p "$VPS_SSH_PORT"
  -o StrictHostKeyChecking=no
  -o UserKnownHostsFile=/dev/null
)
REMOTE="${VPS_SSH_USER}@${VPS_HOST}"

echo "▶ Ensuring remote app directory exists at $REMOTE:$VPS_APP_DIR ..."
ssh "${SSH_OPTS[@]}" "$REMOTE" "mkdir -p '$VPS_APP_DIR/deploy'"

echo "▶ Syncing deploy assets to $REMOTE:$VPS_APP_DIR ..."
rsync -az \
  -e "ssh ${SSH_OPTS[*]}" \
  docker-compose.vps.yml \
  deploy/ \
  "$REMOTE:$VPS_APP_DIR/"

if [[ -f "$LOCAL_ENV_FILE" ]]; then
  echo "▶ Syncing env file to $REMOTE:$REMOTE_ENV_FILE ..."
  rsync -az -e "ssh ${SSH_OPTS[*]}" "$LOCAL_ENV_FILE" "$REMOTE:$REMOTE_ENV_FILE"
else
  echo "▶ Verifying remote env file exists at $REMOTE:$REMOTE_ENV_FILE ..."
  if ! ssh "${SSH_OPTS[@]}" "$REMOTE" "test -f '$REMOTE_ENV_FILE'"; then
    echo "✗ Missing env file. Expected local $LOCAL_ENV_FILE or remote $REMOTE_ENV_FILE."
    exit 1
  fi
fi

echo "▶ Pulling and restarting stack on VPS ..."
ssh "${SSH_OPTS[@]}" "$REMOTE" \
  "APP_DIR='$VPS_APP_DIR' \
  COMPOSE_FILE='$COMPOSE_FILE' \
  GHCR_PULL_USERNAME='$GHCR_PULL_USERNAME' \
  GHCR_PULL_TOKEN='$GHCR_PULL_TOKEN' \
  VPS_GATEWAY_IMAGE='$VPS_GATEWAY_IMAGE' \
  VPS_CORE_IMAGE='$VPS_CORE_IMAGE' \
  VPS_CORE_CHAT_WORKER_IMAGE='$VPS_CORE_CHAT_WORKER_IMAGE' \
  VPS_TRANSACTION_WORKER_IMAGE='$VPS_TRANSACTION_WORKER_IMAGE' \
  VPS_RECEIPT_WORKER_IMAGE='$VPS_RECEIPT_WORKER_IMAGE' \
  bash -s" <<'REMOTE_SCRIPT'
set -euo pipefail
cd "$APP_DIR"

export DOCKER_CONFIG
DOCKER_CONFIG="$(mktemp -d)"
trap 'rm -rf "$DOCKER_CONFIG"' EXIT
printf '%s' "$GHCR_PULL_TOKEN" | docker login ghcr.io -u "$GHCR_PULL_USERNAME" --password-stdin

pull_service() {
  local service="$1"
  local attempt=1
  until [ "$attempt" -gt 3 ]; do
    if docker compose -f "$COMPOSE_FILE" pull "$service"; then
      return 0
    fi
    if [ "$attempt" -eq 3 ]; then
      echo "Pull failed for $service after $attempt attempts"
      return 1
    fi
    echo "Pull failed for $service on attempt $attempt, retrying..."
    attempt=$((attempt + 1))
    sleep 20
  done
}

docker compose -f "$COMPOSE_FILE" config >/dev/null
pull_service gateway
pull_service core
pull_service core-chat-worker
pull_service transaction-worker
pull_service receipt-worker
docker compose -f "$COMPOSE_FILE" up -d --remove-orphans gateway core core-chat-worker transaction-worker receipt-worker caddy
docker compose -f "$COMPOSE_FILE" ps

check_health() {
  local name="$1"
  local url="$2"
  local attempt
  echo "  Waiting for $name ..."
  for attempt in $(seq 1 20); do
    if curl -fsS "$url" >/tmp/health.out 2>/dev/null; then
      echo "  ✓ $name: $(cat /tmp/health.out)"
      return 0
    fi
    sleep 5
  done
  echo "  ✗ Health check failed for $name ($url)"
  return 1
}

check_health gateway "http://localhost/health"
check_health core "http://localhost/core/health"
check_health transaction "http://localhost/transaction/health"
check_health receipt "http://localhost/receipt/health"
REMOTE_SCRIPT

echo "✅ Deploy complete."
