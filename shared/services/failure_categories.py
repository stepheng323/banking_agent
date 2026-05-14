"""Shared failure category helpers for replay-safe transaction metadata."""

from __future__ import annotations

from typing import Literal

FailureCategory = Literal[
    "provider_unavailable",
    "provider_declined",
    "insufficient_funds",
    "validation_error",
    "source_account",
    "execution_error",
    "unknown",
]

_INSUFFICIENT_FUNDS_CODES = {"51", "insufficient_funds", "insufficient-funds"}
_PROVIDER_UNAVAILABLE_CODES = {"91", "96", "timeout", "timed_out", "unavailable"}
_PROVIDER_DECLINED_CODES = {"05", "14", "30", "57", "58", "declined", "rejected"}


def classify_failure_category(
    *,
    message: str | None = None,
    code: str | None = None,
    context: str | None = None,
) -> FailureCategory:
    """Classify executor failures into stable replay/repair categories."""

    normalized_code = str(code or "").strip().lower()
    normalized_context = str(context or "").strip().lower()
    normalized_message = str(message or "").strip().lower()
    combined = " ".join(part for part in (normalized_context, normalized_message) if part)

    if normalized_code in _INSUFFICIENT_FUNDS_CODES or any(
        phrase in combined
        for phrase in (
            "insufficient fund",
            "insufficient balance",
            "not enough balance",
            "not enough funds",
        )
    ):
        return "insufficient_funds"

    if any(
        phrase in combined
        for phrase in (
            "missing_source_account_id",
            "source_account_mandate_not_ready",
            "source account",
            "mandate",
        )
    ):
        return "source_account"

    if any(
        phrase in combined
        for phrase in (
            "missing_recipient_account_details",
            "invalid_transfer_amount",
            "invalid amount",
            "invalid recipient",
            "account details",
            "validation",
        )
    ):
        return "validation_error"

    if normalized_code in _PROVIDER_UNAVAILABLE_CODES or any(
        phrase in combined
        for phrase in (
            "provider down",
            "provider unavailable",
            "service unavailable",
            "network unavailable",
            "timed out",
            "timeout",
            "temporarily unavailable",
        )
    ):
        return "provider_unavailable"

    if normalized_code in _PROVIDER_DECLINED_CODES or any(
        phrase in combined
        for phrase in (
            "provider declined",
            "declined",
            "rejected",
            "not allowed",
        )
    ):
        return "provider_declined"

    if normalized_context == "execution":
        return "execution_error"

    return "unknown"


__all__ = ["FailureCategory", "classify_failure_category"]
