"""Shared mandate status validation for all transaction flows."""

import ast
import json
from datetime import datetime, timedelta
from typing import Any

import structlog

from shared.config.settings import settings
from shared.i18n import render_message

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


def _get_pending_mandate_info(account: dict, locale: str = "en") -> tuple[str, dict[str, Any]]:
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
            render_message("mandate.expired_reinitiate", locale),
            {"needs_reinitiation": True},
        )

    try:
        created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
        if created_at.tzinfo:
            created_at = created_at.replace(tzinfo=None)
    except (ValueError, AttributeError):
        return (
            render_message("mandate.expired_reinitiate", locale),
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
                render_message(
                    "mandate.pending_with_destinations",
                    locale,
                    {"destinations": dest_lines, "minutes_left": minutes_left},
                )
            )
        else:
            message = render_message("mandate.pending_complete_transfer", locale)

        return (message, {"transfer_destinations": transfer_destinations})
    else:
        return (
            render_message("mandate.expired_reinitiate", locale),
            {"needs_reinitiation": True},
        )


def validate_mandate_status(
    account: dict,
    locale: str = "en",
) -> tuple[bool, str | None, dict[str, Any] | None]:
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
        message, metadata = _get_pending_mandate_info(account, locale=locale)
        return (False, message, metadata)

    # Handle approved - transfer received, waiting for NIBSS confirmation (up to 24 hours)
    if mandate_status == "approved":
        bank_name = account.get("bank_name", render_message("mandate.bank_fallback", locale))
        account_number = account.get("account_number", "")
        account_suffix = f"({account_number[-4:]})" if account_number else ""
        message = render_message(
            "mandate.approved_awaiting_nibss",
            locale,
            {"bank_name": bank_name, "account_suffix": account_suffix},
        )
        return (False, message, {"awaiting_nibss": True})

    status_messages = {
        "paused": render_message("mandate.status_paused", locale),
        "rejected": render_message("mandate.status_rejected", locale),
        "cancelled": render_message("mandate.status_cancelled", locale),
    }

    message = status_messages.get(mandate_status, render_message("mandate.status_not_ready", locale))

    metadata = {"needs_reinitiation": True} if mandate_status == "cancelled" else None

    return (False, message, metadata)
