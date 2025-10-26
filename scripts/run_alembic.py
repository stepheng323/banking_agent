#!/usr/bin/env python3
"""
Run Alembic commands with proper environment setup.
"""

import sys
import os
from pathlib import Path

# Add project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Load .env file
env_file = project_root / ".env"
if env_file.exists():
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ[key.strip()] = value.strip()

# Import Alembic and run
import alembic
from alembic.config import main as alembic_main

if __name__ == "__main__":
    # Get Alembic arguments (skip script name)
    alembic_args = sys.argv[1:]

    # Set up sys.argv for Alembic
    old_argv = sys.argv
    sys.argv = ["alembic"] + alembic_args
    try:
        alembic_main()
    finally:
        sys.argv = old_argv
