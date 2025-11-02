#!/bin/bash
# Setup script for Banking Agent development environment

set -e

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

echo "🔧 Setting up Banking Agent development environment..."
echo ""

# Create virtual environment if it doesn't exist
if [ ! -d ".venv" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv .venv
    echo "✅ Virtual environment created!"
else
    echo "✅ Virtual environment already exists"
fi

# Activate virtual environment
echo ""
echo "📦 Installing dependencies..."
source .venv/bin/activate

# Upgrade pip
pip install --upgrade pip

# Install dependencies
pip install -r requirements.txt

# Install development tools
echo ""
echo "🔧 Installing development tools..."
pip install ruff mypy pytest pytest-cov pytest-watch black || echo "⚠️  Some dev tools failed to install"

# Set up PYTHONPATH
echo ""
echo "💡 Setting PYTHONPATH for monorepo imports..."
export PYTHONPATH="$PROJECT_ROOT:$PYTHONPATH"

# Add to .env file if it exists, or create one
if [ -f ".env" ]; then
    if ! grep -q "PYTHONPATH" .env; then
        echo "PYTHONPATH=$PROJECT_ROOT" >> .env
        echo "✅ Added PYTHONPATH to .env"
    else
        echo "✅ PYTHONPATH already in .env"
    fi
else
    echo "PYTHONPATH=$PROJECT_ROOT" > .env
    echo "✅ Created .env with PYTHONPATH"
fi

echo ""
echo "✅ Setup complete!"
echo ""
echo "📋 Next steps:"
echo "  1. Activate virtual environment:"
echo "     source .venv/bin/activate"
echo ""
echo "  2. Set PYTHONPATH (or source .env):"
echo "     export PYTHONPATH=$PROJECT_ROOT:\$PYTHONPATH"
echo ""
echo "  3. Run services:"
echo "     make run-gateway    # Start gateway on port 8000"
echo "     make run-core       # Start core on port 8001"
echo ""
echo "  4. Or use Docker:"
echo "     make docker-up      # Start all services"
echo ""

