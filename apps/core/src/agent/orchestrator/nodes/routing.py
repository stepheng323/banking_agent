"""Routing functions for the orchestrator graph."""

from langgraph.graph import END

from apps.core.src.agent.orchestrator.state import OrchestratorState


def route_after_continuation_check(state: OrchestratorState) -> str:
    """Route based on whether this is a continuation."""
    if state.get("is_continuation"):
        return "task_executor"
    return "quick_classify"


def route_after_quick_classification(state: OrchestratorState) -> str:
    """Route based on quick intent classification result."""
    quick_intent = state.get("quick_classification")

    if quick_intent == "conversational":
        return "conversational"

    return "load_context"


def _needs_disambiguation(message: str) -> bool:
    """
    Check if message needs disambiguation (multi-recipient or ambiguous patterns).

    Returns True if:
    - Multiple recipients mentioned ("and", "also", comma-separated names)
    - Ambiguous split patterns ("send X to A and B" without explicit split keyword)
    """
    message_lower = message.lower()

    multi_recipient_patterns = [
        " and ", 
        ", and ", 
        " also ",
        ", ",
    ]

    has_multiple_to = message_lower.count(" to ") >= 2

    has_multi_pattern = any(
        pattern in message_lower for pattern in multi_recipient_patterns)

    split_keywords = ["equally", "split", "divide", "each", "per person"]
    has_split_keyword = any(
        keyword in message_lower for keyword in split_keywords)

    needs_disambiguation = (
        has_multi_pattern or has_multiple_to) and not has_split_keyword

    simple_transfer_phrases = [
        "i would like to send",
        "i want to send",
        "send funds",
        "transfer money",
        "send money",
        "make a transfer",
        "send some money",
    ]
    is_simple_transfer = any(
        phrase in message_lower for phrase in simple_transfer_phrases)

    if is_simple_transfer and not has_multi_pattern:
        return False

    return needs_disambiguation


def route_after_planning(state: OrchestratorState) -> str:
    """Route based on primary intent."""
    primary_intent = state.get("primary_intent")
    task_plan = state.get("task_plan", [])

    if primary_intent == "conversational" and not task_plan:
        return "conversational"

    if task_plan:
        return "task_executor"

    return END


def route_after_execution(state: OrchestratorState) -> str:
    """Route based on execution result."""
    if state.get("awaiting_clarification"):
        return END
    return "response_formatter"
