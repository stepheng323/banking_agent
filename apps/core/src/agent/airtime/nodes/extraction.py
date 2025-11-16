"""Extraction node for airtime purchase flow."""

from apps.core.src.agent.airtime.state import AirtimeState

from ..graph.utils import debug_log


async def extract_entities(state: AirtimeState) -> AirtimeState:
    """Extract entities from user message."""
    # TODO: Implement extraction logic
    debug_log("extract_entities: placeholder")
    return state

