"""Data task handler wrapping DataAuthorization."""

from typing import Any

from apps.core.src.agent.graphs.__shared__.validation.amount_validator import (
    DATA_LIMITS,
)
from apps.core.src.agent.graphs.__shared__.validation.amount_validator import (
    validate_amount as validate_amount_limits,
)
from apps.core.src.agent.graphs.data.graph.nodes.authorization import DataAuthorization
from shared.types.planner import PlannedTask
from shared.utils.logging import get_logger

from ..models import ErrorKind, TaskResult
from ..workflow_context import WorkflowContext
from .base import BaseTaskHandler

logger = get_logger(__name__)


class DataHandler(BaseTaskHandler):
    """Handler for data purchase tasks using DataAuthorization."""

    async def execute(self, task: PlannedTask, context: WorkflowContext) -> TaskResult:
        """Execute data purchase using DataAuthorization directly."""
        try:
            # Get collected data from task queue results
            task_result = context.get_result(task.task_id)
            result_data: dict[str, Any] = task_result.data if task_result else {}

            if not result_data:
                task_queue_service = context.task_queue_service
                if task_queue_service:
                    all_results = await task_queue_service.get_task_results(context.phone_number)
                    result_data = all_results.get(task.task_id, {}).get("result", {})

            selected_plan = result_data.get("selected_plan")
            if not selected_plan:
                return self._create_failure_result(task, "No data plan selected", ErrorKind.BUSINESS)

            # Validate plan amount
            plan_amount = selected_plan.get("amount") if isinstance(selected_plan, dict) else None
            if plan_amount is not None:
                is_valid, error_msg, _ = validate_amount_limits(plan_amount, DATA_LIMITS)
                if not is_valid:
                    logger.warning(f"[DATA] Invalid plan amount: {error_msg}")
                    return self._create_failure_result(task, error_msg or "Invalid plan amount", ErrorKind.BUSINESS)

            idem_key = self._get_idempotency_key(task, context)

            # Build state for authorization
            state = {
                "phone_number": context.phone_number,
                "idempotency_key": idem_key,
                "pin_verified": True,
                "user_profile": {"id": context.user_id},
                "selected_plan": selected_plan,
                "target_phone": result_data.get("target_phone", context.phone_number),
                "network": result_data.get("network", ""),
                "source": result_data.get("source", "self"),
            }

            # Execute via authorization class
            auth = DataAuthorization(context.redis_client, context.queue)
            result_state = await auth.authorize(state)

            if result_state.get("data_status") == "authorized":
                plan_name = selected_plan.get("name") if isinstance(selected_plan, dict) else str(selected_plan)
                plan_amount_val = selected_plan.get("amount") if isinstance(selected_plan, dict) else None
                return self._create_success_result(
                    task,
                    data={
                        "amount": plan_amount_val,
                        "plan": plan_name,
                        "target_phone": state["target_phone"],
                    },
                    provider_ref=result_state.get("transaction_id"),
                )
            else:
                error = result_state.get("response", "Data purchase failed")
                error_kind = self._classify_error(error)
                return self._create_failure_result(task, error, error_kind)

        except Exception as e:
            logger.error(f"[DATA] Error: {e}", exc_info=True)
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
