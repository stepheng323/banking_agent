.PHONY: help dev test format lint docker-build docker-up docker-down clean

help: 
	@echo 'Usage: make [target]'
	@echo ''
	@echo 'Available targets:'
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

setup: 
	pants generate-lockfiles --resolve=python-default

dev-gateway: 
	PANTS_CONCURRENT=True pants run apps/gateway:bin

dev-core:
	PANTS_CONCURRENT=True pants run apps/core:bin

dev:
	PANTS_CONCURRENT=True pants run apps/gateway:bin &
	PANTS_CONCURRENT=True pants run apps/core:bin &
	@echo "🚀 Both services starting..."
	@echo "   Gateway: http://localhost:8000"
	@echo "   Core: http://localhost:8001"
	@echo "   Press Ctrl+C to stop all services"
	wait

test:
	pants test ::

format:
	pants fmt ::

lint:
	pants lint ::

check:
	pants fmt :: && pants lint :: && pants test ::

docker-build:
	docker-compose build

docker-up:
	docker-compose up -d

docker-down: 
	docker-compose down

docker-logs:
	docker-compose logs -f

clean:
	pants clean-all

list:
	pants list ::

.DEFAULT_GOAL := help

