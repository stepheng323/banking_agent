from typing import Any

from apps.chat.src.agent.orchestrator.models.domain import TaskSpec
from banking.presentation.formatters.currency import format_naira
from shared.utils.network_utils import format_network_display_name


def _fmt_amount(value: Any) -> str | None:
    try:
        return format_naira(float(value))
    except (TypeError, ValueError):
        return None


def _format_task_details_for_status(task: TaskSpec, task_type: str) -> str:
    payload = task.payload or {}

    if task_type == "transfer":
        recipient = payload.get("recipient_resolved_name") or payload.get("recipient_name")
        amount = payload.get("amount")
        source_bank = payload.get("source_bank_name")
        parts: list[str] = []
        formatted_amount = _fmt_amount(amount)
        if formatted_amount:
            parts.append(f"amount {formatted_amount}")
        if recipient:
            parts.append(f"recipient {recipient}")
        if source_bank:
            parts.append(f"source {source_bank}")
        if parts:
            return "Known details: " + ", ".join(parts) + "."

    if task_type == "airtime":
        phone = payload.get("phone") or payload.get("recipient_phone")
        amount = payload.get("amount")
        network = format_network_display_name(payload.get("network"))
        source_bank = payload.get("source_bank_name")
        parts = []
        formatted_amount = _fmt_amount(amount)
        if formatted_amount:
            parts.append(f"amount {formatted_amount}")
        if network:
            parts.append(f"network {network}")
        if phone:
            parts.append(f"line {phone}")
        if source_bank:
            parts.append(f"source {source_bank}")
        if parts:
            return "Known details: " + ", ".join(parts) + "."

    if task_type == "data":
        phone = payload.get("target_phone") or payload.get("phone") or payload.get("recipient_phone")
        plan = payload.get("plan_name") or payload.get("biller_item_name") or payload.get("plan")
        amount = payload.get("amount")
        network = format_network_display_name(payload.get("network"))
        source_bank = payload.get("source_bank_name")
        parts = []
        if plan:
            parts.append(f"plan {plan}")
        formatted_amount = _fmt_amount(amount)
        if formatted_amount:
            parts.append(f"amount {formatted_amount}")
        if network:
            parts.append(f"network {network}")
        if phone:
            parts.append(f"line {phone}")
        if source_bank:
            parts.append(f"source {source_bank}")
        if parts:
            return "Known details: " + ", ".join(parts) + "."

    return ""


__all__ = ["_format_task_details_for_status"]
