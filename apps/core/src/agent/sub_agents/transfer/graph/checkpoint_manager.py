"""Checkpoint and state management for transfer flow."""

import re
from typing import Optional, cast, TYPE_CHECKING

from shared.cache.redis_client import Redis
from apps.core.src.agent.sub_agents.transfer.state import TransferState
from shared.config.settings import settings

from .run_context import TransferRunContext
from .state import (
    create_initial_state,
    get_transfer_session_age,
    clear_all_transfer_state,
)
from shared.utils.logging import get_logger

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph

logger = get_logger(__name__)

# Transfer intent keywords
TRANSFER_INTENTS = {"transfer", "send_money", "send money", "send", "pay"}

# Non-transfer intents
NON_TRANSFER_INTENTS = {"conversational", "query", "utility", "cancel"}


async def load_checkpoint_state(
    ctx: TransferRunContext,
    graph: "CompiledStateGraph",
) -> Optional[dict]:
    """Load existing state from checkpoint."""
    try:
        current_state = await graph.aget_state(ctx.config)
        if current_state and current_state.values:
            return dict(current_state.values)
    except Exception:
        pass
    return None


def merge_message_into_state(ctx: TransferRunContext, input_state: dict) -> dict:
    """Merge new message and context into existing state."""
    old_message = input_state.get("message", "")
    input_state["message"] = ctx.message
    input_state["phone_number"] = ctx.phone_number
    input_state["message_id"] = ctx.message_id
    
    # Preserve image_data for vision extraction
    if ctx.image_data:
        input_state["image_data"] = ctx.image_data
    
    logger.debug(f"Message update - old: '{old_message}' -> new: '{ctx.message}'")
    return input_state


def apply_classification_result(ctx: TransferRunContext, input_state: dict) -> dict:
    """Apply classification result to state."""
    if not ctx.classification_result:
        return input_state
    
    input_state["classification_result"] = ctx.classification_result
    
    if "detected_language" in ctx.classification_result:
        input_state["language"] = ctx.classification_result["detected_language"]
    
    return input_state


def apply_task_parameters(ctx: TransferRunContext, input_state: dict) -> dict:
    """Apply task parameters from classification to state."""
    task_params = ctx.get_task_params()
    if not task_params:
        return input_state
    
    task_amount = task_params.get("amount")
    task_recipient = task_params.get("recipient")
    
    if task_amount:
        input_state["amount"] = float(task_amount)
        logger.debug(f"Set amount from task_parameters: {task_amount}")
    
    if task_recipient:
        input_state["recipient_name"] = task_recipient
        logger.debug(f"Set recipient_name from task_parameters: {task_recipient}")
    
    return input_state


def clear_stale_new_task_state(ctx: TransferRunContext, input_state: dict) -> dict:
    """Clear stale state when starting a new task."""
    checkpoint_transfer_status = input_state.get("transfer_status")
    
    # Check if message contains account numbers (indicates continuation, not new task)
    has_account_number = bool(re.search(r'\b\d{10}\b', ctx.message))
    is_new_task_start = ctx.has_task_params and not has_account_number
    
    if checkpoint_transfer_status == "collection_complete" and is_new_task_start:
        logger.debug("Clearing stale collection_complete status for new task")
        input_state["transfer_status"] = None
        input_state["flow_state"] = "extracting"
        input_state["recipient_account"] = None
        input_state["recipient_bank_code"] = None
        input_state["recipient_bank_name"] = None
        input_state["account_resolved"] = None
        input_state["amount"] = None
        input_state["recipient_name"] = None
        input_state["idempotency_key"] = None
    
    return input_state


async def check_session_status(
    ctx: TransferRunContext,
    input_state: dict,
    graph: "CompiledStateGraph",
    redis_client: Redis,
) -> tuple[bool, Optional[dict]]:
    """
    Check if session is expired or terminal.
    
    Returns:
        (should_clear, fresh_state): Whether to clear and optional fresh state
    """
    checkpoint_transfer_status = input_state.get("transfer_status")
    session_age = await get_transfer_session_age(ctx.phone_number)
    
    is_terminal_status = checkpoint_transfer_status in ("authorized", "completed", "failed")
    is_session_expired = session_age is not None and session_age >= settings.flow_session_timeout
    
    if is_terminal_status or is_session_expired:
        await clear_all_transfer_state(ctx.phone_number, redis_client, graph, ctx.config)
        fresh_state = create_initial_state(
            ctx.phone_number, ctx.message, ctx.message_id, ctx.classification_result
        )
        return True, fresh_state
    
    return False, None


def is_non_transfer_intent(ctx: TransferRunContext) -> bool:
    """Check if intent is non-transfer (should abort flow)."""
    return ctx.get_classification_intent() in NON_TRANSFER_INTENTS


def is_new_transfer_intent(ctx: TransferRunContext) -> bool:
    """Check if intent is a new transfer."""
    return ctx.get_classification_intent() in TRANSFER_INTENTS


