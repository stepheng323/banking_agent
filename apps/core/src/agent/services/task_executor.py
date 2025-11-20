"""Task execution engine for multi-task execution."""

from typing import Optional, Dict, Any, TYPE_CHECKING, Union

from apps.core.src.agent.models.planner import PlannedTask
from apps.core.src.agent.services.task_queue_service import TaskQueueService
from apps.core.src.agent.services.flow_completion_callback import FlowCompletionCallback
from shared.types.agent_types import TaskStatus

if TYPE_CHECKING:
    from apps.core.src.agent.transfer import TransferService
    from apps.core.src.agent.airtime import AirtimeService


class TaskExecutor:
    """Task execution engine for executing planned tasks."""

    def __init__(
        self,
        transfer_service: "TransferService",
        airtime_service: "AirtimeService",
        task_queue_service: TaskQueueService,
        completion_callback: Optional[FlowCompletionCallback] = None,
    ):
        self.transfer_service = transfer_service
        self.airtime_service = airtime_service
        self.task_queue_service = task_queue_service
        self.completion_callback = completion_callback

    async def execute_task(
        self, phone_number: str, task: PlannedTask, user_message: str
    ) -> Dict[str, Any]:
        """
        Execute a single task.

        Args:
            phone_number: User's phone number
            task: Task to execute
            user_message: Original user message

        Returns:
            Task execution result
        """
        await self.task_queue_service.update_task_status(
            phone_number, task.id, TaskStatus.IN_PROGRESS
        )
        await self.task_queue_service.set_current_task(phone_number, task.id)

        try:
            executor_service = self.get_executor_for_task(task)
            if not executor_service:
                raise ValueError(f"Unknown executor: {task.executor}")

            if task.executor == "transfer":
                result = await executor_service.run_simple(
                    phone_number, user_message, None
                )
            elif task.executor == "airtime":
                result = await executor_service.run_simple(
                    phone_number, user_message, None
                )
            elif task.executor == "query":
                result = "Query executor not yet implemented"
            elif task.executor == "utility":
                result = "Utility executor not yet implemented"
            else:
                result = f"Executor {task.executor} not supported"

            await self.task_queue_service.update_task_status(
                phone_number, task.id, TaskStatus.COMPLETED, {"result": result}
            )

            if self.completion_callback:
                await self.completion_callback.on_flow_complete(
                    phone_number, task.executor, {"status": "completed", "result": result}
                )

            return {"status": "completed", "result": result}

        except Exception as e:
            await self.task_queue_service.update_task_status(
                phone_number, task.id, TaskStatus.FAILED, {"error": str(e)}
            )
            raise

        finally:
            await self.task_queue_service.set_current_task(phone_number, None)

    def can_execute_task(
        self, task: PlannedTask, completed_task_ids: list[str]
    ) -> bool:
        """
        Check if task dependencies are satisfied.

        Args:
            task: Task to check
            completed_task_ids: List of completed task IDs

        Returns:
            True if task can be executed, False otherwise
        """
        return all(dep_id in completed_task_ids for dep_id in task.depends_on)

    def get_executor_for_task(
        self, task: PlannedTask
    ) -> Optional[Any]:
        """
        Map task executor to service instance.

        Args:
            task: Task to get executor for

        Returns:
            Service instance or None if not found
        """
        if task.executor == "transfer":
            return self.transfer_service
        elif task.executor == "airtime":
            return self.airtime_service
        elif task.executor == "data":
            return None
        elif task.executor == "query":
            return None
        elif task.executor == "utility":
            return None
        return None

