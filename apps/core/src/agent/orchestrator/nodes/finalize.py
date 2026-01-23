from typing import TYPE_CHECKING

import redis.asyncio as redis
from langchain_core.runnables import RunnableConfig

if TYPE_CHECKING:
    from apps.core.src.agent.graphs.__shared__.beneficiary.suggestion_service import BeneficiarySuggestionService

from apps.core.src.agent.orchestrator.models.domain import TaskStage
from apps.core.src.agent.orchestrator.state import OrchestratorState
from shared.formatters import format_multi_action_summary
from shared.queue.redis_queue import RedisQueue
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def finalize(state: OrchestratorState, config: RunnableConfig) -> dict:
    """Final Step. Generate response and queue receipts."""
    outbox = list(state.outbox)

    beneficiary_service: BeneficiarySuggestionService | None = config["configurable"].get(
        "beneficiary_suggestion_service"
    )
    redis_client: redis.Redis | None = config["configurable"].get("redis_client")
    queue: RedisQueue | None = config["configurable"].get("queue")

    completed_tasks = [task for task in state.tasks.values() if task.stage == TaskStage.COMPLETED]
    failed_tasks = [task for task in state.tasks.values() if task.stage == TaskStage.FAILED]
    cancelled_tasks = [task for task in state.tasks.values() if task.stage == TaskStage.CANCELLED]

    if completed_tasks:
        await _handle_completed_tasks(
            completed_tasks=completed_tasks,
            state=state,
            queue=queue,
            redis_client=redis_client,
            beneficiary_service=beneficiary_service,
            outbox=outbox,
        )

    for task in failed_tasks:
        outbox.append({"type": "say", "text": f"Failed: {task.payload.get('error')}"})

    for _ in cancelled_tasks:
        outbox.append({"type": "say", "text": "Transaction cancelled, how else can I help you today?"})

    return {
        "outbox": outbox,
        "tasks": {},  # Wipe tasks so the next turn is fresh
        "waves": [],  # Clear waves so next turn triggers Planner
        "current_wave_index": 0,
        "pin_verified": False,  # Security: Reset PIN verification status
        "last_callback": None,  # Security: Clear stale callback data
    }


async def _handle_completed_tasks(
    completed_tasks: list,
    state: OrchestratorState,
    queue: RedisQueue,
    redis_client: redis.Redis,
    beneficiary_service: "BeneficiarySuggestionService",
    outbox: list,
) -> None:
    """Handle completed tasks and generate receipts or summaries."""
    is_single_transfer = (
        len(completed_tasks) == 1
        and completed_tasks[0].type == "transfer"
        and not completed_tasks[0].payload.get("is_batch", False)
        and len(completed_tasks[0].payload.get("recipients", [])) <= 1
    )

    read_only_task_types = {"account", "query", "faq", "support"}
    all_read_only = all(task.type in read_only_task_types for task in completed_tasks)

    if is_single_transfer:
        task = completed_tasks[0]
        await _queue_single_transfer_receipt(
            task=task,
            state=state,
            queue=queue,
            redis_client=redis_client,
        )

        if beneficiary_service:
            await _handle_beneficiary_suggestion(
                task=task,
                beneficiary_service=beneficiary_service,
                phone_number=state.phone_number,
                outbox=outbox,
            )
    elif all_read_only:
        pass
    else:
        summary_text = format_multi_action_summary(completed_tasks)
        outbox.append({"type": "say", "text": summary_text})


async def _queue_single_transfer_receipt(
    task,
    state: OrchestratorState,
    queue: RedisQueue | None,
    redis_client: redis.Redis | None,
) -> None:
    """Queue receipt for a single transfer."""
    receipt_data = task.payload.get("receipt")
    if not receipt_data or not (queue or redis_client):
        return

    logger.info("queuing_single_transfer_receipt", task_id=task.id)
    import uuid

    signal_key = f"receipt:signal:{uuid.uuid4()}"

    payload = {
        "phone_number": state.phone_number,
        "transaction_reference": task.payload.get("transaction_id") or task.payload.get("idempotency_key") or "N/A",
        "amount": task.payload.get("amount"),
        "source": {
            "name": task.payload.get("source_bank_name"),
            "account_name": task.payload.get("source_account_name"),
        },
        "recipient": {
            "name": task.payload.get("recipient_name"),
            "account_number": task.payload.get("recipient_account"),
            "bank_name": task.payload.get("recipient_bank_name"),
        },
        "narration": task.payload.get("narration"),
    }

    job_payload = {
        "payload": payload,
        "signal_key": signal_key,
    }

    if queue:
        await queue.enqueue("banking:receipt_jobs", job_payload)
    else:
        logger.warning("queue_not_available", message="Cannot queue receipt, RedisQueue is None")


async def _handle_beneficiary_suggestion(
    task,
    beneficiary_service: "BeneficiarySuggestionService",
    phone_number: str,
    outbox: list,
) -> None:
    """Handle beneficiary suggestion after transfer."""
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
    )

    if suggestion_msg:
        outbox.append({"type": "say", "text": suggestion_msg})
