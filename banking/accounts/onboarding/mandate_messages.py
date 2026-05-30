"""Shared helpers for rendering mandate pending/auth messages."""

from __future__ import annotations

import json
from typing import Any

from banking.presentation.i18n.renderer import render_message


def format_mandate_auth_message(
    *,
    account_number: str,
    bank_name: str,
    transfer_destinations: list[dict[str, str]],
    is_reinitiation: bool = False,
) -> str:
    """Build a rich mandate authorization instruction message."""
    if is_reinitiation:
        lines = [
            "✓ *Mandate Reinitiated Successfully!*",
            "",
            f"To activate your {bank_name} account ending in {account_number[-4:]}, "
            "transfer ₦50 from that account to any of these accounts:",
            "",
        ]
    else:
        lines = [
            "📋 *One Last Step to Complete Setup*",
            "",
            f"To activate your {bank_name} account ending in {account_number[-4:]}, "
            "transfer ₦50 from that account to any of these accounts:",
            "",
        ]

    for destination in transfer_destinations:
        dest_bank = destination.get("bank_name", "")
        dest_account = destination.get("account_number", "")
        lines.append(f"• *{dest_bank}*: {dest_account}")

    lines.extend(
        [
            "",
            "⚠️ Important:",
            "• Transfer must come from your linked account",
            "• Complete within 1 hour",
            "• This ₦50 goes to NIBSS for verification",
            "",
            "Once done, your account will be ready in about 1 hour!",
        ]
    )
    return "\n".join(lines)


def _load_extra_data(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _normalize_destinations(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []

    destinations: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        bank_name = str(item.get("bank_name") or "").strip()
        account_number = str(item.get("account_number") or "").strip()
        if not bank_name or not account_number:
            continue
        destinations.append(
            {
                "bank_name": bank_name,
                "account_number": account_number,
            }
        )
    return destinations


def build_pending_mandate_message(accounts: list[dict[str, Any]], locale: str) -> str:
    """Build contextual pending-mandate message from account context, with fallback."""
    for account in accounts:
        if not isinstance(account, dict):
            continue
        status = str(account.get("mandate_status") or "").strip().lower()
        if not status or status == "ready":
            continue

        extra_data = _load_extra_data(account.get("extra_data"))
        destinations = _normalize_destinations(extra_data.get("transfer_destinations"))
        if not destinations:
            continue

        return format_mandate_auth_message(
            account_number=str(account.get("account_number") or ""),
            bank_name=str(account.get("bank_name") or "your bank"),
            transfer_destinations=destinations,
        )

    return render_message("mandate.pending_complete_transfer", locale)
