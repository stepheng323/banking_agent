#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

RUN_SMOKE=1
RUN_BUILD=1
TAG="${TAG:-local-$(git rev-parse --short HEAD 2>/dev/null || date +%Y%m%d%H%M%S)}"
SELECTED_IMAGES=()

usage() {
  cat <<'EOF'
Usage: scripts/build_local.sh [options]

Options:
  --smoke-only             Run runtime boundary import checks only.
  --build-only             Build Docker images only.
  --tag <value>            Override image tag suffix (default: local-<git-sha>).
  --image <name>           Build only one image (repeatable). Values:
                           gateway-vps
                           transaction-worker-vps
                           receipt-worker-vps
                           chat-worker
  -h, --help               Show this help.

Examples:
  scripts/build_local.sh
  scripts/build_local.sh --smoke-only
  scripts/build_local.sh --build-only --image gateway-vps
  scripts/build_local.sh --tag local-test
EOF
}

require_cmd() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "ERROR: missing required command: $cmd"
    exit 1
  fi
}

contains_image() {
  local candidate="$1"
  if [ "${#SELECTED_IMAGES[@]}" -eq 0 ]; then
    return 0
  fi

  local img
  for img in "${SELECTED_IMAGES[@]}"; do
    if [ "$img" = "$candidate" ]; then
      return 0
    fi
  done
  return 1
}

run_runtime_smoke() {
  require_cmd uv

  run_check() {
    local name="$1"
    local extra="$2"
    local module="$3"
    local symbol="$4"
    local venv=".venv-${name}"

    rm -rf "$venv"
    uv venv "$venv"
    uv pip install --python "$venv/bin/python" ".[${extra}]"
    "$venv/bin/python" -c "import importlib; m = importlib.import_module('${module}'); getattr(m, '${symbol}')"
    echo "✓ ${name} import check passed"
  }

  echo "Running runtime boundary smoke checks..."
  run_check "receipt-runtime" "runtime-worker-receipt" "apps.receipt.lambda_handler" "handler"
  run_check "transaction-runtime" "runtime-worker-transaction" "apps.transaction.lambda_handler" "handler"
  run_check "gateway-runtime" "runtime-gateway" "apps.gateway.lambda_handler" "handler"
}

run_vps_image_smoke() {
  require_cmd docker
  docker buildx version >/dev/null

  run_check() {
    local name="$1"
    local dockerfile="$2"
    local module="$3"
    local symbol="$4"
    local tag="banking-agent-${name}:${TAG}"

    docker buildx build --load -f "$dockerfile" -t "$tag" .
    docker run --rm "$tag" sh -lc \
      "command -v uvicorn >/dev/null && python -c \"import importlib; m = importlib.import_module('${module}'); getattr(m, '${symbol}')\""
    echo "✓ ${name} image smoke passed"
  }

  echo "Running VPS image runtime smoke checks..."
  run_check "gateway-vps" "apps/gateway/Dockerfile" "apps.gateway.main" "app"
  run_check "transaction-worker-vps" "apps/transaction/Dockerfile" "apps.transaction.main" "app"
  run_check "receipt-worker-vps" "apps/receipt/Dockerfile.local" "apps.receipt.main" "app"
}

build_image() {
  local name="$1"
  local repo="$2"
  local dockerfile="$3"
  local tag_sha="${repo}:${name}-${TAG}"
  local tag_local="${repo}:${name}-local"

  docker buildx build \
    --load \
    -f "$dockerfile" \
    -t "$tag_sha" \
    -t "$tag_local" \
    .

  echo "✓ Built ${name}"
  echo "  - ${tag_sha}"
  echo "  - ${tag_local}"
}

run_docker_builds() {
  require_cmd docker
  docker buildx version >/dev/null

  echo "Building local Docker images (tag suffix: ${TAG})..."

  if contains_image "gateway-vps"; then
    build_image "gateway-vps" "banking-agent-gateway-dev" "apps/gateway/Dockerfile"
  fi

  if contains_image "transaction-worker-vps"; then
    build_image "transaction-worker-vps" "banking-agent-transaction-dev" "apps/transaction/Dockerfile"
  fi

  if contains_image "receipt-worker-vps"; then
    build_image "receipt-worker-vps" "banking-agent-receipt-dev" "apps/receipt/Dockerfile.local"
  fi

  if contains_image "chat-worker"; then
    build_image "chat-worker" "banking-agent-chat-dev" "apps/chat/Dockerfile"
  fi
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --smoke-only)
      RUN_BUILD=0
      shift
      ;;
    --build-only)
      RUN_SMOKE=0
      shift
      ;;
    --tag)
      TAG="${2:-}"
      if [ -z "$TAG" ]; then
        echo "ERROR: --tag requires a value"
        exit 1
      fi
      shift 2
      ;;
    --image)
      img="${2:-}"
      case "$img" in
        gateway-vps|transaction-worker-vps|receipt-worker-vps|chat-worker)
          SELECTED_IMAGES+=("$img")
          ;;
        *)
          echo "ERROR: unknown image '$img'"
          exit 1
          ;;
      esac
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "ERROR: unknown argument '$1'"
      usage
      exit 1
      ;;
  esac
done

if [ "$RUN_SMOKE" -eq 0 ] && [ "$RUN_BUILD" -eq 0 ]; then
  echo "ERROR: nothing to do (both smoke and build disabled)"
  exit 1
fi

if [ "$RUN_SMOKE" -eq 1 ]; then
  run_runtime_smoke
  run_vps_image_smoke
fi

if [ "$RUN_BUILD" -eq 1 ]; then
  run_docker_builds
fi

echo "Local build workflow complete."
