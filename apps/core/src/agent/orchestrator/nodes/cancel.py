"""Global cancel node to stop any pending transfer and reply immediately.

LangGraph Checkpoints:
- Store conversation state in PostgreSQL (checkpoints table)
- Persist state across turns via thread_id
- To clear: invoke graph with cleared state (empty dicts, flags False)
- Each agent has its own thread_id namespace (e.g., TransferAgent:{phone})

When cancel is detected:
1. Clear transfer agent checkpoint completely (empty transfer_details, all flags False)
2. Clear orchestrator checkpoint (clear all clarification flags)
3. Clear ConversationContext (in-memory)
4. Return cancel response immediately - NO routing, NO task execution
"""

from typing import Callable

from apps.core.src.agent.orchestrator.state import OrchestratorState
from apps.core.src.agent.banking.transfer.transfer_agent import TransferAgent


class GlobalCancelNode:
    """Clears ALL transfer state and checkpoints immediately when cancel is detected."""

    def __init__(self, get_transfer_agent: Callable[[], TransferAgent], orchestrator_instance=None, get_context_func=None):
        self._get_transfer_agent = get_transfer_agent
        self._orchestrator = orchestrator_instance
        self._get_context = get_context_func

    async def __call__(self, state: OrchestratorState) -> OrchestratorState:
        phone_number = state["phone_number"]
        print(
            f"🛑 CANCEL DETECTED: Clearing ALL transfer state and checkpoints for {phone_number}")

        # STEP 1: Clear Transfer Agent Checkpoint
        # LangGraph checkpoint is cleared by invoking with cleared state
        try:
            agent = self._get_transfer_agent()
            await agent._ensure_checkpointer()
            transfer_config = {"configurable": {
                "thread_id": f"TransferAgent:{phone_number}"}}

            # Create completely cleared state for transfer agent
            cleared_transfer_state = {
                "phone_number": phone_number,
                "message": "CLEAR_STATE",
                "message_id": state.get("message_id", ""),
                "transfer_details": {},  # Empty - clears all transfer data
                "awaiting_clarification": False,
                "clarification_type": None,
                "waiting_for_confirmation": False,
                "pending_clarification": None,
                "conversation_stage": None,
                "missing_slots": [],
                "messages": [],
            }
            await agent.graph.ainvoke(cleared_transfer_state, transfer_config)
            print("   ✅ Transfer agent checkpoint CLEARED completely")
        except Exception as e:
            print(f"   ⚠️  Failed to clear transfer checkpoint: {e}")

        # STEP 2: Clear Orchestrator Checkpoint
        if self._orchestrator:
            try:
                await self._orchestrator._ensure_checkpointer()
                orchestrator_config = {
                    "configurable": {"thread_id": phone_number}}

                # Create completely cleared state for orchestrator
                cleared_orchestrator_state = {
                    "phone_number": phone_number,
                    "message": "CLEAR_STATE",
                    "message_id": state.get("message_id", ""),
                    "awaiting_clarification": False,
                    "clarification_type": None,
                    "pending_clarification": None,
                    "global_cancel": False,
                    "task_plan": [],
                    "task_results": [],
                }
                if self._orchestrator.graph:
                    await self._orchestrator.graph.ainvoke(cleared_orchestrator_state, orchestrator_config)
                print("   ✅ Orchestrator checkpoint CLEARED completely")
            except Exception as e:
                print(f"   ⚠️  Failed to clear orchestrator checkpoint: {e}")

        # STEP 3: Clear ConversationContext (in-memory)
        if self._get_context:
            try:
                context = self._get_context(phone_number)
                context.clear_awaiting_clarification()
                context.switch_agent("query")  # Reset to default agent
                print("   ✅ ConversationContext CLEARED")
            except Exception as e:
                print(f"   ⚠️  Failed to clear ConversationContext: {e}")

        # STEP 4: Set response and clear all flags in current state
        state["response"] = "Transfer cancelled. Is there anything else I can help you with?"
        state["awaiting_clarification"] = False
        state["clarification_type"] = None
        state["global_cancel"] = False
        state["task_plan"] = []  # Clear any pending tasks
        state["task_results"] = []  # Clear any task results

        print(f"   ✅ Cancel response set: {state['response']}")
        print(
            f"   🎯 ALL STATE CLEARED - Returning cancel response immediately (NO routing)")
        return state
