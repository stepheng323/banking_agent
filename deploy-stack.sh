#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: ./deploy-stack.sh [remote|local]

Canonical stack entrypoint for local and VPS runs.

Modes:
  remote   Sync deploy assets to the VPS, then pull and restart the stack there.
  local    Run the same VPS compose stack locally from this checkout.

Required env when pulling from GHCR:
  GHCR_PULL_USERNAME
  GHCR_PULL_TOKEN

Optional env:
  VPS_HOST
  VPS_SSH_USER
  VPS_SSH_PORT
  VPS_APP_DIR
COMPOSE_FILE
  GHCR_REGISTRY
  GHCR_OWNER
  GHCR_IMAGE_PREFIX
  IMAGE_TAG
  LOCAL_ENV_FILE
  AWS_HOST_DIR
  SKIP_PULL=1         Skip registry login + image pulls (use preloaded local images).
  STACK_GATEWAY_IMAGE
  STACK_CHAT_WORKER_IMAGE
  STACK_TRANSACTION_WORKER_IMAGE
  STACK_RECEIPT_WORKER_IMAGE

Examples:
  ./deploy-stack.sh remote
  IMAGE_TAG=latest ./deploy-stack.sh local
  SKIP_PULL=1 IMAGE_TAG=local ./deploy-stack.sh local
EOF
}

