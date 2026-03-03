"""Transfer Pipeline Abstractions."""

import time
from abc import ABC, abstractmethod
from typing import Any, TypeVar

from apps.core.src.agent.graphs.transfer.models.types import (
    TransferContext,
    TransferGates,
    TransferPayload,
)
from apps.core.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
from shared.utils.logging import get_logger
from shared.utils.sanitize import normalize_bank_account_number

logger = get_logger(__name__)

T = TypeVar("T")


class TransferStep(ABC):
    """Abstract base class for a single step in the transfer pipeline."""

    @abstractmethod
    async def execute(
        self,
        data: TransferPayload,
        context: TransferContext,
        gates: TransferGates,
        worker_context: Any = None,
    ) -> TransactionResult:
        """Execute the step logic."""
        pass

    def with_key(self, result: TransactionResult, data: TransferPayload) -> TransactionResult:
        """Helper to attach state and idempotency key to result."""
        if result.patch is None:
            result.patch = {}

        if hasattr(data, "model_dump"):
            current_state = data.model_dump(exclude_unset=True)
        else:
            current_state = data.dict(exclude_unset=True)

        result.patch.update(current_state)
        if data.idempotency_key:
            result.patch["idempotency_key"] = data.idempotency_key
        return result


class TransferPipeline:
    """Execute a sequence of TransferSteps."""

    def __init__(self, steps: list[TransferStep]):
        self.steps = steps

    @staticmethod
    def _normalize_patch(patch: dict[str, Any]) -> dict[str, Any]:
        normalized_patch = dict(patch)
        if "recipient_account" in normalized_patch:
            normalized_account = normalize_bank_account_number(normalized_patch.get("recipient_account"))
            if normalized_account:
                normalized_patch["recipient_account"] = normalized_account
        return normalized_patch

    async def run(
        self,
        data: TransferPayload,
        context: TransferContext,
        gates: TransferGates,
        worker_context: Any = None,
    ) -> TransactionResult:
        """Run all steps in sequence."""
        last_result = None
        for step in self.steps:
            step_name = step.__class__.__name__
            s_start = time.perf_counter()
            result = await step.execute(data, context, gates, worker_context)
            s_duration = (time.perf_counter() - s_start) * 1000

            logger.info(
                "perf_timer_latency",
                gate=f"transfer_pipeline_step_{step_name.lower().replace('step', '')}",
                duration_ms=round(s_duration, 2),
                phone_number=context.phone_number,
            )

            if result.outcome != TransactionOutcome.OK:
                return self._finalize_result(result, data)

            last_result = result
            if result.patch:
                normalized_patch = self._normalize_patch(result.patch)
                result.patch = normalized_patch
                data = data.model_copy(update=normalized_patch)

        if last_result:
            return self._finalize_result(last_result, data)

        return self._finalize_result(TransactionResult(outcome=TransactionOutcome.OK, patch={}), data)

    def _finalize_result(self, result: TransactionResult, data: TransferPayload) -> TransactionResult:
        """Finalize result with accumulated state."""
        if result.patch is None:
            result.patch = {}

        if hasattr(data, "model_dump"):
            current_state = data.model_dump(exclude_unset=True)
        else:
            current_state = data.dict(exclude_unset=True)

        result.patch.update(current_state)
        if data.idempotency_key:
            result.patch["idempotency_key"] = data.idempotency_key

        if result.receipt:
            pass

        return result
