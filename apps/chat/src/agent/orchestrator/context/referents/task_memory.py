"""Referent memory ingestion from completed and stashed tasks."""

from __future__ import annotations

import time
from typing import Any

from apps.chat.src.agent.orchestrator.context.referents.models import (
    DEFAULT_REFERENT_TTL_SECONDS,
    STASHED_REFERENT_TTL_SECONDS,
)
from apps.chat.src.agent.orchestrator.context.referents.values import first_text, safe_data
from apps.chat.src.agent.orchestrator.context.referents.writers import (
    remember_amount,
    remember_data_plan,
    remember_phone,
    remember_recipient_like,
    remember_source_account,
    remember_transaction,
)


def remember_referents_from_completed_task(state: Any, task: Any) -> None:
    """Seed canonical referents from a completed task payload."""
    task_type = str(getattr(task, "type", "") or "").strip()
    payload = getattr(task, "payload", None)
    if not isinstance(payload, dict) or task_type not in {"transfer", "airtime", "data"}:
        return
    created_at_ts = int(time.time())
    safe_payload = safe_data(payload)
    label = None
    if task_type == "transfer":
        label = first_text(
            payload.get("recipient_resolved_name"),
            payload.get("recipient_name"),
            payload.get("recipient_account"),
        )
        recipient_data = safe_data(
            {
                "id": payload.get("beneficiary_id"),
                "beneficiary_id": payload.get("beneficiary_id"),
                "alias": payload.get("recipient_name"),
                "account_name": payload.get("recipient_resolved_name") or payload.get("recipient_name"),
                "account_number": payload.get("recipient_account"),
                "bank_name": payload.get("recipient_bank_name"),
                "bank_code": payload.get("recipient_bank_code"),
                "recipient_name": payload.get("recipient_name"),
                "recipient_resolved_name": payload.get("recipient_resolved_name"),
                "recipient_account": payload.get("recipient_account"),
                "recipient_bank_name": payload.get("recipient_bank_name"),
                "recipient_bank_code": payload.get("recipient_bank_code"),
                "beneficiary_type": "transfer",
            }
        )
        remember_recipient_like(
            state,
            data=recipient_data,
            label=label,
            entity_id=first_text(payload.get("beneficiary_id"), payload.get("transaction_id")),
            source="completed_task",
            confidence=0.94,
            created_at_ts=created_at_ts,
            ttl_seconds=DEFAULT_REFERENT_TTL_SECONDS,
            as_beneficiary=bool(payload.get("beneficiary_id")),
        )
    else:
        phone = first_text(payload.get("recipient_phone"), payload.get("target_phone"), payload.get("phone_number"))
        remember_phone(
            state,
            data={**safe_payload, "phone": phone, "recipient_phone": phone, "target_phone": phone},
            label=phone,
            source="completed_task",
            created_at_ts=created_at_ts,
            ttl_seconds=DEFAULT_REFERENT_TTL_SECONDS,
        )
        if task_type == "data":
            remember_data_plan(
                state,
                data=safe_payload,
                label=first_text(payload.get("plan_name"), payload.get("plan_code")),
                source="completed_task",
                created_at_ts=created_at_ts,
                ttl_seconds=DEFAULT_REFERENT_TTL_SECONDS,
            )

    remember_amount(
        state,
        amount=payload.get("amount"),
        source="completed_task",
        created_at_ts=created_at_ts,
        ttl_seconds=DEFAULT_REFERENT_TTL_SECONDS,
    )
    remember_source_account(
        state,
        data=safe_payload,
        label=first_text(payload.get("source_bank_name"), payload.get("source_account_number")),
        source="completed_task",
        created_at_ts=created_at_ts,
        ttl_seconds=DEFAULT_REFERENT_TTL_SECONDS,
    )
    remember_transaction(
        state,
        data={**safe_payload, "task_type": task_type, "task_id": getattr(task, "id", None)},
        label=label or first_text(payload.get("recipient_phone"), payload.get("target_phone"), task_type),
        entity_id=first_text(payload.get("transaction_id"), payload.get("reference"), getattr(task, "id", None)),
        source="completed_task",
        created_at_ts=created_at_ts,
        ttl_seconds=DEFAULT_REFERENT_TTL_SECONDS,
    )


