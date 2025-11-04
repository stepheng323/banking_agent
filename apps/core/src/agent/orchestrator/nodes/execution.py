"""Task execution and response formatting nodes."""

from apps.core.src.agent.orchestrator.state import OrchestratorState


class TaskExecutorNode:
    """Executes planned tasks by routing to specialized agents."""

    def __init__(self, execute_task_plan_func, get_context_func, get_orchestrator_func=None):
        """Initialize with task execution and context functions."""
        self._execute_task_plan = execute_task_plan_func
        self._get_context = get_context_func
        # Optional: to restore task_plan from checkpoint
        self._get_orchestrator = get_orchestrator_func

    async def __call__(self, state: OrchestratorState) -> OrchestratorState:
        """Execute planned tasks by routing to specialized agents."""
        task_plan = state.get("task_plan", [])
        phone_number = state["phone_number"]
        message_id = state["message_id"]
        user_message = state["message"]  # Get actual user message
        context = self._get_context(phone_number)

        print(f"🎯 TASK EXECUTOR NODE CALLED:")
        print(f"   User message: {user_message[:50]}...")
        print(f"   Task plan: {len(task_plan)} task(s)")
        print(
            f"   Context awaiting_clarification: {context.awaiting_clarification}")
        print(f"   Context active_agent: {context.active_agent}")

        # If task_plan is empty but we're continuing, try to restore from checkpoint
        if not task_plan and context.awaiting_clarification and self._get_orchestrator:
            try:
                # Ensure the orchestrator's checkpointer is initialized
                orchestrator = self._get_orchestrator()
                await orchestrator._ensure_checkpointer()

                config = {"configurable": {"thread_id": phone_number}}
                existing_state = await orchestrator.graph.aget_state(config)
                if existing_state and existing_state.values:
                    checkpointed_plan = existing_state.values.get("task_plan")
                    if checkpointed_plan:
                        task_plan = checkpointed_plan
                        state["task_plan"] = task_plan
                        print(
                            f"📋 Restored task_plan from checkpoint: {len(task_plan)} task(s)")
            except Exception as e:
                print(f"⚠️  Could not restore task_plan from checkpoint: {e}")

        # If still no task_plan but continuing, create a minimal task for the active agent
        if not task_plan and context.awaiting_clarification:
            active_agent = context.active_agent
            continuation_task = {
                "id": f"{active_agent}_continuation",
                "action": "continue_conversation",
                "executor": active_agent,
                "instruction": user_message,  # Use actual user message
                "description": f"Continue {active_agent} conversation with user's response",
                "parameters": {},
                "depends_on": [],
                "condition": None,
                "status": "pending"
            }
            task_plan = [continuation_task]  # type: ignore[list-item]
            state["task_plan"] = task_plan  # type: ignore[assignment]
            print(f"📋 Created continuation task for {active_agent} agent")

        if not task_plan:
            state["response"] = "I'm sorry, I couldn't figure out the best steps for that request yet. Could you rephrase or add a bit more detail?"
            return state

        print(f"⚙️  Executing {len(task_plan)} task(s)...")

        _, task_results = await self._execute_task_plan(
            phone_number=phone_number,
            message_id=message_id,
            tasks=task_plan,
            context=context,
            user_message=user_message,  # Pass user's actual message for continuations
        )

        state["task_results"] = task_results

        # Always set response from last task result (for both clarification and completion cases)
        if task_results:
            last_result = task_results[-1]
            response_text = last_result.get("response", "")
            
            # Set response if available (for both clarification and completion cases)
            if response_text:
                state["response"] = response_text
            
            # Set awaiting_clarification flag based on task result
            if last_result.get("awaiting_clarification"):
                state["awaiting_clarification"] = True
                state["clarification_type"] = last_result.get("clarification_type")
            else:
                # Clear flags if not awaiting clarification (e.g., on cancel/completion)
                state["awaiting_clarification"] = False
                state["clarification_type"] = None

        return state


class ResponseFormatterNode:
    """Formats final response using LLM."""

    def __init__(self, format_response_func):
        """Initialize with response formatting function."""
        self._format_response = format_response_func

    async def __call__(self, state: OrchestratorState) -> OrchestratorState:
        """Format final response using LLM."""
        task_results = state.get("task_results", [])
        normalized_instruction = state.get("normalized_instruction", "")
        planner_notes = state.get("planner_notes")
        phone_number = state["phone_number"]
        original_message = state["message"]

        print("📝 Formatting final response...")

        final_response = await self._format_response(
            phone_number=phone_number,
            original_message=original_message,
            normalized_instruction=normalized_instruction,
            task_results=task_results,
            planner_notes=planner_notes,
        )

        state["response"] = final_response or "I'm sorry, I couldn't process your request."
        return state
