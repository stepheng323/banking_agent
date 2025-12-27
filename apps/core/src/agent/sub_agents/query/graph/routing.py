"""Routing logic for query flow graph."""

from typing import Literal

from apps.core.src.agent.sub_agents.query.graph.state import QueryState


def route_after_parse(state: QueryState) -> Literal["fetch", "error"]:
    """Route after parsing based on state."""
    flow_state = state.get("flow_state", "")
    if flow_state == "error":
        return "error"
    return "fetch"


def route_after_fetch(state: QueryState) -> Literal["aggregate", "format", "error"]:
    """Route after fetching based on query type."""
    flow_state = state.get("flow_state", "")

    if flow_state == "error":
        return "error"

    if flow_state == "formatting":
        return "format"

    return "aggregate"


def route_continuation(state: QueryState) -> Literal["paginate", "refine", "parse"]:
    """Route continuation messages (show more, filter, new query)."""
    continuation_type = state.get("continuation_type")

    if continuation_type == "show_more":
        return "paginate"
    elif continuation_type == "filter":
        return "refine"
    else:
        return "parse"


def detect_continuation_type(
    message: str, session_active: bool
) -> Literal["show_more", "filter", "new_query", None]:
    """
    Detect if message is a continuation or new query.

    Args:
        message: User message
        session_active: Whether there's an active query session

    Returns:
        Continuation type or None for new query
    """
    if not session_active:
        return None

    msg_lower = message.lower().strip()

    show_more_patterns = [
        "show more",
        "more",
        "next",
        "continue",
        "see more",
        "next page",
    ]
    for pattern in show_more_patterns:
        if pattern in msg_lower:
            return "show_more"

    filter_patterns = [
        "filter by",
        "only show",
        "just show",
        "show only",
        "filter",
    ]
    for pattern in filter_patterns:
        if pattern in msg_lower:
            return "filter"

    return "new_query"


def extract_filter_term(message: str) -> str:
    """Extract filter term from message."""
    msg_lower = message.lower()

    patterns = [
        "filter by ",
        "only show ",
        "just show ",
        "show only ",
        "filter ",
    ]

    for pattern in patterns:
        if pattern in msg_lower:
            idx = msg_lower.find(pattern) + len(pattern)
            return message[idx:].strip()

    return message.strip()
