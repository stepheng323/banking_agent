"""Flow completion callback implementation for orchestrator."""

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from apps.core.src.agent.orchestrator.orchestrator import OrchestratorAgent

from apps.core.src.agent.services.task_queue_service import TaskQueueService
from shared.types.agent_types import TaskStatus
from shared.cache.redis_client import RedisClient
from shared.config import settings
import time


def debug_log(message: str) -> None:
    """Simple debug logging."""
    print(message)


class OrchestratorFlowCompletionCallback:
    """Callback that triggers next task execution when a flow completes."""

    def __init__(
        self,
        task_queue_service: TaskQueueService,
        orchestrator: "OrchestratorAgent",
    ):
        self.task_queue_service = task_queue_service
        self.orchestrator = orchestrator

    def _format_task_description(self, task: Any) -> str:
        """
        Format a task into a human-readable description.
        
        Args:
            task: PlannedTask object
            
        Returns:
            Formatted task description
        """
        params = task.parameters or {}
        executor = task.executor
        
        if executor == "transfer":
            amount = params.get("amount")
            recipient = params.get("recipient")
            # Normalize recipient name to title case for consistency
            if recipient:
                recipient = recipient.strip().title()
            if amount and recipient:
                return f"₦{amount:,.0f} to {recipient}"
            elif amount:
                return f"₦{amount:,.0f} transfer"
            elif recipient:
                return f"transfer to {recipient}"
            return "transfer"
        elif executor == "airtime":
            amount = params.get("amount")
            if amount:
                return f"₦{amount:,.0f} airtime purchase"
            return "airtime purchase"
        else:
            return task.instruction or task.action

    async def _generate_batch_summary(
        self, phone_number: str
    ) -> str:
        """
        Generate summary of all tasks ready for batch authorization.
        Shows all tasks with resolved account names.
        
        Args:
            phone_number: User's phone number
            
        Returns:
            Summary message string
        """
        planner_output = await self.task_queue_service.get_task_queue(phone_number)
        if not planner_output:
            return "Ready to authorize transactions."
        
        # Get task results to get resolved account names
        results = await self.task_queue_service.get_task_results(phone_number)
        
        # Build summary with resolved account names
        task_summaries = []
        total_amount = 0
        has_authorizable_tasks = False  # Track if any tasks need authorization
        
        for i, task in enumerate(planner_output.tasks, 1):
            task_result = results.get(task.id, {})
            result_data = task_result.get("result", {})
            executor = task.executor
            
            if executor == "transfer":
                has_authorizable_tasks = True
                # Get resolved account name from result
                account_resolved = result_data.get("account_resolved") if isinstance(result_data, dict) else None
                amount = task.parameters.get("amount") if task.parameters else None
                recipient = task.parameters.get("recipient") if task.parameters else None
                
                if amount:
                    total_amount += float(amount)
                
                # Format task description with resolved account name
                if account_resolved and isinstance(account_resolved, dict):
                    account_name = account_resolved.get("account_name", recipient or "Recipient")
                    account_number = result_data.get("recipient_account", "") if isinstance(result_data, dict) else ""
                    bank_name = result_data.get("recipient_bank_name", "") if isinstance(result_data, dict) else ""
                    if account_number:
                        account_display = f"{account_name} ({account_number[-4:] if len(account_number) >= 4 else account_number} - {bank_name})"
                    else:
                        account_display = account_name
                else:
                    account_display = recipient or "Recipient"
                
                # Get source account info
                source_account = result_data.get("selected_source_account") if isinstance(result_data, dict) else None
                source_display = ""
                
                if source_account and isinstance(source_account, dict):
                    source_bank = source_account.get("bank_name", "")
                    source_num = source_account.get("account_number", "")
                    
                    if source_bank and source_num:
                        # Format: "from GTBank (5261)"
                        source_display = f" from {source_bank} ({source_num[-4:]})"
                    elif source_bank:
                        # Only bank name available
                        source_display = f" from {source_bank}"
                
                if amount:
                    task_summaries.append(f"{i}. ₦{amount:,.0f} to {account_display}{source_display}")
                else:
                    task_summaries.append(f"{i}. Transfer to {account_display}{source_display}")
            
            elif executor == "airtime":
                has_authorizable_tasks = True
                amount = task.parameters.get("amount") if task.parameters else None
                recipient = task.parameters.get("recipient") if task.parameters else None
                if amount:
                    total_amount += float(amount)
                    task_summaries.append(f"{i}. ₦{amount:,.0f} airtime to {recipient or 'recipient'}")
                else:
                    task_summaries.append(f"{i}. Airtime purchase")
            
            elif executor == "query":
                # For balance checks or other queries, they don't need authorization
                # But we should still show them in the summary
                query_type = task.parameters.get("query_type", "query") if task.parameters else "query"
                task_summaries.append(f"{i}. {query_type.title()}")
            
            else:
                # Generic task
                task_desc = self._format_task_description(task)
                task_summaries.append(f"{i}. {task_desc}")
                # Assume generic tasks might need authorization
                if executor not in ("query", "conversational"):
                    has_authorizable_tasks = True
        
        summary = f"📋 Summary of {len(task_summaries)} task{'s' if len(task_summaries) > 1 else ''}:\n\n"
        summary += "\n".join(task_summaries)
        if total_amount > 0:
            summary += f"\n\n*Total: ₦{total_amount:,.0f}*"
        
        if has_authorizable_tasks:
            summary += "\n\nPlease confirm to authorize all transactions with a single PIN entry."
        else:
            summary += "\n\nAll tasks are ready. Proceed?"
        
        return summary

    async def _generate_completion_summary(
        self, phone_number: str
    ) -> str:
        """
        Generate final summary message when all tasks complete.
        
        Args:
            phone_number: User's phone number
            
        Returns:
            Summary message string
        """
        planner_output = await self.task_queue_service.get_task_queue(phone_number)
        if not planner_output:
            return "✅ All tasks completed!"
        
        # Get task results
        results = await self.task_queue_service.get_task_results(phone_number)
        
        # Build summary
        completed_tasks = []
        for i, task in enumerate(planner_output.tasks, 1):
            task_desc = self._format_task_description(task)
            task_result = results.get(task.id, {})
            status = task_result.get("status")
            
            if status == TaskStatus.COMPLETED.value:
                completed_tasks.append(f"{i}. {task_desc}")
            elif status == TaskStatus.FAILED.value:
                completed_tasks.append(f"{i}. {task_desc} (failed)")
        
        if completed_tasks:
            summary = "✅ All tasks completed!\n\n" + "\n".join(completed_tasks)
            summary += "\n\nIs there anything else I can help you with?"
            return summary
        else:
            return "✅ All tasks completed!"

    async def _clear_conversation_state(self, phone_number: str) -> None:
        """
        Clear conversation state to prevent active flows from continuing.
        
        Args:
            phone_number: User's phone number
        """
        try:
            redis_client = RedisClient.get_client()
            conversation_state_key = f"user:{phone_number}:conversation_state"
            await redis_client.delete(conversation_state_key)
            print(f"✅ Cleared conversation_state for {phone_number} after all tasks completed")
        except Exception as e:
            print(f"⚠️  Error clearing conversation_state: {e}")

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
            # Check if this is collection_complete (ready for authorization) or fully completed
            # For all flow types, check the status in the result
            status = result.get("status") if isinstance(result, dict) else None
            is_collection_complete = status == "collection_complete"
            
            # CRITICAL: Mark the current task appropriately
            current_task_id = await self.task_queue_service.get_current_task(phone_number)
            # Save the completed_task_id before clearing current_task
            completed_task_id = current_task_id
            if current_task_id:
                if is_collection_complete:
                    # Task has all info but not yet authorized - mark as COLLECTION_COMPLETE
                    await self.task_queue_service.update_task_status(
                        phone_number, 
                        current_task_id, 
                        TaskStatus.COLLECTION_COMPLETE, 
                        result
                    )
                    print(f"✅ Marked task {current_task_id} ({flow_type}) as COLLECTION_COMPLETE (ready for authorization)")
                    
                    # CRITICAL: Clear transfer checkpoint so next task starts fresh
                    # The checkpoint has transfer_status=collection_complete which would cause next task to end immediately
                    if flow_type == "transfer" and hasattr(self.orchestrator, 'transfer'):
                        try:
                            await self.orchestrator.transfer.clear_checkpoint(phone_number)
                            debug_log(f"🧹 [CALLBACK] Cleared transfer checkpoint for {phone_number} after task {current_task_id} reached collection_complete")
                        except Exception as e:
                            debug_log(f"⚠️  [CALLBACK] Error clearing transfer checkpoint: {e}")
                else:
                    # Task fully completed (authorized) - mark as COMPLETED
                    await self.task_queue_service.update_task_status(
                        phone_number, 
                        current_task_id, 
                        TaskStatus.COMPLETED, 
                        result
                    )
                    print(f"✅ Marked task {current_task_id} ({flow_type}) as COMPLETED")
            
            # Get task statuses to check if all are ready
            planner_output = await self.task_queue_service.get_task_queue(phone_number)
            if not planner_output:
                return
            
            # Get all task results to check statuses
            task_results = await self.task_queue_service.get_task_results(phone_number)
            
            # Check if all tasks are collection_complete (ready for batch authorization)
            all_tasks_ready = True
            for task in planner_output.tasks:
                task_status = task_results.get(task.id, {}).get("status")
                if task_status not in (TaskStatus.COLLECTION_COMPLETE.value, TaskStatus.COMPLETED.value):
                    all_tasks_ready = False
                    break
            
            # Get completed task IDs to verify we don't re-execute
            # Include both COMPLETED and COLLECTION_COMPLETE tasks
            completed_task_ids = await self.task_queue_service.get_completed_task_ids(phone_number)
            # Also get collection_complete task IDs (refresh after marking current task)
            task_results = await self.task_queue_service.get_task_results(phone_number)
            collection_complete_ids = [
                task_id for task_id, result in task_results.items()
                if result.get("status") == TaskStatus.COLLECTION_COMPLETE.value
            ]
            # Combine both sets to get all "done" tasks (completed or collection_complete)
            all_done_task_ids = set(completed_task_ids) | set(collection_complete_ids)
            
            # Check if all tasks are collection_complete (ready for batch authorization)
            # Re-check after getting all_done_task_ids to ensure we have the latest status
            all_tasks_ready = all(
                task.id in all_done_task_ids
                for task in planner_output.tasks
            )
            
            if is_collection_complete and all_tasks_ready:
                # All tasks are ready - show summary instead of moving to next task
                debug_log(f"🔍 [CALLBACK] All tasks ready for batch authorization")
                summary = await self._generate_batch_summary(phone_number)
                if hasattr(self.orchestrator, 'whatsapp_client'):
                    # Use WhatsApp Flow for batch authorization
                    flow_token = f"batch-auth-{phone_number}-{int(time.time())}"
                    await self.orchestrator.whatsapp_client.send_flow(
                        to=phone_number,
                        header="Authorize Transactions",
                        flow_cta="Authorize All",
                        flow_id=settings.pin_confirmation_flow_id,
                        screen_name="Pin",
                        flow_token=flow_token,
                        text_body=summary,
                    )
                await self.orchestrator.context_manager.save_last_response(phone_number, summary)
                # Don't proceed to next task - wait for user to confirm summary
                return
            
            # Clear current task to ensure we can find the next one
            await self.task_queue_service.set_current_task(phone_number, None)
            
            # After a task is authorized (fully completed), check if there are more collection_complete tasks
            # If so, automatically send authorization flow for the next one
            if not is_collection_complete:
                # Task was fully authorized - check for next collection_complete task
                task_results = await self.task_queue_service.get_task_results(phone_number)
                next_collection_complete_task = None
                for task in planner_output.tasks:
                    task_status = task_results.get(task.id, {}).get("status")
                    if task_status == TaskStatus.COLLECTION_COMPLETE.value:
                        next_collection_complete_task = task
                        break
                
                if next_collection_complete_task:
                    # Send authorization flow for next task
                    await self.task_queue_service.set_current_task(phone_number, next_collection_complete_task.id)
                    # Trigger authorization by sending message to transfer flow
                    if next_collection_complete_task.executor == "transfer" and hasattr(self.orchestrator, 'transfer'):
                        await self.orchestrator.transfer.run_simple(
                            phone_number, "authorize", {"intent": "transfer"}
                        )
                        return  # Don't proceed with normal next task logic
            
            # Get next pending task (for collection phase)
            # CRITICAL: Only proceed if we have a completed_task_id that was just marked
            # This prevents re-executing tasks that are already collection_complete
            if not completed_task_id:
                # No task was marked - this shouldn't happen, but prevent loop
                debug_log(f"⚠️  [CALLBACK] No completed_task_id found, cannot identify completed task")
                return
            
            # Verify the completed_task_id is in all_done_task_ids (should be after marking)
            if completed_task_id not in all_done_task_ids:
                debug_log(f"⚠️  [CALLBACK] Completed task {completed_task_id} not found in done tasks, may not have been marked correctly")
            
            next_task = await self.task_queue_service.get_next_task(phone_number)
            if next_task:
                # Prevent loop: verify next task is different from completed/collection_complete ones
                if next_task.id in all_done_task_ids:
                    print(f"⚠️  Next task {next_task.id} is already completed/collection_complete, skipping to prevent loop")
                    # Also check if it's the same as the task that just completed
                    if next_task.id == completed_task_id:
                        print(f"⚠️  Next task {next_task.id} is same as just-completed task {completed_task_id}, this is a bug")
                    # Check if there are more tasks or if we should clear the queue
                    planner_output = await self.task_queue_service.get_task_queue(phone_number)
                    if planner_output:
                        all_completed = all(
                            task.id in all_done_task_ids 
                            for task in planner_output.tasks
                        )
                        if all_completed:
                            # All tasks completed, send summary and clear
                            summary = await self._generate_completion_summary(phone_number)
                            if hasattr(self.orchestrator, 'whatsapp_client'):
                                await self.orchestrator.whatsapp_client.send_text(
                                    phone_number, summary
                                )
                            await self.task_queue_service.clear_task_queue(phone_number)
                            await self._clear_conversation_state(phone_number)
                    return
                
                # CRITICAL: Also check if next_task is the same as the task that just completed
                if next_task.id == completed_task_id:
                    print(f"⚠️  Next task {next_task.id} is same as just-completed task {completed_task_id}, skipping to prevent loop")
                    return
                
                # Check if task is already in progress (shouldn't happen after clearing, but safety check)
                current_task_id = await self.task_queue_service.get_current_task(phone_number)
                if current_task_id == next_task.id:
                    print(f"⚠️  Task {next_task.id} is already in progress, skipping to prevent loop")
                    return
                
                # Set the next task as current before executing to prevent race conditions
                await self.task_queue_service.set_current_task(phone_number, next_task.id)
                # Generate progress update message
                planner_output = await self.task_queue_service.get_task_queue(phone_number)
                if planner_output:
                    # Use the completed_task_id that was just marked as the completed task
                    # This is more reliable than searching through tasks
                    completed_task = None
                    if completed_task_id:
                        for task in planner_output.tasks:
                            if task.id == completed_task_id:
                                completed_task = task
                                break
                    
                    # Fallback: If we couldn't find it, search in reverse
                    if not completed_task:
                        for task in reversed(planner_output.tasks):
                            if task.id in all_done_task_ids:
                                completed_task = task
                                break
                    
                    # Debug logging: verify task IDs are different
                    print(f"🔍 Task identification: completed_task_id={completed_task.id if completed_task else None} (saved_completed_task_id={completed_task_id}), next_task_id={next_task.id}")
                    
                    # Also verify this is NOT the same as next_task
                    if completed_task and completed_task.id == next_task.id:
                        print(f"⚠️  Completed task {completed_task.id} is same as next task, skipping transition")
                        completed_task = None
                    
                    # Build transition message only if tasks are different
                    if completed_task and completed_task.id != next_task.id:
                        completed_desc = self._format_task_description(completed_task)
                        next_desc = self._format_task_description(next_task)
                        
                        # Check status of completed task to determine message
                        completed_status = task_results.get(completed_task.id, {}).get("status")
                        
                        if completed_status == TaskStatus.COLLECTION_COMPLETE.value:
                            transition_msg = f"📝 Details for {completed_desc} received. Now let's process {next_desc}."
                        else:
                            transition_msg = f"✅ {completed_desc.capitalize()} completed. Now let's process {next_desc}."
                        
                        # Send transition message via orchestrator's WhatsApp client
                        if hasattr(self.orchestrator, 'whatsapp_client'):
                            await self.orchestrator.whatsapp_client.send_text(
                                phone_number, transition_msg
                            )
                    
                    # Automatically execute next task (regardless of transition message)
                    if hasattr(self.orchestrator, 'task_planner'):
                        next_task_response = await self.orchestrator.task_planner.handle_next_task(
                            phone_number, ""
                        )
                        if next_task_response:
                            # Send next task response
                            if hasattr(self.orchestrator, 'whatsapp_client'):
                                await self.orchestrator.whatsapp_client.send_text(
                                    phone_number, next_task_response
                                )
                            # Save last response
                            if hasattr(self.orchestrator, 'context_manager'):
                                await self.orchestrator.context_manager.save_last_response(
                                    phone_number, next_task_response
                                )
                    
                    print(f"✅ Flow {flow_type} completed. Next task ready: {next_task.id} ({next_task.executor})")
            else:
                # All tasks completed - generate and send summary
                summary = await self._generate_completion_summary(phone_number)
                
                # Send summary via orchestrator's WhatsApp client
                if hasattr(self.orchestrator, 'whatsapp_client'):
                    await self.orchestrator.whatsapp_client.send_text(
                        phone_number, summary
                    )
                
                # Clear task queue
                await self.task_queue_service.clear_task_queue(phone_number)
                
                # Clear conversation state to prevent any active flows from continuing
                await self._clear_conversation_state(phone_number)
                
                print(f"✅ All tasks completed for {phone_number}")
