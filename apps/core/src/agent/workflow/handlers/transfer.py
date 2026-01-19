"""Transfer task handler.

Wraps TransferService for workflow execution.
Hybrid preflight: fast param check + capability negotiation.
"""

from shared.types.planner import PlannedTask
from shared.utils.logging import get_logger
from shared.capabilities.transfer import derive_requirements, decide_capability
from shared.protocols.services import TransferServiceProtocol

from apps.core.src.agent.workflow.context import WorkflowContext
from apps.core.src.agent.workflow.models import TaskResult, TaskStatus
from apps.core.src.agent.workflow.handlers.base import success_result, needs_input_result, failed_result

logger = get_logger(__name__)


class TransferTaskHandler:
    """Handler that executes transfer tasks via TransferService."""
    
    def __init__(self, transfer_service):
        """
        Args:
            transfer_service: TransferService instance
        """
        self.service = transfer_service
    
    async def validate(self, task: PlannedTask, ctx: WorkflowContext) -> TaskResult | None:
        """
        Hybrid preflight validation:
        1. Fast param check (amount, recipient)
        2. Graph capability check (scheduled, recurring, etc.)
        
        Returns None if ready, TaskResult with NEEDS_INPUT if missing fields or negotiation needed.
        """
        params = task.parameters
        
        recipient = params.recipient or params.recipient_name
        amount = params.amount
        account = params.recipient_account
        bank = params.bank_name
        
        has_recipient = bool(recipient) or (account and bank)
        
        missing = []
        if not amount:
            missing.append("amount")
        if not has_recipient:
            missing.append("recipient")
        
        if missing:
            return needs_input_result(
                task.task_id,
                missing,
                f"I need the {' and '.join(missing)} for this transfer.",
            )
        
        if recipient and not (account and bank):
            if ctx.beneficiaries:
                beneficiary = self._find_beneficiary(recipient, ctx.beneficiaries)
                if not beneficiary:
                    return needs_input_result(
                        task.task_id,
                        ["recipient_account", "bank_name"],
                        f"Who is {recipient}? Please provide their bank and account number.",
                    )
        instruction = task.instruction or ""
        requirements = derive_requirements(user_message=instruction)
        capability_decision = decide_capability(requirements)
        
        if not capability_decision.allowed:
            prompt = capability_decision.prompt
            
            if capability_decision.suggested_action:
                return TaskResult(
                    task_id=task.task_id,
                    status=TaskStatus.NEEDS_INPUT,
                    missing_fields=["capability_confirmation"],
                    user_prompt=prompt,
                    data={
                        "negotiation": True,
                        "suggested_action": capability_decision.suggested_action,
                        "patch": capability_decision.patch,
                    },
                )
            else:
                return TaskResult(
                    task_id=task.task_id,
                    status=TaskStatus.FAILED,
                    error=prompt,
                )
        
        return None
    
    async def execute(self, task: PlannedTask, ctx: WorkflowContext) -> TaskResult:
        """Execute transfer via TransferService."""
        try:
            params = task.parameters
            instruction = task.instruction
            
            # Send the task instruction to the graph
            # Note: For mid-flow updates, the orchestrator handles passing new user input via ctx.additional_input
            text_to_send = instruction
            
            # Check if this is a mid-flow resume (from ctx.additional_input set by resume_workflow)
            is_resume = bool(ctx.additional_input and ctx.additional_input.get("user_text"))
            classification = {"intent": "transfer", "confidence": 1.0}
            if is_resume:
                text_to_send = ctx.additional_input["user_text"]
                classification["complexity_reason"] = "Flow resume after interrupt"
            
            logger.info(
                "transfer_handler_debug",
                task_instruction=instruction[:50] if instruction else None,
                text_to_send=text_to_send[:50] if text_to_send else None,
                is_resume=is_resume,
            )
            
            response = await self.service.run_simple(
                phone=ctx.phone_number,
                text=text_to_send,
                classification_result=classification,
            )

            # Check for auth requirement from graph state
            last_state = await self.service.get_last_state(ctx.phone_number)
            if last_state:
                flow_state = last_state.get("flow_state")
                if flow_state in ["confirming", "confirming_funding", "authorizing"]:
                    return TaskResult(
                        task_id=task.task_id,
                        status=TaskStatus.AWAITING_AUTH,
                        data={
                            "confirmation_summary": last_state.get("confirmation_summary"),
                            "confirmation_token": last_state.get("confirmation_token"),
                        },
                        user_prompt=last_state.get("confirmation_summary"),
                    )
            
            logger.info(
                "transfer_task_completed",
                task_id=task.task_id,
                amount=params.amount,
                recipient=params.recipient,
            )
            
            return success_result(
                task.task_id,
                {
                    "response": response,
                    "amount": params.amount,
                    "recipient": params.recipient,
                },
            )
            
        except Exception as e:
            logger.exception("transfer_task_failed", task_id=task.task_id)
            return failed_result(task.task_id, str(e))
    
    def _find_beneficiary(self, name: str, beneficiaries: list[dict]) -> dict | None:
        """Find beneficiary by name (case-insensitive)."""
        name_lower = name.lower()
        for b in beneficiaries:
            if b.get("name", "").lower() == name_lower:
                return b
            if b.get("alias", "").lower() == name_lower:
                return b
        return None
