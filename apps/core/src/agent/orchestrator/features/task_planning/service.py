"""Task planning and execution for the orchestrator."""

import json
import re
from typing import Optional
import traceback

from langchain_core.runnables import Runnable
from langchain_core.messages import AIMessage
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
        
        # Handle different return types from LLM
        if isinstance(result, PlannerOutput):
            return result
        
        # Extract content from AIMessage if needed
        if isinstance(result, AIMessage):
            content = result.content
        else:
            content = result
        
        # Parse JSON string if needed
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except json.JSONDecodeError:
                # If it's not valid JSON, try to extract JSON from the string
                # Some LLMs return JSON wrapped in markdown code blocks
                json_match = re.search(r'\{[^{}]*\}', content, re.DOTALL)
                if json_match:
                    content = json.loads(json_match.group())
                else:
                    raise ValueError(f"Could not parse JSON from LLM response: {content}")
        
        return PlannerOutput.model_validate(content)

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

