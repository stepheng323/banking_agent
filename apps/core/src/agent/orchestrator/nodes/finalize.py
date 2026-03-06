from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import redis.asyncio as redis
from langchain_core.runnables import RunnableConfig

if TYPE_CHECKING:
    from apps.core.src.agent.graphs.__shared__.beneficiary.suggestion_service import BeneficiarySuggestionService

from apps.core.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from shared.formatters.transaction_summary import format_multi_action_summary
from shared.i18n import LocaleManager, render_cancelled_prompt, render_generic_capability_blocked, render_message
from shared.queue.adapter import QueuePublisher
from shared.queue.models import ReceiptJobPayload, ReceiptTransferData
from shared.utils.logging import get_logger

logger = get_logger(__name__)
TRANSACTION_TASK_TYPES = {"transfer", "airtime", "data"}


async def finalize(state: OrchestratorState, config: RunnableConfig) -> dict[str, Any]:
    """Final Step. Generate response and queue receipts."""
    outbox = list(state.outbox)
    locale = LocaleManager.normalize((state.loaded_context or {}).get("language")).value

    configurable = cast(dict[str, Any], config.get("configurable", {}))
    beneficiary_service: BeneficiarySuggestionService | None = configurable.get("beneficiary_suggestion_service")
    redis_client: redis.Redis | None = configurable.get("redis_client")
    publisher: QueuePublisher | None = configurable.get("publisher")

    completed_tasks = [task for task in state.tasks.values() if task.stage == TaskStage.COMPLETED]
    failed_tasks = [task for task in state.tasks.values() if task.stage == TaskStage.FAILED]
    cancelled_tasks = [task for task in state.tasks.values() if task.stage == TaskStage.CANCELLED]

    if completed_tasks:
        await _handle_completed_tasks(
            completed_tasks=completed_tasks,
            state=state,
            publisher=publisher,
            redis_client=redis_client,
            beneficiary_service=beneficiary_service,
            outbox=outbox,
        )

    for task in failed_tasks:
        if task.payload.get("capability_blocked"):
            message = task.payload.get("error") or render_generic_capability_blocked(locale)
            outbox.append({"type": "say", "text": message})
        elif task.payload.get("is_pending_mandate"):
            # Pending mandate messages are contextual and include clear instructions — no "Failed:" prefix
            error_text = task.payload.get("error") or render_message("orchestrator.finalize.failed_unknown", locale)
            outbox.append({"type": "say", "text": error_text})
        else:
            error_text = task.payload.get("error") or render_message("orchestrator.finalize.failed_unknown", locale)
            outbox.append(
                {
                    "type": "say",
                    "text": render_message("orchestrator.finalize.failed_prefix", locale, {"error": error_text}),
                }
            )

    if cancelled_tasks:
        outbox.append({"type": "say", "text": render_cancelled_prompt(locale)})

    # Check for stashed sessions and prompt
    context_updates = {}
    has_completed_non_transaction = any(task.type not in TRANSACTION_TASK_TYPES for task in completed_tasks)
    if state.stashed_sessions and has_completed_non_transaction:
        last_session = state.stashed_sessions[-1]
        intent = last_session.get("intent", render_message("orchestrator.session.default_intent", locale))
        resume_prompt = render_message("orchestrator.finalize.resume_prompt", locale, {"intent": intent})

        # Add prompt to outbox
        if outbox and outbox[-1].get("type") == "say":
            outbox[-1]["text"] += f"\n\n{resume_prompt}"
        else:
            outbox.append({"type": "say", "text": resume_prompt})

        # Add Context Frame to signal active prompt
        import time
        import uuid

        from apps.core.src.agent.orchestrator.context.models import (
            ContextEntity,
            ContextFrame,
            ContextFrameType,
            EntityType,
        )

        frame = ContextFrame(
            frame_id=str(uuid.uuid4()),
            frame_type=ContextFrameType.GENERIC,
            items=[
                ContextEntity(
                    entity_id="resumption_prompt",
                    label=f"Resume {intent}",
                    entity_type=EntityType.GENERIC,
                    data={"intent": intent, "resume_prompt": True},
                )
            ],
            created_at_ts=int(time.time()),
            ttl_seconds=300,
        )
        current_frames = list(state.context_frames)
        current_frames.append(frame)
        context_updates["context_frames"] = current_frames

    return {
        "outbox": outbox,
        "tasks": {},  # Wipe tasks so the next turn is fresh
        "waves": [],  # Clear waves so next turn triggers Planner
        "current_wave_index": 0,
        "pin_verified": False,  # Security: Reset PIN verification status
        "last_callback": None,  # Security: Clear stale callback data
        **context_updates,
    }


