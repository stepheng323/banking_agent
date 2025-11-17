"""Flow completion callback implementation for orchestrator."""

from typing import Any

from apps.core.src.agent.services.flow_completion_callback import BaseFlowCompletionCallback
from apps.core.src.agent.services.task_queue_service import TaskQueueService


class OrchestratorFlowCompletionCallback(BaseFlowCompletionCallback):
    """Callback that triggers next task execution when a flow completes."""

    def __init__(
        self,
        task_queue_service: TaskQueueService,
        orchestrator: Any,  # Avoid circular import
    ):
        self.task_queue_service = task_queue_service
        self.orchestrator = orchestrator

    async def on_flow_complete(
        self,
        phone_number: str,
        flow_type: str,
        result: dict[str, Any],
    ) -> None:
        """
        Called when a flow completes. Triggers next task execution if available.

        Args:
            phone_number: User's phone number
            flow_type: Type of flow that completed (transfer, airtime, etc.)
            result: Flow execution result
        """
        has_queue = await self.task_queue_service.has_active_queue(phone_number)
        if has_queue:
            next_task = await self.task_queue_service.get_next_task(phone_number)
            if next_task:
                print(
                    f"✅ Flow {flow_type} completed. Next task ready: {next_task.id} ({next_task.executor})")
            else:
                await self.task_queue_service.clear_task_queue(phone_number)
                print(f"✅ All tasks completed for {phone_number}")
