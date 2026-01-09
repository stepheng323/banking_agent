"""Shared mandate status validation for all transaction flows."""

import ast
import json
from datetime import datetime, timedelta
from typing import Any

import structlog

from shared.config.settings import settings

logger = structlog.get_logger(__name__)

MANDATE_EXPIRY_HOURS = 1


def _parse_extra_data(raw_data: Any) -> dict:
    """Parse extra_data which may be a dict, JSON string, or Python dict literal string."""
    if isinstance(raw_data, dict):
        return raw_data
    if not raw_data:
        return {}
    if not isinstance(raw_data, str):
        return {}

    try:
        return json.loads(raw_data)
    except (json.JSONDecodeError, TypeError):
        pass

    try:
        parsed = ast.literal_eval(raw_data)
        if isinstance(parsed, dict):
            return parsed
    except (ValueError, SyntaxError):
        pass

    logger.warning("mandate_validator_parse_failed", raw_data_type=type(raw_data).__name__)
    return {}


def _get_pending_mandate_info(account: dict) -> tuple[str, dict[str, Any]]:
    """
    Get appropriate message and metadata for pending mandate based on time.

    Returns (message, metadata) where metadata may contain:
    - needs_reinitiation: True if mandate expired and needs to be recreated
    - transfer_destinations: List of banks to transfer ₦50 to
    """
    extra_data = _parse_extra_data(account.get("extra_data"))

    created_at_str = extra_data.get("mandate_created_at")
    transfer_destinations = extra_data.get("transfer_destinations", [])

    if not created_at_str:
        return (
            "Your account authorization has expired. Please reinitiate to continue.",
            {"needs_reinitiation": True},
        )

    try:
        created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
        if created_at.tzinfo:
            created_at = created_at.replace(tzinfo=None)
    except (ValueError, AttributeError):
        return (
            "Your account authorization has expired. Please reinitiate to continue.",
            {"needs_reinitiation": True},
        )

    now = datetime.utcnow()
    time_elapsed = now - created_at

    if time_elapsed <= timedelta(hours=MANDATE_EXPIRY_HOURS):
        minutes_left = int(
            (timedelta(hours=MANDATE_EXPIRY_HOURS) - time_elapsed).total_seconds() / 60
        )

        if transfer_destinations:
            dest_lines = "\n".join(
                [
                    f"• *{d.get('bank_name')}*: {d.get('account_number')}"
                    for d in transfer_destinations
                ]
            )
            message = (
                f"Your account authorization is pending.\n\n"
                f"Transfer ₦50 to any of these accounts:\n{dest_lines}\n\n"
                f"⏱️ {minutes_left} minutes remaining"
            )
        else:
            message = "Your account authorization is pending. Please complete the ₦50 transfer to activate your account."

        return (message, {"transfer_destinations": transfer_destinations})
    else:
        return (
            "Your account authorization has expired. Please reinitiate to continue.",
            {"needs_reinitiation": True},
        )


def validate_mandate_status(account: dict) -> tuple[bool, str | None, dict[str, Any] | None]:
    """
    Validate that an account's mandate status allows transactions.

    Args:
        account: Account dict with mandate_status and extra_data fields

    Returns:
        Tuple of (is_valid, error_message, metadata).
        - is_valid: True if account can transact, False otherwise
        - error_message: Human-readable error message if not valid, None if valid
        - metadata: Additional info (needs_reinitiation, transfer_destinations, etc.)
    """
    if settings.app_env == "development":
        if account.get("_mock_mandate_ready"):
            return (True, None, None)

    mandate_status = account.get("mandate_status", "pending")

    if mandate_status == "ready":
        return (True, None, None)

    # Handle pending/initiated - user hasn't sent ₦50 transfer yet
    if mandate_status in ("pending", "initiated"):
        message, metadata = _get_pending_mandate_info(account)
        return (False, message, metadata)

    # Handle approved - transfer received, waiting for NIBSS confirmation (up to 24 hours)
    if mandate_status == "approved":
        bank_name = account.get("bank_name", "your bank")
        account_number = account.get("account_number", "")
        account_suffix = f"({account_number[-4:]})" if account_number else ""
        message = (
            f"✓ Your ₦50 authorization transfer was received!\n\n"
            f"Your {bank_name} account {account_suffix} is being verified by NIBSS. "
            f"This usually takes a few minutes but can take up to 24 hours.\n\n"
            f"We'll notify you as soon as it's ready! 🔔"
        )
        return (False, message, {"awaiting_nibss": True})

    status_messages = {
        "paused": (
            "⏸️ Your account has been temporarily paused.\n"
            "Please contact support to reinstate it."
        ),
        "rejected": (
            "❌ Your account authorization was rejected.\n"
            "Please contact support to resolve this."
        ),
        "cancelled": (
            "Your account authorization was cancelled.\n"
            "Would you like to reinitiate? Say *'reinitiate'* to start again."
        ),
    }

    message = status_messages.get(mandate_status, "Your account is not ready for payments yet.")

    metadata = {"needs_reinitiation": True} if mandate_status == "cancelled" else None

    return (False, message, metadata)
