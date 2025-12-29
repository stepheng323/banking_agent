"""Cancellation handling for transfer flow."""

import json
from typing import TYPE_CHECKING, cast

from apps.core.src.agent.tools.response import (
    ResponseIntent,
    build_response_context,
    get_synthesizer,
)
from apps.core.src.agent.sub_agents.transfer.state import TransferState
from shared.cache.redis_client import Redis, RedisClient
from shared.config.settings import settings
from shared.utils.logging import get_logger

from .run_context import TransferRunContext
from .state import (
    clear_all_transfer_state,
    create_initial_state,
    get_transfer_session_age,
    has_substantial_transfer_data,
    update_conversation_state,
)

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
        ctx.message_lower in CONFIRMATION_WORDS
        and CANCELLATION_PROMPT_PHRASE in last_response_lower
    )


def is_cancellation_decline(ctx: TransferRunContext, last_response: str) -> bool:
    """Check if user declined cancellation."""
    last_response_lower = (last_response or "").lower()
    return ctx.message_lower in DECLINE_WORDS and CANCELLATION_PROMPT_PHRASE in last_response_lower


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
        image_data=ctx.image_data,
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
            context = build_response_context(ResponseIntent.CANCELLATION_CONTINUE, conv_state)
            synthesizer = get_synthesizer()
            return await synthesizer.synthesize(context)
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
            prev_recipient = current_state.values.get("recipient_name") or current_state.values.get(
                "recipient_account", ""
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
    """Check if we should prompt user about cancellation.

    Only prompt if message looks like a NEW transfer request, not a correction.
    A new transfer has both an action word AND a recipient indicator.

    Examples:
    - "Send 50k to Jackson" → action ("send") + recipient ("to") → prompt
    - "I meant 50k" → no action, no recipient → don't prompt, let flow update
    - "Use account 0760..." → no action → don't prompt
    """
    message_lower = ctx.message_lower

    # Action words that indicate starting a new transfer
    has_action = any(keyword in message_lower for keyword in ["send", "transfer", "pay", "give"])

    # Recipient indicator - "to" followed by something, or a name/account
    has_recipient = " to " in message_lower

    # Only prompt if clearly a new transfer (action + recipient)
    if not (has_action and has_recipient):
        return False

    has_substantial_data = has_substantial_transfer_data(cast(TransferState, input_state))
    if not has_substantial_data:
        return False

    session_age = await get_transfer_session_age(ctx.phone_number)
    if session_age is None:
        return False

    return session_age < settings.flow_session_timeout


def build_cancellation_prompt(input_state: dict) -> str:
    """Build fallback cancellation prompt (classifier usually handles this via LLM)."""
    amount = input_state.get("amount", 0)
    recipient_name = input_state.get("recipient_name") or input_state.get(
        "recipient_account", "recipient"
    )
    return f"You have a pending ₦{amount:,.0f} transfer to {recipient_name}. Cancel it and start fresh? (Yes/No)"
