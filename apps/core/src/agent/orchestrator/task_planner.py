"""Task planning and execution for the orchestrator."""

from typing import Optional
import traceback

from langchain_core.runnables import Runnable
from apps.core.src.agent.models.planner import PlannerOutput
from apps.core.src.agent.services.task_queue_service import TaskQueueService
from apps.core.src.agent.services.task_executor import TaskExecutor
from apps.core.src.agent.prompts.planner import PLANNER_SYSTEM_PROMPT, PLANNER_USER_PROMPT_TEMPLATE


class OrchestratorTaskPlanner:
    """Handles task planning and execution for multi-step requests."""

    def __init__(
        self,
        planner_llm: Runnable,
        task_queue_service: TaskQueueService,
        task_executor: TaskExecutor,
    ) -> None:
        self.planner_llm = planner_llm
        self.task_queue_service = task_queue_service
        self.task_executor = task_executor

    async def plan_tasks(self, phone_number: str, text: str) -> PlannerOutput:
        """
        Use planner to break down request into tasks.

        Args:
            phone_number: User's phone number
            text: User's message

        Returns:
            PlannerOutput with planned tasks
        """
        user_prompt = PLANNER_USER_PROMPT_TEMPLATE.format(
            phone_number=phone_number, user_message=text
        )
        result = await self.planner_llm.ainvoke(
            [
                {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ]
        )
        if isinstance(result, PlannerOutput):
            return result
        return PlannerOutput.model_validate(result)

    async def handle_next_task(self, phone_number: str, text: str) -> Optional[str]:
        """
        Handle next task in queue if available.

        Args:
            phone_number: User's phone number
            text: User's message

        Returns:
            Response string if task executed, None if no task available
        """
        next_task = await self.task_queue_service.get_next_task(phone_number)
        if next_task:
            try:
                result = await self.task_executor.execute_task(
                    phone_number, next_task, text
                )
                result_value = result.get("result", "Task executed")
                return str(result_value) if result_value is not None else "Task executed"
            except Exception as e:
                print(f"⚠️  Error executing task: {e}")
                traceback.print_exc()
                return f"Error executing task: {str(e)}"
        return None

