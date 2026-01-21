from typing import TYPE_CHECKING

from langchain_core.runnables import RunnableConfig

if TYPE_CHECKING:
    from apps.core.src.agent.graphs.__shared__.beneficiary.suggestion_service import BeneficiarySuggestionService

from apps.core.src.agent.orchestrator.models.domain import TaskStage
from apps.core.src.agent.orchestrator.state import OrchestratorState
from shared.clients.whatsapp.client import WhatsAppClient
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def finalize(state: OrchestratorState, config: RunnableConfig) -> dict:
    """Final Step. Generate response."""
    results = []
    outbox = []

    beneficiary_service: BeneficiarySuggestionService | None = config["configurable"].get(
        "beneficiary_suggestion_service"
    )
    whatsapp_client: WhatsAppClient | None = config["configurable"].get("whatsapp_client")

    for _, task in state.tasks.items():
        if task.stage == TaskStage.COMPLETED:
            if task.type == "transfer":
                amount = task.payload.get("amount", "unknown")
                text_response = f"✓ Transfer of {amount} to {task.payload.get('recipient_name')} is processing..."
                if whatsapp_client:
                    await whatsapp_client.send_text(state.phone_number, text_response)
                else:
                    results.append(text_response)

            logger.info("finalize_task_completed", type=task.type, phone=state.phone_number)

            logger.info("finalize_suggestion_check", has_beneficiary_service=bool(beneficiary_service))
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
