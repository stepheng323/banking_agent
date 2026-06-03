"""Account-status response helpers for context-frame follow-ups."""

import json
from typing import Any

from apps.chat.src.agent.orchestrator.context.models import ContextEntity, ContextFrame, ContextFrameType
from banking.accounts.mandate_state import READY, effective_mandate_status


def pending_account_entities(frame: ContextFrame) -> list[ContextEntity]:
    if frame.frame_type != ContextFrameType.ACCOUNT_LIST:
        return []
    entities: list[ContextEntity] = []
    for entity in frame.items:
        data = entity.data if isinstance(entity.data, dict) else {}
        status = effective_mandate_status(data)
        if status and status != READY:
            entities.append(entity)
    return entities


def format_account_status_explanation(entity: ContextEntity) -> str | None:
    data = entity.data if isinstance(entity.data, dict) else {}
    display_status = str(data.get("mandate_status") or data.get("status") or "").strip()
    status = effective_mandate_status(data)
    if not status:
        return None

    label = entity.label or str(data.get("bank_name") or "This account")
    normalized_status = status.lower()
    if normalized_status in {"pending", "awaiting_authorization"}:
        raw_display = display_status or status
        readable_status = "awaiting authorization" if raw_display.lower() == "awaiting_authorization" else "pending"
        lines = [
            f"{label} is still {readable_status} because the account authorization is not complete yet.",
            "",
            f"Mandate Status: {raw_display}",
        ]
        lines.extend(["", _format_account_status_instruction(normalized_status, data)])
        return "\n".join(lines)
    if normalized_status == "approved":
        return (
            f"{label} authorization has been approved, but the account is still waiting for final readiness checks.\n\n"
            f"Mandate Status: {status}\n\n"
            f"{_format_account_status_instruction(normalized_status, data)}"
        )
    if normalized_status == "ready":
        return (
            f"{label} is ready for transactions.\n\n"
            "Mandate Status: ready\n\n"
            f"{_format_account_status_instruction(normalized_status, data)}"
        )
    if normalized_status == "rejected":
        return (
            f"{label} authorization was rejected. "
            "You may need to restart account authorization or relink the account.\n\n"
            f"Mandate Status: {status}\n\n"
            f"{_format_account_status_instruction(normalized_status, data)}"
        )
    if normalized_status == "cancelled":
        return (
            f"{label} authorization was cancelled. Relink or reauthorize the account to use it for transactions.\n\n"
            f"Mandate Status: {status}\n\n"
            f"{_format_account_status_instruction(normalized_status, data)}"
        )
    if normalized_status == "expired":
        return (
            f"{label} authorization expired before completion. Restart account authorization to activate it.\n\n"
            f"Mandate Status: {status}\n\n"
            f"{_format_account_status_instruction(normalized_status, data)}"
        )
    if normalized_status == "paused":
        return (
            f"{label} authorization is paused.\n\n"
            f"Mandate Status: {status}\n\n"
            f"{_format_account_status_instruction(normalized_status, data)}"
        )
    return f"{label} has mandate status: {status}."


def _account_extra_data(data: dict[str, Any]) -> dict[str, Any]:
    extra_data = data.get("extra_data")
    if isinstance(extra_data, dict):
        return extra_data
    if isinstance(extra_data, str):
        try:
            parsed = json.loads(extra_data)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _account_transfer_destinations(data: dict[str, Any]) -> list[dict[str, str]]:
    raw = data.get("transfer_destinations")
    if not isinstance(raw, list):
        raw = _account_extra_data(data).get("transfer_destinations")
    if not isinstance(raw, list):
        return []

    destinations: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        bank_name = str(item.get("bank_name") or "").strip()
        account_number = str(item.get("account_number") or "").strip()
        if bank_name and account_number:
            destinations.append({"bank_name": bank_name, "account_number": account_number})
    return destinations


def _format_pending_account_completion_steps(data: dict[str, Any]) -> str:
    bank_name = str(data.get("bank_name") or data.get("bank") or "the pending account").strip()
    account_number = str(data.get("account_number") or data.get("number") or "").strip()
    suffix = f" ending in {account_number[-4:]}" if account_number else ""
    destinations = _account_transfer_destinations(data)

    if not destinations:
        return (
            f"To complete it, make the ₦50 authorization transfer from your {bank_name} account{suffix}. "
            "Once the bank/NIBSS confirms it, the account becomes ready."
        )

    lines = [f"To complete it, transfer ₦50 from your {bank_name} account{suffix} to any of these accounts:"]
    for destination in destinations:
        lines.append(f"• {destination['bank_name']}: {destination['account_number']}")
    lines.append("Once the bank/NIBSS confirms it, the account becomes ready.")
    return "\n".join(lines)


def _format_account_status_instruction(normalized_status: str, data: dict[str, Any]) -> str:
    if normalized_status in {"pending", "awaiting_authorization"}:
        return _format_pending_account_completion_steps(data)
    if normalized_status == "approved":
        return (
            "Next step: wait for NIBSS/bank verification. This usually takes a few minutes but can take up to 24 hours."
        )
    if normalized_status == "ready":
        return "Next step: no action needed. You can use this account for payments."
    if normalized_status == "rejected":
        return "Next step: contact support or restart account authorization before using this account for payments."
    if normalized_status == "cancelled":
        return "Next step: reinitiate account authorization or relink the account."
    if normalized_status == "expired":
        return "Next step: restart account authorization; the previous authorization window has expired."
    if normalized_status == "paused":
        return "Next step: contact support to reinstate this account authorization."
    return ""


__all__ = ["format_account_status_explanation", "pending_account_entities"]
