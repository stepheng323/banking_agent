"""Airtime task handler.

Wraps AirtimeService for workflow execution.
Hybrid preflight: fast param check + capability negotiation.
"""

from shared.types.planner import PlannedTask
from shared.utils.logging import get_logger
from shared.capabilities.airtime import derive_requirements, decide_capability
from shared.protocols.services import AirtimeServiceProtocol

from apps.core.src.agent.workflow.context import WorkflowContext
from apps.core.src.agent.workflow.models import TaskResult, TaskStatus
from apps.core.src.agent.workflow.handlers.base import success_result, needs_input_result, failed_result

logger = get_logger(__name__)


class AirtimeTaskHandler:
    """Handler that executes airtime tasks via AirtimeService."""
    
    def __init__(self, airtime_service):
        self.service = airtime_service
    
    async def validate(self, task: PlannedTask, ctx: WorkflowContext) -> TaskResult | None:
        """
        Hybrid preflight validation:
        1. Fast param check (amount, phone)
        2. Capability negotiation (scheduled, etc.)
        """
        params = task.parameters
        
        amount = params.amount
        phone = params.phone or params.recipient_phone
        is_self = params.is_self
        
        missing = []
        if not amount:
            missing.append("amount")
        if not phone and not is_self:
            missing.append("phone")
        
        if missing:
            return needs_input_result(
                task.task_id,
                missing,
                f"I need the {' and '.join(missing)} for this airtime purchase.",
            )
        
        instruction = task.instruction or ""
        requirements = derive_requirements(user_message=instruction, is_self=is_self)
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
        """Execute airtime purchase."""
        try:
            response = await self.service.run_simple(
                phone=ctx.phone_number,
                text=task.instruction,
                classification_result={"intent": "airtime", "confidence": 1.0},
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
                "airtime_task_completed",
                task_id=task.task_id,
                amount=task.parameters.amount,
            )
            
            return success_result(
                task.task_id,
                {"response": response, "amount": task.parameters.amount},
            )
            
        except Exception as e:
            logger.exception("airtime_task_failed", task_id=task.task_id)
            return failed_result(task.task_id, str(e))


