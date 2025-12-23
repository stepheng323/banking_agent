#!/usr/bin/env python3
"""
Drop all database tables.
Loads DATABASE_URL from .env file.
"""

import os
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

env_file = project_root / ".env"
if env_file.exists():
    with open(env_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                if "=" in line:
                    key, value = line.split("=", 1)
                    os.environ[key.strip()] = value.strip()

from shared.database.connection import drop_db


if __name__ == "__main__":
    print("⚠️  Dropping all tables...")
    drop_db()
    print("✓ Tables dropped")
