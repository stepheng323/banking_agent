"""Completed-task output handling for orchestrator finalization."""

from __future__ import annotations

import uuid
from typing import Any

from langchain_core.runnables import RunnableConfig

from apps.chat.src.agent.orchestrator.models.domain import TaskSpec
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.utils.actionable_payload import build_actionable_payload_for_tasks
from apps.chat.src.agent.orchestrator.workflows.lifecycle.completed_transaction_frames import (
    ASYNC_RECEIPT_STATUSES,
    TRANSACTION_TASK_TYPES,
    completed_transaction_reference,
    first_non_empty_text,
    is_async_transfer_task,
    is_grouped_or_batch_task,
    receipt_status,
)
from banking.beneficiaries.services.post_transaction_beneficiary import (
    append_beneficiary_suggestion,
    suggest_mobile_beneficiary,
    suggest_transfer_beneficiary,
)
from banking.presentation.formatters.multi_action_summary import format_multi_action_summary
from banking.presentation.i18n.locale import LocaleManager
from banking.presentation.i18n.renderer import render_message, render_text
from banking.receipts.choice import build_receipt_choice_intent
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def append_transfer_processing_message(task: TaskSpec, outbox: list[dict[str, Any]], locale: str) -> None:
    display_name = (
        task.payload.get("recipient_resolved_name")
        or task.payload.get("recipient_name")
        or render_message("orchestrator.finalize.recipient_fallback", locale)
    )
    amount = task.payload.get("amount", "")
    outbox.append(
        {
            "type": "say",
            "text": render_message(
                "orchestrator.finalize.transfer_processing",
                locale,
                {"amount": f"{amount:,.2f}", "display_name": display_name},
            ),
        }
    )


async def enqueue_finalize_transfer_receipt(
    *,
    task: TaskSpec,
    state: OrchestratorState,
    locale: str,
) -> dict[str, Any] | None:
    transaction_reference = task.payload.get("transaction_id")
    if not isinstance(transaction_reference, str) or not transaction_reference.strip():
        return None

    recipient_account = task.payload.get("recipient_account")
    payload: dict[str, Any] = {
        "phone_number": state.phone_number,
        "channel": state.channel,
        "channel_identity": state.channel_identity,
        "transfer_data": {
            "amount": task.payload.get("amount"),
            "source": {
                "name": task.payload.get("source_bank_name"),
                "account_name": task.payload.get("source_account_name"),
                "account_number": task.payload.get("source_account_number"),
            },
            "recipient": {
                "name": task.payload.get("recipient_resolved_name") or task.payload.get("recipient_name"),
                "account_number": recipient_account,
                "bank_name": task.payload.get("recipient_bank_name"),
            },
            "narration": task.payload.get("narration"),
            "channel": state.channel,
            "session_id": task.payload.get("idempotency_key") or transaction_reference,
        },
        "transaction_reference": transaction_reference,
        "signal_key": f"receipt:{uuid.uuid4()}",
    }

    return build_receipt_choice_intent(payload, locale).to_dict()


async def build_single_task_beneficiary_suggestion(
    *,
    task: TaskSpec,
    state: OrchestratorState,
    config: RunnableConfig,
    locale: str,
) -> str | None:
    if is_grouped_or_batch_task(task):
        return None

    suggestion_service = config.get("configurable", {}).get("beneficiary_suggestion_service")
    if suggestion_service is None:
        return None

    payload = task.payload
    receipt = payload.get("receipt") if isinstance(payload.get("receipt"), dict) else {}
    transaction_reference = completed_transaction_reference(task, payload, receipt)

    if task.type == "transfer":
        return await suggest_transfer_beneficiary(
            suggestion_service,
            phone_number=state.phone_number,
            channel=state.channel,
            locale=locale,
            transaction_id=transaction_reference,
            account_number=payload.get("recipient_account"),
            bank_code=payload.get("recipient_bank_code"),
            bank_name=payload.get("recipient_bank_name"),
            recipient_name=payload.get("recipient_resolved_name") or payload.get("recipient_name"),
            original_alias=payload.get("recipient_name"),
            bank_code_provider=payload.get("recipient_bank_code_provider"),
            resolution_provider=payload.get("recipient_resolution_provider"),
            is_self=payload.get("is_self") or payload.get("is_own_account"),
        )

    if task.type == "airtime":
        return await suggest_mobile_beneficiary(
            suggestion_service,
            phone_number=state.phone_number,
            channel=state.channel,
            locale=locale,
            transaction_id=transaction_reference,
            beneficiary_type="airtime",
            recipient_phone=first_non_empty_text(
                payload.get("recipient_phone"),
                payload.get("phone_number"),
                payload.get("recipientPhone"),
                payload.get("phone"),
                receipt.get("phone"),
            ),
            network=first_non_empty_text(payload.get("network"), receipt.get("network")),
            recipient_name=first_non_empty_text(payload.get("recipient_name"), payload.get("name")),
        )

    if task.type == "data":
        return await suggest_mobile_beneficiary(
            suggestion_service,
            phone_number=state.phone_number,
            channel=state.channel,
            locale=locale,
            transaction_id=transaction_reference,
            beneficiary_type="data",
            recipient_phone=first_non_empty_text(
                payload.get("target_phone"),
                payload.get("recipient_phone"),
                payload.get("phone_number"),
                payload.get("phone"),
                receipt.get("phone"),
            ),
            network=first_non_empty_text(payload.get("network"), receipt.get("network")),
            recipient_name=first_non_empty_text(payload.get("recipient_name"), payload.get("name")),
        )

    return None


