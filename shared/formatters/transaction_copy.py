"""Shared helpers for context-aware transaction copy."""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date, datetime
from typing import Any

from shared.formatters.currency import format_amount_number, format_naira, format_naira_compact
from shared.formatters.recipient_display import format_summary_recipient_display_label
from shared.i18n import MessageKey, render_message
from shared.i18n.personality import PersonalityContext, render_personalized_message


def format_amount_compact(amount: float | int | str | None) -> str:
    """Format Naira values without forced trailing decimals."""
    return format_naira_compact(amount)


def _normalize_status_for_copy(status: Any) -> str:
    raw = getattr(status, "value", status)
    normalized = str(raw or "").strip().lower().replace("-", " ").replace("_", " ")
    if "." in normalized:
        normalized = normalized.rsplit(".", 1)[-1]
    return " ".join(normalized.split())


def format_transaction_status_reply(
    status: Any,
    *,
    locale: str,
    local_status: Any | None = None,
    bank_status: Any | None = None,
    needs_review: bool = False,
) -> str:
    """Return canonical user-facing copy for a transaction status fact."""
    normalized = _normalize_status_for_copy(status)
    if not normalized:
        return render_message("query.reply.status.unavailable", locale)

    normalized_local = _normalize_status_for_copy(local_status)
    normalized_bank = _normalize_status_for_copy(bank_status)
    if needs_review and normalized_local == "failed" and normalized_bank == "posted":
        return render_message("query.reply.status.failed_bank_posted", locale)
    if normalized_local in {"pending", "processing"} and normalized_bank == "posted":
        return render_message("query.reply.status.processing_bank_posted", locale)

    key_by_status: dict[str, MessageKey] = {
        "posted": "query.reply.status.posted",
        "success": "query.reply.status.successful",
        "successful": "query.reply.status.successful",
        "complete": "query.reply.status.successful",
        "completed": "query.reply.status.successful",
        "confirmed": "query.reply.status.successful",
        "pending": "query.reply.status.pending",
        "processing": "query.reply.status.processing",
        "queued": "query.reply.status.processing",
        "in progress": "query.reply.status.processing",
        "failed": "query.reply.status.failed",
        "failure": "query.reply.status.failed",
        "declined": "query.reply.status.failed",
        "rejected": "query.reply.status.failed",
        "reversed": "query.reply.status.reversed",
        "refunded": "query.reply.status.reversed",
    }
    key = key_by_status.get(normalized)
    if key is not None:
        return render_message(key, locale)
    return render_message("query.reply.status.generic", locale, {"status": normalized})


def format_transaction_status_field_value(status: Any, *, locale: str) -> str:
    """Return compact status copy for transaction detail fields."""
    normalized = _normalize_status_for_copy(status)
    if normalized in {"success", "successful", "completed", "complete", "confirmed"}:
        return render_message("query.format.status_success", locale)
    return render_message("query.format.status_pending_generic", locale, {"status": normalized.title()})


def format_transaction_list_item(
    transaction: Any,
    *,
    locale: str,
    metadata: dict[str, Any] | None = None,
) -> str:
    """Format a compact transaction row for list-style query surfaces."""
    data = metadata or _metadata(transaction)
    description = _string(_field(transaction, "description"))
    counterparty = data.get("counterparty")
    tx_type = _string(data.get("type")).lower()
    transaction_type = _string(data.get("transaction_type")).lower()
    amount = _format_amount_plain(_field(transaction, "amount"))

    if transaction_type in {"airtime", "data"}:
        recipient = counterparty or _extract_phone_recipient(description)
        narration = render_message(
            "query.format.narration.type_for_recipient",
            locale,
            {
                "type": transaction_type.title(),
                "recipient": recipient or render_message("query.format.recipient_fallback", locale),
            },
        )
    elif isinstance(counterparty, str) and counterparty.strip():
        if "transfer" in description.lower():
            prefix = (
                render_message("query.format.narration.transfer_from", locale)
                if tx_type == "credit"
                else render_message("query.format.narration.transfer_to", locale)
            )
            narration = f"{prefix} {counterparty.strip()}"
        else:
            narration = counterparty.strip()
    else:
        narration = description or render_message("query.format.narration.transaction", locale)

    label = (
        render_message("query.format.label_received", locale)
        if tx_type == "credit"
        else render_message("query.format.label_sent", locale)
    )
    bank_name = _string(data.get("bank_name"))
    if bank_name:
        return render_message(
            "query.format.transaction_item_with_bank",
            locale,
            {"amount": amount, "label": label, "narration": narration, "bank_name": bank_name},
        )
    return render_message(
        "query.format.transaction_item",
        locale,
        {"amount": amount, "label": label, "narration": narration},
    )


