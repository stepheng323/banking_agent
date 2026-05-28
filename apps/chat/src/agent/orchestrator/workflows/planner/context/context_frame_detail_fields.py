"""Field selection helpers for context-frame detail responses."""

from typing import Any

from apps.chat.src.agent.orchestrator.context.models import ContextEntity, ContextFrameType

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


def frame_noun(frame_type: ContextFrameType, *, plural: bool) -> str:
    if frame_type == ContextFrameType.BENEFICIARY_LIST:
        return "saved beneficiaries" if plural else "saved beneficiary"
    if frame_type == ContextFrameType.ACCOUNT_LIST:
        return "linked accounts" if plural else "linked account"
    if frame_type == ContextFrameType.SCHEDULE_LIST:
        return "scheduled transactions" if plural else "scheduled transaction"
    if frame_type == ContextFrameType.TRANSACTION_LIST:
        return "transactions or results" if plural else "transaction or result"
    if frame_type == ContextFrameType.TRANSACTION_DETAIL:
        return "transactions" if plural else "transaction"
    if frame_type == ContextFrameType.RECEIPT:
        return "receipt"
    return "items" if plural else "item"


def candidate_detail_fields(entity: ContextEntity) -> list[tuple[str, Any]]:
    data = entity.data if isinstance(entity.data, dict) else {}
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
