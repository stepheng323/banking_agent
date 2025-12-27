#!/usr/bin/env python3


import os
import sys
from pathlib import Path

# Add project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Try to load .env file
env_file = project_root / ".env"
if env_file.exists():
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                if "=" in line:
                    key, value = line.split("=", 1)
                    os.environ[key.strip()] = value.strip()

# Now import and run
from shared.database.connection import init_db  # noqa: E402

if __name__ == "__main__":
    print("🗄️  Initializing database...")
    db_url = os.getenv("DATABASE_URL", "not set")
    print(f"   Using: {db_url[:50]}..." if len(db_url) > 50 else f"   Using: {db_url}")
    init_db()
