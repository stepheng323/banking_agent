"""Shared utilities for transfer flow graph."""

import os

# Performance: Only enable debug logging in debug mode
# Enable when any common debug env is set
_flag = (os.getenv("DEBUG") or os.getenv("FUSEPAY_DEBUG") or os.getenv("APP_DEBUG") or "").lower()
_log_level = (os.getenv("LOG_LEVEL") or "").lower()
DEBUG_MODE = _flag in ("1", "true", "yes", "on") or _log_level == "debug"


def debug_log(message: str) -> None:
    """Conditional debug logging - only logs if DEBUG env var is set."""
    if DEBUG_MODE:
        print(message)

