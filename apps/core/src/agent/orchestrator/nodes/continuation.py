"""Continuation detection node for the orchestrator."""

from typing import Callable
from apps.core.src.agent.conversation_context import ConversationContext
from apps.core.src.agent.orchestrator.state import OrchestratorState


class ContinuationNode:
    """Handles continuation detection for multi-turn conversations."""

    def __init__(self, get_context_func: Callable[[str], ConversationContext], orchestrator_instance=None):
        """Initialize with context retrieval function and orchestrator instance."""
        self._get_context = get_context_func
        self._orchestrator = orchestrator_instance

    async def _check_orchestrator_checkpoint(self, phone_number: str) -> tuple[bool, str | None]:
        """Check orchestrator checkpoint for pending clarifications."""
        if not self._orchestrator:
            return False, None
        
        try:
            await self._orchestrator._ensure_checkpointer()
            config = {"configurable": {"thread_id": phone_number}}
            checkpoint = await self._orchestrator.graph.aget_state(config)
            if checkpoint and checkpoint.values:
                values = checkpoint.values
                awaiting = values.get("awaiting_clarification")
                clarification_type = values.get("clarification_type")
                if awaiting and clarification_type:
                    return True, clarification_type
        except Exception as exc:
            print(f"   ⚠️  Failed to check orchestrator checkpoint: {exc}")
        return False, None

    async def __call__(self, state: OrchestratorState) -> OrchestratorState:
        """Check if this is a continuation of an existing conversation."""
        phone_number = state["phone_number"]
        context = self._get_context(phone_number)

        print(f"🔍 CONTINUATION CHECK:")
        print(
            f"   context.awaiting_clarification: {context.awaiting_clarification}")
        print(f"   context.active_agent: {context.active_agent}")
        print(f"   context.clarification_type: {context.clarification_type}")

        # Check orchestrator checkpoint for pending_switch_confirmation or other orchestrator-level clarifications
        orchestrator_awaiting, orchestrator_clarification_type = await self._check_orchestrator_checkpoint(phone_number)
        
        # If orchestrator has pending clarification, treat as continuation
        if orchestrator_awaiting:
            print(f"   🔍 Orchestrator checkpoint shows awaiting_clarification: True, type: {orchestrator_clarification_type}")
            
            # CRITICAL: Synchronize context with orchestrator checkpoint
            # This ensures TaskExecutor can properly reset completed tasks
            # Set active_agent first (if needed), then set awaiting_clarification
            # (switch_agent clears awaiting_clarification when switching, so we set it after)
            if orchestrator_clarification_type == "pin_confirmation":
                context.switch_agent("transfer")
                state["primary_intent"] = "transfer"
            elif orchestrator_clarification_type == "pending_switch_confirmation":
                # Keep current agent but ensure context is updated
                state["primary_intent"] = context.active_agent
            
            # Now set awaiting_clarification after agent switch
            context.set_awaiting_clarification(orchestrator_clarification_type)
            
            state["is_continuation"] = True
            state["clarification_type"] = orchestrator_clarification_type
            state["awaiting_clarification"] = True
            print(f"🔁 Continuation detected (orchestrator-level clarification)")
            print(f"   ✅ Context synced: awaiting={context.awaiting_clarification}, agent={context.active_agent}, type={context.clarification_type}")
            return state

        # Fall back to context-based continuation check
        state["is_continuation"] = context.awaiting_clarification

        if context.awaiting_clarification:
            print(
                f"🔁 Continuation detected. Active agent: {context.active_agent.upper()}")
            state["primary_intent"] = context.active_agent
        else:
            print(f"🆕 New conversation (not a continuation)")

        return state
