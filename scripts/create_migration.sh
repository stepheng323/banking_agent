#!/bin/bash
# Load environment variables and run Alembic migration

cd "$(dirname "$0")/.." || exit

# Load .env file
export $(cat .env | grep -v '^#' | xargs)

# Run Alembic
alembic revision --autogenerate -m "${1:-add_migration}"