def format_transaction_evidence_line(
    *,
    amount: Any,
    date_value: Any,
    counterparty: str | None,
    bank_name: str | None,
    used_fields: set[str],
    locale: str,
) -> str | None:
    """Build the compact evidence line below a direct transaction fact."""
    parts: list[str] = []
    if "amount" not in used_fields:
        parts.append(_format_amount_plain(amount))
    if "date" not in used_fields:
        date_text = _format_short_date(date_value, locale=locale)
        if date_text:
            parts.append(date_text)
    if "counterparty" not in used_fields and counterparty:
        parts.append(counterparty)
    if "bank" not in used_fields and bank_name:
        parts.append(bank_name)
    return " • ".join(parts[:3]) or None


def build_transaction_detail_lines(
    transaction: Any,
    *,
    locale: str,
    metadata: dict[str, Any] | None = None,
) -> list[str]:
    """Build canonical detail fields for a single transaction surface."""
    data = metadata or _metadata(transaction)
    description = _string(_field(transaction, "description")) or render_message(
        "query.format.narration.transaction",
        locale,
    )
    lines = [
        render_message(
            "query.format.field_amount",
            locale,
            {"amount": _format_amount_plain(_field(transaction, "amount"), detail=True)},
        ),
        render_message("query.format.field_description", locale, {"description": description}),
        render_message(
            "query.format.field_date",
            locale,
            {"date": _format_long_date(_field(transaction, "date"), locale=locale)},
        ),
    ]

    tx_type = _string(data.get("type"))
    if tx_type:
        direction = (
            render_message("query.format.type_outgoing_debit", locale)
            if tx_type == "debit"
            else render_message("query.format.type_incoming_credit", locale)
        )
        lines.append(render_message("query.format.field_type", locale, {"type": direction}))

    bank_name = _string(data.get("bank_name"))
    if bank_name:
        lines.append(render_message("query.format.field_bank", locale, {"bank_name": bank_name}))

    transaction_type = _string(data.get("transaction_type"))
    if transaction_type:
        lines.append(render_message("query.format.field_category", locale, {"category": transaction_type.title()}))

    status = _string(data.get("status"))
    if status:
        lines.append(
            render_message(
                "query.format.field_status",
                locale,
                {"status": format_transaction_status_field_value(status, locale=locale)},
            )
        )

    reference = _display_reference(transaction, data)
    if reference:
        lines.append(render_message("query.format.field_ref", locale, {"reference": reference}))
    return lines


def format_support_transfer_status_sentence(
    transaction: dict[str, Any],
    *,
    status: str,
    locale: str,
) -> str:
    """Build support's compact transfer status/detail sentence."""
    amount = _format_support_amount_value(transaction.get("amount"))
    recipient = transaction.get("recipient_name") or "recipient"

    if status == "successful":
        time_str = _parse_support_time(transaction.get("created_at"))
        if time_str:
            return render_message(
                "support.status.success_with_time",
                locale,
                {"amount": amount, "recipient": recipient, "time": time_str},
            )
        return render_message(
            "support.status.success_no_time",
            locale,
            {"amount": amount, "recipient": recipient},
        )

    if status in {"pending", "processing"}:
        return render_message(
            "support.status.pending",
            locale,
            {"amount": amount, "recipient": recipient},
        )

    if status == "failed":
        return render_message(
            "support.status.failed",
            locale,
            {"amount": amount, "recipient": recipient},
        )

    return render_message("support.status.raw_status", locale, {"status": status})


