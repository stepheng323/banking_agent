#!/bin/bash

set -e

if [ -f .env ]; then
    export $(cat .env | grep -v '^#' | xargs)
fi

export DATABASE_URL="${DATABASE_URL:-postgresql://banking_user:banking_pass@localhost:5432/banking_db}"

echo "🗄️  Running database migrations..."
echo "   Database: $DATABASE_URL"
echo ""

# Use .venv if it exists, otherwise use system python
if [ -d ".venv" ] && [ -f ".venv/bin/python" ]; then
    PYTHON_CMD=".venv/bin/python -m alembic"
    echo "✅ Using virtual environment: .venv"
elif command -v python3 &> /dev/null; then
    PYTHON_CMD="python3 -m alembic"
    echo "✅ Using system Python: $(python3 --version)"
else
    echo "❌ No Python found. Please:"
    echo "   1. Create virtual environment: python3 -m venv .venv"
    echo "   2. Activate it: source .venv/bin/activate"
    echo "   3. Install dependencies: pip install -r requirements.txt"
    exit 1
fi

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
