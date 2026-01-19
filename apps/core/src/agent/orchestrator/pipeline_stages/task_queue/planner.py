"""Task planning and execution for the orchestrator."""

import traceback

from langchain_openai import ChatOpenAI

from apps.core.src.agent.orchestrator.prompts.planning import (
    PLANNER_SYSTEM_PROMPT,
    PLANNER_USER_PROMPT_TEMPLATE,
)
from shared.services.task_queue import TaskQueueService
from shared.types.planner import PlannerOutput
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class OrchestratorTaskPlanner:
    """Handles task planning for multi-step requests."""

    def __init__(
        self,
        planner_llm: ChatOpenAI,
        task_queue_service: TaskQueueService,
    ) -> None:
        self.planner_llm = planner_llm
        self.structured_planner = planner_llm.with_structured_output(PlannerOutput)
        self.task_queue_service = task_queue_service

    async def plan_tasks(self, phone_number: str, text: str, context: str = "None") -> PlannerOutput:
        """
        Use planner to break down request into tasks.

        Args:
            phone_number: User's phone number
            text: User's message
            context: Current flow state/context summary

        Returns:
            PlannerOutput with planned tasks
        """
        user_prompt = PLANNER_USER_PROMPT_TEMPLATE.format(
            phone_number=phone_number, user_message=text, context=context
        )

        result = await self.structured_planner.ainvoke(
            [
                {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ]
        )

        if isinstance(result, PlannerOutput):
            return result
        return PlannerOutput.model_validate(result)

