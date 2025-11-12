"""Shared utilities for transfer flow graph."""

import os

# Performance: Only enable debug logging in debug mode
DEBUG_MODE = os.getenv("DEBUG", "false").lower() == "true"


def debug_log(message: str) -> None:
    """Conditional debug logging - only logs if DEBUG env var is set."""
    if DEBUG_MODE:
        print(message)

