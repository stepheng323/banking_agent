"""Confirmation node for airtime purchase flow."""

from apps.core.src.agent.airtime.state import AirtimeState

from ..graph.utils import debug_log


async def prepare_confirmation(state: AirtimeState) -> AirtimeState:
    """Prepare confirmation message for airtime purchase."""
    # TODO: Implement confirmation logic
    debug_log("prepare_confirmation: placeholder")
    return state