def _task_type(task: Any) -> str:
    if isinstance(task, dict):
        return str(task.get("type") or "").strip()
    return str(getattr(task, "type", "") or "").strip()


def _task_payload(task: Any) -> dict[str, Any]:
    payload = task.get("payload") if isinstance(task, dict) else getattr(task, "payload", None)
    return payload if isinstance(payload, dict) else {}


def _task_id(task: Any, fallback: str) -> str:
    raw_id = task.get("id") if isinstance(task, dict) else getattr(task, "id", None)
    return str(raw_id or fallback)


def remember_referents_from_stashed_session(state: Any, session: dict[str, Any]) -> None:
    """Seed safe referents from a stashed transaction session."""
    stash_id = first_text(session.get("stash_id"))
    tasks = session.get("tasks")
    if not stash_id or not isinstance(tasks, dict):
        return
    created_at_ts = int(session.get("stashed_at_ts") or time.time())
    extra_data = {"stash_id": stash_id}
    for fallback_id, task in tasks.items():
        task_type = _task_type(task)
        if task_type not in {"transfer", "airtime", "data"}:
            continue
        payload = _task_payload(task)
        if not payload:
            continue
        safe_payload = safe_data(payload)
        task_identifier = _task_id(task, str(fallback_id))
        label = None
        if task_type == "transfer":
            label = first_text(
                payload.get("recipient_resolved_name"),
                payload.get("recipient_name"),
                payload.get("recipient_account"),
            )
            recipient_data = safe_data(
                {
                    "id": payload.get("beneficiary_id"),
                    "beneficiary_id": payload.get("beneficiary_id"),
                    "alias": payload.get("recipient_name"),
                    "account_name": payload.get("recipient_resolved_name") or payload.get("recipient_name"),
                    "account_number": payload.get("recipient_account"),
                    "bank_name": payload.get("recipient_bank_name"),
                    "bank_code": payload.get("recipient_bank_code"),
                    "recipient_name": payload.get("recipient_name"),
                    "recipient_resolved_name": payload.get("recipient_resolved_name"),
                    "recipient_account": payload.get("recipient_account"),
                    "recipient_bank_name": payload.get("recipient_bank_name"),
                    "recipient_bank_code": payload.get("recipient_bank_code"),
                    "beneficiary_type": "transfer",
                }
            )
            remember_recipient_like(
                state,
                data=recipient_data,
                label=label,
                entity_id=first_text(payload.get("beneficiary_id"), payload.get("transaction_id"), task_identifier),
                source="stashed_session",
                confidence=0.93,
                created_at_ts=created_at_ts,
                ttl_seconds=STASHED_REFERENT_TTL_SECONDS,
                as_beneficiary=bool(payload.get("beneficiary_id")),
                extra_data=extra_data,
            )
        else:
            phone = first_text(
                payload.get("recipient_phone"),
                payload.get("target_phone"),
                payload.get("phone_number"),
            )
            remember_phone(
                state,
                data={**safe_payload, "phone": phone, "recipient_phone": phone, "target_phone": phone},
                label=phone,
                source="stashed_session",
                created_at_ts=created_at_ts,
                ttl_seconds=STASHED_REFERENT_TTL_SECONDS,
                extra_data=extra_data,
            )

        remember_amount(
            state,
            amount=payload.get("amount"),
            source="stashed_session",
            created_at_ts=created_at_ts,
            ttl_seconds=STASHED_REFERENT_TTL_SECONDS,
            extra_data=extra_data,
        )
        remember_source_account(
            state,
            data=safe_payload,
            label=first_text(payload.get("source_bank_name"), payload.get("source_account_number")),
            source="stashed_session",
            created_at_ts=created_at_ts,
            ttl_seconds=STASHED_REFERENT_TTL_SECONDS,
            extra_data=extra_data,
        )
        remember_transaction(
            state,
            data={**safe_payload, "task_type": task_type, "task_id": task_identifier},
            label=label or first_text(payload.get("recipient_phone"), payload.get("target_phone"), task_type),
            entity_id=first_text(payload.get("transaction_id"), payload.get("reference"), task_identifier),
            source="stashed_session",
            created_at_ts=created_at_ts,
            ttl_seconds=STASHED_REFERENT_TTL_SECONDS,
            extra_data=extra_data,
        )
