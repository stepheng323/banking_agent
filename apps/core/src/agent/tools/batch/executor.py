"""Batch executor service for parallel task execution."""

import asyncio
from typing import List, Dict, Any, Optional, TYPE_CHECKING

from shared.cache.redis_client import RedisClient
from shared.clients.whatsapp.client import WhatsAppClient
from shared.services.task_queue import TaskQueueService
from apps.core.src.agent.tools.batch.utils import (
    ExecutionState,
    format_amount,
)
from shared.types.planner import PlannedTask
from shared.types.agent_types import TaskStatus
from shared.utils.logging import get_logger

logger = get_logger(__name__)

if TYPE_CHECKING:
    from apps.core.src.agent.sub_agents.transfer.service import TransferService
    from apps.core.src.agent.sub_agents.airtime.service import AirtimeService
    from apps.core.src.agent.sub_agents.query.graph import QueryFlowGraph
    from apps.core.src.agent.sub_agents.account_management.service import AccountManagementService
    from shared.cache.user_data import UserDataCache


async def execute_batch(
    phone_number: str,
    pin_verified: bool,
    whatsapp_client: WhatsAppClient,
    task_queue_service: TaskQueueService,
    transfer_service: "TransferService",
    airtime_service: Optional["AirtimeService"] = None,
    query_graph: Optional["QueryFlowGraph"] = None,
    user_cache: Optional["UserDataCache"] = None,
    account_management_service: Optional["AccountManagementService"] = None,
) -> Dict[str, Any]:
    """
    Execute all tasks in parallel using asyncio.gather().
    
    Args:
        phone_number: User's phone number
        pin_verified: Whether PIN was verified
        whatsapp_client: WhatsApp client for sending messages
        task_queue_service: Task queue service
        transfer_service: Transfer service
        airtime_service: Optional airtime service
        
    Returns:
        Dictionary with execution results
    """
    redis_client = RedisClient.get_client()
    
    try:
        await redis_client.set(
            f"queue:{phone_number}:execution_state",
            ExecutionState.EXECUTING_BATCH,
            ex=600
        )
        
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
        
        await whatsapp_client.send_text(
            phone_number,
            f"⏳ Processing {total} task{'s' if total > 1 else ''} in parallel..."
        )
        
        async_tasks = []
        for task in tasks_to_execute:
            async_task = _execute_single_task(
                phone_number=phone_number,
                task=task,
                total_tasks=total,
                pin_verified=pin_verified,
                whatsapp_client=whatsapp_client,
                task_queue_service=task_queue_service,
                transfer_service=transfer_service,
                airtime_service=airtime_service,
            )
            async_tasks.append(async_task)
        
        results = await asyncio.gather(*async_tasks, return_exceptions=True)
        
        completed = []
        failed = []
        
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"[BATCH] Task {tasks_to_execute[i].id} raised exception: {result}")
                failed.append({
                    "task": tasks_to_execute[i].model_dump(),
                    "task_id": tasks_to_execute[i].id,
                    "error": str(result)
                })
            elif result.get("success"):
                completed.append(result)
            else:
                failed.append(result)
        
        summary = _generate_final_summary(tasks_to_execute, completed, failed)
        await whatsapp_client.send_text(phone_number, summary)
        
        # Execute remaining non-auth tasks (query, utility, manage_accounts) that were waiting on transfers
        remaining_tasks = [
            task for task in planner_output.tasks
            if task.executor in ("query", "utility", "manage_accounts") and 
               task_results.get(task.id, {}).get("status") != TaskStatus.COMPLETED.value
        ]
        
        if remaining_tasks and user_cache:
            # Load user accounts from cache for context
            user_data = await user_cache.get_all(phone_number)
            accounts = user_data.get("accounts", []) if user_data else []
            user_ctx = {"accounts": accounts}
            
            for task in remaining_tasks:
                try:
                    if task.executor == "query" and query_graph:
                        query_message = task.instruction or "show balance"
                        result = await query_graph.run(phone_number, query_message, user_ctx)
                    elif task.executor == "manage_accounts" and account_management_service:
                        task_message = task.instruction or "show accounts"
                        result = await account_management_service.handle_account_management(
                            phone_number, task_message, user_ctx
                        )
                    else:
                        result = None
                    
                    if result:
                        await whatsapp_client.send_text(phone_number, result)
                    await task_queue_service.update_task_status(
                        phone_number, task.id, TaskStatus.COMPLETED, {"result": result}
                    )
                except Exception as e:
                    logger.error(f"[BATCH] Error executing {task.executor} task {task.id}: {e}")
        
        await task_queue_service.clear_task_queue(phone_number)
        await redis_client.delete(f"queue:{phone_number}:execution_state")
        
        logger.info(f"[BATCH] Completed {len(completed)}/{total} tasks for {phone_number}")
        
        return {
            "completed": len(completed),
            "failed": len(failed),
            "total": total,
            "results": results
        }
    
    except Exception as e:
        logger.error(f"[BATCH] Error executing batch: {e}", exc_info=True)
        
        try:
            await whatsapp_client.send_text(
                phone_number,
                "❌ An error occurred while processing your tasks. Please try again."
            )
        except Exception:
            pass
        
        await redis_client.delete(f"queue:{phone_number}:execution_state")
        
        return {
            "completed": 0,
            "failed": 0,
            "total": 0,
            "error": str(e)
        }