def _string(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _field(payload: Any, key: str) -> Any:
    if isinstance(payload, dict):
        return payload.get(key)
    return getattr(payload, key, None)


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


def _metadata(payload: Any) -> dict[str, Any]:
    metadata = _field(payload, "metadata")
    return dict(metadata) if isinstance(metadata, dict) else {}


def _format_amount_plain(amount: Any, *, detail: bool = False) -> str:
    if detail:
        return format_naira(amount, decimal_places=2, absolute=True)
    return format_naira_compact(amount, absolute=True)


def _format_support_amount_value(amount: Any) -> str:
    return format_amount_number(amount)


def _format_short_date(value: Any, *, locale: str) -> str:
    del locale
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.strftime("%b %d").replace(" 0", " ")
    raw_date = _string(value)
    try:
        parsed = datetime.strptime(raw_date[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return raw_date[:10] if raw_date else ""
    return parsed.strftime("%b %d").replace(" 0", " ")


def _format_long_date(value: Any, *, locale: str) -> str:
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.strftime("%B %d, %Y")
    raw_date = _string(value)
    try:
        parsed = datetime.strptime(raw_date[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return render_message("query.format.unknown", locale)
    return parsed.strftime("%B %d, %Y")


def _parse_support_time(value: Any) -> str:
    raw = _string(value)
    if not raw:
        return ""
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return ""
    return parsed.strftime("%b %d at %I:%M %p")


def _display_reference(payload: Any, metadata: dict[str, Any]) -> str | None:
    for key in ("transaction_id", "reference", "ref"):
        value = _string(metadata.get(key))
        if value:
            return value

    item_id = _string(_field(payload, "id"))
    if not item_id or re.fullmatch(r"\d+", item_id):
        return None
    return item_id


def _extract_phone_recipient(description: str) -> str | None:
    phone_match = re.search(r"(\d{10,11})", description or "")
    return phone_match.group(1) if phone_match else None


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


def build_confirmation_header(
    *,
    task_types: Iterable[str],
    locale: str,
    task_count: int,
    task_actions: Iterable[str] | None = None,
    personality_context: PersonalityContext | None = None,
) -> str:
    """Build a context-aware confirmation header."""
    if task_count > 1:
        return render_message("transaction_copy.confirmation.mixed", locale)

    mix = derive_task_mix(task_types)
    actions = {str(action or "").strip() for action in (task_actions or [])}
    if actions & {"edit_scheduled_transaction"}:
        return render_message("transaction_copy.confirmation.schedule_update", locale)
    if mix == "transfer":
        if actions & {"schedule_transfer", "recurring_transfer"}:
            return render_message("transaction_copy.confirmation.scheduled_transfer", locale)
        return render_personalized_message(
            "transaction_copy.confirmation.transfer",
            locale,
            context=personality_context,
        )
    if mix == "airtime":
        if actions & {"schedule_airtime", "recurring_airtime"}:
            return render_message("transaction_copy.confirmation.scheduled_airtime", locale)
        return render_personalized_message(
            "transaction_copy.confirmation.airtime",
            locale,
            context=personality_context,
        )
    if mix == "data":
        if actions & {"schedule_data", "recurring_data"}:
            return render_message("transaction_copy.confirmation.scheduled_data", locale)
        return render_personalized_message(
            "transaction_copy.confirmation.data",
            locale,
            context=personality_context,
        )
    return render_message("transaction_copy.confirmation.generic", locale)


def build_confirmation_section_label(task_type: str, *, locale: str) -> str:
    """Return the short section label for one task inside a mixed confirmation."""
    normalized = str(task_type or "").strip().lower()
    if normalized not in {"transfer", "airtime", "data"}:
        normalized = "generic"
    return render_message(f"transaction_copy.confirmation.section.{normalized}", locale)


def format_confirmation_section(*, task_type: str, summary: str, locale: str) -> str:
    """Prefix a task summary with a readable label for mixed confirmations."""
    cleaned = str(summary or "").strip()
    if not cleaned:
        return ""
    label = build_confirmation_section_label(task_type, locale=locale).strip()
    if not label:
        return cleaned
    return f"{label}\n{cleaned}"


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
