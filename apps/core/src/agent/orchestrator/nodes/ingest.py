from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def ingest_message(state: OrchestratorState) -> dict:
    """Entry point. Setup state for the new turn."""
    logger.info("ingest_message", user=state.phone_number, text=state.last_message_text)

    updates = {"outbox": [], "final_response": None}

    if state.last_callback:
        logger.info("processing_callback", payload=state.last_callback)
        if state.last_callback.get("pin_verified"):
            updates["pin_verified"] = True

    return updates
