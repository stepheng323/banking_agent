"""Node to handle pending switch confirmation responses (old/new)."""

from typing import Callable

from apps.core.src.agent.orchestrator.state import OrchestratorState
from apps.core.src.agent.banking.transfer.transfer_agent import TransferAgent


class PendingSwitchResponseNode:
    """Handles user's response to pending switch confirmation (old/new)."""

    def __init__(self, get_transfer_agent: Callable[[], TransferAgent], orchestrator_instance=None):
        self._get_transfer_agent = get_transfer_agent
        self._orchestrator = orchestrator_instance

    async def __call__(self, state: OrchestratorState) -> OrchestratorState:
        phone_number = state["phone_number"]
        user_response = state["message"].lower().strip()
        
        # CRITICAL: Retrieve new_instruction from checkpoint FIRST, then clear
        # This prevents check_continuation from reading stale checkpoint values on next turn
        saved_new_instruction = state.get("new_instruction")
        
        if self._orchestrator:
            try:
                await self._orchestrator._ensure_checkpointer()
                orchestrator_config = {"configurable": {"thread_id": phone_number}}
                orchestrator_checkpoint = await self._orchestrator.graph.aget_state(orchestrator_config)
                if orchestrator_checkpoint and orchestrator_checkpoint.values:
                    orchestrator_values = dict(orchestrator_checkpoint.values)
                    if orchestrator_values.get("clarification_type") == "pending_switch_confirmation":
                        # Retrieve new_instruction BEFORE clearing - try multiple sources
                        if not saved_new_instruction:
                            saved_new_instruction = orchestrator_values.get("new_instruction")
                            if not saved_new_instruction:
                                pending_clar = orchestrator_values.get("pending_clarification", {})
                                if isinstance(pending_clar, dict):
                                    saved_new_instruction = pending_clar.get("new_instruction")
                        
                        print(f"   📋 Retrieved new_instruction: {saved_new_instruction[:50] if saved_new_instruction else 'None'}...")
                        
                        # Clear the clarification flags - but DON'T clear new_instruction yet
                        # We'll clear it after we've processed the response
                        clear_state = {
                            "phone_number": phone_number,
                            "message": "CLEAR_PENDING_SWITCH",
                            "message_id": state.get("message_id", ""),
                            "awaiting_clarification": False,
                            "clarification_type": None,
                            "pending_clarification": None,
                            "new_transfer": False,
                            # Keep new_instruction temporarily so we can use it
                            "new_instruction": saved_new_instruction,
                        }
                        await self._orchestrator.graph.ainvoke(clear_state, orchestrator_config)
                        
                        # Verify it's cleared
                        verify_checkpoint = await self._orchestrator.graph.aget_state(orchestrator_config)
                        if verify_checkpoint and verify_checkpoint.values:
                            still_awaiting = verify_checkpoint.values.get("awaiting_clarification")
                            still_clarification_type = verify_checkpoint.values.get("clarification_type")
                            if still_awaiting or still_clarification_type == "pending_switch_confirmation":
                                print(f"   ⚠️  Warning: Orchestrator checkpoint still shows awaiting={still_awaiting}, type={still_clarification_type}")
                                # Try one more time with a more aggressive clear
                                final_clear = dict(verify_checkpoint.values)
                                final_clear["awaiting_clarification"] = False
                                final_clear["clarification_type"] = None
                                final_clear["pending_clarification"] = None
                                final_clear["new_transfer"] = False
                                final_clear["message"] = "CLEAR_PENDING_SWITCH_FINAL"
                                await self._orchestrator.graph.ainvoke(final_clear, orchestrator_config)
                            else:
                                print("   ✅ Cleared orchestrator clarification flags (verified)")
                        else:
                            print("   ✅ Cleared orchestrator clarification flags")
            except Exception as e:
                print(f"   ⚠️  Failed to clear orchestrator clarification flags: {e}")
                import traceback
                traceback.print_exc()
        
        # Restore new_instruction to state if we saved it
        if saved_new_instruction:
            state["new_instruction"] = saved_new_instruction
            print(f"   ✅ Restored new_instruction to state: {saved_new_instruction[:50]}...")
        
        # Parse the user's choice
        if "old" in user_response:
            print("   ✅ User chose to continue with OLD pending transfer")
            
            # Clear the orchestrator-level clarification since we're continuing with transfer agent
            state["awaiting_clarification"] = False
            state["clarification_type"] = None
            state["pending_clarification"] = None
            state["new_transfer"] = False
            state["new_instruction"] = None
            
            # Directly invoke the transfer agent with a continuation message
            # This will load the checkpoint and continue waiting for PIN
            from apps.core.src.agent.banking.transfer.transfer_router import route_transfer_request
            
            # Invoke transfer agent directly - it will load checkpoint and continue waiting for PIN
            transfer_result = await route_transfer_request(
                phone_number=phone_number,
                message="CONTINUE_PENDING_TRANSFER",  # Signal to continue with pending transfer
                message_id=state.get("message_id", "")
            )
            
            # Set the response from transfer agent
            state["response"] = transfer_result.get("response", "Please use the secure PIN prompt to authorize the transfer.")
            state["awaiting_clarification"] = transfer_result.get("awaiting_clarification", True)
            state["clarification_type"] = transfer_result.get("clarification_type", "pin_confirmation")
            
            # Set active_agent so context is updated
            state["active_agent"] = "transfer"
            
            # Clear any existing task plan since we're directly invoking the transfer agent
            state["task_plan"] = []
            
            return state
        elif "new" in user_response:
            print("   ✅ User chose to switch to NEW transfer")
            
            # Use the saved_new_instruction we retrieved at the start
            new_instruction = saved_new_instruction
            
            if not new_instruction:
                # Fallback: try to get from state
                new_instruction = state.get("new_instruction")
                if not new_instruction:
                    # Last resort: use the original message from when pending_switch was triggered
                    # This should have been stored in the checkpoint
                    print(f"   ⚠️  No new instruction found in saved state, checking checkpoint...")
                    if self._orchestrator:
                        try:
                            await self._orchestrator._ensure_checkpointer()
                            orchestrator_config = {"configurable": {"thread_id": phone_number}}
                            orchestrator_checkpoint = await self._orchestrator.graph.aget_state(orchestrator_config)
                            if orchestrator_checkpoint and orchestrator_checkpoint.values:
                                orchestrator_values = orchestrator_checkpoint.values
                                new_instruction = orchestrator_values.get("new_instruction")
                                if new_instruction:
                                    print(f"   📋 Retrieved new instruction from checkpoint: {new_instruction[:50]}...")
                        except Exception as e:
                            print(f"   ⚠️  Failed to get new instruction from checkpoint: {e}")
            
            # If still no new instruction, we can't proceed - this is an error
            if not new_instruction:
                print(f"   ❌ ERROR: No new instruction found! Cannot process new transfer.")
                state["response"] = "I'm sorry, I couldn't retrieve the transfer details. Please send your transfer request again."
                state["awaiting_clarification"] = False
                state["clarification_type"] = None
                return state
            
            print(f"   ✅ Using new instruction: {new_instruction[:50]}...")
            
            # Clear the old transfer state completely
            agent = self._get_transfer_agent()
            await agent._ensure_checkpointer()
            config = {"configurable": {"thread_id": f"TransferAgent:{phone_number}"}}
            checkpoint = await agent.graph.aget_state(config)
            if checkpoint and checkpoint.values:
                # Create a completely fresh state
                values = {
                    "phone_number": phone_number,
                    "message": "CLEAR_STATE",
                    "message_id": state.get("message_id", ""),
                    "awaiting_clarification": False,
                    "clarification_type": None,
                    "waiting_for_confirmation": False,
                    "pending_clarification": None,
                    "conversation_stage": "gathering",
                    "transfer_details": {},
                    "messages": [],
                }
                await agent.graph.ainvoke(values, config)
                # Verify it's cleared
                verify_checkpoint = await agent.graph.aget_state(config)
                if verify_checkpoint and verify_checkpoint.values:
                    still_awaiting = verify_checkpoint.values.get("awaiting_clarification")
                    if still_awaiting:
                        print(f"   ⚠️  Warning: Transfer checkpoint still shows awaiting_clarification={still_awaiting}")
                    else:
                        print("   ✅ Cleared old transfer state completely (verified)")
                else:
                    print("   ✅ Cleared old transfer state completely")
            
            # Clear orchestrator's clarification flags so it doesn't route back to pending_switch
            if self._orchestrator:
                try:
                    await self._orchestrator._ensure_checkpointer()
                    orchestrator_config = {"configurable": {"thread_id": phone_number}}
                    orchestrator_checkpoint = await self._orchestrator.graph.aget_state(orchestrator_config)
                    if orchestrator_checkpoint and orchestrator_checkpoint.values:
                        orchestrator_values = dict(orchestrator_checkpoint.values)
                        orchestrator_values["awaiting_clarification"] = False
                        orchestrator_values["clarification_type"] = None
                        orchestrator_values["pending_clarification"] = None
                        orchestrator_values["new_transfer"] = False  # Clear this flag too
                        orchestrator_values["new_instruction"] = None  # Clear this too
                        orchestrator_values["message"] = "CLEAR_PENDING_SWITCH"
                        await self._orchestrator.graph.ainvoke(orchestrator_values, orchestrator_config)
                        # Verify it's cleared
                        verify_orch_checkpoint = await self._orchestrator.graph.aget_state(orchestrator_config)
                        if verify_orch_checkpoint and verify_orch_checkpoint.values:
                            still_awaiting = verify_orch_checkpoint.values.get("awaiting_clarification")
                            still_clarification_type = verify_orch_checkpoint.values.get("clarification_type")
                            if still_awaiting or still_clarification_type == "pending_switch_confirmation":
                                print(f"   ⚠️  Warning: Orchestrator checkpoint still shows awaiting={still_awaiting}, type={still_clarification_type}")
                            else:
                                print("   ✅ Cleared orchestrator clarification flags (verified)")
                        else:
                            print("   ✅ Cleared orchestrator clarification flags")
                except Exception as e:
                    print(f"   ⚠️  Failed to clear orchestrator clarification flags: {e}")
                    import traceback
                    traceback.print_exc()
            
            # Set the new instruction as the message to process
            state["message"] = new_instruction
            state["awaiting_clarification"] = False
            state["clarification_type"] = None
            state["pending_clarification"] = None
            state["new_instruction"] = None  # Clear this too
            state["new_transfer"] = False  # Clear this flag
            # Route to normal flow to process the new instruction
            return state
        else:
            # Unclear response, ask again
            print("   ⚠️  Unclear response, asking again")
            state["response"] = (
                "I didn't understand. Do you want to continue with the OLD pending transfer "
                "or switch to the NEW instruction?\n"
                "Reply 'old' to continue, or 'new' to switch."
            )
            state["awaiting_clarification"] = True
            state["clarification_type"] = "pending_switch_confirmation"
            return state

