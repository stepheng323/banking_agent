from typing import Any

from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def ingest_message(state: OrchestratorState) -> dict[str, Any]:
    """Entry point. Setup state for the new turn."""
    logger.info("ingest_message", user=state.phone_number, text=state.last_message_text)

    updates: dict[str, Any] = {
        "outbox": [],
        "final_response": None,
        "policy_notice": None,
        "fast_path_triggered": False,
    }

    if state.last_callback:
        logger.info("processing_callback", payload=state.last_callback)
        updates["last_message_text"] = None
        updates["last_message_id"] = None
        if state.last_callback.get("pin_verified"):
            updates["pin_verified"] = True

    return updates
