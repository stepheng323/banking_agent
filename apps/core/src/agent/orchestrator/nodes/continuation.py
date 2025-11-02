"""Continuation detection node for the orchestrator."""

from typing import Callable
from apps.core.src.agent.conversation_context import ConversationContext
from apps.core.src.agent.orchestrator.state import OrchestratorState


class ContinuationNode:
    """Handles continuation detection for multi-turn conversations."""

    def __init__(self, get_context_func: Callable[[str], ConversationContext]):
        """Initialize with context retrieval function."""
        self._get_context = get_context_func

    async def __call__(self, state: OrchestratorState) -> OrchestratorState:
        """Check if this is a continuation of an existing conversation."""
        phone_number = state["phone_number"]
        context = self._get_context(phone_number)

        print(f"🔍 CONTINUATION CHECK:")
        print(
            f"   context.awaiting_clarification: {context.awaiting_clarification}")
        print(f"   context.active_agent: {context.active_agent}")
        print(f"   context.clarification_type: {context.clarification_type}")

        state["is_continuation"] = context.awaiting_clarification

        if context.awaiting_clarification:
            print(
                f"🔁 Continuation detected. Active agent: {context.active_agent.upper()}")
            state["primary_intent"] = context.active_agent
        else:
            print(f"🆕 New conversation (not a continuation)")

        return state