async def _handle_completed_tasks(
    completed_tasks: list[TaskSpec],
    state: OrchestratorState,
    publisher: QueuePublisher | None,
    redis_client: redis.Redis | None,
    beneficiary_service: BeneficiarySuggestionService | None,
    outbox: list[dict[str, Any]],
) -> None:
    """Handle completed tasks and generate receipts or summaries."""
    locale = LocaleManager.normalize((state.loaded_context or {}).get("language")).value
    visible_tasks = [task for task in completed_tasks if not task.payload.get("skip_finalize_summary")]
    if not visible_tasks:
        return

    logger.info("handling_completed_tasks", count=len(completed_tasks), tasks=[t.type for t in completed_tasks])
    for t in visible_tasks:
        logger.info(
            "completed_task_detail",
            type=t.type,
            has_receipt="receipt" in t.payload,
            payload_keys=list(t.payload.keys()),
        )
    is_single_transfer = (
        len(visible_tasks) == 1
        and visible_tasks[0].type == "transfer"
        and not visible_tasks[0].payload.get("is_batch", False)
        and len(visible_tasks[0].payload.get("recipients", [])) <= 1
    )

    read_only_task_types = {"account", "query", "faq", "support", "beneficiary"}
    all_read_only = all(task.type in read_only_task_types for task in visible_tasks)

    # Check for single async transaction (Airtime/Data)
    is_async_transaction = len(visible_tasks) == 1 and visible_tasks[0].type in ("airtime", "data")

    if is_single_transfer:
        task = visible_tasks[0]
        beneficiary_suggestion_message: str | None = None
        if beneficiary_service:
            beneficiary_suggestion_message = await _build_beneficiary_suggestion(
                task=task,
                beneficiary_service=beneficiary_service,
                phone_number=state.phone_number,
                locale=locale,
            )

        await _queue_single_transfer_receipt(
            task=task,
            state=state,
            publisher=publisher,
            redis_client=redis_client,
            beneficiary_suggestion_message=beneficiary_suggestion_message,
        )

        # Immediate success feedback (receipt follows asynchronously)
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

    elif is_async_transaction:
        task = visible_tasks[0]
        receipt = task.payload.get("receipt", {})
        status = receipt.get("status", "").title()
        message = receipt.get("message", render_message("orchestrator.finalize.transaction_completed", locale))

        logger.info("generating_async_receipt", task_type=task.type, status=status, message=message)

        # Simple text confirmation for async tasks
        outbox.append(
            {
                "type": "say",
                "text": render_message(
                    "orchestrator.finalize.async_status_message",
                    locale,
                    {"status": status, "message": message},
                ),
            }
        )

    elif all_read_only:
        pass
    else:
        summary_text = format_multi_action_summary(visible_tasks, locale=locale)
        outbox.append({"type": "say", "text": summary_text})


async def _queue_single_transfer_receipt(
    task: TaskSpec,
    state: OrchestratorState,
    publisher: QueuePublisher | None,
    redis_client: redis.Redis | None,
    beneficiary_suggestion_message: str | None = None,
) -> None:
    """Queue receipt for a single transfer."""
    receipt_data = task.payload.get("receipt")
    if not receipt_data or not (publisher or redis_client):
        return

    logger.info("queuing_single_transfer_receipt", task_id=task.id)
    import uuid

    signal_key = f"receipt:signal:{uuid.uuid4()}"

    locale = LocaleManager.normalize((state.loaded_context or {}).get("language")).value
    transfer_data: ReceiptTransferData = {
        "amount": task.payload.get("amount"),
        "source": {
            "name": cast(str | None, task.payload.get("source_bank_name")),
            "account_name": cast(str | None, task.payload.get("source_account_name")),
        },
        "recipient": {
            "name": cast(str | None, task.payload.get("recipient_resolved_name") or task.payload.get("recipient_name")),
            "account_number": cast(str | None, task.payload.get("recipient_account")),
            "bank_name": cast(str | None, task.payload.get("recipient_bank_name")),
        },
        "narration": cast(str | None, task.payload.get("narration")),
    }
    job_payload: ReceiptJobPayload = {
        "phone_number": state.phone_number,
        "channel": state.channel,
        "channel_identity": state.channel_identity,
        "transfer_data": transfer_data,
        "transaction_reference": cast(
            str | None,
            task.payload.get("transaction_id")
            or task.payload.get("idempotency_key")
            or render_message("orchestrator.finalize.na", locale),
        ),
        "signal_key": signal_key,
    }
    if beneficiary_suggestion_message:
        job_payload["beneficiary_suggestion_message"] = beneficiary_suggestion_message

    if publisher:
        await publisher.publish("receipt.process", cast(dict[str, Any], job_payload))
    else:
        logger.warning("publisher_not_available", message="Cannot queue receipt, QueuePublisher is None")


async def _build_beneficiary_suggestion(
    task: TaskSpec,
    beneficiary_service: BeneficiarySuggestionService,
    phone_number: str,
    locale: str = "en",
) -> str | None:
    """Build beneficiary suggestion text for deferred delivery after receipt."""
    suggestion_msg = await beneficiary_service.check_and_suggest_beneficiary(
        phone_number=phone_number,
        beneficiary_type="transfer",
        recipient_data={
            "account_number": task.payload.get("recipient_account"),
            "bank_code": task.payload.get("recipient_bank_code"),
            "bank_name": task.payload.get("recipient_bank_name"),
            "name": task.payload.get("recipient_name"),
            "is_self": False,
        },
        transaction_id=task.payload.get("transaction_id") or task.payload.get("idempotency_key"),
        send_message=False,
        locale=locale,
    )
    return cast(str | None, suggestion_msg)
