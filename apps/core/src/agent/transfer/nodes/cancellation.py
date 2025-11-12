"""Cancellation node for transfer flow."""

from typing import cast
import redis.asyncio as redis

from apps.core.src.agent.common.cancellation import handle_transaction_cancellation
from apps.core.src.agent.transfer.state import TransferState

from .utils import debug_log


async def handle_cancellation(
    state: TransferState,
    redis_client: redis.Redis,
) -> TransferState:
    """
    Handle cancellation of transfer in progress.
    Uses shared cancellation utilities for consistency across transaction types.
    """
    debug_log(
        f"🛑 handle_cancellation called: flow_state={state.get('flow_state')}, amount={state.get('amount')}, recipient={state.get('recipient_name')}")
    # Convert TransferState to dict for the generic handler
    state_dict = dict(state)
    result = await handle_transaction_cancellation(
        state=state_dict,
        transaction_type="transfer",
        redis_client=redis_client,
    )
    debug_log(
        f"✅ handle_cancellation completed: response={result.get('response', '')[:50]}...")
    return cast(TransferState, result)
