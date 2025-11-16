"""Cancellation node for airtime purchase flow."""

from apps.core.src.agent.airtime.state import AirtimeState

from ..graph.utils import debug_log


async def handle_cancellation(state: AirtimeState) -> AirtimeState:
    """Handle cancellation of airtime purchase."""
    # TODO: Implement cancellation logic
    debug_log("handle_cancellation: placeholder")
    return state

