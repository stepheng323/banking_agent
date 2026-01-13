"""Batch executor service for parallel task execution.

Optimized to use authorization classes directly instead of re-traversing graphs.
"""

import asyncio
from typing import TYPE_CHECKING, Any

from apps.core.src.agent.graphs.__shared__.validation.amount_validator import (
    AIRTIME_LIMITS,
    DATA_LIMITS,
    TRANSFER_LIMITS,
)
from apps.core.src.agent.graphs.__shared__.validation.amount_validator import (
    validate_amount as validate_amount_limits,
)
from apps.core.src.agent.graphs.airtime.graph.nodes.authorization import AirtimeAuthorization
from apps.core.src.agent.graphs.data.graph.nodes.authorization import DataAuthorization
from apps.core.src.agent.graphs.transfer.graph.nodes.authorization import TransferAuthorization
from apps.core.src.agent.shared.batch.utils import ExecutionState, format_amount
from shared.cache.redis_client import Redis
from shared.clients.whatsapp.client import WhatsAppClient
from shared.queue.redis_queue import RedisQueue
from shared.services.task_queue import TaskQueueService
from shared.types.agent_types import TaskStatus
from shared.types.planner import PlannedTask
from shared.utils.logging import get_logger

logger = get_logger(__name__)

if TYPE_CHECKING:
    from apps.core.src.agent.graphs.account_management.service import AccountManagementService
    from apps.core.src.agent.graphs.airtime.service import AirtimeService
    from apps.core.src.agent.graphs.data.graph.graph import DataPurchaseGraph
    from apps.core.src.agent.graphs.query.graph import QueryFlowGraph
    from apps.core.src.agent.graphs.transfer.service import TransferService
    from shared.cache.user_data import UserDataCache


async def execute_batch(
    phone_number: str,
    pin_verified: bool,
    user_id: str,
    whatsapp_client: WhatsAppClient,
    task_queue_service: TaskQueueService,
    redis_client: Redis,
    queue: RedisQueue,
    transfer_service: "TransferService | None" = None,
    airtime_service: "AirtimeService | None" = None,
    data_service: "DataPurchaseGraph | None" = None,
    query_graph: "QueryFlowGraph | None" = None,
    user_cache: "UserDataCache | None" = None,
    account_management_service: "AccountManagementService | None" = None,
) -> dict[str, Any]:
    """
    Execute all tasks in parallel using authorization classes directly.

    PIN is verified once at the start, then all tasks execute without
    re-checking PIN.
    """
    try:
        await redis_client.set(f"queue:{phone_number}:execution_state", ExecutionState.EXECUTING_BATCH, ex=600)

        planner_output = await task_queue_service.get_task_queue(phone_number)
        if not planner_output:
            return {"completed": 0, "failed": 0, "total": 0}

        task_results = await task_queue_service.get_task_results(phone_number)

        tasks_to_execute = []
        for task in planner_output.tasks:
            status = task_results.get(task.id, {}).get("status")
            if status == TaskStatus.COLLECTION_COMPLETE.value:
                tasks_to_execute.append(task)

        total = len(tasks_to_execute)
        if total == 0:
            return {"completed": 0, "failed": 0, "total": 0}

        if not pin_verified:
            await whatsapp_client.send_text(phone_number, "❌ PIN verification required. Please try again.")
            return {"completed": 0, "failed": 0, "total": total, "error": "PIN not verified"}

        await whatsapp_client.send_text(
            phone_number, f"⏳ Processing {total} task{'s' if total > 1 else ''} in parallel..."
        )

        async_tasks = []
        for task in tasks_to_execute:
            task_result = task_results.get(task.id, {})
            async_task = _execute_task_direct(
                phone_number=phone_number,
                user_id=user_id,
                task=task,
                task_result=task_result,
                redis_client=redis_client,
                queue=queue,
                whatsapp_client=whatsapp_client,
                task_queue_service=task_queue_service,
            )
            async_tasks.append(async_task)

        results = await asyncio.gather(*async_tasks, return_exceptions=True)

        completed = []
        failed = []

        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"[BATCH] Task {tasks_to_execute[i].id} raised exception: {result}")
                failed.append(
                    {
                        "task_id": tasks_to_execute[i].id,
                        "error": str(result),
                    }
                )
            elif result.get("success"):
                completed.append(result)
            else:
                failed.append(result)

        summary = _generate_final_summary(tasks_to_execute, completed, failed)
        await whatsapp_client.send_text(phone_number, summary)

        # Execute non-auth tasks
        await _execute_remaining_tasks(
            phone_number=phone_number,
            planner_output=planner_output,
            task_results=task_results,
            user_cache=user_cache,
            query_graph=query_graph,
            account_management_service=account_management_service,
            whatsapp_client=whatsapp_client,
            task_queue_service=task_queue_service,
        )

        await task_queue_service.clear_task_queue(phone_number)
        await redis_client.delete(f"queue:{phone_number}:execution_state")

        logger.info(f"[BATCH] Completed {len(completed)}/{total} tasks for {phone_number}")

        return {
            "completed": len(completed),
            "failed": len(failed),
            "total": total,
            "results": results,
        }

    except Exception as e:
        logger.error(f"[BATCH] Error executing batch: {e}", exc_info=True)

        try:
            await whatsapp_client.send_text(phone_number, "❌ An error occurred processing tasks. Please try again.")
        except Exception:
            pass

        await redis_client.delete(f"queue:{phone_number}:execution_state")
        return {"completed": 0, "failed": 0, "total": 0, "error": str(e)}


