"""Task queue service for managing multi-task execution."""

import json
import time
from typing import Optional, List, Dict, Any

from shared.types.agent_types import TaskStatus
from shared.cache.redis_client import RedisClient
from apps.core.src.agent.models.planner import PlannerOutput, PlannedTask


class TaskQueueService:
    """Service for managing task queues in Redis."""

    def __init__(self, redis_client=None):
        self.redis_client = redis_client or RedisClient.get_client()

    async def create_task_queue(
        self, phone_number: str, planner_output: PlannerOutput
    ) -> None:
        """
        Store task queue from planner output.

        Args:
            phone_number: User's phone number
            planner_output: Planner output containing tasks
        """
        queue_key = f"user:{phone_number}:task_queue"
        queue_data = {
            "planner_output": planner_output.model_dump(),
            "tasks": [task.model_dump() for task in planner_output.tasks],
            "created_at": time.time(),
        }
        await self.redis_client.set(
            queue_key, json.dumps(queue_data), ex=3600
        )

    async def get_task_queue(
        self, phone_number: str
    ) -> Optional[PlannerOutput]:
        """
        Retrieve current task queue.

        Args:
            phone_number: User's phone number

        Returns:
            PlannerOutput if queue exists, None otherwise
        """
        queue_key = f"user:{phone_number}:task_queue"
        data = await self.redis_client.get(queue_key)
        if not data:
            return None
        queue_data = json.loads(data)
        return PlannerOutput.model_validate(queue_data["planner_output"])

    async def get_next_task(
        self, phone_number: str
    ) -> Optional[PlannedTask]:
        """
        Get next executable task (dependencies satisfied).

        Args:
            phone_number: User's phone number

        Returns:
            Next executable PlannedTask, or None if no tasks available
        """
        planner_output = await self.get_task_queue(phone_number)
        if not planner_output:
            return None

        completed_tasks = await self.get_completed_task_ids(phone_number)

        for task in planner_output.tasks:
            if task.status == TaskStatus.PENDING:
                if all(dep_id in completed_tasks for dep_id in task.depends_on):
                    return task

        return None

    async def get_completed_task_ids(self, phone_number: str) -> List[str]:
        """Get list of completed task IDs."""
        results_key = f"user:{phone_number}:task_results"
        data = await self.redis_client.get(results_key)
        if not data:
            return []
        results = json.loads(data)
        return [
            task_id
            for task_id, result in results.items()
            if result.get("status") == TaskStatus.COMPLETED
        ]

    async def update_task_status(
        self,
        phone_number: str,
        task_id: str,
        status: TaskStatus,
        result: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Update task status and store result.

        Args:
            phone_number: User's phone number
            task_id: Task ID to update
            status: New task status
            result: Optional task execution result
        """
        queue_key = f"user:{phone_number}:task_queue"
        queue_data_str = await self.redis_client.get(queue_key)
        if queue_data_str:
            queue_data = json.loads(queue_data_str)
            for task in queue_data["tasks"]:
                if task["id"] == task_id:
                    task["status"] = status.value
                    break
            await self.redis_client.set(
                queue_key, json.dumps(queue_data), ex=3600
            )

        results_key = f"user:{phone_number}:task_results"
        results_data_str = await self.redis_client.get(results_key)
        results = json.loads(results_data_str) if results_data_str else {}
        results[task_id] = {"status": status.value, "result": result}
        await self.redis_client.set(
            results_key, json.dumps(results), ex=3600
        )

    async def clear_task_queue(self, phone_number: str) -> None:
        """
        Clear completed/failed task queue.

        Args:
            phone_number: User's phone number
        """
        queue_key = f"user:{phone_number}:task_queue"
        current_task_key = f"user:{phone_number}:current_task"
        results_key = f"user:{phone_number}:task_results"
        await self.redis_client.delete(queue_key)
        await self.redis_client.delete(current_task_key)
        await self.redis_client.delete(results_key)

    async def has_active_queue(self, phone_number: str) -> bool:
        """
        Check if user has active task queue.

        Args:
            phone_number: User's phone number

        Returns:
            True if active queue exists, False otherwise
        """
        queue_key = f"user:{phone_number}:task_queue"
        return await self.redis_client.exists(queue_key) > 0

    async def get_current_task(self, phone_number: str) -> Optional[str]:
        """Get currently executing task ID."""
        current_task_key = f"user:{phone_number}:current_task"
        return await self.redis_client.get(current_task_key)

    async def set_current_task(
        self, phone_number: str, task_id: Optional[str]
    ) -> None:
        """Set currently executing task ID."""
        current_task_key = f"user:{phone_number}:current_task"
        if task_id:
            await self.redis_client.set(current_task_key, task_id, ex=3600)
        else:
            await self.redis_client.delete(current_task_key)
