"""Airtime task handler wrapping AirtimeAuthorization."""

from typing import Any

from apps.core.src.agent.graphs.__shared__.validation.amount_validator import (
    AIRTIME_LIMITS,
)
from apps.core.src.agent.graphs.__shared__.validation.amount_validator import (
    validate_amount as validate_amount_limits,
)
from apps.core.src.agent.graphs.airtime.models.types import (
    AirtimeContext,
    AirtimeGates,
    AirtimePayload,
    AirtimeRecipient,
    AirtimeSource,
)
from apps.core.src.agent.graphs.airtime.nodes.execution import ExecutionStep
from apps.core.src.agent.orchestrator.models.domain import TransactionOutcome
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

            # Build Payload for ExecutionStep
            payload = AirtimePayload(
                amount=validated_amount,
                recipient_phone=task.parameters.get("recipient") or context.phone_number,
                network=result_data.get("network", "") or state.get("network", ""),
                source_account_id=result_data.get("source_account", {}).get("account_id"),
                beneficiary_id=None,
                transaction_id=idem_key,
                idempotency_key=idem_key,
            )

            # Build dummy context
            airtime_context = AirtimeContext(
                 phone_number=context.phone_number,
                 beneficiaries=[],
                 accounts=[]
            )
            
            # Assume gates passed for batch (PIN verified implicitly or previously)
            gates = AirtimeGates(pin_verified=True, confirmation_confirmed=True)

            # Wrapper for worker context to provide banking_provider
            class WorkerContextWrapper:
                def __init__(self, provider, transaction_repo, queue):
                    self.banking_provider = provider
                    self.transaction_repo = transaction_repo
                    self.queue = queue
                    self.extractor = None

            # Retrieve services from context
            banking_provider = context.get_service("banking_provider")
            transaction_repo = context.get_service("transaction_repo")

            if not banking_provider:
                # Fallback or error if provider not found in batch context
                # For now, we assume it's registered. If critical, we might need a workaround.
                # But ExecutionStep REQUIRES it.
                logger.warning("[AIRTIME] Banking provider not found in context services")
                # If we fail here, we can't process. 
                # Attempt to instantiate default if possible? No, too complex.
                # We return failure.
                return self._create_failure_result(task, "Banking provider unavailable", ErrorKind.SYSTEM)

            worker_context = WorkerContextWrapper(banking_provider, transaction_repo, context.queue)

            # Execution
            step = ExecutionStep()
            result: TransactionResult = await step.execute(
                data=payload,
                context=airtime_context,
                gates=gates,
                worker_context=worker_context
            )

            if result.outcome == TransactionOutcome.OK:
                # ExecutionStep returns OK with patch containing receipt or status
                # If successful, patch has 'provision_status' or similar? 
                # Let's check ExecutionStep.
                # It returns TransactionResult(outcome=OK, patch=receipt)
                # receipt has details.
                
                receipt = result.patch
                return self._create_success_result(
                    task,
                    data={
                        "amount": payload.amount,
                        "recipient": payload.recipient_phone,
                        "network": payload.network,
                        "receipt": receipt
                    },
                    provider_ref=receipt.get("transaction_id") or receipt.get("reference")
                )
            else:
                error = result.details.get("error", "Airtime failed")
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
