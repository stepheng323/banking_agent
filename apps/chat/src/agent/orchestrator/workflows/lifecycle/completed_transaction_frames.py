"""Completed-transaction context-frame helpers for finalization."""

import time
import uuid
from typing import Any

from apps.chat.src.agent.orchestrator.context.models import ContextEntity, ContextFrame, ContextFrameType, EntityType
from apps.chat.src.agent.orchestrator.models.domain import TaskSpec
from banking.presentation.formatters.transaction_copy_context import format_amount_compact
from banking.transactions.query.contracts import SelectionPayload

TRANSACTION_TASK_TYPES = {"transfer", "airtime", "data"}
ASYNC_RECEIPT_STATUSES = {"queued", "processing", "pending"}
COMPLETED_TRANSACTION_FRAME_TTL_SECONDS = 900


def receipt_status(task: TaskSpec) -> str:
    receipt = task.payload.get("receipt")
    if not isinstance(receipt, dict):
        return ""
    raw_status = receipt.get("status")
    if not isinstance(raw_status, str):
        return ""
    return raw_status.strip().lower()


def is_async_transfer_task(task: TaskSpec) -> bool:
    return task.type == "transfer" and receipt_status(task) in ASYNC_RECEIPT_STATUSES


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def is_grouped_or_batch_task(task: TaskSpec) -> bool:
    payload = task.payload
    if payload.get("is_batch") is True:
        return True
    scheduled_meta = payload.get("scheduled_meta")
    if isinstance(scheduled_meta, dict) and scheduled_meta.get("run_source") == "scheduled":
        return True
    recipients = payload.get("recipients")
    if isinstance(recipients, list) and len(recipients) > 1:
        return True
    async_group = payload.get("async_group")
    if isinstance(async_group, dict) and _int_value(async_group.get("async_group_size")) > 1:
        return True
    return _int_value(payload.get("async_group_size")) > 1


