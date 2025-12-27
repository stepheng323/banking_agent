"""Shared utilities for transfer nodes."""

import os

from shared.utils.logging import get_logger

# Performance: Only enable debug logging in debug mode
# Enable when any common debug env is set
_flag = (os.getenv("DEBUG") or os.getenv("FUSEPAY_DEBUG") or os.getenv("APP_DEBUG") or "").lower()
_log_level = (os.getenv("LOG_LEVEL") or "").lower()
DEBUG_MODE = _flag in ("1", "true", "yes", "on") or _log_level == "debug"

logger = get_logger("transfer_nodes")


def debug_log(message: str) -> None:
    """Conditional debug logging - now using structlog."""
    if DEBUG_MODE:
        logger.debug(message)
