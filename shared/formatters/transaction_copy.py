"""Shared helpers for context-aware transaction copy."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from shared.formatters.recipient_display import format_summary_recipient_display_label
from shared.i18n import render_message


def format_amount_compact(amount: float | int | str | None) -> str:
    """Format Naira values without forced trailing decimals."""
    try:
        value = float(amount or 0)
    except (TypeError, ValueError):
        return "₦0"
    if value.is_integer():
        return f"₦{value:,.0f}"
    return f"₦{value:,.2f}".rstrip("0").rstrip(".")


def _string(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _mapping(payload: Any) -> dict[str, Any]:
    if payload is None:
        return {}
    if isinstance(payload, dict):
        return dict(payload)
    if hasattr(payload, "model_dump"):
        dumped = payload.model_dump(exclude_none=True)
        if isinstance(dumped, dict):
            return dumped
    return {}


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
    initial_task_type = task_type or (
        effective_task_types[0] if effective_task_types else None
    )
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


def build_confirmation_header(*, task_types: Iterable[str], locale: str, task_count: int) -> str:
    """Build a context-aware confirmation header."""
    if task_count > 1:
        return render_message("transaction_copy.confirmation.mixed", locale)

    mix = derive_task_mix(task_types)
    if mix == "transfer":
        return render_message("transaction_copy.confirmation.transfer", locale)
    if mix == "airtime":
        return render_message("transaction_copy.confirmation.airtime", locale)
    if mix == "data":
        return render_message("transaction_copy.confirmation.data", locale)
    return render_message("transaction_copy.confirmation.generic", locale)


def build_completion_frame(*, task_types: Iterable[str], locale: str, task_count: int) -> tuple[str, str]:
    """Return completion header/footer copy for the task composition."""
    mix = derive_task_mix(task_types)
    plural = task_count > 1

    if mix == "transfer":
        header_key = (
            "transaction_copy.completion.header.transfer_plural"
            if plural
            else "transaction_copy.completion.header.transfer"
        )
        footer_key = (
            "transaction_copy.completion.footer.transfer_plural"
            if plural
            else "transaction_copy.completion.footer.transfer"
        )
        return render_message(header_key, locale), render_message(footer_key, locale)

    if mix == "airtime":
        header_key = (
            "transaction_copy.completion.header.airtime_plural"
            if plural
            else "transaction_copy.completion.header.airtime"
        )
        footer_key = (
            "transaction_copy.completion.footer.airtime_plural"
            if plural
            else "transaction_copy.completion.footer.airtime"
        )
        return render_message(header_key, locale), render_message(footer_key, locale)

    if mix == "data":
        header_key = (
            "transaction_copy.completion.header.data_plural"
            if plural
            else "transaction_copy.completion.header.data"
        )
        footer_key = (
            "transaction_copy.completion.footer.data_plural"
            if plural
            else "transaction_copy.completion.footer.data"
        )
        return render_message(header_key, locale), render_message(footer_key, locale)

    return (
        render_message("transaction_copy.completion.header.mixed", locale),
        render_message("transaction_copy.completion.footer.mixed", locale),
    )
