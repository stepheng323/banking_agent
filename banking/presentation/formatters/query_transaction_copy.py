"""Transaction query copy helpers."""

from __future__ import annotations

from typing import Any

from banking.presentation.formatters.transaction_copy_common import (
    _display_reference,
    _extract_phone_recipient,
    _field,
    _format_amount_plain,
    _format_long_date,
    _format_short_date,
    _metadata,
    _normalize_status_for_copy,
    _string,
)
from banking.presentation.i18n.message_keys import MessageKey
from banking.presentation.i18n.renderer import render_message


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
    status_label = _transaction_list_status_label(transaction, data)

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

    if status_label is not None and isinstance(counterparty, str) and counterparty.strip():
        narration = counterparty.strip()

    label = status_label or (
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


def _transaction_list_status_label(transaction: Any, metadata: dict[str, Any]) -> str | None:
    status = _normalize_status_for_copy(
        metadata.get("display_status")
        or metadata.get("status")
        or metadata.get("local_status")
        or metadata.get("provider_status")
        or _field(transaction, "display_status")
        or _field(transaction, "status")
    )
    if status in {"success", "successful", "completed", "complete", "confirmed", "posted"}:
        return None

    transaction_type = _string(metadata.get("transaction_type")).lower()
    description = _string(_field(transaction, "description")).lower()
    is_transfer = transaction_type == "transfer" or "transfer" in description
    noun = "transfer" if is_transfer else "transaction"

    if status in {"failed", "failure", "declined", "rejected"}:
        return f"Failed {noun}"
    if status in {"reversed", "refunded"}:
        return f"Reversed {noun}"
    if status in {"pending", "processing", "queued", "in progress"}:
        return f"Processing {noun}"
    return None


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
        date_text = _format_short_date(date_value)
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
