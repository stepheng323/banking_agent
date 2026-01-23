"""Batch executor service for DAG-based parallel task execution.

Uses WorkflowDAGExecutor for dependency-aware parallel execution of batch tasks.
"""

import uuid
from typing import TYPE_CHECKING, Any

from apps.core.src.agent.shared.batch.utils import ExecutionState, format_amount
from apps.core.src.agent.shared.batch.workflow import (
    WorkflowContext,
    WorkflowDAGExecutor,
    WorkflowHandlerRegistry,
    WorkflowResult,
    compute_approval_hash,
)
from apps.core.src.agent.shared.batch.workflow.handlers import (
    AccountHandler,
    AirtimeHandler,
    DataHandler,
    QueryHandler,
    TransferHandler,
)
from shared.cache.redis_client import Redis
from shared.clients.whatsapp.client import WhatsAppClient
from shared.queue.redis_queue import RedisQueue
from shared.services.task_queue import TaskQueueService
from shared.types.agent_types import TaskStatus
from shared.types.planner import PlannedTask
from shared.utils.logging import get_logger

logger = get_logger(__name__)

if TYPE_CHECKING:
    from apps.core.src.agent.graphs.account.service import AccountService
    from apps.core.src.agent.graphs.query import QueryService
    from shared.cache.user_data import UserDataCache


def _build_handler_registry() -> WorkflowHandlerRegistry:
    """Build and return a configured handler registry."""
    registry = WorkflowHandlerRegistry()
    registry.register("transfer", TransferHandler())
    registry.register("airtime", AirtimeHandler())
    registry.register("data", DataHandler())
    registry.register("query", QueryHandler())
    registry.register("account", AccountHandler())
    return registry


def _format_task_description(task: PlannedTask) -> str:
    """Format a task for display."""
    if task.executor == "transfer":
        amount = task.parameters.get("amount") if task.parameters else None
        recipient = task.parameters.get("recipient") if task.parameters else None
        if amount and recipient:
            return f"Transfer {format_amount(amount)} to {recipient}"
        elif amount:
            return f"Transfer {format_amount(amount)}"
        return "Transfer"

    elif task.executor == "airtime":
        amount = task.parameters.get("amount") if task.parameters else None
        recipient = task.parameters.get("recipient") if task.parameters else None
        if amount and recipient:
            return f"Airtime {format_amount(amount)} to {recipient}"
        elif amount:
            return f"Airtime {format_amount(amount)}"
        return "Airtime"

    elif task.executor == "data":
        amount = task.parameters.get("amount") if task.parameters else None
        if amount:
            return f"Data {format_amount(amount)}"
        return "Data purchase"

    return task.action or task.executor


def _generate_final_summary(
    tasks: list[PlannedTask], completed: list[dict[str, Any]], failed: list[dict[str, Any]]
) -> str:
    """Generate final summary of batch execution."""
    total = len(tasks)
    completed_count = len(completed)
    failed_count = len(failed)

    if failed_count == 0:
        summary = f"✓ All {total} task{'s' if total > 1 else ''} completed!\n\n"
        total_amount = 0
        for i, task in enumerate(tasks, 1):
            task_desc = _format_task_description(task)
            summary += f"{i}. {task_desc} ✓\n"
            if task.parameters and task.parameters.get("amount"):
                total_amount += task.parameters["amount"]

        if total_amount > 0:
            summary += f"\nTotal: {format_amount(total_amount)}"

    elif completed_count == 0:
        summary = "❌ All tasks failed. Please try again.\n\n"
        for i, task in enumerate(tasks, 1):
            task_desc = _format_task_description(task)
            error = failed[i - 1].get("error", "Unknown error") if i <= len(failed) else "Unknown"
            summary += f"{i}. {task_desc} ✗ ({error})\n"

    else:
        summary = f"⚠️ {completed_count} of {total} tasks completed.\n\n"
        completed_map = {r.get("task_id"): r for r in completed if r.get("task_id")}
        failed_map = {r.get("task_id"): r for r in failed if r.get("task_id")}

        total_amount = 0
        for i, task in enumerate(tasks, 1):
            task_desc = _format_task_description(task)
            if task.task_id in completed_map:
                summary += f"{i}. {task_desc} ✓\n"
                if task.parameters and task.parameters.get("amount"):
                    total_amount += task.parameters["amount"]
            else:
                error = failed_map.get(task.task_id, {}).get("error", "Unknown error")
                summary += f"{i}. {task_desc} ✗ ({error})\n"

        if total_amount > 0:
            summary += f"\nTotal sent: {format_amount(total_amount)}"

    return summary


