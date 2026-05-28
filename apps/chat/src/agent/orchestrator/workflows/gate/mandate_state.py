"""Mandate state checks used by gate guardrails."""

from typing import Any


def _has_pending_mandate_without_ready_accounts(loaded_context: dict[str, Any] | None) -> bool:
    if not isinstance(loaded_context, dict):
        return False
    accounts_raw = loaded_context.get("accounts")
    if not isinstance(accounts_raw, list):
        return False

    has_ready = False
    has_pending_like = False
    for account in accounts_raw:
        if not isinstance(account, dict):
            continue
        status = str(account.get("mandate_status") or "").strip().lower()
        if not status:
            continue
        if status == "ready":
            has_ready = True
        else:
            has_pending_like = True

    return has_pending_like and not has_ready
