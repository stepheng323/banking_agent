"""Shared task-context helpers for transaction copy."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from banking.presentation.formatters.currency import format_naira_compact
from banking.presentation.formatters.recipient_display import format_summary_recipient_display_label
from banking.presentation.formatters.transaction_copy_common import _mapping, _string


def format_amount_compact(amount: Any) -> str:
    """Format Naira values without forced trailing decimals."""
    return format_naira_compact(amount)


def _normalized_task_type(task_type: str | None) -> str:
    normalized = _string(task_type).lower()
    return normalized or "generic"


def derive_task_mix(task_types: Iterable[str]) -> str:
    """Collapse one or more task types into a copy framing mix."""
    normalized = {_normalized_task_type(task_type) for task_type in task_types if _string(task_type)}
    if not normalized:
        return "generic"
    if len(normalized) > 1:
        return "mixed"
    only = next(iter(normalized))
    if only in {"transfer", "airtime", "data"}:
        return only
    return "generic"


def build_copy_context(
    *,
    task_type: str | None = None,
    payload: Any | None = None,
    task_types: Iterable[str] | None = None,
    task_count: int | None = None,
) -> dict[str, str]:
    """Normalize common copy fields from task/query payloads."""
    data = _mapping(payload)
    effective_task_types = list(task_types or [])
    initial_task_type = task_type or (effective_task_types[0] if effective_task_types else None)
    normalized_task_type = _normalized_task_type(initial_task_type)
    task_mix = derive_task_mix(effective_task_types or [normalized_task_type])
    if task_count and task_count > 1:
        task_mix = "mixed"

    recipient_display = format_summary_recipient_display_label(
        _string(data.get("recipient_name") or data.get("recipientName") or data.get("counterparty")),
        _string(data.get("recipient_resolved_name") or data.get("recipientResolvedName")),
    )
    phone = _string(
        data.get("phone")
        or data.get("phone_number")
        or data.get("recipient_phone")
        or data.get("recipientPhone")
        or data.get("target_phone")
    )
    network = _string(data.get("network"))
    plan_name = _string(data.get("plan_name") or data.get("planName"))
    counterparty_label = _string(data.get("counterparty_label") or data.get("counterparty"))
    if not counterparty_label and recipient_display:
        counterparty_label = recipient_display
    if not recipient_display and counterparty_label:
        recipient_display = counterparty_label

    context = {
        "task_type": normalized_task_type,
        "task_mix": task_mix,
    }
    if "amount" in data and data.get("amount") is not None:
        context["amount"] = format_amount_compact(data.get("amount"))
    if recipient_display:
        context["recipient_display"] = recipient_display
    if counterparty_label:
        context["counterparty_label"] = counterparty_label
    if phone:
        context["phone"] = phone
    if network:
        context["network"] = network
    if plan_name:
        context["plan_name"] = plan_name
    if _string(data.get("direction")):
        context["direction"] = _string(data.get("direction"))
    if _string(data.get("time_label")):
        context["time_label"] = _string(data.get("time_label"))
    if _string(data.get("scope_label")):
        context["scope_label"] = _string(data.get("scope_label"))
    return context
