"""Shared authorization module for transaction flows.

Provides a base class and utilities for handling authorization
across transfer, airtime, and other transaction types.
"""

from .base import AuthorizationBase
from .pin_handler import PinResult, handle_pin_failure
from .responses import (
    build_awaiting_pin_response,
    build_max_retries_response,
    build_pin_failed_response,
    build_session_expired_response,
)

__all__ = [
    "AuthorizationBase",
    "PinResult",
    "handle_pin_failure",
    "build_awaiting_pin_response",
    "build_max_retries_response",
    "build_pin_failed_response",
    "build_session_expired_response",
]
