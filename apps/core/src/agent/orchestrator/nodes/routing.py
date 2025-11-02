"""Routing functions for the orchestrator graph."""

from langgraph.graph import END

from apps.core.src.agent.orchestrator.state import OrchestratorState


def route_after_continuation_check(state: OrchestratorState) -> str:
    """Route based on whether this is a continuation."""
    if state.get("is_continuation"):
        # Skip normalization for continuations, go straight to task execution
        return "task_executor"
    # New messages go through quick classification first
    return "quick_classify"


def route_after_quick_classification(state: OrchestratorState) -> str:
    """Route based on quick intent classification result."""
    quick_intent = state.get("quick_classification")

    # If detected as conversational, skip all normalization and go straight to response
    if quick_intent == "conversational":
        return "conversational"

    # For banking intents, continue with normalization pipeline
    return "load_context"


def _needs_disambiguation(message: str) -> bool:
    """
    Check if message needs disambiguation (multi-recipient or ambiguous patterns).

    Returns True if:
    - Multiple recipients mentioned ("and", "also", comma-separated names)
    - Ambiguous split patterns ("send X to A and B" without explicit split keyword)
    """
    message_lower = message.lower()

    # Multi-recipient indicators
    multi_recipient_patterns = [
        " and ",  # "send to A and B"
        ", and ",  # "send to A, and B"
        " also ",  # "send to A, also send to B"
        # "send to A, B, C" (multiple commas suggest multiple recipients)
        ", ",
    ]

    # Count potential recipients (multiple "to" clauses suggest multiple recipients)
    has_multiple_to = message_lower.count(" to ") >= 2

    # Check for multi-recipient patterns
    has_multi_pattern = any(
        pattern in message_lower for pattern in multi_recipient_patterns)

    # Check for ambiguous split (multiple recipients with single amount, no split keyword)
    split_keywords = ["equally", "split", "divide", "each", "per person"]
    has_split_keyword = any(
        keyword in message_lower for keyword in split_keywords)

    # If it has multiple recipients but no explicit split keyword, it's ambiguous
    needs_disambiguation = (
        has_multi_pattern or has_multiple_to) and not has_split_keyword

    # Simple transfer expressions don't need disambiguation
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
        return False  # Simple transfer, skip disambiguation

    return needs_disambiguation


def route_after_typo_correction(state: OrchestratorState) -> str:
    """Route based on typo correction result."""
    if state.get("awaiting_clarification"):
        return END

    # Check if disambiguation is needed (only for multi-recipient/ambiguous cases)
    corrected_intent = state.get("corrected_intent")
    if corrected_intent:
        message = corrected_intent.corrected or state.get("message", "")
        if not _needs_disambiguation(message):
            # Skip disambiguation for simple transfers, go straight to planning
            print("⏩ Skipping disambiguation (simple transfer)")
            return "planner"

    return "disambiguation"


def route_after_disambiguation(state: OrchestratorState) -> str:
    """Route based on disambiguation result."""
    if state.get("awaiting_clarification"):
        return END
    return "planner"


def route_after_planning(state: OrchestratorState) -> str:
    """Route based on primary intent."""
    primary_intent = state.get("primary_intent")
    task_plan = state.get("task_plan", [])

    # Handle conversational intent (no tasks)
    if primary_intent == "conversational" and not task_plan:
        return "conversational"

    # Handle banking tasks
    if task_plan:
        return "task_executor"

    # No tasks and not conversational - error state
    return END


def route_after_execution(state: OrchestratorState) -> str:
    """Route based on execution result."""
    if state.get("awaiting_clarification"):
        return END
    return "response_formatter"
