import json
import uuid

import redis.asyncio as redis
from langchain_core.runnables import RunnableConfig

from apps.core.src.agent.orchestrator.models.domain import TaskStage
from apps.core.src.agent.orchestrator.state import OrchestratorState


async def finalize(state: OrchestratorState, config: RunnableConfig) -> dict:
    """Final Step. Generate response."""
    results = []
    outbox = []

    bs_service = config["configurable"].get("beneficiary_suggestion_service")
    redis_client: redis.Redis = config["configurable"].get("redis_client")

    for tid, task in state.tasks.items():
        if task.stage == TaskStage.COMPLETED:
            amount = task.payload.get("amount", "unknown")
            results.append(f"✓ Transfer of {amount} to {task.payload.get('recipient_name')} is processing...")

            if task.type == "transfer":
                # Offload Receipt Generation to Worker
                if redis_client:
                    signal_key = f"receipt:signal:{uuid.uuid4()}"
                    job_payload = {
                        "phone_number": state.phone_number,
                        "transfer_data": {
                            "amount": task.payload.get("amount"),
                            "source": {
                                "name": task.payload.get("source_bank_name"), # Using source bank as proxy or need account name?
                                "account_name": "User", # Placeholder if source name not in payload
                            },
                            "recipient": {
                                "name": task.payload.get("recipient_name"),
                                "account_number": task.payload.get("recipient_account"),
                                "bank_name": task.payload.get("recipient_bank_name"),
                            },
                            "narration": task.payload.get("narration"),
                        },
                        "transfer_result": {
                            "transaction_id": task.payload.get("transaction_id"),
                            "reference": task.payload.get("idempotency_key"),
                        },
                        "signal_key": signal_key,
                    }
                    
                    await redis_client.rpush("banking:receipt_jobs", json.dumps(job_payload))
                    
                    # Wait for Receipt Worker to send the image
                    # This ensures Receipt -> Suggestion ordering
                    await redis_client.blpop(signal_key, timeout=20)
            
            # Trigger Beneficiary Suggestion
            if task.type == "transfer" and bs_service:
                suggestion_msg = await bs_service.check_and_suggest_beneficiary(
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
            results.append(f"❌ Failed: {task.payload.get('error')}")

        elif task.stage == TaskStage.CANCELLED:
            results.append("🚫 Transaction cancelled.")

    final_text = "\n".join(results) if results else "I'm done processing."

    return {
        "final_response": final_text,
        "outbox": outbox,
        "tasks": {},  # Wipe tasks so next turn is fresh
        "waves": [],  # Clear waves so next turn triggers Planner
        "current_wave_index": 0,
        "pin_verified": False,  # Security: Reset PIN verification status
        "last_callback": None,  # Security: Clear stale callback data
    }
