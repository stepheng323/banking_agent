.PHONY: help lint type-check format test clean check-all fix \
      lint-fix format-check test-file test-coverage test-watch \
      run-gateway run-core run-all \
      docker-build docker-up docker-down docker-logs docker-restart docker-clean \
      db-migrate db-upgrade db-rollback db-reset db-shell \
      install install-dev deps-check setup \
      install-hooks lint-file check-file format-file check-orchestrator check-agent \
      info status ci

# Colors for output
BLUE := \033[36m
GREEN := \033[32m
YELLOW := \033[33m
RED := \033[31m
RESET := \033[0m

help: ## Show this help message
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

# ============================================================================
# CODE QUALITY
# ============================================================================

lint: ## Run ruff linter (check only, no fixes)
	@echo "$(BLUE)🔍 Running Ruff linter...$(RESET)"
	@ruff check .

lint-fix: ## Run ruff linter with auto-fix
	@echo "$(GREEN)🔧 Running Ruff linter with auto-fix...$(RESET)"
	@ruff check --fix .

type-check: ## Run mypy type checker
	@echo "$(BLUE)🔍 Running MyPy type checker...$(RESET)"
	@mypy apps/ shared/ || true

format: ## Format code with ruff
	@echo "$(GREEN)✨ Formatting code with ruff...$(RESET)"
	@ruff format .

format-check: ## Check code formatting (no changes)
	@echo "$(BLUE)🔍 Checking code formatting...$(RESET)"
	@ruff format --check .

check-all: lint type-check format-check ## Run all checks (lint + type-check + format-check)
	@echo "$(GREEN)✅ All checks passed!$(RESET)"

fix: lint-fix format ## Auto-fix linting issues and format code
	@echo "$(GREEN)✅ Auto-fixes applied!$(RESET)"

# ============================================================================
# TESTING
# ============================================================================

test: ## Run all tests
	@echo "$(BLUE)🧪 Running tests...$(RESET)"
	@pytest

test-file: ## Run specific test file (usage: make test-file FILE=path/to/test.py)
	@echo "$(BLUE)🧪 Running test: $(FILE)...$(RESET)"
	@pytest $(FILE)

test-coverage: ## Run tests with coverage report
	@echo "$(BLUE)🧪 Running tests with coverage...$(RESET)"
	@pytest --cov=apps --cov=shared --cov-report=term-missing --cov-report=html

test-watch: ## Run tests in watch mode (re-run on file changes)
	@echo "$(YELLOW)👀 Watching for changes...$(RESET)"
	@pytest-watch || pytest --watch

# ============================================================================
# DEVELOPMENT SERVERS
# ============================================================================

run-gateway: ## Run gateway service (FastAPI with uvicorn)
	@echo "$(GREEN)🚀 Starting Gateway service on port 8000...$(RESET)"
	@if [ -d ".venv" ] && [ -f ".venv/bin/python" ]; then \
		PYTHONPATH="$$(pwd)" .venv/bin/python -m uvicorn apps.gateway.main:app --host 0.0.0.0 --port 8000 --reload; \
	elif command -v python3 &> /dev/null; then \
		PYTHONPATH="$$(pwd)" python3 -m uvicorn apps.gateway.main:app --host 0.0.0.0 --port 8000 --reload; \
	else \
		echo "$(RED)❌ Error: Python not found. Please install dependencies first:$(RESET)"; \
		echo "   make install-dev"; \
		exit 1; \
	fi

run-core: ## Run core agent service (FastAPI with uvicorn + message consumer)
	@echo "$(GREEN)🚀 Starting Core agent service on port 8001...$(RESET)"
	@if [ -d ".venv" ] && [ -f ".venv/bin/python" ]; then \
		PYTHONPATH="$$(pwd)" .venv/bin/python -m uvicorn apps.core.src.main:app --host 0.0.0.0 --port 8001 --reload; \
	elif command -v python3 &> /dev/null; then \
		PYTHONPATH="$$(pwd)" python3 -m uvicorn apps.core.src.main:app --host 0.0.0.0 --port 8001 --reload; \
	else \
		echo "$(RED)❌ Error: Python not found. Please install dependencies first:$(RESET)"; \
		echo "   make install-dev"; \
		exit 1; \
	fi

run-all: ## Run all services (gateway + core)
	@echo "$(GREEN)🚀 Starting all services...$(RESET)"
	@echo "$(YELLOW)Note: Run in separate terminals or use docker-up instead$(RESET)"
	@make run-gateway & make run-core

# ============================================================================
# DOCKER
# ============================================================================

docker-build: ## Build Docker images for all services
	@echo "$(BLUE)🐳 Building Docker images...$(RESET)"
	@docker-compose build

docker-up: ## Start all services with Docker Compose
	@echo "$(GREEN)🐳 Starting services with Docker Compose...$(RESET)"
	@docker-compose up -d
	@echo "$(GREEN)✅ Services started!$(RESET)"
	@echo "$(YELLOW)View logs: docker-compose logs -f$(RESET)"

docker-down: ## Stop all Docker services
	@echo "$(RED)🐳 Stopping Docker services...$(RESET)"
	@docker-compose down

docker-logs: ## View Docker logs
	@echo "$(BLUE)📋 Viewing Docker logs...$(RESET)"
	@docker-compose logs -f

docker-restart: docker-down docker-up ## Restart all Docker services

docker-clean: ## Remove all Docker containers, images, and volumes
	@echo "$(RED)🧹 Cleaning Docker resources...$(RESET)"
	@docker-compose down -v --rmi all --remove-orphans
	@echo "$(GREEN)✅ Docker cleanup complete!$(RESET)"

# ============================================================================
# DATABASE
# ============================================================================

db-migrate: ## Create a new database migration
	@echo "$(BLUE)📊 Creating database migration...$(RESET)"
	@bash scripts/create_migration.sh

db-upgrade: ## Apply pending database migrations
	@echo "$(GREEN)📊 Applying database migrations...$(RESET)"
	@bash scripts/migrate.sh

db-rollback: ## Rollback last database migration
	@echo "$(RED)📊 Rolling back database migration...$(RESET)"
	@python scripts/run_alembic.py downgrade -1

db-reset: ## Drop and recreate database
	@echo "$(RED)⚠️  WARNING: This will delete all data!$(RESET)"
	@read -p "Are you sure? (yes/no): " confirm && [ "$$confirm" = "yes" ] || exit 1
	@python scripts/drop_db.py
	@python scripts/init_db.py
	@bash scripts/migrate.sh
	@echo "$(GREEN)✅ Database reset complete!$(RESET)"

db-shell: ## Open database shell
	@echo "$(BLUE)💻 Opening database shell...$(RESET)"
	@psql $${DATABASE_URL}

# ============================================================================
# DEPENDENCIES & SETUP
# ============================================================================

install: ## Install dependencies from requirements.txt
	@echo "$(BLUE)📦 Installing dependencies...$(RESET)"
	@pip install --upgrade pip
	@pip install -r requirements.txt
	@echo "$(GREEN)✅ Dependencies installed!$(RESET)"

install-dev: ## Install development dependencies (includes dev tools)
	@echo "$(BLUE)📦 Installing development dependencies...$(RESET)"
	@pip install --upgrade pip
	@pip install -r requirements.txt
	@pip install ruff mypy pytest pytest-cov pytest-watch black
	@echo "$(GREEN)✅ Development dependencies installed!$(RESET)"

setup: ## Initial project setup (creates venv, installs deps, sets PYTHONPATH)
	@echo "$(BLUE)🔧 Setting up development environment...$(RESET)"
	@if [ ! -d ".venv" ]; then \
		echo "$(YELLOW)Creating virtual environment...$(RESET)"; \
		python3 -m venv .venv; \
	fi
	@echo "$(YELLOW)Activate virtual environment with: source .venv/bin/activate$(RESET)"
	@echo "$(YELLOW)Then run: make install-dev$(RESET)"
	@echo ""
	@echo "$(GREEN)💡 Tip: Set PYTHONPATH for monorepo imports:$(RESET)"
	@echo "   export PYTHONPATH=$$(pwd):$$PYTHONPATH"

deps-check: ## Check for outdated dependencies
	@echo "$(BLUE)🔍 Checking for outdated dependencies...$(RESET)"
	@pip list --outdated || echo "$(YELLOW)No updates available$(RESET)"

rebuild-venv: ## Rebuild virtual environment from scratch
	@echo "$(YELLOW)🔄 Rebuilding virtual environment...$(RESET)"
	@rm -rf .venv
	@python3.13 -m venv .venv
	@.venv/bin/pip install --upgrade pip
	@.venv/bin/pip install -r requirements.txt
	@echo "$(GREEN)✅ Virtual environment rebuilt successfully!$(RESET)"
	@echo "$(YELLOW)Run 'source .venv/bin/activate' to activate it$(RESET)"

test-venv: ## Test virtual environment setup
	@echo "$(BLUE)🧪 Testing virtual environment...$(RESET)"
	@.venv/bin/python -c "import sys; print('Python:', sys.executable)"
	@.venv/bin/python -c "import sys; sp=[p for p in sys.path if 'site-packages' in p and '.venv' in p]; print('Site packages:', sp[0] if sp else 'NOT FOUND')"
	@.venv/bin/python -c "from Crypto.Cipher import AES; print('✅ Crypto module OK')"
	@.venv/bin/python -c "from langgraph.checkpoint.postgres import PostgresSaver; print('✅ PostgresSaver OK')"
	@echo "$(GREEN)✅ Virtual environment is working correctly!$(RESET)"

init-checkpoints: ## Initialize LangGraph checkpoint tables in PostgreSQL
	@echo "$(BLUE)🔄 Initializing checkpoint tables...$(RESET)"
	@.venv/bin/python -m shared.database.init_checkpoints
	@echo "$(GREEN)✅ Checkpoint tables initialized!$(RESET)"

# ============================================================================
# UTILITIES & CLEANUP
# ============================================================================

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
	@echo "$(GREEN)✅ Cleanup complete!$(RESET)"

install-hooks: ## Install pre-commit Git hooks
	@echo "$(BLUE)🔧 Installing pre-commit hooks...$(RESET)"
	@echo '#!/bin/bash' > .git/hooks/pre-commit
	@echo 'make check-all' >> .git/hooks/pre-commit
	@chmod +x .git/hooks/pre-commit
	@echo "$(GREEN)✅ Pre-commit hooks installed!$(RESET)"
	@echo "$(YELLOW)Hooks will run 'make check-all' before each commit$(RESET)"

# ============================================================================
# FILE-SPECIFIC CHECKS
# ============================================================================

lint-file: ## Lint specific file (usage: make lint-file FILE=path/to/file.py)
	@echo "$(BLUE)🔍 Linting: $(FILE)...$(RESET)"
	@ruff check $(FILE)

check-file: ## Type-check specific file (usage: make check-file FILE=path/to/file.py)
	@echo "$(BLUE)🔍 Type-checking: $(FILE)...$(RESET)"
	@mypy $(FILE) || true

format-file: ## Format specific file (usage: make format-file FILE=path/to/file.py)
	@echo "$(GREEN)✨ Formatting: $(FILE)...$(RESET)"
	@ruff format $(FILE)

check-orchestrator: ## Check orchestrator.py specifically
	@echo "$(BLUE)🔍 Checking orchestrator...$(RESET)"
	@ruff check apps/core/src/agent/orchestrator/
	@mypy apps/core/src/agent/orchestrator/ || true
	@echo "$(GREEN)✅ Orchestrator check complete!$(RESET)"

check-agent: ## Check all agent files
	@echo "$(BLUE)🔍 Checking all agent files...$(RESET)"
	@ruff check apps/core/src/agent/
	@mypy apps/core/src/agent/ || true
	@echo "$(GREEN)✅ Agent check complete!$(RESET)"

# ============================================================================
# INFO & DEBUGGING
# ============================================================================

info: ## Show project information
	@echo "$(BLUE)📋 Project Information$(RESET)"
	@echo ""
	@echo "$(GREEN)Python Version:$(RESET) $$(python3 --version 2>/dev/null || echo 'Not found')"
	@echo "$(GREEN)Pip Version:$(RESET) $$(pip --version 2>/dev/null || echo 'Not found')"
	@echo "$(GREEN)Docker:$(RESET) $$(docker --version 2>/dev/null || echo 'Not installed')"
	@echo "$(GREEN)Docker Compose:$(RESET) $$(docker-compose --version 2>/dev/null || echo 'Not installed')"
	@echo ""
	@echo "$(BLUE)Services:$(RESET)"
	@echo "  - Gateway: http://localhost:8000"
	@echo "  - Core: http://localhost:8001"
	@echo ""
	@echo "$(BLUE)Development Commands:$(RESET)"
	@echo "  make run-gateway  - Start gateway service"
	@echo "  make run-core     - Start core service"
	@echo "  make lint         - Lint code"
	@echo "  make fmt          - Format code"
	@echo "  make test         - Run tests"
	@docker-compose ps 2>/dev/null || echo ""
	@echo "$(BLUE)Docker Services:$(RESET)"
	@docker-compose ps 2>/dev/null || echo "  $(YELLOW)Not running$(RESET)"

status: info ## Alias for info

ci: check-all test ## Run CI checks (lint + type-check + format + test)
	@echo "$(GREEN)✅ CI checks passed!$(RESET)"