async def handle_completed_tasks(
    completed_tasks: list[TaskSpec],
    state: OrchestratorState,
    config: RunnableConfig,
    outbox: list[dict[str, Any]],
) -> bool:
    """Handle completed tasks and generate receipts or summaries."""
    locale = LocaleManager.normalize((state.loaded_context or {}).get("language")).value
    visible_tasks = [task for task in completed_tasks if not task.payload.get("skip_finalize_summary")]
    if not visible_tasks:
        return False

    logger.info("handling_completed_tasks", count=len(completed_tasks), tasks=[task.type for task in completed_tasks])
    for task in visible_tasks:
        logger.info(
            "completed_task_detail",
            type=task.type,
            has_receipt="receipt" in task.payload,
            payload_keys=list(task.payload.keys()),
        )
    transaction_visible_tasks = [task for task in visible_tasks if task.type in TRANSACTION_TASK_TYPES]
    async_transaction_tasks = [
        task for task in transaction_visible_tasks if receipt_status(task) in ASYNC_RECEIPT_STATUSES
    ]
    async_transfer_tasks = [task for task in transaction_visible_tasks if is_async_transfer_task(task)]
    is_single_transfer = (
        len(visible_tasks) == 1
        and visible_tasks[0].type == "transfer"
        and not visible_tasks[0].payload.get("is_batch", False)
        and len(visible_tasks[0].payload.get("recipients", [])) <= 1
    )

    read_only_task_types = {"account", "query", "faq", "support", "beneficiary", "schedule"}
    all_read_only = all(task.type in read_only_task_types for task in visible_tasks)

    is_async_transaction = len(visible_tasks) == 1 and visible_tasks[0].type in ("airtime", "data")

    if is_single_transfer:
        task = visible_tasks[0]
        if receipt_status(task) in ASYNC_RECEIPT_STATUSES:
            logger.info(
                "finalize_single_transfer_status_deferred_to_executor",
                task_id=task.id,
                receipt_status=receipt_status(task),
            )
            return True

        receipt_offer = await enqueue_finalize_transfer_receipt(
            task=task,
            state=state,
            locale=locale,
        )
        if receipt_offer:
            outbox.append(receipt_offer)
        if suggestion := await build_single_task_beneficiary_suggestion(
            task=task,
            state=state,
            config=config,
            locale=locale,
        ):
            outbox.append({"type": "say", "text": suggestion})

    elif len(async_transaction_tasks) > 1:
        logger.info(
            "finalize_async_batch_processing",
            count=len(async_transaction_tasks),
            task_ids=[task.id for task in async_transaction_tasks],
        )
        outbox.append({"type": "say", "text": render_text("Your transactions are being processed.", locale)})

    elif async_transfer_tasks:
        logger.info(
            "finalize_async_transfer_processing",
            count=len(async_transfer_tasks),
            task_ids=[task.id for task in async_transfer_tasks],
        )
        for task in async_transfer_tasks:
            append_transfer_processing_message(task, outbox, locale)

    elif is_async_transaction:
        task = visible_tasks[0]
        receipt = task.payload.get("receipt", {})
        status = receipt.get("status", "").title()
        message = receipt.get("message", render_message("orchestrator.finalize.transaction_completed", locale))

        logger.info("generating_async_receipt", task_type=task.type, status=status, message=message)

        text = render_message(
            "orchestrator.finalize.async_status_message",
            locale,
            {"status": status, "message": message},
        )
        if str(receipt.get("status") or "").lower() not in ASYNC_RECEIPT_STATUSES:
            if suggestion := await build_single_task_beneficiary_suggestion(
                task=task,
                state=state,
                config=config,
                locale=locale,
            ):
                text = append_beneficiary_suggestion(text, suggestion)

        outbox.append({"type": "say", "text": text})

    elif all_read_only:
        pass
    else:
        summary_source = transaction_visible_tasks or visible_tasks
        summary_text = format_multi_action_summary(summary_source, locale=locale)
        summary_outbox: dict[str, Any] = {"type": "say", "text": summary_text}
        if actionable_payload := build_actionable_payload_for_tasks(summary_source):
            summary_outbox["actionable_payload"] = actionable_payload
        outbox.append(summary_outbox)

    return False


__all__ = ["handle_completed_tasks"]
