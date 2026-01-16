"""Task coordinator service - manages flow completion and task transitions."""

import time
from typing import TYPE_CHECKING, Any

from apps.core.src.agent.orchestrator.pipeline_stages.task_queue.coordination.summary import TaskSummaryGenerator
from shared.cache.redis_client import RedisClient
from shared.config import settings
from shared.services.task_queue import TaskQueueService
from shared.types.agent_types import TaskStatus
from shared.utils.logging import get_logger

if TYPE_CHECKING:
    from apps.core.src.agent.graphs.transfer import TransferService
    from apps.core.src.agent.orchestrator.pipeline_stages.context_loader.service import OrchestratorContextManager
    from apps.core.src.agent.orchestrator.pipeline_stages.task_queue.planner import OrchestratorTaskPlanner
    from shared.clients.whatsapp.client import WhatsAppClient


logger = get_logger(__name__)


class TaskCoordinator:
    """
    Coordinates task execution and flow completion.

    Handles the logic for what happens when a flow (transfer, airtime, etc.) completes:
    1. Updates task status (COLLECTION_COMPLETE vs COMPLETED).
    2. Triggers batch authorization if tasks are ready.
    3. Triggers the next task in the queue.
    4. Sends summaries and clears state when all tasks are done.
    """

    def __init__(
        self,
        task_queue_service: TaskQueueService,
        whatsapp_client: "WhatsAppClient",
        context_manager: "OrchestratorContextManager",
        task_planner: "OrchestratorTaskPlanner",
        transfer_service: "TransferService",
    ):
        self.task_queue_service = task_queue_service
        self.whatsapp_client = whatsapp_client
        self.context_manager = context_manager
        self.task_planner = task_planner
        self.transfer_service = transfer_service
        self.summary_generator = TaskSummaryGenerator(task_queue_service)

    async def on_flow_complete(
        self,
        phone_number: str,
        flow_type: str,
        result: dict[str, Any],
    ) -> None:
        """Called when a flow completes. Triggers next task execution if available."""
        has_queue = await self.task_queue_service.has_active_queue(phone_number)
        if not has_queue:
            return

        status = result.get("status") if isinstance(result, dict) else None
        is_collection_complete = status == "collection_complete"

        current_task_id = await self.task_queue_service.get_current_task(phone_number)
        completed_task_id = current_task_id

        if current_task_id:
            await self._update_task_status(
                phone_number, current_task_id, flow_type, result, is_collection_complete
            )

        if is_collection_complete:
            if await self._check_batch_auth_ready(phone_number):
                await self._trigger_batch_authorization(phone_number)
                return

        await self.task_queue_service.set_current_task(phone_number, None)

        if not is_collection_complete:
            if await self._trigger_next_auth_if_ready(phone_number):
                return

        if completed_task_id:
            await self._process_next_task(phone_number, completed_task_id)

    async def _update_task_status(
        self,
        phone_number: str,
        task_id: str,
        flow_type: str,
        result: dict[str, Any],
        is_collection_complete: bool,
    ) -> None:
        """Update the status of the current task."""
        if is_collection_complete:
            await self.task_queue_service.update_task_status(
                phone_number, task_id, TaskStatus.COLLECTION_COMPLETE, result
            )
            logger.info(f"Marked task {task_id} ({flow_type}) as COLLECTION_COMPLETE")

            if flow_type == "transfer":
                try:
                    await self.transfer_service.clear_checkpoint(phone_number)
                except Exception as e:
                    logger.error(f"Error clearing transfer checkpoint: {e}")
        else:
            await self.task_queue_service.update_task_status(
                phone_number, task_id, TaskStatus.COMPLETED, result
            )
            logger.info(f"Marked task {task_id} ({flow_type}) as COMPLETED")

    async def _check_batch_auth_ready(self, phone_number: str) -> bool:
        """Check if all tasks are ready for batch authorization."""
        planner_output = await self.task_queue_service.get_task_queue(phone_number)
        if not planner_output:
            return False

        completed_ids = await self.task_queue_service.get_completed_task_ids(phone_number)
        task_results = await self.task_queue_service.get_task_results(phone_number)
        collection_complete_ids = [
            task_id
            for task_id, result in task_results.items()
            if result.get("status") == TaskStatus.COLLECTION_COMPLETE.value
        ]
        all_done_task_ids = set(completed_ids) | set(collection_complete_ids)

        auth_required_tasks = [
            t for t in planner_output.tasks if t.executor in ("transfer", "airtime")
        ]

        return (
            all(task.task_id in all_done_task_ids for task in auth_required_tasks)
            if auth_required_tasks
            else False
        )

    async def _trigger_batch_authorization(self, phone_number: str) -> None:
        """Trigger the batch authorization flow."""
        logger.debug("All tasks ready for batch authorization")
        summary = await self.summary_generator.generate_batch_summary(phone_number)

        flow_token = f"batch-auth-{phone_number}-{int(time.time())}"
        await self.whatsapp_client.send_flow(
            to=phone_number,
            header="Authorize Transactions",
            flow_cta="Authorize All",
            flow_id=settings.pin_confirmation_flow_id,
            screen_name="Pin",
            flow_token=flow_token,
            text_body=summary,
        )

        await self.context_manager.save_last_response(phone_number, summary)

    async def _trigger_next_auth_if_ready(self, phone_number: str) -> bool:
        """Check for next task ready for authorization and trigger it."""
        planner_output = await self.task_queue_service.get_task_queue(phone_number)
        if not planner_output:
            return False

        task_results = await self.task_queue_service.get_task_results(phone_number)

        next_collection_complete_task = None
        for task in planner_output.tasks:
            task_status = task_results.get(task.task_id, {}).get("status")
            if task_status == TaskStatus.COLLECTION_COMPLETE.value:
                next_collection_complete_task = task
                break

        if next_collection_complete_task:
            await self.task_queue_service.set_current_task(
                phone_number, next_collection_complete_task.task_id
            )
            if next_collection_complete_task.executor == "transfer":
                await self.transfer_service.run_simple(
                    phone_number, "authorize", {"intent": "transfer"}
                )
                return True

        return False

    async def _process_next_task(self, phone_number: str, completed_task_id: str) -> None:
        """Find and process the next task in the queue."""
        completed_ids = await self.task_queue_service.get_completed_task_ids(phone_number)
        task_results = await self.task_queue_service.get_task_results(phone_number)
        collection_complete_ids = [
            tid for tid, res in task_results.items()
            if res.get("status") == TaskStatus.COLLECTION_COMPLETE.value
        ]
        all_done_task_ids = set(completed_ids) | set(collection_complete_ids)

        next_task = await self.task_queue_service.get_next_task(phone_number)

        if next_task:
            if next_task.task_id in all_done_task_ids:
                planner_output = await self.task_queue_service.get_task_queue(phone_number)
                if planner_output and all(t.id in all_done_task_ids for t in planner_output.tasks):
                    await self._finish_all_tasks(phone_number)
                return

            current_task_id = await self.task_queue_service.get_current_task(phone_number)
            if current_task_id == next_task.task_id:
                return

            await self.task_queue_service.set_current_task(phone_number, next_task.task_id)
            await self._send_transition_message(phone_number, completed_task_id, next_task)

            next_task_response = await self.task_planner.handle_next_task(phone_number, "")
            if next_task_response:
                await self.whatsapp_client.send_text(phone_number, next_task_response)
                await self.context_manager.save_last_response(phone_number, next_task_response)
        else:
            await self._finish_all_tasks(phone_number)

    async def _finish_all_tasks(self, phone_number: str) -> None:
        """Finish processing: send final summary and clear queue."""
        summary = await self.summary_generator.generate_completion_summary(phone_number)
        await self.whatsapp_client.send_text(phone_number, summary)
        await self.task_queue_service.clear_task_queue(phone_number)
        await self._clear_conversation_state(phone_number)
        logger.info(f"All tasks completed for {phone_number}")

    async def _send_transition_message(self, phone_number: str, completed_task_id: str, next_task: Any) -> None:
        """Send a message transitioning from the completed task to the next one."""
        planner_output = await self.task_queue_service.get_task_queue(phone_number)
        if not planner_output:
            return

        completed_task = next((t for t in planner_output.tasks if t.id == completed_task_id), None)

        if not completed_task:
            all_done = await self.task_queue_service.get_completed_task_ids(phone_number)
            for task in reversed(planner_output.tasks):
                if task.task_id in all_done:
                    completed_task = task
                    break

        if completed_task and completed_task.task_id != next_task.task_id:
            completed_desc = self.summary_generator.format_task_description(completed_task)
            next_desc = self.summary_generator.format_task_description(next_task)

            task_results = await self.task_queue_service.get_task_results(phone_number)
            completed_status = task_results.get(completed_task.task_id, {}).get("status")

            if completed_status == TaskStatus.COLLECTION_COMPLETE.value:
                msg = f"📝 Details for {completed_desc} received. Now let's process {next_desc}."
            else:
                msg = f"✓ {completed_desc.capitalize()} completed. Now let's process {next_desc}."

            await self.whatsapp_client.send_text(phone_number, msg)

    async def _clear_conversation_state(self, phone_number: str) -> None:
        """Clear conversation state."""
        try:
            redis_client = RedisClient.get_client()
            await redis_client.delete(f"user:{phone_number}:conversation_state")
            logger.info(f"Cleared conversation_state for {phone_number}")
        except Exception as e:
            logger.error(f"Error clearing conversation_state: {e}")