async def clear_old_transfer_values(
    ctx: TransferRunContext,
    input_state: dict,
    redis_client: Redis,
) -> dict:
    """Clear old transfer values when starting a new transfer."""
    is_new_transfer = is_new_transfer_intent(ctx)
    has_old_values = input_state.get("amount") or input_state.get("recipient_account")
    is_continuing_flow = input_state.get("flow_state") in (
        "collecting_recipient", "collecting_amount", "validating", "confirming"
    )
    
    if not (is_new_transfer and has_old_values):
        return input_state
    
    # Only clear if this is truly a NEW transfer
    if ctx.has_task_params or is_continuing_flow:
        logger.debug(f"Preserving transfer values - has_task_params={ctx.has_task_params}, is_continuing_flow={is_continuing_flow}")
        return input_state
    
    logger.debug("Clearing old transfer values - new intent detected")
    
    # Clear old transfer values
    input_state["amount"] = None
    input_state["recipient_account"] = None
    input_state["recipient_bank_code"] = None
    input_state["recipient_bank_name"] = None
    input_state["recipient_name"] = None
    input_state["idempotency_key"] = None
    input_state["transfer_status"] = None
    input_state["account_resolved"] = None
    input_state["matched_beneficiary"] = None
    input_state["flow_state"] = "extracting"
    
    # Clear Redis previous values key
    prev_key = f"transfer:prev:{ctx.phone_number}:{input_state.get('idempotency_key', '')}"
    await redis_client.delete(prev_key)
    
    return input_state


def clear_recipient_if_needed(ctx: TransferRunContext, input_state: dict) -> dict:
    """Clear recipient data if starting a new transfer."""
    message_lower = ctx.message_lower
    
    # Only clear recipient for new transfer requests (action + recipient), not corrections
    has_action = any(keyword in message_lower for keyword in [
        "send", "transfer", "pay", "give"
    ])
    has_recipient = " to " in message_lower
    
    stale_recipient = input_state.get("recipient_account")
    stale_bank = input_state.get("recipient_bank_code") or input_state.get("recipient_bank_name")
    current_flow_state = input_state.get("flow_state")
    stale_transfer_status = input_state.get("transfer_status")
    
    # Check for account numbers in message
    account_numbers_in_message = re.findall(
        r'\b\d{10}\b', 
        ctx.message.replace(",", " ").replace(".", " ")
    )
    has_account_in_message = len(account_numbers_in_message) > 0
    
    logger.debug(
        f"Message: '{ctx.message}', has_action={has_action}, has_recipient={has_recipient}, "
        f"has_account_in_message={has_account_in_message}"
    )
    
    # Only clear recipient if this looks like a new transfer request (action + recipient)
    is_new_transfer = has_action and has_recipient
    should_clear_recipient = (
        is_new_transfer and
        (stale_recipient or stale_bank) and
        current_flow_state not in ("collecting_recipient", "collecting_amount", "confirming") and
        stale_transfer_status not in ("pending", "collection_complete") and
        not has_account_in_message
    )
    
    if should_clear_recipient:
        logger.debug("Clearing stale recipient data")
        input_state["recipient_account"] = None
        input_state["recipient_bank_code"] = None
        input_state["recipient_bank_name"] = None
        input_state["recipient_name"] = None
        input_state["account_resolved"] = None
        input_state["matched_beneficiary"] = None
        input_state["validation_errors"] = []
        input_state["narration"] = None
    
    return input_state


def clear_completed_transfer_state(input_state: dict) -> dict:
    """Clear state after a completed/failed/cancelled transfer."""
    stale_transfer_status = input_state.get("transfer_status")
    
    if stale_transfer_status in ("completed", "failed", "cancelled"):
        input_state["recipient_account"] = None
        input_state["recipient_bank_code"] = None
        input_state["recipient_bank_name"] = None
        input_state["recipient_name"] = None
        input_state["account_resolved"] = None
        input_state["matched_beneficiary"] = None
        input_state["validation_errors"] = []
        input_state["transfer_status"] = None
        input_state["idempotency_key"] = None
        input_state["flow_state"] = "extracting"
        input_state["amount"] = None
        input_state["narration"] = None
    
    return input_state


async def prepare_checkpoint_state(
    ctx: TransferRunContext,
    input_state: dict,
    graph: "CompiledStateGraph",
    redis_client: Redis,
) -> Optional[dict]:
    """
    Prepare checkpoint state for graph execution.
    
    Returns state dict or None if non-transfer intent.
    """
    # Clear stale new task state
    input_state = clear_stale_new_task_state(ctx, input_state)
    
    # Merge message and context
    input_state = merge_message_into_state(ctx, input_state)
    input_state = apply_classification_result(ctx, input_state)
    input_state = apply_task_parameters(ctx, input_state)
    
    # Check session status
    should_clear, fresh_state = await check_session_status(
        ctx, input_state, graph, redis_client
    )
    if should_clear and fresh_state:
        return fresh_state
    
    # Check for non-transfer intent
    if is_non_transfer_intent(ctx):
        await clear_all_transfer_state(ctx.phone_number, redis_client, graph, ctx.config)
        return None  # Signal to return early
    
    # Clear old values if new transfer
    input_state = await clear_old_transfer_values(ctx, input_state, redis_client)
    
    # Clear recipient if needed
    input_state = clear_recipient_if_needed(ctx, input_state)
    
    # Clear completed transfer state
    input_state = clear_completed_transfer_state(input_state)
    
    return input_state