async def _execute_single_task(
    phone_number: str,
    task: PlannedTask,
    total_tasks: int,
    pin_verified: bool,
    whatsapp_client: WhatsAppClient,
    task_queue_service: TaskQueueService,
    transfer_service: "TransferService",
    airtime_service: Optional["AirtimeService"],
) -> Dict[str, Any]:
    """
    Execute a single task and send progress update when done.
    
    This runs in parallel with other tasks.
    """
    redis_client = RedisClient.get_client()
    task_desc = _format_task_description(task)
    
    try:
        cancelled = await redis_client.get(f"queue:{phone_number}:cancel_batch")
        if cancelled:
            logger.warning(f"[BATCH] Task {task.id} cancelled before execution")
            return {
                "success": False,
                "cancelled": True,
                "task_id": task.id,
                "task_desc": task_desc
            }
        
        await task_queue_service.update_task_status(
            phone_number,
            task.id,
            TaskStatus.IN_PROGRESS
        )
        
        logger.info(f"[BATCH] Executing task {task.id}: {task_desc}")
        
        if task.executor == "transfer":
            result = await _execute_transfer_task(
                phone_number, task, transfer_service, pin_verified, task_queue_service
            )
        elif task.executor == "airtime":
            if not airtime_service:
                result = {
                    "success": False,
                    "error": "Airtime service not available"
                }
            else:
                result = await _execute_airtime_task(
                    phone_number, task, airtime_service, pin_verified, task_queue_service
                )
        else:
            result = {
                "success": False,
                "error": f"Unknown executor: {task.executor}"
            }
        
        result["task_desc"] = task_desc
        result["task_id"] = task.id
        
        if result["success"]:
            await task_queue_service.update_task_status(
                phone_number,
                task.id,
                TaskStatus.COMPLETED,
                result
            )
            
            success_msg = _format_success_message(task, result)
            await whatsapp_client.send_text(phone_number, success_msg)
            
            logger.info(f"[BATCH] Task {task.id} completed: {task_desc}")
        else:
            await task_queue_service.update_task_status(
                phone_number,
                task.id,
                TaskStatus.FAILED,
                result
            )
            
            error_msg = result.get('error', 'Unknown error')
            await whatsapp_client.send_text(
                phone_number,
                f"⚠️ {task_desc} failed: {error_msg}"
            )
            
            logger.error(f"[BATCH] Task {task.id} failed: {error_msg}")
        
        return result
        
    except Exception as e:
        logger.error(f"[BATCH] Task {task.id} exception: {e}", exc_info=True)
        
        await task_queue_service.update_task_status(
            phone_number,
            task.id,
            TaskStatus.FAILED,
            {"error": str(e)}
        )
        
        await whatsapp_client.send_text(
            phone_number,
            f"❌ {task_desc} error: {str(e)}"
        )
        
        return {
            "success": False,
            "error": str(e),
            "task_id": task.id,
            "task_desc": task_desc
        }


async def _execute_transfer_task(
    phone_number: str,
    task: PlannedTask,
    transfer_service: "TransferService",
    pin_verified: bool,
    task_queue_service: TaskQueueService,
) -> Dict[str, Any]:
    """Execute a transfer task."""
    try:
        task_results = await task_queue_service.get_task_results(phone_number)
        task_result = task_results.get(task.id, {})
        result_data = task_result.get("result", {})
        
        response = await transfer_service.graph.resume_after_pin_verification(
            phone_number, pin_verified, None
        )
        
        from apps.core.src.agent.sub_agents.transfer.graph.graph import TransferFlowGraph
        config = {
            "configurable": {
                "thread_id": f"transfer:{phone_number}",
            }
        }
        final_state = None
        if transfer_service.graph.graph:
            final_state = await transfer_service.graph.graph.aget_state(config)
        
        if final_state and final_state.values:
            transfer_status = final_state.values.get("transfer_status")
            
            if transfer_status == "completed":
                return {
                    "success": True,
                    "amount": task.parameters.get("amount") if task.parameters else None,
                    "recipient": result_data.get("account_resolved", {}).get("account_name") if isinstance(result_data, dict) else None,
                    "response": response
                }
            else:
                return {
                    "success": False,
                    "error": final_state.values.get("response", "Transfer failed")
                }
        
        return {"success": True, "response": response}
        
    except Exception as e:
        logger.error(f"Error executing transfer task: {e}")
        return {"success": False, "error": str(e)}


