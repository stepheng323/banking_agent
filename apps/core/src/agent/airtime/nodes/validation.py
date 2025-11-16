"""Validation node for airtime purchase flow."""

from apps.core.src.agent.airtime.state import AirtimeState

from ..graph.utils import debug_log


async def validate_amount(state: AirtimeState) -> AirtimeState:
    """Validate that amount is present."""
    # TODO: Implement validation logic
    debug_log("validate_amount: placeholder")
    return state


async def validate_phone(state: AirtimeState) -> AirtimeState:
    """Validate that phone number is present and valid."""
    # TODO: Implement validation logic
    debug_log("validate_phone: placeholder")
    return state


async def validate_network(state: AirtimeState) -> AirtimeState:
    """Validate that network is present."""
    # TODO: Implement validation logic
    debug_log("validate_network: placeholder")
    return state

