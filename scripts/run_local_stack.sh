#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

usage() {
  cat <<'EOF'
Usage: scripts/run_local_stack.sh [--migrate]

Run the full local app stack against the DATABASE_URL and REDIS_URL in .env.

Services:
  gateway              http://localhost:8000
  chat-worker          background Redis Streams worker
  transaction-worker   http://localhost:8003
  receipt-worker       http://localhost:8002

Required .env values:
  DATABASE_URL
  REDIS_URL

Optional:
  --migrate   Run alembic upgrade head before starting services.
EOF
}

RUN_MIGRATIONS=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --migrate)
      RUN_MIGRATIONS=1
      shift
      ;;
    -h|--help|help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1"
      usage
      exit 1
      ;;
  esac
done

if [[ ! -f .env ]]; then
  echo "Missing .env. Add DATABASE_URL and REDIS_URL."
  exit 1
fi

env_value() {
  local key="$1"
  awk -F= -v key="$key" '
    $0 !~ /^[[:space:]]*#/ {
      lhs = $1
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", lhs)
      sub(/^export[[:space:]]+/, "", lhs)
    }
    $0 !~ /^[[:space:]]*#/ && lhs == key {
      sub(/^[^=]*=/, "")
      gsub(/^[[:space:]]+|[[:space:]]+$/, "")
      gsub(/\r$/, "")
      gsub(/^"|"$/, "")
      gsub(/^'\''|'\''$/, "")
      print
      exit
    }
  ' .env
}

require_env_key() {
  local key="$1"
  local value
  value="$(env_value "$key")"
  if [[ -z "$value" ]]; then
    echo "Missing $key in .env."
    exit 1
  fi
}

require_env_key "DATABASE_URL"
require_env_key "REDIS_URL"

CHAT_TRANSPORT_VALUE="$(env_value "CHAT_TRANSPORT")"
if [[ -n "$CHAT_TRANSPORT_VALUE" && "${CHAT_TRANSPORT_VALUE,,}" != "redis" ]]; then
  echo "Set CHAT_TRANSPORT=redis in .env for local chat ingress."
  echo "Current CHAT_TRANSPORT=$CHAT_TRANSPORT_VALUE"
  exit 1
fi

if [[ "$RUN_MIGRATIONS" == "1" ]]; then
  echo "Running migrations..."
  uv run --extra all alembic upgrade head
fi

export PYTHONPATH="$ROOT_DIR"

echo "Preparing Redis stream consumer groups..."
uv run --extra all python -c $'import asyncio\nfrom shared.cache.redis_client import RedisClient\nfrom shared.config.settings import settings\nfrom shared.queue.contracts import get_contract_by_topic\n\nasync def main():\n    redis = RedisClient.get_client()\n    group = f"{settings.project_name}-chat-worker-{settings.runtime.infrastructure_environment}"\n    for topic in ("message.received", "flow_event.process"):\n        stream = get_contract_by_topic(topic).redis_stream_name\n        if not stream:\n            continue\n        try:\n            await redis.xgroup_create(stream, group, id="0", mkstream=True)\n        except Exception as exc:\n            if "BUSYGROUP" not in str(exc):\n                raise\n\nasyncio.run(main())'

pids=()

cleanup() {
  local exit_code=$?
  if [[ ${#pids[@]} -gt 0 ]]; then
    echo
    echo "Stopping local stack..."
    kill "${pids[@]}" 2>/dev/null || true
    wait "${pids[@]}" 2>/dev/null || true
  fi
  exit "$exit_code"
}

trap cleanup EXIT INT TERM

start_service() {
  local name="$1"
  shift
  echo "Starting $name..."
  "$@" &
  pids+=("$!")
}

start_service "gateway" \
  uv run --extra all uvicorn apps.gateway.main:app \
    --host 0.0.0.0 \
    --port "${GATEWAY_PORT:-8000}" \
    --reload \
    --reload-dir apps \
    --reload-dir shared \
    --reload-exclude "*__pycache__*" \
    --reload-exclude "*.git*"

start_service "chat-worker" \
  uv run --extra all python -m apps.chat.src.worker_main

start_service "transaction-worker" \
  uv run --extra all uvicorn apps.transaction.main:app \
    --host 0.0.0.0 \
    --port "${TRANSACTION_WORKER_PORT:-8003}" \
    --reload \
    --reload-dir apps \
    --reload-dir shared \
    --reload-exclude "*__pycache__*" \
    --reload-exclude "*.git*"

start_service "receipt-worker" \
  uv run --extra all uvicorn apps.receipt.main:app \
    --host 0.0.0.0 \
    --port "${RECEIPT_WORKER_PORT:-8002}" \
    --reload \
    --reload-dir apps \
    --reload-dir shared \
    --reload-exclude "*__pycache__*" \
    --reload-exclude "*.git*"

cat <<EOF

Local stack started.
  Gateway:            http://localhost:${GATEWAY_PORT:-8000}/health
  Transaction worker: http://localhost:${TRANSACTION_WORKER_PORT:-8003}/health
  Receipt worker:     http://localhost:${RECEIPT_WORKER_PORT:-8002}/health

Press Ctrl+C to stop all services.
EOF

wait -n "${pids[@]}"
