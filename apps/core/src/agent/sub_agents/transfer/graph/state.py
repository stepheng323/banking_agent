"""State management utilities for transfer flow graph."""

import json
from typing import Optional

from shared.cache.redis_client import RedisClient
from shared.cache.flow_session_manager import (
    get_flow_session_age,
    start_flow_session,
    clear_flow_session,
)

from apps.core.src.agent.sub_agents.transfer.state import TransferState

from apps.core.src.agent.sub_agents.transfer.nodes.utils import debug_log


def create_initial_state(
    phone_number: str, 
    message: str, 
    message_id: str, 
    classification_result: Optional[dict] = None,
    image_data: str | None = None
) -> TransferState:
    """Create initial state for transfer flow."""
    task_params = None
    if classification_result and "task_parameters" in classification_result:
        task_params = classification_result.get("task_parameters", {})
    
    amount = None
    recipient_name = None
    language = None
    
    if classification_result and "detected_language" in classification_result:
        language = classification_result.get("detected_language")

    if task_params:
        amount = task_params.get("amount")
        if isinstance(amount, (int, float)):
            amount = float(amount)
        recipient_name = task_params.get("recipient")
    
    return {
        # User identification
        "phone_number": phone_number,
        "message": message,
        "message_id": message_id,
        # Flow state
        "active_flow": "transfer",
        "flow_state": "extracting",
        "language": language,
        # Entities - pre-populate from task parameters if available
        "amount": amount,
        "recipient_name": recipient_name,
        "recipient_account": None,
        "recipient_bank_code": None,
        "recipient_bank_name": None,
        "source_account_id": None,
        "narration": None,
        "missing_fields": [],
        # User context
        "user_profile": None,
        "accounts": [],
        "beneficiaries": [],
        # Selected/resolved values
        "selected_source_account": None,
        "matched_beneficiary": None,
        "account_resolved": None,
        "balance_available": None,
        "validation_errors": [],
        # Response
        "response": "",
        "llm_reply": None,
        # Metadata
        "idempotency_key": None,
        "transfer_status": None,
        # Classification result from orchestrator
        "classification_result": classification_result,
        # Image data for vision extraction
        "image_data": image_data,
    }


async def update_conversation_state(phone_number: str, state: TransferState) -> None:
    """Update conversation_state in Redis so orchestrator can detect active transactions."""
    try:
        redis_client = RedisClient.get_client()
        flow_state = state.get("flow_state")
        active_flow = state.get("active_flow")
        transfer_status = state.get("transfer_status")
        idem_key = state.get("idempotency_key")

        should_save = False

        if flow_state == "cancelled":
            key = f"user:{phone_number}:conversation_state"
            await redis_client.delete(key)
            return

        amount = state.get("amount")
        awaiting_confirmation = state.get("awaiting_confirmation")
        
        if (transfer_status == "pending" or
            (flow_state not in ("extracting", "error", None) and active_flow == "transfer") or
            idem_key or
            (flow_state == "extracting" and amount) or
            awaiting_confirmation):
            should_save = True

        if should_save:
            conversation_state = {
                "active_flow": active_flow,
                "flow_state": flow_state,
                "transfer_status": transfer_status,
                "idempotency_key": idem_key,
                "amount": state.get("amount"),
                "recipient_account": state.get("recipient_account"),
                "recipient_name": state.get("recipient_name"),
                "recipient_bank_code": state.get("recipient_bank_code"),
                "recipient_bank_name": state.get("recipient_bank_name"),
                "funding_status": state.get("funding_status"),
                "funding_required": state.get("funding_required"),
                "awaiting_confirmation": awaiting_confirmation,
                "confirmation_context": state.get("confirmation_context"),
                "max_available": state.get("max_available"),  # For amount adjustment
            }

            key = f"user:{phone_number}:conversation_state"
            await redis_client.set(key, json.dumps(conversation_state), ex=3600)
            debug_log(
                f"✓ Updated conversation_state for {phone_number}: active_flow={active_flow}, flow_state={flow_state}, transfer_status={transfer_status}, awaiting_confirmation={awaiting_confirmation}")
        else:
            key = f"user:{phone_number}:conversation_state"
            await redis_client.delete(key)
    except Exception as e:
        debug_log(f"⚠️  Error updating conversation_state: {e}")


async def get_transfer_session_age(phone_number: str) -> Optional[float]:
    """Get the age of the current transfer session in seconds, or None if no active session."""
    return await get_flow_session_age(phone_number, "transfer")


async def start_transfer_session(phone_number: str) -> None:
    """Start a new transfer session by storing the current timestamp."""
    await start_flow_session(phone_number, "transfer")


async def clear_transfer_session(phone_number: str) -> None:
    """Clear the transfer session."""
    await clear_flow_session(phone_number, "transfer")


def has_substantial_transfer_data(state: TransferState) -> bool:
    """Check if transfer has substantial data (amount + recipient info + pending status)."""
    amount = state.get("amount")
    recipient_account = state.get("recipient_account")
    recipient_name = state.get("recipient_name")
    transfer_status = state.get("transfer_status")
    
    has_amount = amount is not None and amount > 0
    has_recipient = recipient_account is not None or recipient_name is not None
    is_pending = transfer_status == "pending"
    
    return has_amount and has_recipient and is_pending


async def clear_all_transfer_state(phone_number: str, redis_client, graph, config) -> None:
    """Clear all transfer-related state (checkpoint, Redis keys)."""
    try:
        if graph and config:
            try:
                if hasattr(graph, "checkpointer") and graph.checkpointer:
                    thread_id = config["configurable"]["thread_id"]
                    if hasattr(graph.checkpointer, "adelete_thread"):
                        await graph.checkpointer.adelete_thread(thread_id)
                    elif hasattr(graph.checkpointer, "adelete"):
                        await graph.checkpointer.adelete(config)
                    else:
                        await graph.adelete(config)
                else:
                    await graph.adelete(config)
                    
                debug_log(f"🧹 Cleared LangGraph checkpoint for {phone_number}")
            except Exception as e:
                debug_log(f"⚠️  Error clearing checkpoint (may not exist): {e}")
        
        await clear_flow_session(phone_number, "transfer")
        
        keys_to_delete = [
            f"user:{phone_number}:conversation_state",
            f"user:{phone_number}:pending_transfer",
            f"user:{phone_number}:pending_transfer_flow_token",
        ]
        
        for key in keys_to_delete:
            await redis_client.delete(key)
        
        debug_log(f"🧹 Cleared all transfer state for {phone_number}")
    except Exception as e:
        debug_log(f"⚠️  Error clearing transfer state: {e}")

