"""Cancellation handling for transfer flow."""

import json
from typing import Optional, cast, TYPE_CHECKING

from shared.cache.redis_client import RedisClient, Redis
from apps.core.src.agent.sub_agents.transfer.state import TransferState

from .run_context import TransferRunContext
from .state import (
    create_initial_state,
    update_conversation_state,
    clear_all_transfer_state,
    has_substantial_transfer_data,
    get_transfer_session_age,
)
from shared.config.settings import settings
from shared.utils.logging import get_logger

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph

logger = get_logger(__name__)

CONFIRMATION_WORDS = {"yes", "y", "cancel", "ok", "proceed", "sure", "yeah", "yep"}
DECLINE_WORDS = {"no", "n", "continue", "ni", "nah", "nope", "don't", "dont"}
CANCELLATION_PROMPT_PHRASE = "cancel it and start a new transfer"


def is_cancellation_confirmation(ctx: TransferRunContext, last_response: str) -> bool:
    """Check if user confirmed cancellation."""
    last_response_lower = (last_response or "").lower()
    return (
        ctx.message_lower in CONFIRMATION_WORDS and
        CANCELLATION_PROMPT_PHRASE in last_response_lower
    )


def is_cancellation_decline(ctx: TransferRunContext, last_response: str) -> bool:
    """Check if user declined cancellation."""
    last_response_lower = (last_response or "").lower()
    return (
        ctx.message_lower in DECLINE_WORDS and
        CANCELLATION_PROMPT_PHRASE in last_response_lower
    )


async def handle_cancellation_confirmation(
    ctx: TransferRunContext,
    graph: "CompiledStateGraph",
    redis_client: Redis,
) -> str:
    """Handle user confirming cancellation - clear state and start fresh."""
    await clear_all_transfer_state(ctx.phone_number, redis_client, graph, ctx.config)
    
    input_state = create_initial_state(
        ctx.phone_number,
        ctx.message,
        ctx.message_id,
        ctx.classification_result,
        image_data=ctx.image_data
    )
    
    final_state = await graph.ainvoke(cast(TransferState, input_state), ctx.config)
    await update_conversation_state(ctx.phone_number, cast(TransferState, final_state))
    
    return final_state.get("response", "")


async def handle_cancellation_decline(ctx: TransferRunContext) -> str:
    """Handle user declining cancellation - continue with previous transfer."""
    try:
        redis_client = RedisClient.get_client()
        conv_state_key = f"user:{ctx.phone_number}:conversation_state"
        conv_state_data = await redis_client.get(conv_state_key)

        if conv_state_data:
            conv_state = json.loads(conv_state_data)
            prev_amount = conv_state.get("amount", 0)
            prev_recipient_name = conv_state.get("recipient_name")
            prev_recipient_account = conv_state.get("recipient_account", "")
            prev_recipient = prev_recipient_name or prev_recipient_account or "the recipient"
            transfer_status = conv_state.get("transfer_status")

            if transfer_status == "pending":
                return f"Got it. Continuing with your pending transfer of ₦{prev_amount:,.2f} to {prev_recipient}. Please enter your PIN to confirm."
            else:
                return f"Got it. Continuing with your transfer of ₦{prev_amount:,.2f} to {prev_recipient}."
    except Exception:
        pass

    return "Got it. Continuing with your previous transfer."


async def handle_cancellation_decline_with_checkpoint(
    ctx: TransferRunContext,
    graph: "CompiledStateGraph",
) -> str:
    """Handle cancellation decline, falling back to checkpoint if needed."""
    # Try conversation state first
    result = await handle_cancellation_decline(ctx)
    if result != "Got it. Continuing with your previous transfer.":
        return result
    
    # Fallback: Try checkpoint
    try:
        current_state = await graph.aget_state(ctx.config)
        if current_state and current_state.values:
            prev_amount = current_state.values.get("amount", 0)
            prev_recipient = (
                current_state.values.get("recipient_name") or 
                current_state.values.get("recipient_account", "")
            )
            transfer_status = current_state.values.get("transfer_status")

            if transfer_status == "pending":
                return f"Got it. Continuing with your pending transfer of ₦{prev_amount:,.2f} to {prev_recipient}. Please enter your PIN to confirm."
            else:
                return f"Got it. Continuing with your transfer of ₦{prev_amount:,.2f} to {prev_recipient}."
    except Exception:
        pass
    
    return "Got it. Continuing with your previous transfer."


async def should_prompt_for_cancellation(
    ctx: TransferRunContext,
    input_state: dict,
) -> bool:
    """Check if we should prompt user about cancellation."""
    message_lower = ctx.message_lower
    
    has_amount_keywords = any(keyword in message_lower for keyword in [
        "send", "transfer", "pay", "give"
    ]) or any(char in ctx.message for char in ["k", "₦"]) or any(
        word in message_lower for word in ["thousand", "naira"]
    )
    
    has_substantial_data = has_substantial_transfer_data(cast(TransferState, input_state))
    
    if not (has_amount_keywords and has_substantial_data):
        return False
    
    session_age = await get_transfer_session_age(ctx.phone_number)
    if session_age is None:
        return False
    
    return session_age < settings.flow_session_timeout


def build_cancellation_prompt(input_state: dict) -> str:
    """Build cancellation prompt message."""
    amount = input_state.get("amount", 0)
    recipient_name = input_state.get("recipient_name")
    recipient_account = input_state.get("recipient_account")

    if recipient_name:
        recipient_display = recipient_name
    elif recipient_account:
        recipient_display = f"account {recipient_account[-4:]}"
    else:
        recipient_display = "the recipient"

    return (
        f"You have a pending transfer of ₦{amount:,.2f} to {recipient_display}. "
        f"Would you like to cancel it and start a new transfer? "
        f"(Reply 'yes' to cancel, 'no' to continue with the previous transfer)"
    )