async def execute_batch_dag(
    phone_number: str,
    pin_verified: bool,
    user_id: str,
    whatsapp_client: WhatsAppClient,
    task_queue_service: TaskQueueService,
    redis_client: Redis,
    queue: RedisQueue,
    query_service: "QueryService | None" = None,
    user_cache: "UserDataCache | None" = None,
    account_service: "AccountService | None" = None,
) -> dict[str, Any]:
    """
    Execute all tasks using the DAG-based workflow executor.

    This version respects task dependencies (depends_on), executes tasks
    in parallel waves, and handles errors with retry logic.

    Args:
        phone_number: User's phone number
        pin_verified: Whether PIN was verified
        user_id: User's ID
        whatsapp_client: WhatsApp client for sending messages
        task_queue_service: Service for task queue management
        redis_client: Redis client
        queue: Redis queue for background jobs
        query_service: Optional query service for balance checks
        user_cache: Optional user data cache
        account_service: Optional account service

    Returns:
        Dict with completed, failed, blocked, skipped counts and workflow result
    """
    try:
        workflow_id = str(uuid.uuid4())

        from apps.core.src.agent.shared.batch.state_machine import BatchStateMachine

        sm = BatchStateMachine(redis_client, phone_number)
        if not await sm.transition_to(ExecutionState.EXECUTING_BATCH):
            logger.warning(f"Invalid state transition to EXECUTING for {phone_number}")
            return {"completed": 0, "failed": 0, "total": 0, "error": "Invalid state"}

        planner_output = await task_queue_service.get_task_queue(phone_number)
        if not planner_output:
            return {"completed": 0, "failed": 0, "total": 0}

        # Check TTL (15 minutes)
        import time

        batch_data_ttl = 900  # 15 minutes
        if time.time() - planner_output.created_at > batch_data_ttl:
            await whatsapp_client.send_text(
                phone_number, "❌ Batch expired (data too old). Please describe your request again."
            )
            await task_queue_service.clear_task_queue(phone_number)
            return {"completed": 0, "failed": 0, "total": 0, "error": "Batch data expired"}

        task_results = await task_queue_service.get_task_results(phone_number)

        # Filter to tasks ready for execution
        tasks_to_execute = [
            task
            for task in planner_output.tasks
            if task_results.get(task.task_id, {}).get("status") == TaskStatus.COLLECTION_COMPLETE.value
        ]

        total = len(tasks_to_execute)
        if total == 0:
            return {"completed": 0, "failed": 0, "total": 0}

        if not pin_verified:
            await whatsapp_client.send_text(phone_number, "❌ PIN verification required. Please try again.")
            return {"completed": 0, "failed": 0, "total": total, "error": "PIN not verified"}

        # Verify Approval Hash
        stored_hash = await redis_client.get(f"batch:approval:{phone_number}")
        if stored_hash:
            current_hash = compute_approval_hash(tasks_to_execute)
            if stored_hash != current_hash:
                logger.error(
                    f"[BATCH-DAG] Approval hash mismatch for {phone_number}. Stored: {stored_hash}, Computed: {current_hash}"
                )
                await whatsapp_client.send_text(
                    phone_number, "❌ Security Alert: Batch contents have changed since approval. Please try again."
                )
                await redis_client.delete(f"queue:{phone_number}:execution_state")
                return {"completed": 0, "failed": 0, "total": 0, "error": "Approval hash mismatch"}
        else:
            logger.warning(f"[BATCH-DAG] No approval hash found for {phone_number}. Allowing execution (legacy).")

        await whatsapp_client.send_text(
            phone_number, f"⏳ Processing {total} task{'s' if total > 1 else ''} in parallel..."
        )

        # Set idempotency keys on tasks
        for task in tasks_to_execute:
            if not task.idempotency_key:
                task.idempotency_key = f"{user_id}:{workflow_id}:{task.task_id}"

        # Build workflow context
        context = WorkflowContext(
            phone_number=phone_number,
            user_id=user_id,
            workflow_id=workflow_id,
            pin_verified=True,
            redis_client=redis_client,
            queue=queue,
            whatsapp_client=whatsapp_client,
            task_queue_service=task_queue_service,
        )

        # Register services
        if query_service:
            context.register_service("query_service", query_service)
        if user_cache:
            context.register_service("user_cache", user_cache)
        if account_service:
            context.register_service("account_service", account_service)

        # Execute with DAG executor
        registry = _build_handler_registry()
        executor = WorkflowDAGExecutor(registry, max_retries=2, retry_delay_seconds=1.0)
        result: WorkflowResult = await executor.execute(tasks_to_execute, context)

        # Convert to legacy format for summary
        completed = [{"task_id": r.task_id, "success": True, **r.data} for r in result.completed]
        failed = [{"task_id": r.task_id, "error": r.error_message or "Unknown error"} for r in result.failed]

        # Add blocked/skipped to failed for summary
        for r in result.blocked:
            failed.append({"task_id": r.task_id, "error": "Blocked by failed dependency"})
        for r in result.skipped:
            failed.append({"task_id": r.task_id, "error": f"Skipped: {r.error_message}"})

        summary = _generate_final_summary(tasks_to_execute, completed, failed)

        # Store retryable failures in Redis (24h TTL) for user-triggered retry
        if result.failed:
            await _store_retryable_tasks(redis_client, phone_number, workflow_id, tasks_to_execute, result)
            summary += "\n\n💡 Some failures may be retryable. Say 'retry failed' to try again."

        await whatsapp_client.send_text(phone_number, summary)

        await task_queue_service.clear_task_queue(phone_number)
        if result.stopped_early:
            await sm.transition_to(ExecutionState.STOPPED)
        else:
            await sm.transition_to(ExecutionState.COMPLETED)
        await sm.clear()

        logger.info(
            f"[BATCH-DAG] Completed {len(result.completed)}/{total} tasks for {phone_number}. "
            f"Failed: {len(result.failed)}, Blocked: {len(result.blocked)}, Skipped: {len(result.skipped)}"
        )

        return {
            "completed": len(result.completed),
            "failed": len(result.failed),
            "blocked": len(result.blocked),
            "skipped": len(result.skipped),
            "total": total,
            "workflow_result": result,
        }

    except Exception as e:
        logger.error(f"[BATCH-DAG] Error executing batch: {e}", exc_info=True)

        try:
            await whatsapp_client.send_text(phone_number, "❌ An error occurred processing tasks. Please try again.")
        except Exception:
            pass

        if "sm" in locals():
            await sm.clear()
        else:
            await redis_client.delete(f"queue:{phone_number}:execution_state")
        return {"completed": 0, "failed": 0, "total": 0, "error": str(e)}


