"""Field selection helpers for context-frame detail responses."""

from typing import Any

from apps.chat.src.agent.orchestrator.context.models import ContextEntity, ContextFrameType
from banking.presentation.i18n.message_keys import as_message_key
from banking.presentation.i18n.renderer import render_message

CONTEXT_READ_LIST_LIMIT = 5
SENSITIVE_DETAIL_KEYS = {"pin", "otp", "password", "token", "secret"}
FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "amount": ("amount",),
    "bank": ("bank_name", "bank", "recipient_bank_name", "source_bank_name"),
    "counterparty": ("counterparty", "recipient_resolved_name", "recipient_name", "merchant", "name"),
    "date": ("date", "created_at", "completed_at"),
    "network": ("network",),
    "phone": ("recipient_phone", "phone", "phone_number", "target_phone"),
    "reference": ("reference", "transaction_id", "idempotency_key"),
    "status": ("status", "provider_status", "final_status", "mandate_status"),
}


def frame_noun(frame_type: ContextFrameType, *, plural: bool, locale: str = "en") -> str:
    if frame_type == ContextFrameType.BENEFICIARY_LIST:
        key = "saved_beneficiaries" if plural else "saved_beneficiary"
        return render_message(as_message_key(f"context_frame.noun.{key}"), locale)
    if frame_type == ContextFrameType.ACCOUNT_LIST:
        key = "linked_accounts" if plural else "linked_account"
        return render_message(as_message_key(f"context_frame.noun.{key}"), locale)
    if frame_type == ContextFrameType.SCHEDULE_LIST:
        key = "scheduled_transactions" if plural else "scheduled_transaction"
        return render_message(as_message_key(f"context_frame.noun.{key}"), locale)
    if frame_type == ContextFrameType.SCHEDULE_RUN_LIST:
        return render_message(
            "context_frame.noun.scheduled_runs" if plural else "context_frame.noun.scheduled_run",
            locale,
        )
    if frame_type == ContextFrameType.SUPPORT_TICKET_LIST:
        return render_message(
            "context_frame.noun.support_tickets" if plural else "context_frame.noun.support_ticket",
            locale,
        )
    if frame_type == ContextFrameType.TRANSACTION_LIST:
        key = "transactions_or_results" if plural else "transaction_or_result"
        return render_message(as_message_key(f"context_frame.noun.{key}"), locale)
    if frame_type == ContextFrameType.TRANSACTION_DETAIL:
        key = "transactions" if plural else "transaction"
        return render_message(as_message_key(f"context_frame.noun.{key}"), locale)
    if frame_type == ContextFrameType.RECEIPT:
        return render_message("context_frame.noun.receipt", locale)
    key = "items" if plural else "item"
    return render_message(as_message_key(f"context_frame.noun.{key}"), locale)


def candidate_detail_fields(entity: ContextEntity) -> list[tuple[str, Any]]:
    data = entity.data if isinstance(entity.data, dict) else {}
    keys: tuple[str, ...]
    if entity.entity_type.value == "beneficiary":
        keys = ("account_name", "name", "bank_name", "bank", "account_number", "account")
    elif entity.entity_type.value == "account":
        keys = ("bank_name", "account_number", "mandate_status", "available_balance", "balance")
    elif entity.entity_type.value == "transaction":
        keys = (
            "amount",
            "date",
            "counterparty",
            "description",
            "bank_name",
            "bank",
            "transaction_type",
            "type",
            "direction",
            "status",
            "category",
            "merchant",
            "network",
            "phone",
            "recipient_phone",
            "reference",
            "narration",
        )
    elif entity.data.get("type") == "scheduled_transaction":
        keys = (
            "domain",
            "amount",
            "target",
            "recurrence",
            "schedule_time",
            "next_run",
            "source_bank_name",
            "status",
        )
    else:
        keys = (
            "summary",
            "description",
            "status",
            "amount",
            "count",
            "date",
            "bank_name",
            "bank",
            "group_by",
            "group_key",
            "category",
            "merchant",
            "network",
            "phone",
            "recipient_phone",
            "transaction_type",
            "type",
        )

    fields: list[tuple[str, Any]] = []
    for key in keys:
        if key in SENSITIVE_DETAIL_KEYS:
            continue
        value = data.get(key)
        if value is None or value == "":
            continue
        fields.append((display_key(key), value))
    return fields


def display_key(key: str) -> str:
    return key.replace("_", " ").strip().title()


def requested_field_keys(requested_field: str | None) -> tuple[str, tuple[str, ...]] | None:
    if not requested_field:
        return None
    label = requested_field.strip().lower()
    keys = FIELD_ALIASES.get(label)
    return (label, keys) if keys else None


def entity_field_value(entity: ContextEntity, keys: tuple[str, ...]) -> Any:
    data = entity.data if isinstance(entity.data, dict) else {}
    for key in keys:
        value = data.get(key)
        if value is not None and value != "":
            return value
    return None


__all__ = [
    "CONTEXT_READ_LIST_LIMIT",
    "candidate_detail_fields",
    "display_key",
    "entity_field_value",
    "frame_noun",
    "requested_field_keys",
]
