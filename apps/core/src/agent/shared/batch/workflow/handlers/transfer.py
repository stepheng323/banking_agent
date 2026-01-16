"""Transfer task handler wrapping TransferAuthorization."""

from typing import Any

from apps.core.src.agent.graphs.__shared__.validation.amount_validator import (
    TRANSFER_LIMITS,
)
from apps.core.src.agent.graphs.__shared__.validation.amount_validator import (
    validate_amount as validate_amount_limits,
)
from apps.core.src.agent.graphs.transfer.graph.nodes.authorization import TransferAuthorization
from shared.types.planner import PlannedTask
from shared.utils.logging import get_logger

from ..models import ErrorKind, TaskResult
from ..workflow_context import WorkflowContext
from .base import BaseTaskHandler

logger = get_logger(__name__)


class TransferHandler(BaseTaskHandler):
    """Handler for transfer tasks using TransferAuthorization."""

    async def execute(self, task: PlannedTask, context: WorkflowContext) -> TaskResult:
        """Execute transfer using TransferAuthorization directly."""
        try:
            # Validate amount
            amount = task.parameters.get("amount") if task.parameters else None
            is_valid, error_msg, validated_amount = validate_amount_limits(amount, TRANSFER_LIMITS)
            if not is_valid:
                logger.warning(f"[TRANSFER] Invalid amount: {error_msg}")
                return self._create_failure_result(task, error_msg or "Invalid amount", ErrorKind.BUSINESS)

            # Get collected data from task queue results
            task_result = context.get_result(task.task_id)
            result_data: dict[str, Any] = task_result.data if task_result else {}

            # If no previous result, try to get from services
            if not result_data:
                task_queue_service = context.task_queue_service
                if task_queue_service:
                    all_results = await task_queue_service.get_task_results(context.phone_number)
                    result_data = all_results.get(task.task_id, {}).get("result", {})

            account_resolved = result_data.get("account_resolved", {})
            idem_key = self._get_idempotency_key(task, context)

            # Build state for authorization
            state = {
                "phone_number": context.phone_number,
                "idempotency_key": idem_key,
                "pin_verified": True,
                "user_profile": {"id": context.user_id},
                "amount": validated_amount,
                "recipient_account": account_resolved.get("account_number"),
                "recipient_bank_code": account_resolved.get("bank_code"),
                "recipient_bank_name": account_resolved.get("bank_name"),
                "recipient_name": account_resolved.get("account_name"),
                "selected_source_account": result_data.get("source_account", {}),
                "narration": task.parameters.get("narration") if task.parameters else None,
            }

            # Execute via authorization class
            auth = TransferAuthorization(context.redis_client, context.queue)
            result_state = await auth.authorize(state)

            if result_state.get("transfer_status") == "authorized":
                return self._create_success_result(
                    task,
                    data={
                        "amount": state["amount"],
                        "recipient": state["recipient_name"],
                        "recipient_account": state["recipient_account"],
                    },
                    provider_ref=result_state.get("transaction_id"),
                )
            else:
                error = result_state.get("response", "Transfer failed")
                # Classify error
                error_kind = self._classify_error(error)
                return self._create_failure_result(task, error, error_kind)

        except Exception as e:
            logger.error(f"[TRANSFER] Error: {e}", exc_info=True)
            return self._create_failure_result(task, str(e), ErrorKind.UNKNOWN)

    def _classify_error(self, error: str) -> ErrorKind:
        """Classify error message to determine if retryable."""
        error_lower = error.lower()

        # Transient errors (retryable)
        transient_keywords = ["timeout", "connection", "unavailable", "503", "502", "network"]
        if any(kw in error_lower for kw in transient_keywords):
            return ErrorKind.TRANSIENT

        # Business errors (non-retryable)
        business_keywords = ["insufficient", "invalid", "not found", "blocked", "limit"]
        if any(kw in error_lower for kw in business_keywords):
            return ErrorKind.BUSINESS

        return ErrorKind.UNKNOWN
