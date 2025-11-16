"""Account selection node for airtime purchase flow."""

from apps.core.src.agent.airtime.state import AirtimeState

from ..graph.utils import debug_log


async def select_source_account(state: AirtimeState) -> AirtimeState:
    """Select source account for airtime purchase."""
    # TODO: Implement account selection logic
    debug_log("select_source_account: placeholder")
    return state

