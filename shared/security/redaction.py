"""Redaction helpers for sensitive bank identifiers in logs and JSON blobs."""

from __future__ import annotations

from typing import Any

from shared.security.field_encryption import account_number_last4
from shared.utils.logging import log_fingerprint

_ACCOUNT_NUMBER_KEYS = {
    "account_number",
    "beneficiary_account",
    "recipient_account",
    "recipient_account_number",
    "source_account_number",
}
_MANDATE_KEYS = {"mandate", "mandate_id"}


def mask_account_number(value: Any) -> str:
    """Return a last4-only account-number display string."""
    last4 = account_number_last4(value)
    return f"****{last4}" if last4 else ""


def redact_sensitive_identifiers(value: Any) -> Any:
    """Recursively redact full bank account numbers and mandate IDs."""
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            normalized_key = str(key).strip().lower()
            if normalized_key in _ACCOUNT_NUMBER_KEYS:
                redacted[key] = mask_account_number(item)
            elif normalized_key == "account":
                redacted[key] = (
                    redact_sensitive_identifiers(item) if isinstance(item, (dict, list)) else mask_account_number(item)
                )
            elif normalized_key in _MANDATE_KEYS:
                redacted[key] = log_fingerprint(item)
            else:
                redacted[key] = redact_sensitive_identifiers(item)
        return redacted
    if isinstance(value, list):
        return [redact_sensitive_identifiers(item) for item in value]
    return value
