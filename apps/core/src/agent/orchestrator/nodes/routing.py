"""Routing functions for the orchestrator graph."""

from langgraph.graph import END

from apps.core.src.agent.orchestrator.state import OrchestratorState


def route_after_continuation_check(state: OrchestratorState) -> str:
    """Route based on whether this is a continuation."""
    # CRITICAL: Check for pending_switch_confirmation FIRST
    # This must be handled before any other routing logic
    clarification_type = state.get("clarification_type")
    if clarification_type == "pending_switch_confirmation":
        print("   ✅ Pending switch confirmation detected, routing to handle_pending_switch_response")
        return "handle_pending_switch_response"
    
    # Only route to cancel if:
    # 1. The message contains cancel keywords, OR
    # 2. It's a continuation AND global_cancel is set AND message doesn't contain transfer keywords
    # This prevents stale global_cancel flags from persisting across turns
    message = state.get("message", "").lower()
    cancel_keywords = ["cancel", "abort", "stop", "nevermind", "never mind", "quit", "disregard"]
    has_cancel_keyword = any(kw in message for kw in cancel_keywords)
    is_continuation = state.get("is_continuation", False)
    primary_intent = state.get("primary_intent")
    
    # Check for transfer keywords - if present, don't route to cancel (likely a new transfer)
    transfer_keywords = ["send", "transfer", "pay"]
    has_transfer_keyword = any(kw in message for kw in transfer_keywords)
    
    # CRITICAL: Check for cancel keywords FIRST before routing to task_executor
    # This ensures cancel messages during PIN confirmation are handled properly
    if is_continuation and has_cancel_keyword:
        print("   🛑 Cancel keyword detected in continuation, routing to handle_global_cancel node")
        state["global_cancel"] = True  # Set flag so cancel handler knows to process it
        return "handle_global_cancel"
    
    # Only route to cancel if:
    # OR if it's a continuation and global_cancel is set and message doesn't contain transfer keywords
    if state.get("global_cancel"):
        if has_cancel_keyword:
            print("   🛑 global_cancel detected + cancel keyword in message, routing to handle_global_cancel node")
            return "handle_global_cancel"
        elif has_transfer_keyword:
            print("   🔄 Transfer keywords detected, ignoring stale global_cancel flag, routing to quick_classify")
            # Don't route to cancel - let quick_classify handle it
        elif is_continuation:
            print("   🛑 global_cancel detected in continuation, routing to handle_global_cancel node")
            return "handle_global_cancel"
    
    # If it's a continuation with transfer agent, route directly to task_executor
    # This avoids re-classifying responses to transfer agent clarifications
    if is_continuation and primary_intent == "transfer":
        print("   ✅ Transfer agent continuation detected, routing directly to task_executor")
        return "task_executor"
    
    # Even on continuation, run quick_classify first so we can detect
    # global_cancel/new_transfer and short-circuit appropriately.
    return "quick_classify"


def route_after_quick_classification(state: OrchestratorState) -> str:
    """Route based on quick intent classification result."""
    # Check if we're responding to a pending_switch_confirmation
    clarification_type = state.get("clarification_type")
    if clarification_type == "pending_switch_confirmation":
        # Route to transfer agent to handle the "old" or "new" response
        return "handle_pending_switch_response"
    
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
    # Safety check: if global_cancel was detected, don't execute tasks
    if state.get("global_cancel"):
        return END
    
    primary_intent = state.get("primary_intent")
    task_plan = state.get("task_plan", [])

    if primary_intent == "conversational" and not task_plan:
        return "conversational"

    if task_plan:
        return "task_executor"

    return END


def route_after_execution(state: OrchestratorState) -> str:
    """Route based on execution result."""
    # Safety check: if global_cancel was detected, don't continue
    if state.get("global_cancel"):
        return END
    
    if state.get("awaiting_clarification"):
        return END
    return "response_formatter"
