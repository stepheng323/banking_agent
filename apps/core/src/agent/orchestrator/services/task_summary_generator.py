"""Task summary generation for the orchestrator."""

from typing import Any

from shared.services.task_queue import TaskQueueService
from shared.types.agent_types import TaskStatus
from shared.formatters.transfer import _calculate_transfer_fee, _format_currency_naira
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class TaskSummaryGenerator:
    """Generates summary messages for task completion and batch authorization."""

    def __init__(self, task_queue_service: TaskQueueService):
        self.task_queue_service = task_queue_service

    def format_task_description(self, task: Any) -> str:
        """Format a task into a human-readable description."""
        params = task.parameters or {}
        executor = task.executor

        if executor == "transfer":
            amount = params.get("amount")
            recipient = params.get("recipient")
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

    async def generate_batch_summary(self, phone_number: str) -> str:
        """Generate summary of all tasks ready for batch authorization."""
        planner_output = await self.task_queue_service.get_task_queue(phone_number)
        if not planner_output:
            return "Ready to authorize transactions."

        results = await self.task_queue_service.get_task_results(phone_number)
        
        transfers = []
        source_accounts = set()
        total_amount = 0
        total_fee = 0

        for i, task in enumerate(planner_output.tasks, 1):
            task_result = results.get(task.id, {})
            result_data = task_result.get("result", {})
            executor = task.executor

            if executor == "transfer":
                amount = float(task.parameters.get("amount", 0)) if task.parameters else 0
                if amount:
                    total_amount += amount
                    total_fee += _calculate_transfer_fee(amount)

                account_resolved = result_data.get("account_resolved") if isinstance(result_data, dict) else None
                recipient_name = (
                    account_resolved.get("account_name") if account_resolved
                    else task.parameters.get("recipient", "Recipient") if task.parameters
                    else "Recipient"
                )
                
                recipient_account = result_data.get("recipient_account", "") if isinstance(result_data, dict) else ""
                recipient_bank = result_data.get("recipient_bank_name", "") if isinstance(result_data, dict) else ""
                
                selected_source = result_data.get("selected_source_account") if isinstance(result_data, dict) else None
                source_bank = selected_source.get("bank_name", "") if selected_source else ""
                source_account = selected_source.get("account_number", "") if selected_source else ""

                if source_account:
                    source_accounts.add((source_bank, source_account))

                transfers.append({
                    "index": i,
                    "amount": amount,
                    "recipient_name": recipient_name.title() if recipient_name else "Recipient",
                    "recipient_bank": recipient_bank.title() if recipient_bank else "",
                    "recipient_account": recipient_account,
                    "source_bank": source_bank,
                    "source_account": source_account,
                    "type": "transfer"
                })
            elif executor == "airtime":
                amount = float(task.parameters.get("amount", 0)) if task.parameters else 0
                recipient = task.parameters.get("recipient") if task.parameters else "recipient"
                if amount:
                    total_amount += amount
                    transfers.append({
                        "index": i,
                        "type": "airtime",
                        "amount": amount,
                        "recipient": recipient,
                        "source_account": "",
                    })

        if not transfers:
            return "Ready to authorize transactions."

        return self._format_batch_summary(transfers, source_accounts, total_amount, total_fee)

    def _format_batch_summary(
        self,
        transfers: list[dict],
        source_accounts: set,
        total_amount: float,
        total_fee: float
    ) -> str:
        """Format the batch summary message."""
        same_source = len(source_accounts) == 1
        shared_source = list(source_accounts)[0] if same_source and source_accounts else None

        lines = [f"*Authorize {len(transfers)} Transaction{'s' if len(transfers) > 1 else ''}*"]

        if same_source and shared_source:
            source_bank, source_account = shared_source
            last4 = source_account[-4:] if source_account else "????"
            lines.append(f"From: {source_bank} (...{last4})")

        lines.append("")

        for t in transfers:
            if t.get("type") == "airtime":
                lines.append(f"{t['index']}. {_format_currency_naira(t['amount'])} → {t['recipient']} (airtime)")
            else:
                lines.append(f"{t['index']}. {_format_currency_naira(t['amount'])} → *{t['recipient_name']}*")
                lines.append(f"   {t['recipient_bank']} • {t['recipient_account']}")
                
                if not same_source and t["source_account"]:
                    last4 = t["source_account"][-4:] if t["source_account"] else "????"
                    lines.append(f"   From: {t['source_bank']} (...{last4})")

        lines.append("")
        lines.append(
            f"Total: {_format_currency_naira(total_amount)} + {_format_currency_naira(total_fee)} fee = *{_format_currency_naira(total_amount + total_fee)}*"
        )

        return "\n".join(lines)

    async def generate_completion_summary(self, phone_number: str) -> str:
        """Generate final summary message when all tasks complete."""
        planner_output = await self.task_queue_service.get_task_queue(phone_number)
        if not planner_output:
            return "✓ All tasks completed!"

        results = await self.task_queue_service.get_task_results(phone_number)
        completed_tasks = []
        
        for i, task in enumerate(planner_output.tasks, 1):
            task_desc = self.format_task_description(task)
            task_result = results.get(task.id, {})
            status = task_result.get("status")

            if status == TaskStatus.COMPLETED.value:
                completed_tasks.append(f"{i}. {task_desc}")
            elif status == TaskStatus.FAILED.value:
                completed_tasks.append(f"{i}. {task_desc} (failed)")

        if completed_tasks:
            summary = "✓ All tasks completed!\n\n" + "\n".join(completed_tasks)
            summary += "\n\nIs there anything else I can help you with?"
            return summary
        else:
            return "✓ All tasks completed!"