async def _execute_task_direct(
    phone_number: str,
    user_id: str,
    task: PlannedTask,
    task_result: dict[str, Any],
    redis_client: Redis,
    queue: RedisQueue,
    whatsapp_client: WhatsAppClient,
    task_queue_service: TaskQueueService,
) -> dict[str, Any]:
    """Execute a single task using authorization class directly."""
    task_desc = _format_task_description(task)

    try:
        cancelled = await redis_client.get(f"queue:{phone_number}:cancel_batch")
        if cancelled:
            logger.warning(f"[BATCH] Task {task.id} cancelled")
            return {"success": False, "cancelled": True, "task_id": task.id}

        await task_queue_service.update_task_status(phone_number, task.id, TaskStatus.IN_PROGRESS)

        result_data = task_result.get("result", {})

        if task.executor == "transfer":
            result = await _execute_transfer_direct(phone_number, user_id, task, result_data, redis_client, queue)
        elif task.executor == "airtime":
            result = await _execute_airtime_direct(phone_number, user_id, task, result_data, redis_client, queue)
        elif task.executor == "data":
            result = await _execute_data_direct(phone_number, user_id, task, result_data, redis_client, queue)
        else:
            result = {"success": False, "error": f"Unknown executor: {task.executor}"}

        result["task_id"] = task.id
        result["task_desc"] = task_desc

        if result.get("success"):
            await task_queue_service.update_task_status(phone_number, task.id, TaskStatus.COMPLETED, result)
            success_msg = _format_success_message(task, result)
            await whatsapp_client.send_text(phone_number, success_msg)
            logger.info(f"[BATCH] Task {task.id} completed: {task_desc}")
        else:
            await task_queue_service.update_task_status(phone_number, task.id, TaskStatus.FAILED, result)
            error_msg = result.get("error", "Unknown error")
            await whatsapp_client.send_text(phone_number, f"⚠️ {task_desc} failed: {error_msg}")
            logger.error(f"[BATCH] Task {task.id} failed: {error_msg}")

        return result

    except Exception as e:
        logger.error(f"[BATCH] Task {task.id} exception: {e}", exc_info=True)
        await task_queue_service.update_task_status(phone_number, task.id, TaskStatus.FAILED, {"error": str(e)})
        await whatsapp_client.send_text(phone_number, f"❌ {task_desc} error: {str(e)}")
        return {"success": False, "error": str(e), "task_id": task.id}