async def _store_retryable_tasks(
    redis_client: Redis,
    phone_number: str,
    workflow_id: str,
    tasks: list[PlannedTask],
    result: WorkflowResult,
) -> None:
    """Store retryable failed tasks in Redis for user-triggered retry."""
    import json

    # Get task IDs of retryable failures
    from apps.core.src.agent.shared.batch.workflow.models import ErrorKind

    retryable_ids = {r.task_id for r in result.failed if r.error_kind == ErrorKind.TRANSIENT}
    if not retryable_ids:
        return

    # Store tasks that can be retried
    retryable_tasks = [t.model_dump() for t in tasks if t.task_id in retryable_ids]

    key = f"retry:{phone_number}:failed_tasks"
    await redis_client.set(
        key,
        json.dumps({"workflow_id": workflow_id, "tasks": retryable_tasks}),
        ex=86400,  # 24 hour TTL
    )
    logger.info(f"[BATCH-DAG] Stored {len(retryable_tasks)} retryable tasks for {phone_number}")


async def retry_failed_tasks(
    phone_number: str,
    user_id: str,
    whatsapp_client: WhatsAppClient,
    task_queue_service: TaskQueueService,
    redis_client: Redis,
    queue: RedisQueue,
    query_service: "QueryService | None" = None,
    user_cache: "UserDataCache | None" = None,
    account_service: "AccountService | None" = None,
) -> dict[str, Any]:
    """
    Retry previously failed tasks.

    Only retries TRANSIENT failures (network, timeout, etc).
    """
    import json

    key = f"retry:{phone_number}:failed_tasks"
    data = await redis_client.get(key)

    if not data:
        await whatsapp_client.send_text(phone_number, "No failed tasks to retry.")
        return {"completed": 0, "failed": 0, "total": 0, "error": "No retryable tasks"}

    try:
        parsed = json.loads(data)
        workflow_id = parsed.get("workflow_id", str(uuid.uuid4()))
        tasks_data = parsed.get("tasks", [])

        if not tasks_data:
            await whatsapp_client.send_text(phone_number, "No failed tasks to retry.")
            return {"completed": 0, "failed": 0, "total": 0}

        # Reconstruct PlannedTask objects
        tasks = [PlannedTask(**t) for t in tasks_data]
        total = len(tasks)

        await whatsapp_client.send_text(phone_number, f"⏳ Retrying {total} failed task{'s' if total > 1 else ''}...")

        # Build context
        context = WorkflowContext(
            phone_number=phone_number,
            user_id=user_id,
            workflow_id=workflow_id,
            pin_verified=True,
            redis_client=redis_client,
            queue=queue,
            whatsapp_client=whatsapp_client,
            task_queue_service=task_queue_service,
        )

        if query_service:
            context.register_service("query_service", query_service)
        if user_cache:
            context.register_service("user_cache", user_cache)
        if account_service:
            context.register_service("account_service", account_service)

        # Execute retry
        registry = _build_handler_registry()
        executor = WorkflowDAGExecutor(registry, max_retries=2, retry_delay_seconds=1.0)
        result = await executor.execute(tasks, context, validate_limits=False)

        # Generate summary
        completed = [{"task_id": r.task_id, "success": True, **r.data} for r in result.completed]
        failed = [{"task_id": r.task_id, "error": r.error_message or "Unknown error"} for r in result.failed]

        summary = _generate_final_summary(tasks, completed, failed)
        await whatsapp_client.send_text(phone_number, summary)

        # Clear retry data if all succeeded
        if not result.has_failures:
            await redis_client.delete(key)
        elif result.has_retryable_failures:
            # Update stored tasks with remaining failures
            await _store_retryable_tasks(redis_client, phone_number, workflow_id, tasks, result)

        logger.info(f"[BATCH-RETRY] Retried {total} tasks for {phone_number}: {len(result.completed)} succeeded")

        return {
            "completed": len(result.completed),
            "failed": len(result.failed),
            "total": total,
            "workflow_result": result,
        }

    except Exception as e:
        logger.error(f"[BATCH-RETRY] Error retrying tasks: {e}", exc_info=True)
        await whatsapp_client.send_text(phone_number, "❌ Error retrying tasks. Please try again.")
        return {"completed": 0, "failed": 0, "total": 0, "error": str(e)}
