"""Referent memory ingestion from trusted visible context frames."""

from __future__ import annotations

from typing import Any

from apps.chat.src.agent.orchestrator.context.models import ContextEntity, ContextFrame, ContextFrameType, EntityType
from apps.chat.src.agent.orchestrator.context.referents.models import ReferentSource
from apps.chat.src.agent.orchestrator.context.referents.store import prune_referent_memory
from apps.chat.src.agent.orchestrator.context.referents.values import first_text, safe_data
from apps.chat.src.agent.orchestrator.context.referents.writers import (
    remember_amount,
    remember_data_plan,
    remember_phone,
    remember_recipient_like,
    remember_source_account,
    remember_transaction,
)


def _frame_source(frame: ContextFrame, entity: ContextEntity) -> ReferentSource:
    if entity.focused_referent is not None or frame.frame_id.startswith("query_"):
        return "query_result"
    return "context_frame"


def _frame_confidence(frame: ContextFrame, entity: ContextEntity, index: int) -> float:
    if entity.focused_referent is not None:
        return 0.95
    if len(frame.items) == 1:
        return 0.92
    if frame.frame_type in {ContextFrameType.RECEIPT, ContextFrameType.TRANSACTION_DETAIL}:
        return 0.92
    if frame.focus_index == index and frame.frame_type != ContextFrameType.BENEFICIARY_LIST:
        return 0.88
    return 0.72


def _referent_data_from_focused(entity: ContextEntity) -> dict[str, Any] | None:
    referent = entity.focused_referent
    if referent is None:
        return None
    raw = referent.model_dump(exclude_none=True)
    return safe_data(
        {
            "id": raw.get("entity_id") or entity.entity_id,
            "beneficiary_id": raw.get("entity_id") or entity.entity_id,
            "alias": raw.get("recipient_name") or raw.get("label") or entity.label,
            "account_name": raw.get("recipient_resolved_name") or raw.get("recipient_name"),
            "account_number": raw.get("recipient_account"),
            "bank_name": raw.get("recipient_bank_name"),
            "bank_code": raw.get("recipient_bank_code"),
            "recipient_name": raw.get("recipient_name") or raw.get("label") or entity.label,
            "recipient_resolved_name": raw.get("recipient_resolved_name"),
            "recipient_account": raw.get("recipient_account"),
            "recipient_bank_name": raw.get("recipient_bank_name"),
            "recipient_bank_code": raw.get("recipient_bank_code"),
            "beneficiary_type": "transfer",
        }
    )


def _beneficiary_data_from_entity(entity: ContextEntity) -> dict[str, Any]:
    data = entity.data
    account_number = first_text(data.get("account_number"), data.get("recipient_account"), data.get("account"))
    bank_name = first_text(data.get("bank_name"), data.get("recipient_bank_name"), data.get("bank"))
    bank_code = first_text(data.get("bank_code"), data.get("recipient_bank_code"))
    alias = first_text(data.get("alias"), data.get("recipient_name"), entity.label)
    account_name = first_text(data.get("account_name"), data.get("recipient_resolved_name"), alias)
    return safe_data(
        {
            "id": data.get("id") or data.get("beneficiary_id") or entity.entity_id,
            "beneficiary_id": data.get("beneficiary_id") or data.get("id") or entity.entity_id,
            "alias": alias,
            "account_name": account_name,
            "account_number": account_number,
            "bank_name": bank_name,
            "bank_code": bank_code,
            "recipient_name": alias,
            "recipient_resolved_name": account_name,
            "recipient_account": account_number,
            "recipient_bank_name": bank_name,
            "recipient_bank_code": bank_code,
            "beneficiary_type": data.get("beneficiary_type") or "transfer",
        }
    )


def remember_referents_from_frame(state: Any, frame: ContextFrame) -> None:
    """Seed canonical referents from a trusted visible context frame."""
    prune_referent_memory(state)
    for index, entity in enumerate(frame.items):
        source = _frame_source(frame, entity)
        confidence = _frame_confidence(frame, entity, index)
        label = entity.label
        if entity.focused_referent is not None:
            focused_data = _referent_data_from_focused(entity)
            if focused_data:
                remember_recipient_like(
                    state,
                    data=focused_data,
                    label=label,
                    entity_id=entity.entity_id,
                    source=source,
                    confidence=confidence,
                    created_at_ts=frame.created_at_ts,
                    ttl_seconds=frame.ttl_seconds,
                    as_beneficiary=True,
                )

        safe_entity_data = safe_data(entity.data)
        if entity.entity_type == EntityType.BENEFICIARY:
            beneficiary_data = _beneficiary_data_from_entity(entity)
            remember_recipient_like(
                state,
                data=beneficiary_data,
                label=label,
                entity_id=entity.entity_id,
                source=source,
                confidence=confidence,
                created_at_ts=frame.created_at_ts,
                ttl_seconds=frame.ttl_seconds,
                as_beneficiary=True,
            )
        elif entity.entity_type == EntityType.ACCOUNT:
            remember_source_account(
                state,
                data=safe_entity_data,
                label=label,
                source=source,
                created_at_ts=frame.created_at_ts,
                ttl_seconds=frame.ttl_seconds,
            )
        elif entity.entity_type == EntityType.DATA_PLAN:
            remember_data_plan(
                state,
                data=safe_entity_data,
                label=label,
                source=source,
                created_at_ts=frame.created_at_ts,
                ttl_seconds=frame.ttl_seconds,
            )
        elif entity.entity_type == EntityType.TRANSACTION:
            remember_transaction(
                state,
                data=safe_entity_data,
                label=label,
                entity_id=entity.entity_id,
                source=source,
                created_at_ts=frame.created_at_ts,
                ttl_seconds=frame.ttl_seconds,
            )
            remember_amount(
                state,
                amount=safe_entity_data.get("amount"),
                source=source,
                created_at_ts=frame.created_at_ts,
                ttl_seconds=frame.ttl_seconds,
            )
            remember_phone(
                state,
                data=safe_entity_data,
                label=label,
                source=source,
                created_at_ts=frame.created_at_ts,
                ttl_seconds=frame.ttl_seconds,
            )
            remember_source_account(
                state,
                data=safe_entity_data,
                label=label,
                source=source,
                created_at_ts=frame.created_at_ts,
                ttl_seconds=frame.ttl_seconds,
            )
            recipient_data = _beneficiary_data_from_entity(entity)
            if recipient_data.get("recipient_account") or recipient_data.get("recipient_name"):
                remember_recipient_like(
                    state,
                    data=recipient_data,
                    label=first_text(recipient_data.get("recipient_name"), label),
                    entity_id=entity.entity_id,
                    source=source,
                    confidence=confidence,
                    created_at_ts=frame.created_at_ts,
                    ttl_seconds=frame.ttl_seconds,
                    as_beneficiary=False,
                )