async def _execute_transfer_direct(
    phone_number: str,
    user_id: str,
    task: PlannedTask,
    result_data: dict[str, Any],
    redis_client: Redis,
    queue: RedisQueue,
) -> dict[str, Any]:
    """Execute transfer using TransferAuthorization directly."""
    try:
        amount = task.parameters.get("amount") if task.parameters else None
        is_valid, error_msg, validated_amount = validate_amount_limits(amount, TRANSFER_LIMITS)
        if not is_valid:
            logger.warning(f"[BATCH] Invalid transfer amount: {error_msg}")
            return {"success": False, "error": error_msg or "Invalid amount"}

        account_resolved = result_data.get("account_resolved", {})
        idem_key = result_data.get("idempotency_key") or f"batch-transfer-{task.id}"

        state = {
            "phone_number": phone_number,
            "idempotency_key": idem_key,
            "pin_verified": True,
            "user_profile": {"id": user_id},
            "amount": validated_amount,
            "recipient_account": account_resolved.get("account_number"),
            "recipient_bank_code": account_resolved.get("bank_code"),
            "recipient_bank_name": account_resolved.get("bank_name"),
            "recipient_name": account_resolved.get("account_name"),
            "selected_source_account": result_data.get("source_account", {}),
            "narration": task.parameters.get("narration") if task.parameters else None,
        }

        auth = TransferAuthorization(redis_client, queue)
        result_state = await auth.authorize(state)

        if result_state.get("transfer_status") == "authorized":
            return {
                "success": True,
                "amount": state["amount"],
                "recipient": state["recipient_name"],
                "transaction_id": result_state.get("transaction_id"),
            }
        else:
            return {
                "success": False,
                "error": result_state.get("response", "Transfer failed"),
            }

    except Exception as e:
        logger.error(f"[BATCH] Transfer error: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


async def _execute_airtime_direct(
    phone_number: str,
    user_id: str,
    task: PlannedTask,
    result_data: dict[str, Any],
    redis_client: Redis,
    queue: RedisQueue,
) -> dict[str, Any]:
    """Execute airtime using AirtimeAuthorization directly."""
    try:
        amount = task.parameters.get("amount") if task.parameters else None
        is_valid, error_msg, validated_amount = validate_amount_limits(amount, AIRTIME_LIMITS)
        if not is_valid:
            logger.warning(f"[BATCH] Invalid airtime amount: {error_msg}")
            return {"success": False, "error": error_msg or "Invalid amount"}

        idem_key = result_data.get("idempotency_key") or f"batch-airtime-{task.id}"

        state = {
            "phone_number": phone_number,
            "idempotency_key": idem_key,
            "pin_verified": True,
            "user_profile": {"id": user_id},
            "amount": validated_amount,
            "recipient_phone": task.parameters.get("recipient") if task.parameters else phone_number,
            "network": result_data.get("network", ""),
            "selected_source_account": result_data.get("source_account", {}),
        }

        auth = AirtimeAuthorization(redis_client, queue)
        result_state = await auth.authorize(state)

        if result_state.get("airtime_status") == "authorized":
            return {
                "success": True,
                "amount": state["amount"],
                "recipient": state["recipient_phone"],
            }
        else:
            return {
                "success": False,
                "error": result_state.get("response", "Airtime failed"),
            }

    except Exception as e:
        logger.error(f"[BATCH] Airtime error: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


async def _execute_data_direct(
    phone_number: str,
    user_id: str,
    task: PlannedTask,
    result_data: dict[str, Any],
    redis_client: Redis,
    queue: RedisQueue,
) -> dict[str, Any]:
    """Execute data purchase using DataAuthorization directly."""
    try:
        idem_key = result_data.get("idempotency_key") or f"batch-data-{task.id}"
        selected_plan = result_data.get("selected_plan")

        if not selected_plan:
            return {"success": False, "error": "No data plan selected"}

        plan_amount = selected_plan.get("amount") if isinstance(selected_plan, dict) else None
        if plan_amount is not None:
            is_valid, error_msg, _ = validate_amount_limits(plan_amount, DATA_LIMITS)
            if not is_valid:
                logger.warning(f"[BATCH] Invalid data plan amount: {error_msg}")
                return {"success": False, "error": error_msg or "Invalid plan amount"}

        state = {
            "phone_number": phone_number,
            "idempotency_key": idem_key,
            "pin_verified": True,
            "user_profile": {"id": user_id},
            "selected_plan": selected_plan,
            "target_phone": result_data.get("target_phone", phone_number),
            "network": result_data.get("network", ""),
            "source": result_data.get("source", "self"),
        }

        auth = DataAuthorization(redis_client, queue)
        result_state = await auth.authorize(state)

        if result_state.get("data_status") == "authorized":
            return {
                "success": True,
                "amount": selected_plan.get("amount") if isinstance(selected_plan, dict) else None,
                "plan": selected_plan.get("name") if isinstance(selected_plan, dict) else str(selected_plan),
            }
        else:
            return {
                "success": False,
                "error": result_state.get("response", "Data purchase failed"),
            }

    except Exception as e:
        logger.error(f"[BATCH] Data error: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


async def _execute_remaining_tasks(
    phone_number: str,
    planner_output,
    task_results: dict[str, Any],
    user_cache,
    query_graph,
    account_management_service,
    whatsapp_client: WhatsAppClient,
    task_queue_service: TaskQueueService,
) -> None:
    """Execute non-auth tasks (query, utility, manage_accounts)."""
    remaining_tasks = [
        task
        for task in planner_output.tasks
        if task.executor in ("query", "utility", "manage_accounts")
        and task_results.get(task.id, {}).get("status") != TaskStatus.COMPLETED.value
    ]

    if not remaining_tasks or not user_cache:
        return

    user_data = await user_cache.get_all(phone_number)
    accounts = user_data.get("accounts", []) if user_data else []
    user_ctx = {"accounts": accounts}

    for task in remaining_tasks:
        try:
            result = None
            if task.executor == "query" and query_graph:
                query_message = task.instruction or "show balance"
                result = await query_graph.run(phone_number, query_message, user_ctx)
            elif task.executor == "manage_accounts" and account_management_service:
                task_message = task.instruction or "show accounts"
                result = await account_management_service.handle_account_management(
                    phone_number, task_message, user_ctx
                )

            if result:
                await whatsapp_client.send_text(phone_number, result)
            await task_queue_service.update_task_status(phone_number, task.id, TaskStatus.COMPLETED, {"result": result})
        except Exception as e:
            logger.error(f"[BATCH] Error executing {task.executor} task {task.id}: {e}")


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


def _format_success_message(task: PlannedTask, result: dict[str, Any]) -> str:
    """Format a success message for a completed task."""
    if task.executor == "transfer":
        amount = result.get("amount") or (task.parameters.get("amount") if task.parameters else None)
        recipient = result.get("recipient") or (task.parameters.get("recipient") if task.parameters else None)
        txn_id = result.get("transaction_id", "")
        txn_suffix = f" (ID: {txn_id[:8]})" if txn_id else ""

        if amount and recipient:
            return f"✓ Transfer ₦{float(amount):,.0f} to {recipient}{txn_suffix}"
        elif amount:
            return f"✓ Transfer ₦{float(amount):,.0f}{txn_suffix}"
        return f"✓ Transfer completed{txn_suffix}"

    elif task.executor == "airtime":
        amount = result.get("amount") or (task.parameters.get("amount") if task.parameters else None)
        recipient = result.get("recipient") or (task.parameters.get("recipient") if task.parameters else None)

        if amount and recipient:
            return f"✓ Airtime {format_amount(amount)} to {recipient}"
        return "✓ Airtime completed"

    elif task.executor == "data":
        plan = result.get("plan", "")
        if plan:
            return f"✓ Data {plan} queued"
        return "✓ Data purchase queued"

    return f"✓ {_format_task_description(task)} completed"


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
            if task.id in completed_map:
                summary += f"{i}. {task_desc} ✓\n"
                if task.parameters and task.parameters.get("amount"):
                    total_amount += task.parameters["amount"]
            else:
                error = failed_map.get(task.id, {}).get("error", "Unknown error")
                summary += f"{i}. {task_desc} ✗ ({error})\n"

        if total_amount > 0:
            summary += f"\nTotal sent: {format_amount(total_amount)}"

    return summary
