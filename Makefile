.PHONY: help dev test format lint docker-build docker-up docker-down clean

help: ## Show this help message
	@echo 'Usage: make [target]'
	@echo ''
	@echo 'Available targets:'
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

setup: ## Generate lock files and set up environment
	pants generate-lockfiles --resolve=python-default

dev-gateway: ## Run gateway service in development mode
	pants run apps/gateway:bin -- -m uvicorn apps.gateway.main:app --reload --host 0.0.0.0 --port 8000

dev-core: ## Run core service in development mode
	pants run apps/core:bin -- -m uvicorn apps.core.src.main:app --reload --host 0.0.0.0 --port 8001

test: ## Run all tests
	pants test ::

format: ## Format all code with Black
	pants fmt ::

lint: ## Lint all code
	pants lint ::

check: ## Run format, lint, and tests
	pants fmt :: && pants lint :: && pants test ::

docker-build: ## Build Docker images
	docker-compose build

docker-up: ## Start services with Docker Compose
	docker-compose up -d

docker-down: ## Stop Docker Compose services
	docker-compose down

docker-logs: ## Show Docker logs
	docker-compose logs -f

clean: ## Clean Pants cache
	pants clean-all

list: ## List all targets
	pants list ::

.DEFAULT_GOAL := help

