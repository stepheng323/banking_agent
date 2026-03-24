#!/usr/bin/env bash
# deploy-vps.sh — local equivalent of the GitHub Actions deploy-vps workflow.
# Usage:
#   ./deploy-vps.sh                        # uses defaults below
#   VPS_HOST=1.2.3.4 ./deploy-vps.sh      # override any variable
set -euo pipefail

# ── Config (override via env or edit defaults here) ──────────────────────────
VPS_HOST="${VPS_HOST:-62.169.20.218}"
VPS_SSH_USER="${VPS_SSH_USER:-root}"
VPS_SSH_PORT="${VPS_SSH_PORT:-22}"
VPS_APP_DIR="${VPS_APP_DIR:-/srv/banking_agent}"
COMPOSE_FILE="docker-compose.vps.yml"
# ─────────────────────────────────────────────────────────────────────────────

SSH="ssh -p $VPS_SSH_PORT"
REMOTE="$VPS_SSH_USER@$VPS_HOST"

echo "▶ Syncing repo to $REMOTE:$VPS_APP_DIR ..."
rsync -az \
  --exclude '.git/' \
  --exclude '.venv*/' \
  --exclude 'infrastructure/' \
  --exclude '.mypy_cache/' \
  --exclude '.pytest_cache/' \
  --exclude '.ruff_cache/' \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  --exclude '.env' \
  --exclude 'whatsapp_flow_private_key.pem' \
  -e "$SSH" \
  ./ "$REMOTE:$VPS_APP_DIR/"

echo "▶ Deploying stack on VPS ..."
$SSH "$REMOTE" "APP_DIR='$VPS_APP_DIR' COMPOSE_FILE='$COMPOSE_FILE' bash -s" <<'REMOTE_SCRIPT'
set -euo pipefail
cd "$APP_DIR"

docker compose -f "$COMPOSE_FILE" config > /dev/null
docker compose -f "$COMPOSE_FILE" up -d --build
docker compose -f "$COMPOSE_FILE" ps

check_health() {
  local name="$1"
  local url="$2"
  local attempt
  echo "  Waiting for $name ..."
  for attempt in $(seq 1 20); do
    if curl -fsS "$url" > /tmp/health.out 2>/dev/null; then
      echo "  ✓ $name: $(cat /tmp/health.out)"
      return 0
    fi
    sleep 5
  done
  echo "  ✗ Health check failed for $name ($url)"
  return 1
}

check_health gateway     "http://localhost/health"
check_health core        "http://localhost/core/health"
check_health transaction "http://localhost/transaction/health"
check_health receipt     "http://localhost/receipt/health"
REMOTE_SCRIPT

echo "✅ Deploy complete."
