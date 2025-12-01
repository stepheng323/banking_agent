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
            if not executor_service and task.executor not in ("query", "utility"):
                raise ValueError(f"Unknown executor: {task.executor}")

            # CRITICAL FIX: Use user's actual message if it contains account details or bank info
            # This handles cases where user is providing account details for an existing task
            # Only use constructed task message when starting a new task (user_message is empty or generic)
            use_user_message = False
            if user_message and user_message.strip():
                # Check if user message contains account details (numbers, bank names)
                import re
                has_account_number = bool(re.search(r'\b\d{10}\b', user_message))
                bank_keywords = ['bank', 'access', 'uba', 'gtb', 'zenith', 'first', 'opay', 'palmpay', 'kuda']
                has_bank_name = any(keyword in user_message.lower() for keyword in bank_keywords)
                
                # If user message has account details, use it directly (user is providing info)
                if has_account_number or has_bank_name:
                    use_user_message = True
                    print(f"🔍 [TASK_EXECUTOR] User message contains account details, using user message: '{user_message}'")
            
            if use_user_message:
                message_to_use = user_message
            else:
                # Construct task-specific message from parameters for new task
                message_to_use = self._construct_task_message(task, user_message)
                print(f"🔍 [TASK_EXECUTOR] Using constructed task message: '{message_to_use}'")
                
                # CRITICAL: When starting a NEW task (not continuing with user-provided details),
                # clear the checkpoint to remove stale collection_complete status from previous task
                if task.executor == "transfer" and hasattr(executor_service, 'clear_checkpoint'):
                    try:
                        await executor_service.clear_checkpoint(phone_number)
                        print(f"🧹 [TASK_EXECUTOR] Cleared transfer checkpoint for new task {task.id}")
                    except Exception as e:
                        print(f"⚠️  [TASK_EXECUTOR] Error clearing checkpoint: {e}")
            
            # Create classification_result with task parameters for transfer/airtime flows
            classification_result = None
            if task.executor in ("transfer", "airtime") and task.parameters:
                classification_result = {
                    "intent": task.executor,
                    "task_parameters": task.parameters,  # Pass task parameters
                }

            if task.executor == "transfer":
                result = await executor_service.run_simple(
                    phone_number, message_to_use, classification_result
                )
            elif task.executor == "airtime":
                result = await executor_service.run_simple(
                    phone_number, message_to_use, classification_result
                )
            elif task.executor == "query":
                result = "Query executor not yet implemented"
                # For non-async executors, mark as completed immediately
                await self.task_queue_service.update_task_status(
                    phone_number, task.id, TaskStatus.COMPLETED, {"result": result}
                )
                await self.task_queue_service.set_current_task(phone_number, None)
            elif task.executor == "utility":
                result = "Utility executor not yet implemented"
                await self.task_queue_service.update_task_status(
                    phone_number, task.id, TaskStatus.COMPLETED, {"result": result}
                )
                await self.task_queue_service.set_current_task(phone_number, None)
            else:
                result = f"Executor {task.executor} not supported"
                await self.task_queue_service.update_task_status(
                    phone_number, task.id, TaskStatus.COMPLETED, {"result": result}
                )
                await self.task_queue_service.set_current_task(phone_number, None)

            # CRITICAL: For transfer/airtime, do NOT mark as completed here
            # The flow continues asynchronously and will call completion callback when done
            # The completion callback will mark the task as completed
            # Only return the initial response - completion is handled by flow's callback
            
            return {"status": "in_progress", "result": result}

        except Exception as e:
            await self.task_queue_service.update_task_status(
                phone_number, task.id, TaskStatus.FAILED, {"error": str(e)}
            )
            # Clear current task on error
            await self.task_queue_service.set_current_task(phone_number, None)
            raise

    def _construct_task_message(self, task: PlannedTask, user_message: str) -> str:
        """
        Construct a task-specific message from task parameters.
        
        Args:
            task: PlannedTask with parameters
            user_message: Original user message (fallback)
            
        Returns:
            Task-specific message string
        """
        params = task.parameters or {}
        
        # For transfer tasks, construct message from parameters
        if task.executor == "transfer":
            amount = params.get("amount")
            recipient = params.get("recipient")
            if amount and recipient:
                return f"Send {amount} to {recipient}"
            elif amount:
                return f"Send {amount}"
            elif recipient:
                return f"Send to {recipient}"
        
        # For airtime tasks
        elif task.executor == "airtime":
            amount = params.get("amount")
            if amount:
                return f"Buy {amount} airtime"
        
        # Fall back to original message or task instruction
        if user_message and user_message.strip():
            return user_message
        return task.instruction or task.action or ""

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