def _string(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def first_non_empty_text(*values: Any) -> str:
    for value in values:
        text = _string(value)
        if text:
            return text
    return ""


def _safe_completed_transaction_data(values: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in values.items():
        key_text = str(key)
        lowered = key_text.lower()
        if any(fragment in lowered for fragment in ("pin", "otp", "password", "token", "secret", "auth")):
            continue
        if value is None or value == "":
            continue
        if isinstance(value, (str, int, float, bool)):
            safe[key_text] = value
    return safe


def completed_transaction_reference(task: TaskSpec, payload: dict[str, Any], receipt: dict[str, Any]) -> str:
    return first_non_empty_text(
        payload.get("transaction_id"),
        payload.get("transaction_reference"),
        payload.get("reference"),
        receipt.get("transaction_id"),
        receipt.get("transaction_reference"),
        receipt.get("reference"),
        receipt.get("id"),
        payload.get("idempotency_key"),
        task.id,
    )


def _completed_transfer_recipient_payload(task: TaskSpec, recipient: dict[str, Any], index: int) -> dict[str, Any]:
    payload = task.payload
    amount = recipient.get("amount", payload.get("amount"))
    recipient_name = first_non_empty_text(
        recipient.get("recipient_name"),
        recipient.get("alias"),
        payload.get("recipient_name"),
    )
    resolved_name = first_non_empty_text(
        recipient.get("recipient_resolved_name"),
        recipient.get("name"),
        payload.get("recipient_resolved_name"),
        recipient_name,
    )
    recipient_bank = first_non_empty_text(
        recipient.get("recipient_bank_name"),
        recipient.get("bank_name"),
        payload.get("recipient_bank_name"),
    )
    recipient_bank_code = first_non_empty_text(
        recipient.get("recipient_bank_code"),
        recipient.get("bank_code"),
        payload.get("recipient_bank_code"),
    )
    recipient_account = first_non_empty_text(
        recipient.get("recipient_account"),
        recipient.get("account"),
        payload.get("recipient_account"),
    )
    return {
        "task_id": f"{task.id}:{index}",
        "task_type": "transfer",
        "type": "transfer",
        "transaction_type": "transfer",
        "amount": amount,
        "status": receipt_status(task) or payload.get("final_status") or "success",
        "recipient_name": recipient_name,
        "recipient_resolved_name": resolved_name,
        "counterparty": resolved_name or recipient_name,
        "recipient_bank_name": recipient_bank,
        "recipient_bank_code": recipient_bank_code,
        "bank_name": recipient_bank,
        "bank_code": recipient_bank_code,
        "bank": recipient_bank,
        "recipient_account": recipient_account,
        "account": recipient_account,
        "source_account_id": payload.get("source_account_id"),
        "source_bank_name": payload.get("source_bank_name"),
        "source_account_number": payload.get("source_account_number"),
        "source_account_last4": _string(payload.get("source_account_number"))[-4:],
        "narration": recipient.get("narration") or payload.get("narration"),
        "reference": payload.get("transaction_id") or payload.get("idempotency_key"),
        "date": payload.get("date"),
    }


def _completed_transaction_entity_payloads(task: TaskSpec) -> list[dict[str, Any]]:
    payload = task.payload
    raw_receipt = payload.get("receipt")
    receipt: dict[str, Any] = raw_receipt if isinstance(raw_receipt, dict) else {}
    if task.type == "transfer":
        recipients = payload.get("recipients")
        if isinstance(recipients, list) and recipients:
            return [
                _completed_transfer_recipient_payload(task, recipient, idx)
                for idx, recipient in enumerate(recipients, 1)
                if isinstance(recipient, dict)
            ]
        return [_completed_transfer_recipient_payload(task, {}, 1)]

    if task.type == "airtime":
        phone = first_non_empty_text(
            payload.get("recipient_phone"),
            payload.get("phone_number"),
            payload.get("recipientPhone"),
            payload.get("phone"),
            receipt.get("phone"),
        )
        return [
            {
                "task_id": task.id,
                "task_type": "airtime",
                "type": "airtime",
                "transaction_type": "airtime",
                "amount": payload.get("amount"),
                "status": receipt_status(task) or payload.get("final_status") or "success",
                "phone": phone,
                "recipient_phone": phone,
                "counterparty": phone,
                "network": first_non_empty_text(payload.get("network"), receipt.get("network")),
                "source_account_id": payload.get("source_account_id"),
                "source_bank_name": payload.get("source_bank_name"),
                "source_account_number": payload.get("source_account_number"),
                "reference": completed_transaction_reference(task, payload, receipt),
                "date": first_non_empty_text(payload.get("date"), receipt.get("date")),
                "description": first_non_empty_text(receipt.get("message"), receipt.get("description")),
            }
        ]

    if task.type == "data":
        phone = first_non_empty_text(payload.get("target_phone"), payload.get("phone_number"), payload.get("phone"))
        return [
            {
                "task_id": task.id,
                "task_type": "data",
                "type": "data",
                "transaction_type": "data",
                "amount": payload.get("amount"),
                "status": receipt_status(task) or payload.get("final_status") or "success",
                "phone": phone,
                "recipient_phone": phone,
                "counterparty": phone,
                "network": payload.get("network"),
                "plan_name": payload.get("plan_name"),
                "source_account_id": payload.get("source_account_id"),
                "source_bank_name": payload.get("source_bank_name"),
                "source_account_number": payload.get("source_account_number"),
                "reference": completed_transaction_reference(task, payload, receipt),
                "date": first_non_empty_text(payload.get("date"), receipt.get("date")),
                "description": first_non_empty_text(receipt.get("message"), receipt.get("description")),
            }
        ]

    return []


def _completed_transaction_label(data: dict[str, Any]) -> str:
    task_type = _string(data.get("task_type"))
    amount = format_amount_compact(data.get("amount"))
    if task_type == "transfer":
        recipient = first_non_empty_text(data.get("recipient_resolved_name"), data.get("recipient_name"), "recipient")
        return f"{amount} transfer to {recipient}"
    if task_type == "airtime":
        phone = first_non_empty_text(data.get("phone"), data.get("recipient_phone"), "phone number")
        return f"{amount} airtime for {phone}"
    if task_type == "data":
        phone = first_non_empty_text(data.get("phone"), data.get("recipient_phone"), "phone number")
        plan = _string(data.get("plan_name"))
        return f"{amount} data for {phone}" if not plan else f"{plan} data for {phone}"
    return f"{amount} transaction"


def _selection_payload_for_completed_transaction(data: dict[str, Any]) -> SelectionPayload:
    label = _completed_transaction_label(data)
    task_type = _string(data.get("task_type")) or "transaction"
    handoff_payload = None
    if task_type == "transfer":
        handoff_payload = {
            "recipient_name": data.get("recipient_name"),
            "recipient_resolved_name": data.get("recipient_resolved_name"),
            "recipient_account": data.get("recipient_account"),
            "recipient_bank_name": data.get("recipient_bank_name"),
            "recipient_bank_code": data.get("recipient_bank_code"),
            "amount": data.get("amount"),
            "narration": data.get("narration"),
            "source_account_id": data.get("source_account_id"),
            "source_bank_name": data.get("source_bank_name"),
            "source_account_number": data.get("source_account_number"),
        }
    return SelectionPayload(
        selection_kind="transaction",
        entity_type=task_type,
        entity_id=_string(data.get("reference") or data.get("task_id")) or None,
        label=label,
        fact_capabilities=["date", "amount", "bank", "counterparty"],
        handoff_payload=handoff_payload,
    )


def build_completed_transaction_frame(
    *,
    visible_tasks: list[TaskSpec],
    source_message_id: str | None,
) -> ContextFrame | None:
    transaction_tasks = [task for task in visible_tasks if task.type in TRANSACTION_TASK_TYPES]
    if not transaction_tasks:
        return None

    entities: list[ContextEntity] = []
    has_receipt = False
    for task in transaction_tasks:
        has_receipt = has_receipt or isinstance(task.payload.get("receipt"), dict)
        for raw_data in _completed_transaction_entity_payloads(task):
            data = _safe_completed_transaction_data(raw_data)
            label = _completed_transaction_label(data)
            selection_payload = _selection_payload_for_completed_transaction(data)
            entities.append(
                ContextEntity(
                    entity_type=EntityType.TRANSACTION,
                    entity_id=selection_payload.entity_id or _string(data.get("task_id")) or None,
                    label=label,
                    data=data,
                    selection_payload=selection_payload,
                )
            )

    if not entities:
        return None

    frame_type = ContextFrameType.TRANSACTION_LIST
    if len(entities) == 1:
        frame_type = ContextFrameType.RECEIPT if has_receipt else ContextFrameType.TRANSACTION_DETAIL

    return ContextFrame(
        frame_id=f"completed_transaction_{int(time.time())}_{uuid.uuid4().hex[:8]}",
        frame_type=frame_type,
        items=entities,
        focus_index=0,
        source_message_id=source_message_id,
        created_at_ts=int(time.time()),
        ttl_seconds=COMPLETED_TRANSACTION_FRAME_TTL_SECONDS,
    )
