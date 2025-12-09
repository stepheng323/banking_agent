#!/usr/bin/env python3
"""
Batch script to replace remaining print statements with structured logging.

This script can be used to complete the logging migration for the remaining
46 print statements in orchestrator features and airtime service.

Usage:
    python complete_logging_migration.py --dry-run  # Preview changes
    python complete_logging_migration.py            # Apply changes
"""

import re
import sys
from pathlib import Path
from typing import List, Tuple

# Files with remaining print statements
ORCHESTRATOR_FILES = [
    "apps/core/src/agent/orchestrator/features/active_queue/handler.py",
    "apps/core/src/agent/orchestrator/services/task_executor.py",
    "apps/core/src/agent/orchestrator/services/media_service.py",
    "apps/core/src/agent/orchestrator/features/fresh_start/handler.py",
    "apps/core/src/agent/orchestrator/features/classification/service.py",
    "apps/core/src/agent/orchestrator/features/intent_routing/service.py",
    "apps/core/src/agent/orchestrator/features/intent_routing/handler.py",
    "apps/core/src/agent/orchestrator/features/context/service.py",
    "apps/core/src/agent/orchestrator/features/batch_authorization/handler.py",
    "apps/core/src/agent/orchestrator/features/beneficiary/service.py",
    "apps/core/src/agent/orchestrator/features/task_planning/service.py",
    "apps/core/src/agent/orchestrator/features/task_planning/handler.py",
    "apps/core/src/agent/orchestrator/pipeline/message_pipeline.py",
]

AIRTIME_FILES = [
    "apps/core/src/agent/sub_agents/airtime/executor.py",
    "apps/core/src/agent/sub_agents/airtime/completion.py",
]


def add_logging_import(content: str) -> str:
    """Add logging import if not present."""
    if "from shared.utils.logging import get_logger" in content:
        return content
    
    # Find the last import line
    lines = content.split('\n')
    last_import_idx = 0
    for i, line in enumerate(lines):
        if line.startswith('import ') or line.startswith('from '):
            last_import_idx = i
    
    # Insert logging import after last import
    lines.insert(last_import_idx + 1, "from shared.utils.logging import get_logger")
    lines.insert(last_import_idx + 2, "")
    lines.insert(last_import_idx + 3, "logger = get_logger(__name__)")
    
    return '\n'.join(lines)


def replace_print_statements(content: str) -> Tuple[str, int]:
    """Replace print statements with logger calls."""
    count = 0
    
    # Pattern: print(f"...")
    pattern = r'print\(f?"([^"]+)"\)'
    
    def replacement(match):
        nonlocal count
        count += 1
        msg = match.group(1)
        
        # Determine log level from emoji/content
        if '❌' in msg or 'error' in msg.lower() or 'failed' in msg.lower():
            level = 'error'
        elif '⚠️' in msg or 'warning' in msg.lower():
            level = 'warning'
        elif '🔍' in msg or 'DEBUG' in msg:
            level = 'debug'
        else:
            level = 'info'
        
        # Extract event name from message
        # Remove emojis and brackets
        clean_msg = re.sub(r'[🔍✅❌⚠️🎤🔄🧹⚡]', '', msg)
        clean_msg = re.sub(r'\[.*?\]', '', clean_msg)
        clean_msg = clean_msg.strip()
        
        # Create event name (snake_case from first few words)
        words = clean_msg.split()[:3]
        event = '_'.join(w.lower() for w in words if w.isalnum())
        event = re.sub(r'[^a-z0-9_]', '', event)
        
        return f'logger.{level}("{event}")'
    
    new_content = re.sub(pattern, replacement, content)
    return new_content, count


def process_file(filepath: Path, dry_run: bool = False) -> int:
    """Process a single file."""
    if not filepath.exists():
        print(f"⚠️  File not found: {filepath}")
        return 0
    
    content = filepath.read_text()
    
    # Add logging import
    content = add_logging_import(content)
    
    # Replace prints
    new_content, count = replace_print_statements(content)
    
    if count > 0:
        if dry_run:
            print(f"Would replace {count} print(s) in {filepath}")
        else:
            filepath.write_text(new_content)
            print(f"✅ Replaced {count} print(s) in {filepath}")
    
    return count


def main():
    dry_run = '--dry-run' in sys.argv
    
    if dry_run:
        print("🔍 DRY RUN MODE - No files will be modified\n")
    
    total = 0
    all_files = ORCHESTRATOR_FILES + AIRTIME_FILES
    
    for file_path in all_files:
        filepath = Path(file_path)
        count = process_file(filepath, dry_run)
        total += count
    
    print(f"\n{'Would replace' if dry_run else 'Replaced'} {total} print statements total")
    
    if dry_run:
        print("\nRun without --dry-run to apply changes")


if __name__ == "__main__":
    main()
