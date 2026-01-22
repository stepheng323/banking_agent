import json
from typing import TYPE_CHECKING

import redis.asyncio as redis
from langchain_core.runnables import RunnableConfig

if TYPE_CHECKING:
    from apps.core.src.agent.graphs.__shared__.beneficiary.suggestion_service import BeneficiarySuggestionService

from apps.core.src.agent.orchestrator.models.domain import TaskStage
from apps.core.src.agent.orchestrator.state import OrchestratorState
from shared.queue.redis_queue import RedisQueue
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def finalize(state: OrchestratorState, config: RunnableConfig) -> dict:
    """Final Step. Generate response."""
    results = []
    outbox = []

    beneficiary_service: BeneficiarySuggestionService | None = config["configurable"].get(
        "beneficiary_suggestion_service"
    )
    redis_client: redis.Redis | None = config["configurable"].get("redis_client")
    queue: RedisQueue | None = config["configurable"].get("queue")

    for tid, task in state.tasks.items():
        if task.stage == TaskStage.COMPLETED:
            if task.type == "transfer":
                amount = task.payload.get("amount", "unknown")
                recipient = task.payload.get("recipient_name") or "recipient"
                text_response = f"✓ Transfer of {amount} to {recipient} is processing..."
                outbox.append({"type": "say", "text": text_response})

                receipt_data = task.payload.get("receipt")
                if receipt_data and redis_client:
                    logger.info("finalize_receipt_generation_start")
                    import uuid

                    signal_key = f"receipt:signal:{uuid.uuid4()}"

                    payload = {
                        "phone_number": state.phone_number,
                        "transaction_reference": task.payload.get("transaction_id")
                        or task.payload.get("idempotency_key")
                        or "N/A",
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
                        await redis_client.rpush("banking:receipt_jobs", json.dumps(job_payload))

                    outbox.append(
                        {
                            "type": "show_receipt",
                            "task_id": tid,
                            "receipt": receipt_data,
                            "caption": f"Receipt for transfer to {recipient}",
                        }
                    )

            if task.type == "transfer" and beneficiary_service:
                suggestion_msg = await beneficiary_service.check_and_suggest_beneficiary(
                    phone_number=state.phone_number,
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

        elif task.stage == TaskStage.FAILED:
            results.append(f"Failed: {task.payload.get('error')}")

        elif task.stage == TaskStage.CANCELLED:
            results.append("Transaction cancelled, how else can I help you today?")

    # If we had manual sends, results might be empty, which is fine.
    final_text = "\n".join(results) if results else None

    return {
        "final_response": final_text,
        "outbox": outbox,
        "tasks": {},  # Wipe tasks so next turn is fresh
        "waves": [],  # Clear waves so next turn triggers Planner
        "current_wave_index": 0,
        "pin_verified": False,  # Security: Reset PIN verification status
        "last_callback": None,  # Security: Clear stale callback data
    }
