"""Route functions for query flow graph."""

from apps.core.src.agent.sub_agents.query.graph.state import QueryState


def route_after_parse(state: QueryState) -> str:
    """Route after parsing based on state."""
    flow_state = state.get("flow_state", "")
    if flow_state == "error":
        return "error"
    if flow_state == "clarification_needed":
        return "end"
    return "execute"


def route_after_execute(state: QueryState) -> str:
    """Route after execution based on state."""
    flow_state = state.get("flow_state", "")
    if flow_state == "error":
        return "error"
    return "format"
