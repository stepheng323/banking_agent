"""Airtime task handler wrapping AirtimeAuthorization."""

from typing import Any

from apps.core.src.agent.graphs.__shared__.validation.amount_validator import (
    AIRTIME_LIMITS,
)
from apps.core.src.agent.graphs.__shared__.validation.amount_validator import (
    validate_amount as validate_amount_limits,
)
from apps.core.src.agent.graphs.airtime.graph.nodes.authorization import AirtimeAuthorization
from shared.types.planner import PlannedTask
from shared.utils.logging import get_logger

from ..models import ErrorKind, TaskResult
from ..workflow_context import WorkflowContext
from .base import BaseTaskHandler

logger = get_logger(__name__)


class AirtimeHandler(BaseTaskHandler):
    """Handler for airtime tasks using AirtimeAuthorization."""

    async def execute(self, task: PlannedTask, context: WorkflowContext) -> TaskResult:
        """Execute airtime purchase using AirtimeAuthorization directly."""
        try:
            # Validate amount
            amount = task.parameters.get("amount") if task.parameters else None
            is_valid, error_msg, validated_amount = validate_amount_limits(amount, AIRTIME_LIMITS)
            if not is_valid:
                logger.warning(f"[AIRTIME] Invalid amount: {error_msg}")
                return self._create_failure_result(task, error_msg or "Invalid amount", ErrorKind.BUSINESS)

            # Get collected data from task queue results
            task_result = context.get_result(task.task_id)
            result_data: dict[str, Any] = task_result.data if task_result else {}

            if not result_data:
                task_queue_service = context.task_queue_service
                if task_queue_service:
                    all_results = await task_queue_service.get_task_results(context.phone_number)
                    result_data = all_results.get(task.task_id, {}).get("result", {})

            idem_key = self._get_idempotency_key(task, context)

            # Build state for authorization
            state = {
                "phone_number": context.phone_number,
                "idempotency_key": idem_key,
                "pin_verified": True,
                "user_profile": {"id": context.user_id},
                "amount": validated_amount,
                "recipient_phone": task.parameters.get("recipient") if task.parameters else context.phone_number,
                "network": result_data.get("network", ""),
                "selected_source_account": result_data.get("source_account", {}),
            }

            # Execute via authorization class
            auth = AirtimeAuthorization(context.redis_client, context.queue)
            result_state = await auth.authorize(state)

            if result_state.get("airtime_status") == "authorized":
                return self._create_success_result(
                    task,
                    data={
                        "amount": state["amount"],
                        "recipient": state["recipient_phone"],
                        "network": state["network"],
                    },
                    provider_ref=result_state.get("transaction_id"),
                )
            else:
                error = result_state.get("response", "Airtime failed")
                error_kind = self._classify_error(error)
                return self._create_failure_result(task, error, error_kind)

        except Exception as e:
            logger.error(f"[AIRTIME] Error: {e}", exc_info=True)
            return self._create_failure_result(task, str(e), ErrorKind.UNKNOWN)

    def _classify_error(self, error: str) -> ErrorKind:
        """Classify error message to determine if retryable."""
        error_lower = error.lower()

        transient_keywords = ["timeout", "connection", "unavailable", "503", "502", "network"]
        if any(kw in error_lower for kw in transient_keywords):
            return ErrorKind.TRANSIENT

        business_keywords = ["insufficient", "invalid", "not found", "blocked", "limit"]
        if any(kw in error_lower for kw in business_keywords):
            return ErrorKind.BUSINESS

        return ErrorKind.UNKNOWN
