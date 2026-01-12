"""Cancellation node for transfer flow."""

from typing import cast

from apps.core.src.agent.graphs.transfer.state import TransferState
from shared.cache.redis_client import Redis
from shared.utils.cancellation import handle_transaction_cancellation

from .utils import debug_log


async def handle_cancellation(
    state: TransferState,
    redis_client: Redis,
) -> TransferState:
    """
    Handle cancellation of transfer in progress.
    Uses shared cancellation utilities for consistency across transaction types.
    """
    debug_log(
        f"🛑 handle_cancellation called: flow_state={state.get('flow_state')}, amount={state.get('amount')}, recipient={state.get('recipient_name')}"
    )
    state_dict = dict(state)
    result = await handle_transaction_cancellation(
        state=state_dict,
        transaction_type="transfer",
        redis_client=redis_client,
    )
    debug_log(f"✓ handle_cancellation completed: response={result.get('response', '')[:50]}...")
    return cast(TransferState, result)
