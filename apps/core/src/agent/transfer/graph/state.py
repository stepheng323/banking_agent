"""State management utilities for transfer flow graph."""

import json
import time
from typing import Optional

from shared.cache.redis_client import RedisClient

from apps.core.src.agent.transfer.state import TransferState

from .utils import debug_log

# Session timeout: 10 minutes
TRANSFER_SESSION_TIMEOUT = 600  # 10 minutes in seconds


def create_initial_state(phone_number: str, message: str, message_id: str, classification_result: Optional[dict] = None) -> TransferState:
    """Create initial state for transfer flow."""
    return {
        # User identification
        "phone_number": phone_number,
        "message": message,
        "message_id": message_id,
        # Flow state
        "active_flow": "transfer",
        "flow_state": "extracting",
        # Entities
        "amount": None,
        "recipient_name": None,
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
    }


async def update_conversation_state(phone_number: str, state: TransferState) -> None:
    """Update conversation_state in Redis so orchestrator can detect active transactions."""
    try:
        redis_client = RedisClient.get_client()
        flow_state = state.get("flow_state")
        active_flow = state.get("active_flow")
        transfer_status = state.get("transfer_status")
        idem_key = state.get("idempotency_key")

        # Only save conversation_state if there's an active transaction
        # (not in initial/extracting state unless there's a pending transfer)
        should_save = False

        if flow_state == "cancelled":
            # Transaction cancelled - clear conversation_state
            key = f"user:{phone_number}:conversation_state"
            await redis_client.delete(key)
            return

        # Save if:
        # 1. Transfer is pending (waiting for PIN)
        # 2. Flow state indicates active transaction (not just extracting)
        # 3. Has idempotency key (transaction initiated)
        if (transfer_status == "pending" or
            (flow_state not in ("extracting", "error", None) and active_flow == "transfer") or
                idem_key):
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
            }

            key = f"user:{phone_number}:conversation_state"
            # Save with 1 hour TTL (same as other conversation state)
            await redis_client.set(key, json.dumps(conversation_state), ex=3600)
            debug_log(
                f"✅ Updated conversation_state for {phone_number}: active_flow={active_flow}, flow_state={flow_state}, transfer_status={transfer_status}")
        else:
            # No active transaction - clear conversation_state if it exists
            key = f"user:{phone_number}:conversation_state"
            await redis_client.delete(key)
    except Exception as e:
        debug_log(f"⚠️  Error updating conversation_state: {e}")
        # Don't fail if conversation state update fails


async def get_transfer_session_age(phone_number: str) -> Optional[float]:
    """Get the age of the current transfer session in seconds, or None if no active session."""
    try:
        redis_client = RedisClient.get_client()
        key = f"user:{phone_number}:transfer_session_start"
        session_start = await redis_client.get(key)
        if session_start:
            start_time = float(session_start)
            age = time.time() - start_time
            return age
    except Exception as e:
        debug_log(f"⚠️  Error getting transfer session age: {e}")
    return None


async def start_transfer_session(phone_number: str) -> None:
    """Start a new transfer session by storing the current timestamp."""
    try:
        redis_client = RedisClient.get_client()
        key = f"user:{phone_number}:transfer_session_start"
        await redis_client.set(key, str(time.time()), ex=TRANSFER_SESSION_TIMEOUT)
        debug_log(f"🕐 Started transfer session for {phone_number}")
    except Exception as e:
        debug_log(f"⚠️  Error starting transfer session: {e}")


async def clear_transfer_session(phone_number: str) -> None:
    """Clear the transfer session."""
    try:
        redis_client = RedisClient.get_client()
        key = f"user:{phone_number}:transfer_session_start"
        await redis_client.delete(key)
        debug_log(f"🧹 Cleared transfer session for {phone_number}")
    except Exception as e:
        debug_log(f"⚠️  Error clearing transfer session: {e}")


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
        # Clear LangGraph checkpoint
        if graph and config:
            try:
                await graph.adelete(config)
                debug_log(f"🧹 Cleared LangGraph checkpoint for {phone_number}")
            except Exception as e:
                debug_log(f"⚠️  Error clearing checkpoint (may not exist): {e}")
        
        # Clear Redis keys
        keys_to_delete = [
            f"user:{phone_number}:conversation_state",
            f"user:{phone_number}:pending_transfer",
            f"user:{phone_number}:pending_transfer_flow_token",
            f"user:{phone_number}:transfer_session_start",
        ]
        
        for key in keys_to_delete:
            await redis_client.delete(key)
        
        debug_log(f"🧹 Cleared all transfer state for {phone_number}")
    except Exception as e:
        debug_log(f"⚠️  Error clearing transfer state: {e}")

