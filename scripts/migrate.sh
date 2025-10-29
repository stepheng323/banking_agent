#!/bin/bash

set -e

if [ -f .env ]; then
    export $(cat .env | grep -v '^#' | xargs)
fi

export DATABASE_URL="${DATABASE_URL:-postgresql://banking_user:banking_pass@localhost:5432/banking_db}"

echo "🗄️  Running database migrations..."
echo "   Database: $DATABASE_URL"
echo ""

VENV_BASE="dist/export/python/virtualenvs/python-default"

if [ ! -d "$VENV_BASE" ]; then
    echo "📦 Exporting Pants environment (first time setup)..."
    pants export --resolve=python-default
fi

PYTHON_VERSION=$(find "$VENV_BASE" -mindepth 1 -maxdepth 1 -type d -name "3.*" 2>/dev/null | head -n 1 | xargs -r basename)

if [ -z "$PYTHON_VERSION" ]; then
    echo "📦 Re-exporting Pants environment..."
    pants export --resolve=python-default
    PYTHON_VERSION=$(find "$VENV_BASE" -mindepth 1 -maxdepth 1 -type d -name "3.*" 2>/dev/null | head -n 1 | xargs -r basename)
fi

if [ -z "$PYTHON_VERSION" ]; then
    echo "❌ Failed to find Python environment"
    echo "   Ensure you have Python 3.12+ installed and run:"
    echo "   pants export --resolve=python-default"
    exit 1
fi

VENV_PATH="$VENV_BASE/$PYTHON_VERSION"

if [ ! -f "$VENV_PATH/bin/python" ]; then
    echo "❌ Python environment incomplete at $VENV_PATH"
    echo "   Re-exporting..."
    pants export --resolve=python-default
    exit 1
fi

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
esac
