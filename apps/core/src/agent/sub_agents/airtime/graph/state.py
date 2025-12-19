"""State management utilities for airtime purchase flow graph."""

import json
from typing import Optional

from shared.cache.redis_client import RedisClient

from apps.core.src.agent.sub_agents.airtime.state import AirtimeState

from .utils import debug_log


def create_initial_state(
    phone_number: str,
    message: str,
    message_id: str,
    classification_result: Optional[dict] = None
) -> AirtimeState:
    """Create initial state for airtime purchase flow."""
    language = None
    if classification_result and "detected_language" in classification_result:
        language = classification_result.get("detected_language")

    return {
        "phone_number": phone_number,
        "message": message,
        "message_id": message_id,
        "active_flow": "airtime",
        "flow_state": "extracting",
        "amount": None,
        "recipient_phone": None,
        "network": None,
        "source_account_id": None,
        "narration": None,
        "missing_fields": [],
        "user_profile": None,
        "accounts": [],
        "beneficiaries": [],
        "selected_source_account": None,
        "matched_beneficiary": None,
        "balance_available": None,
        "validation_errors": [],
        "response": "",
        "llm_reply": None,
        "recipient_name": None,
        "idempotency_key": None,
        "airtime_status": None,
        "classification_result": classification_result,
        "language": language,
    }


async def update_conversation_state(phone_number: str, state: AirtimeState) -> None:
    """Update conversation_state in Redis."""
    try:
        redis_client = RedisClient.get_client()
        flow_state = state.get("flow_state")
        active_flow = state.get("active_flow")
        airtime_status = state.get("airtime_status")
        idem_key = state.get("idempotency_key")

        if flow_state == "cancelled":
            key = f"user:{phone_number}:conversation_state"
            await redis_client.delete(key)
            return

        should_save = False
        amount = state.get("amount")
        
        if (airtime_status == "pending" or
            (flow_state not in ("extracting", "error", None) and active_flow == "airtime") or
            idem_key or
            (flow_state == "extracting" and amount)):  # Save during extracting if we have amount
            should_save = True

        if should_save:
            conversation_state = {
                "active_flow": active_flow,
                "flow_state": flow_state,
                "airtime_status": airtime_status,
                "idempotency_key": idem_key,
                "amount": state.get("amount"),
                "recipient_phone": state.get("recipient_phone"),
                "network": state.get("network"),
            }
            key = f"user:{phone_number}:conversation_state"
            await redis_client.set(key, json.dumps(conversation_state), ex=3600)
            debug_log(
                f"✅ Updated conversation_state for {phone_number}: active_flow={active_flow}, flow_state={flow_state}, airtime_status={airtime_status}")
        else:
            key = f"user:{phone_number}:conversation_state"
            await redis_client.delete(key)
    except Exception as e:
        debug_log(f"⚠️  Error updating conversation_state: {e}")
