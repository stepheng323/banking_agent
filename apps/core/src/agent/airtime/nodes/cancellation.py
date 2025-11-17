"""Cancellation node for airtime purchase flow."""

from typing import cast

from shared.cache.redis_client import RedisClient
from apps.core.src.agent.common.cancellation import handle_transaction_cancellation
from apps.core.src.agent.airtime.state import AirtimeState

from ..graph.utils import debug_log


async def handle_cancellation(
    state: AirtimeState,
    redis_client: RedisClient,
) -> AirtimeState:
    """
    Handle cancellation of airtime purchase in progress.
    Uses shared cancellation utilities for consistency across transaction types.
    """
    debug_log(
        f"🛑 handle_cancellation called: flow_state={state.get('flow_state')}, amount={state.get('amount')}, recipient_phone={state.get('recipient_phone')}")
    state_dict = dict(state)
    result = await handle_transaction_cancellation(
        state=state_dict,
        transaction_type="airtime",
        redis_client=redis_client,
    )
    debug_log(
        f"✅ handle_cancellation completed: response={result.get('response', '')[:50]}...")
    return cast(AirtimeState, result)