async def _execute_airtime_task(
    phone_number: str,
    task: PlannedTask,
    airtime_service: "AirtimeService",
    pin_verified: bool,
    task_queue_service: TaskQueueService,
) -> Dict[str, Any]:
    """Execute an airtime task."""
    try:
        # Resume airtime graph for authorization/execution
        response = await airtime_service.graph.resume_after_pin_verification(
            phone_number, pin_verified, None
        )
        
        return {
            "success": True,
            "amount": task.parameters.get("amount") if task.parameters else None,
            "recipient": task.parameters.get("recipient") if task.parameters else None,
            "response": response
        }
        
    except Exception as e:
        logger.error(f"Error executing airtime task: {e}")
        return {"success": False, "error": str(e)}


def _format_task_description(task: PlannedTask) -> str:
    """Format a task for display."""
    if task.executor == "transfer":
        amount = task.parameters.get("amount") if task.parameters else None
        recipient = task.parameters.get("recipient") if task.parameters else None
        if amount and recipient:
            return f"Transfer {format_amount(amount)} to {recipient}"
        elif amount:
            return f"Transfer {format_amount(amount)}"
        else:
            return "Transfer"
    
    elif task.executor == "airtime":
        amount = task.parameters.get("amount") if task.parameters else None
        recipient = task.parameters.get("recipient") if task.parameters else None
        if amount and recipient:
            return f"Airtime {format_amount(amount)} to {recipient}"
        elif amount:
            return f"Airtime {format_amount(amount)}"
        else:
            return "Airtime"
    
    else:
        return task.action or task.executor


def _format_success_message(task: PlannedTask, result: Dict[str, Any]) -> str:
    """Format a success message for a completed task."""
    if task.executor == "transfer":
        amount = result.get("amount") or (task.parameters.get("amount") if task.parameters else None)
        recipient = result.get("recipient") or (task.parameters.get("recipient") if task.parameters else None)
        txn_id = result.get("transaction_id", "N/A")
        
        if amount and recipient:
            return f"✅ Transfer successful! ₦{float(amount):,.0f} has been sent to {recipient}. Transaction ID: {txn_id}"
        elif amount:
            return f"✅ Transfer successful! ₦{float(amount):,.0f} sent. Transaction ID: {txn_id}"
        else:
            return f"✅ Transfer completed. Transaction ID: {txn_id}"
    
    elif task.executor == "airtime":
        amount = result.get("amount") or (task.parameters.get("amount") if task.parameters else None)
        recipient = result.get("recipient") or (task.parameters.get("recipient") if task.parameters else None)
        
        if amount and recipient:
            return f"✅ Airtime {format_amount(amount)} to {recipient} completed"
        else:
            return "✅ Airtime completed"
    
    else:
        return f"✅ {_format_task_description(task)} completed"


def _generate_final_summary(
    tasks: List[PlannedTask],
    completed: List[Dict[str, Any]],
    failed: List[Dict[str, Any]]
) -> str:
    """Generate final summary of batch execution."""
    total = len(tasks)
    completed_count = len(completed)
    failed_count = len(failed)
    
    if failed_count == 0:
        summary = f"✅ All {total} task{'s' if total > 1 else ''} completed!\n\n"
        
        total_amount = 0
        for i, task in enumerate(tasks, 1):
            task_desc = _format_task_description(task)
            summary += f"{i}. {task_desc} ✓\n"
            
            if task.parameters and task.parameters.get("amount"):
                total_amount += task.parameters["amount"]
        
        if total_amount > 0:
            summary += f"\nTotal: {format_amount(total_amount)}"
    
    elif completed_count == 0:
        summary = f"❌ All tasks failed. Please check and try again.\n\n"
        
        for i, task in enumerate(tasks, 1):
            task_desc = _format_task_description(task)
            error = failed[i-1].get("error", "Unknown error") if i <= len(failed) else "Unknown error"
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
