"""Task planning node for the orchestrator."""

from apps.core.src.agent.orchestrator.state import OrchestratorState


class PlanningNode:
    """Plans tasks using LLM-based planner."""

    def __init__(self, plan_tasks_func):
        """Initialize with task planning function."""
        self._plan_tasks = plan_tasks_func

    async def __call__(self, state: OrchestratorState) -> OrchestratorState:
        """Plan tasks using LLM planner."""
        disambiguated_intent = state.get("disambiguated_intent")
        user_context = state.get("user_context")
        phone_number = state["phone_number"]

        if not disambiguated_intent:
            # Fallback to original message if disambiguation failed
            final_instruction = state["message"]
        else:
            final_instruction = disambiguated_intent.normalized_instruction

        print(f"📋 Planning tasks for: {final_instruction}")

        normalized_instruction, task_plan, primary_intent, planner_notes = await self._plan_tasks(
            phone_number, final_instruction, user_context
        )

        state["normalized_instruction"] = normalized_instruction
        state["task_plan"] = task_plan
        state["primary_intent"] = primary_intent
        state["planner_notes"] = planner_notes

        return state
