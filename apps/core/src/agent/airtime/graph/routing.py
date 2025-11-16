"""Routing logic for airtime purchase flow graph."""

from typing import Literal

from apps.core.src.agent.airtime.state import AirtimeState

from .utils import debug_log


def route_by_state(state: AirtimeState) -> Literal["end", "collect_amount", "collect_phone", "select_account", "validate", "confirm", "cancel"]:
    """Route based on current flow state and missing data."""
    # TODO: Implement routing logic
    debug_log("DEBUG route_by_state: placeholder routing")
    return "end"