DEPLOY_TARGET="${DEPLOY_TARGET:-${1:-remote}}"
if [[ $# -gt 0 ]]; then
  shift
fi

if [[ $# -gt 0 ]]; then
  usage
  exit 1
fi

case "$DEPLOY_TARGET" in
  remote|local) ;;
  -h|--help|help)
    usage
    exit 0
    ;;
  *)
    echo "Unknown deploy target: $DEPLOY_TARGET"
    usage
    exit 1
    ;;
esac

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

VPS_HOST="${VPS_HOST:-217.216.66.68}"
VPS_SSH_USER="${VPS_SSH_USER:-root}"
VPS_SSH_PORT="${VPS_SSH_PORT:-22}"
VPS_APP_DIR="${VPS_APP_DIR:-/srv/banking_agent}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"
GHCR_REGISTRY="${GHCR_REGISTRY:-ghcr.io}"
GHCR_OWNER="${GHCR_OWNER:-stepheng323}"
GHCR_IMAGE_PREFIX="${GHCR_IMAGE_PREFIX:-banking-agent-vps}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
LOCAL_ENV_FILE="${LOCAL_ENV_FILE:-.env}"
SKIP_PULL="${SKIP_PULL:-0}"

if [[ "$DEPLOY_TARGET" == "local" ]]; then
  DEFAULT_AWS_HOST_DIR="${HOME}/.aws"
  APP_DIR="$ROOT_DIR"
else
  DEFAULT_AWS_HOST_DIR="/root/.aws"
  APP_DIR="$VPS_APP_DIR"
fi
AWS_HOST_DIR="${AWS_HOST_DIR:-$DEFAULT_AWS_HOST_DIR}"

STACK_GATEWAY_IMAGE="${STACK_GATEWAY_IMAGE:-${GHCR_REGISTRY}/${GHCR_OWNER}/${GHCR_IMAGE_PREFIX}-gateway:${IMAGE_TAG}}"
STACK_CHAT_WORKER_IMAGE="${STACK_CHAT_WORKER_IMAGE:-${GHCR_REGISTRY}/${GHCR_OWNER}/${GHCR_IMAGE_PREFIX}-chat-worker:${IMAGE_TAG}}"
STACK_TRANSACTION_WORKER_IMAGE="${STACK_TRANSACTION_WORKER_IMAGE:-${GHCR_REGISTRY}/${GHCR_OWNER}/${GHCR_IMAGE_PREFIX}-transaction-worker:${IMAGE_TAG}}"
STACK_RECEIPT_WORKER_IMAGE="${STACK_RECEIPT_WORKER_IMAGE:-${GHCR_REGISTRY}/${GHCR_OWNER}/${GHCR_IMAGE_PREFIX}-receipt-worker:${IMAGE_TAG}}"

if [[ "$SKIP_PULL" != "1" ]]; then
  : "${GHCR_PULL_USERNAME:?set GHCR_PULL_USERNAME}"
  : "${GHCR_PULL_TOKEN:?set GHCR_PULL_TOKEN}"
fi

REMOTE_ENV_FILE="$VPS_APP_DIR/.env"
SSH_OPTS=(
  -p "$VPS_SSH_PORT"
  -o StrictHostKeyChecking=no
  -o UserKnownHostsFile=/dev/null
)
REMOTE="${VPS_SSH_USER}@${VPS_HOST}"

write_runtime_script() {
  local script_path="$1"
  cat > "$script_path" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

STACK_SERVICES=(gateway chat-worker transaction-worker receipt-worker)

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

main() {
  cd "$APP_DIR"

  if [[ "${SKIP_PULL:-0}" != "1" ]]; then
    export DOCKER_CONFIG
    DOCKER_CONFIG="$(mktemp -d)"
    trap 'rm -rf "$DOCKER_CONFIG"' EXIT
    printf '%s' "$GHCR_PULL_TOKEN" | docker login ghcr.io -u "$GHCR_PULL_USERNAME" --password-stdin
  fi

  docker compose -f "$COMPOSE_FILE" config >/dev/null

  if [[ "${SKIP_PULL:-0}" != "1" ]]; then
    for service in "${STACK_SERVICES[@]}"; do
      pull_service "$service"
    done
  fi

  docker compose -f "$COMPOSE_FILE" up -d --remove-orphans gateway chat-worker transaction-worker receipt-worker caddy
  docker compose -f "$COMPOSE_FILE" ps

  check_health gateway "http://localhost/health"
  check_health transaction "http://localhost/transaction/health"
  check_health receipt "http://localhost/receipt/health"
}

main "$@"
EOF
  chmod +x "$script_path"
}

run_local() {
  if [[ ! -f "$LOCAL_ENV_FILE" ]]; then
    echo "✗ Missing env file: $LOCAL_ENV_FILE"
    exit 1
  fi

  local runtime_script
  runtime_script="$(mktemp)"
  trap 'rm -f "$runtime_script"' RETURN
  write_runtime_script "$runtime_script"

  echo "▶ Running VPS stack locally from $APP_DIR ..."
  APP_DIR="$APP_DIR" \
  COMPOSE_FILE="$COMPOSE_FILE" \
  GHCR_PULL_USERNAME="${GHCR_PULL_USERNAME:-}" \
  GHCR_PULL_TOKEN="${GHCR_PULL_TOKEN:-}" \
  SKIP_PULL="$SKIP_PULL" \
  AWS_HOST_DIR="$AWS_HOST_DIR" \
  STACK_GATEWAY_IMAGE="$STACK_GATEWAY_IMAGE" \
  STACK_CHAT_WORKER_IMAGE="$STACK_CHAT_WORKER_IMAGE" \
  STACK_TRANSACTION_WORKER_IMAGE="$STACK_TRANSACTION_WORKER_IMAGE" \
  STACK_RECEIPT_WORKER_IMAGE="$STACK_RECEIPT_WORKER_IMAGE" \
  bash "$runtime_script"
}

run_remote() {
  local runtime_script
  runtime_script="$(mktemp)"
  trap 'rm -f "$runtime_script"' RETURN
  write_runtime_script "$runtime_script"

  echo "▶ Ensuring remote app directory exists at $REMOTE:$VPS_APP_DIR ..."
  ssh "${SSH_OPTS[@]}" "$REMOTE" "mkdir -p '$VPS_APP_DIR/deploy'"

  echo "▶ Syncing deploy assets to $REMOTE:$VPS_APP_DIR ..."
  rsync -az \
    -e "ssh ${SSH_OPTS[*]}" \
    docker-compose.yml \
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
  cat "$runtime_script" | ssh "${SSH_OPTS[@]}" "$REMOTE" \
    "APP_DIR='$VPS_APP_DIR' \
    COMPOSE_FILE='$COMPOSE_FILE' \
    GHCR_PULL_USERNAME='${GHCR_PULL_USERNAME:-}' \
    GHCR_PULL_TOKEN='${GHCR_PULL_TOKEN:-}' \
    SKIP_PULL='$SKIP_PULL' \
    AWS_HOST_DIR='$AWS_HOST_DIR' \
    STACK_GATEWAY_IMAGE='$STACK_GATEWAY_IMAGE' \
    STACK_CHAT_WORKER_IMAGE='$STACK_CHAT_WORKER_IMAGE' \
    STACK_TRANSACTION_WORKER_IMAGE='$STACK_TRANSACTION_WORKER_IMAGE' \
    STACK_RECEIPT_WORKER_IMAGE='$STACK_RECEIPT_WORKER_IMAGE' \
    bash -s"
}

if [[ "$DEPLOY_TARGET" == "local" ]]; then
  run_local
else
  run_remote
fi

echo "✅ ${DEPLOY_TARGET^} stack deploy complete."
