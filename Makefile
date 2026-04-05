.PHONY: help lint type-check format test clean clean-runtime-artifacts check-all fix \
      lint-fix format-check test-file test-coverage test-watch \
      run-gateway run-core run-all run-receipt \
      docker-build docker-up docker-down docker-logs docker-restart docker-clean \
      db-migrate db-upgrade db-rollback db-reset db-shell \
      install install-dev install-all deps-check setup rebuild-venv \
      install-hooks lint-file check-file format-file check-orchestrator check-agent \
      info status ci test-venv init-checkpoints local-build local-build-smoke local-build-images

# Colors for output
BLUE := \033[36m
GREEN := \033[32m
YELLOW := \033[33m
RED := \033[31m
RESET := \033[0m
DOCKER_COMPOSE ?= docker compose
COMPOSE_FILE ?= docker-compose.yml

help:
	@echo '$(BLUE)Banking Agent - Makefile Commands$(RESET)'
	@echo ''
	@echo '$(YELLOW)Usage:$(RESET) make [target]'
	@echo ''
	@echo '$(GREEN)Available targets:$(RESET)'
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  $(BLUE)%-25s$(RESET) %s\n", $$1, $$2}'
	@echo ''
	@echo '$(YELLOW)Examples:$(RESET)'
	@echo '  make check-all              # Run all quality checks'
	@echo '  make fix                    # Auto-fix lint issues and format code'
	@echo '  make test                   # Run all tests'
	@echo '  make run-all                # Run gateway and core services'
	@echo '  make docker-up              # Start all services with Docker'
	@echo ''


# Code quality
lint: ## Run ruff linter (check only)
	@echo "$(BLUE)🔍 Running Ruff linter...$(RESET)"
	@uv run ruff check .

lint-fix: ## Run ruff linter with auto-fix
	@echo "$(GREEN)🔧 Running Ruff linter with auto-fix...$(RESET)"
	@uv run ruff check --fix . || true

type-check: ## Run mypy type checker
	@echo "$(BLUE)🔍 Running MyPy type checker...$(RESET)"
	@uv run mypy apps/ shared/ || true

format: ## Format code with ruff
	@echo "$(GREEN)✨ Formatting code with ruff...$(RESET)"
	@uv run ruff format .

format-check: ## Check code formatting (no changes)
	@echo "$(BLUE)🔍 Checking code formatting...$(RESET)"
	@uv run ruff format --check .

check-all: lint type-check format-check ## Run all checks (lint + type-check + format-check)
	@echo "$(GREEN)✅ All checks passed!$(RESET)"

fix: format lint-fix ## Auto-fix: format first, then lint fixes
	@echo "$(GREEN)✅ Auto-fixes applied!$(RESET)"


# Testing
test: ## Run all tests
	@echo "$(BLUE)🧪 Running tests...$(RESET)"
	@uv run pytest

test-file: ## Run specific test file (usage: make test-file FILE=path/to/test.py)
	@echo "$(BLUE)🧪 Running test: $(FILE)...$(RESET)"
	@uv run pytest $(FILE)

test-coverage: ## Run tests with coverage report
	@echo "$(BLUE)🧪 Running tests with coverage...$(RESET)"
	@uv run pytest --cov=apps --cov=shared --cov-report=term-missing --cov-report=html

test-watch: ## Run tests in watch mode
	@echo "$(YELLOW)👀 Watching for changes...$(RESET)"
	@uv run pytest-watch || uv run pytest --watch


# Development servers
run-gateway: ## Run gateway service (port 8000)
	@echo "$(GREEN)🚀 Starting Gateway service on port 8000...$(RESET)"
	@PYTHONPATH="$$(pwd)" uv run uvicorn apps.gateway.main:app --host 0.0.0.0 --port 8000 --reload --reload-dir apps --reload-dir shared --reload-exclude "*__pycache__*" --reload-exclude "*.git*"

run-core: ## Run core agent service (port 8001)
	@echo "$(GREEN)🚀 Starting Core agent service on port 8001...$(RESET)"
	@PYTHONPATH="$$(pwd)" uv run uvicorn apps.core.src.main:app --host 0.0.0.0 --port 8001 --reload --reload-dir apps --reload-dir shared --reload-exclude "*__pycache__*" --reload-exclude "*.git*"

run-receipt: ## Run receipt worker service (port 8002)
	@echo "$(GREEN)🧾 Starting Receipt service on port 8002...$(RESET)"
	@PYTHONPATH="$$(pwd)" uv run uvicorn apps.receipt.main:app --host 0.0.0.0 --port 8002 --reload --reload-dir apps --reload-dir shared --reload-exclude "*__pycache__*" --reload-exclude "*.git*"

run-all: ## Run all services (gateway + core + receipt)
	@echo "$(GREEN)🚀 Starting all services...$(RESET)"
	@echo "$(YELLOW)Note: Run in separate terminals or use docker-up instead$(RESET)"
	@make run-gateway & make run-core & make run-receipt

# Docker
docker-build: ## Build Docker images
	@echo "$(BLUE)🐳 Building Docker images...$(RESET)"
	@$(DOCKER_COMPOSE) -f $(COMPOSE_FILE) build

docker-up: ## Start services with Docker Compose
	@echo "$(GREEN)🐳 Starting services with Docker Compose...$(RESET)"
	@$(DOCKER_COMPOSE) -f $(COMPOSE_FILE) up -d
	@echo "$(GREEN)✅ Services started!$(RESET)"
	@echo "$(YELLOW)View logs: $(DOCKER_COMPOSE) -f $(COMPOSE_FILE) logs -f$(RESET)"

docker-down: ## Stop Docker services
	@echo "$(RED)🐳 Stopping Docker services...$(RESET)"
	@$(DOCKER_COMPOSE) -f $(COMPOSE_FILE) down

docker-logs: ## View Docker logs
	@echo "$(BLUE)📋 Viewing Docker logs...$(RESET)"
	@$(DOCKER_COMPOSE) -f $(COMPOSE_FILE) logs -f

docker-restart: docker-down docker-up ## Restart Docker services

docker-clean: ## Remove all Docker containers, images, and volumes
	@echo "$(RED)🧹 Cleaning Docker resources...$(RESET)"
	@$(DOCKER_COMPOSE) -f $(COMPOSE_FILE) down -v --rmi all --remove-orphans
	@echo "$(GREEN)✅ Docker cleanup complete!$(RESET)"

# Database
db-migrate: ## Create a new database migration
	@echo "$(BLUE)📊 Creating database migration...$(RESET)"
	@bash scripts/create_migration.sh

db-upgrade: ## Apply pending database migrations
	@echo "$(GREEN)📊 Applying database migrations...$(RESET)"
	@uv run alembic upgrade head

db-rollback: ## Rollback last database migration
	@echo "$(RED)📊 Rolling back database migration...$(RESET)"
	@uv run python scripts/run_alembic.py downgrade -1

db-reset: ## Drop and recreate database schema (DANGEROUS)
	@echo "$(RED)⚠️  WARNING: This will delete all data!$(RESET)"
	@read -p "Are you sure? (yes/no): " confirm && [ "$$confirm" = "yes" ] || exit 1
	@echo "$(YELLOW)🗑️  Dropping schema...$(RESET)"
	@docker exec $$(docker ps -qf "name=postgres") psql -U postgres -d banking_agent -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;" || uv run python scripts/drop_db.py
	@echo "$(GREEN)📊 Running migrations...$(RESET)"
	@uv run alembic upgrade head
	@echo "$(GREEN)✅ Database reset complete!$(RESET)"

db-shell: ## Open database shell
	@echo "$(BLUE)💻 Opening database shell...$(RESET)"
	@psql $${DATABASE_URL}

# Dependencies & setup

install: ## Install base dependencies
	@echo "$(BLUE)📦 Installing dependencies...$(RESET)"
	@uv sync
	@echo "$(GREEN)✅ Dependencies installed!$(RESET)"

install-dev: ## Install development dependencies
	@echo "$(BLUE)📦 Installing development dependencies...$(RESET)"
	@uv sync --group dev
	@echo "$(GREEN)✅ Development dependencies installed!$(RESET)"

install-all: ## Install all dependencies (all extras + dev)
	@echo "$(BLUE)📦 Installing all dependencies...$(RESET)"
	@uv sync --all-extras --group dev
	@echo "$(GREEN)✅ All dependencies installed!$(RESET)"

setup: ## Initial project setup
	@echo "$(BLUE)🔧 Setting up development environment...$(RESET)"
	@uv sync --all-extras --group dev
	@echo "$(GREEN)✅ Environment ready!$(RESET)"
	@echo ""
	@echo "$(YELLOW)Quick start:$(RESET)"
	@echo "  make run-core     # Start core service"
	@echo "  make run-gateway  # Start gateway service"

deps-check: ## Check for outdated dependencies
	@echo "$(BLUE)🔍 Checking for outdated dependencies...$(RESET)"
	@uv pip list --outdated || echo "$(YELLOW)No updates available$(RESET)"

rebuild-venv: ## Rebuild virtual environment from scratch
	@echo "$(YELLOW)🔄 Rebuilding virtual environment...$(RESET)"
	@rm -rf .venv
	@uv venv
	@uv sync --all-extras --group dev
	@echo "$(GREEN)✅ Virtual environment rebuilt!$(RESET)"

test-venv: ## Test virtual environment setup
	@echo "$(BLUE)🧪 Testing virtual environment...$(RESET)"
	@uv run python -c "import sys; print('Python:', sys.executable)"
	@uv run python -c "from Crypto.Cipher import AES; print('✅ Crypto module OK')"
	@uv run python -c "from langgraph.checkpoint.postgres import PostgresSaver; print('✅ PostgresSaver OK')"
	@echo "$(GREEN)✅ Virtual environment is working correctly!$(RESET)"

init-checkpoints: ## Initialize LangGraph checkpoint tables
	@echo "$(BLUE)🔄 Initializing checkpoint tables...$(RESET)"
	@uv run python -m shared.database.init_checkpoints
	@echo "$(GREEN)✅ Checkpoint tables initialized!$(RESET)"

# Utilities & cleanup
clean: ## Clean Python artifacts and caches
	@echo "$(YELLOW)🧹 Cleaning up...$(RESET)"
	@find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name "*.pyc" -delete
	@find . -type f -name "*.pyo" -delete
	@find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name "dist" -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name "build" -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	@find . -maxdepth 1 -type f -name ".coverage*" -delete
	@echo "$(GREEN)✅ Cleanup complete!$(RESET)"

clean-runtime-artifacts: ## Remove generated runtime smoke venvs and local test artifacts
	@echo "$(YELLOW)🧹 Removing generated runtime artifacts...$(RESET)"
	@find . -maxdepth 1 -type d -name ".venv-*" -exec rm -rf {} +
	@find . -maxdepth 1 -type d \( -name ".pytest_cache" -o -name ".mypy_cache" -o -name ".ruff_cache" \) -exec rm -rf {} +
	@find . -maxdepth 1 -type f -name ".coverage*" -delete
	@echo "$(GREEN)✅ Runtime artifacts removed!$(RESET)"

install-hooks: ## Install pre-commit Git hooks
	@echo "$(BLUE)🔧 Installing pre-commit hooks...$(RESET)"
	@echo '#!/bin/bash' > .git/hooks/pre-commit
	@echo 'make check-all' >> .git/hooks/pre-commit
	@chmod +x .git/hooks/pre-commit
	@echo "$(GREEN)✅ Pre-commit hooks installed!$(RESET)"

# File-specific checks
lint-file: ## Lint specific file (usage: make lint-file FILE=path)
	@echo "$(BLUE)🔍 Linting: $(FILE)...$(RESET)"
	@uv run ruff check $(FILE)

check-file: ## Type-check specific file (usage: make check-file FILE=path)
	@echo "$(BLUE)🔍 Type-checking: $(FILE)...$(RESET)"
	@uv run mypy $(FILE) || true

format-file: ## Format specific file (usage: make format-file FILE=path)
	@echo "$(GREEN)✨ Formatting: $(FILE)...$(RESET)"
	@uv run ruff format $(FILE)

check-orchestrator: ## Check orchestrator files
	@echo "$(BLUE)🔍 Checking orchestrator...$(RESET)"
	@uv run ruff check apps/core/src/agent/orchestrator/
	@uv run mypy apps/core/src/agent/orchestrator/ || true
	@echo "$(GREEN)✅ Orchestrator check complete!$(RESET)"

check-agent: ## Check all agent files
	@echo "$(BLUE)🔍 Checking all agent files...$(RESET)"
	@uv run ruff check apps/core/src/agent/
	@uv run mypy apps/core/src/agent/ || true
	@echo "$(GREEN)✅ Agent check complete!$(RESET)"

# Info & debugging
info:
	@echo "$(BLUE)📋 Project Information$(RESET)"
	@echo ""
	@echo "$(GREEN)Python:$(RESET) $$(uv run python --version 2>/dev/null || echo 'Not found')"
	@echo "$(GREEN)UV:$(RESET) $$(uv --version 2>/dev/null || echo 'Not installed')"
	@echo "$(GREEN)Docker:$(RESET) $$(docker --version 2>/dev/null || echo 'Not installed')"
	@echo ""
	@echo "$(BLUE)Services:$(RESET)"
	@echo "  Gateway: http://localhost:8000"
	@echo "  Core:    http://localhost:8001"
	@echo "  Receipt: http://localhost:8002"
	@echo ""
	@echo "$(BLUE)Quick Commands:$(RESET)"
	@echo "  make run-core     - Start core service"
	@echo "  make run-gateway  - Start gateway service"
	@echo "  make check-all    - Run all quality checks"
	@echo "  make test         - Run tests"

status: info ## Alias for info

ci: check-all test ## Run CI checks (lint + type-check + format + test)
	@echo "$(GREEN)✅ CI checks passed!$(RESET)"

local-build: ## Run local runtime smoke checks + build all deploy images
	@echo "$(BLUE)🏗️  Running local deploy build workflow...$(RESET)"
	@bash scripts/build_local.sh

local-build-smoke: ## Run local runtime boundary smoke checks only
	@echo "$(BLUE)🧪 Running local runtime smoke checks...$(RESET)"
	@bash scripts/build_local.sh --smoke-only

local-build-images: ## Build local deploy images only
	@echo "$(BLUE)🐳 Building local deploy images...$(RESET)"
	@bash scripts/build_local.sh --build-only
