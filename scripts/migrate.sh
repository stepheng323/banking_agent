#!/bin/bash

set -e

if [ -f .env ]; then
    export $(cat .env | grep -v '^#' | xargs)
fi

export DATABASE_URL="${DATABASE_URL:-postgresql://banking_user:banking_pass@localhost:5432/banking_db}"

echo "🗄️  Running database migrations..."
echo "   Database: $DATABASE_URL"
echo ""

# Setup Pants exported environment for Alembic
VENV_PATH="dist/export/python/virtualenvs/python-default/3.12.3"
if [ ! -d "$VENV_PATH" ]; then
    echo "📦 Exporting Pants environment..."
    pants export --resolve=python-default
fi

# Use Python from Pants environment with Alembic module
PYTHON_CMD="$VENV_PATH/bin/python -m alembic"

case "$1" in
    init)
        echo "📝 Initializing Alembic..."
        $PYTHON_CMD revision --autogenerate -m "Initial migration"
        ;;
    upgrade)
        echo "⬆️  Upgrading database..."
        $PYTHON_CMD upgrade head
        echo "✅ Database upgraded!"
        ;;
    downgrade)
        echo "⬇️  Downgrading database..."
        $PYTHON_CMD downgrade -1
        echo "✅ Database downgraded!"
        ;;
    create)
        shift
        echo "📝 Creating migration: $*"
        $PYTHON_CMD revision --autogenerate -m "$*"
        ;;
    current)
        echo "📍 Current database version:"
        $PYTHON_CMD current
        ;;
    history)
        echo "📜 Migration history:"
        $PYTHON_CMD history
        ;;
    *)
        echo "Usage: $0 {init|upgrade|downgrade|create|current|history}"
        echo ""
        echo "Commands:"
        echo "  init           - Create initial migration from models"
        echo "  upgrade        - Apply all pending migrations"
        echo "  downgrade      - Rollback last migration"
        echo "  create <msg>   - Create new migration with message"
        echo "  current        - Show current migration version"
        echo "  history        - Show migration history"
        exit 1
        ;;
    *)
        echo "Usage: $0 {init|upgrade|downgrade|create|current|history}"
        echo ""
        echo "Commands:"
        echo "  init           - Create initial migration from models"
        echo "  upgrade        - Apply all pending migrations"
        echo "  downgrade      - Rollback last migration"
        echo "  create <msg>   - Create new migration with message"
        echo "  current        - Show current migration version"
        echo "  history        - Show migration history"
        exit 1
        ;;
esac

