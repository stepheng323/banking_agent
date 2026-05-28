"""Low-level referent memory writers."""

from __future__ import annotations

from typing import Any

from apps.chat.src.agent.orchestrator.context.referents.models import ReferentMemoryItem, ReferentSource
from apps.chat.src.agent.orchestrator.context.referents.store import remember_referent_item
from apps.chat.src.agent.orchestrator.context.referents.values import first_number, first_text, safe_data


def remember_recipient_like(
    state: Any,
    *,
    data: dict[str, Any],
    label: str | None,
    entity_id: str | None,
    source: ReferentSource,
    confidence: float,
    created_at_ts: int,
    ttl_seconds: int,
    as_beneficiary: bool,
    extra_data: dict[str, Any] | None = None,
) -> None:
    if not (first_text(data.get("recipient_account"), data.get("account_number")) or label):
        return
    referent_data = safe_data({**data, **(extra_data or {})})
    if as_beneficiary:
        remember_referent_item(
            state,
            ReferentMemoryItem(
                referent_type="beneficiary",
                source=source,
                label=label,
                entity_id=entity_id,
                data=referent_data,
                confidence=confidence,
                created_at_ts=created_at_ts,
                ttl_seconds=ttl_seconds,
            ),
        )
    remember_referent_item(
        state,
        ReferentMemoryItem(
            referent_type="recipient",
            source=source,
            label=label,
            entity_id=entity_id,
            data=referent_data,
            confidence=confidence,
            created_at_ts=created_at_ts,
            ttl_seconds=ttl_seconds,
        ),
    )


def remember_amount(
    state: Any,
    *,
    amount: Any,
    source: ReferentSource,
    created_at_ts: int,
    ttl_seconds: int,
    extra_data: dict[str, Any] | None = None,
) -> None:
    value = first_number(amount)
    if value is None:
        return
    remember_referent_item(
        state,
        ReferentMemoryItem(
            referent_type="amount",
            source=source,
            label=f"{value:g}",
            data=safe_data({"amount": value, **(extra_data or {})}),
            confidence=0.9,
            created_at_ts=created_at_ts,
            ttl_seconds=ttl_seconds,
        ),
    )


def remember_phone(
    state: Any,
    *,
    data: dict[str, Any],
    label: str | None,
    source: ReferentSource,
    created_at_ts: int,
    ttl_seconds: int,
    extra_data: dict[str, Any] | None = None,
) -> None:
    phone = first_text(
        data.get("phone"),
        data.get("recipient_phone"),
        data.get("target_phone"),
        data.get("phone_number"),
    )
    if not phone:
        return
    phone_data = safe_data(
        {
            "phone": phone,
            "recipient_phone": phone,
            "target_phone": phone,
            "recipient_name": data.get("recipient_name"),
            "network": data.get("network"),
            **(extra_data or {}),
        }
    )
    remember_referent_item(
        state,
        ReferentMemoryItem(
            referent_type="phone",
            source=source,
            label=label or phone,
            data=phone_data,
            confidence=0.9,
            created_at_ts=created_at_ts,
            ttl_seconds=ttl_seconds,
        ),
    )


def remember_source_account(
    state: Any,
    *,
    data: dict[str, Any],
    label: str | None,
    source: ReferentSource,
    created_at_ts: int,
    ttl_seconds: int,
    extra_data: dict[str, Any] | None = None,
) -> None:
    account_id = first_text(data.get("source_account_id"), data.get("account_id"), data.get("id"))
    account_number = first_text(data.get("source_account_number"), data.get("account_number"))
    bank_name = first_text(data.get("source_bank_name"), data.get("bank_name"))
    if not (account_id or account_number or bank_name):
        return
    account_data = safe_data(
        {
            "source_account_id": account_id,
            "account_id": account_id,
            "source_account_number": account_number,
            "account_number": account_number,
            "source_bank_name": bank_name,
            "bank_name": bank_name,
            "source_account_name": data.get("source_account_name") or data.get("account_name"),
            **(extra_data or {}),
        }
    )
    remember_referent_item(
        state,
        ReferentMemoryItem(
            referent_type="source_account",
            source=source,
            label=label or bank_name or account_number,
            entity_id=account_id,
            data=account_data,
            confidence=0.85,
            created_at_ts=created_at_ts,
            ttl_seconds=ttl_seconds,
        ),
    )


def remember_transaction(
    state: Any,
    *,
    data: dict[str, Any],
    label: str | None,
    entity_id: str | None,
    source: ReferentSource,
    created_at_ts: int,
    ttl_seconds: int,
    extra_data: dict[str, Any] | None = None,
) -> None:
    reference = first_text(data.get("transaction_id"), data.get("reference"), data.get("task_id"), entity_id)
    if not reference and not label:
        return
    tx_data = safe_data({**data, "transaction_id": reference, "reference": reference, **(extra_data or {})})
    remember_referent_item(
        state,
        ReferentMemoryItem(
            referent_type="transaction",
            source=source,
            label=label,
            entity_id=reference,
            data=tx_data,
            confidence=0.88,
            created_at_ts=created_at_ts,
            ttl_seconds=ttl_seconds,
        ),
    )


def remember_data_plan(
    state: Any,
    *,
    data: dict[str, Any],
    label: str | None,
    source: ReferentSource,
    created_at_ts: int,
    ttl_seconds: int,
) -> None:
    plan_code = first_text(data.get("plan_code"), data.get("item_code"))
    plan_name = first_text(data.get("plan_name"), data.get("name"), label)
    if not plan_code and not plan_name:
        return
    plan_data = safe_data(
        {
            "plan_code": plan_code,
            "item_code": plan_code,
            "plan_name": plan_name,
            "name": plan_name,
            "network": data.get("network"),
            "amount": data.get("amount"),
            "size_gb": data.get("size_gb"),
            "validity_days": data.get("validity_days"),
            "biller_code": data.get("biller_code"),
            "index": data.get("index"),
            "tags": data.get("tags"),
        }
    )
    remember_referent_item(
        state,
        ReferentMemoryItem(
            referent_type="data_plan",
            source=source,
            label=plan_name,
            entity_id=plan_code,
            data=plan_data,
            confidence=0.9,
            created_at_ts=created_at_ts,
            ttl_seconds=ttl_seconds,
        ),
    )
