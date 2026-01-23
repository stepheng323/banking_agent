"""Transfer Pipeline Abstractions."""

from abc import ABC, abstractmethod
from typing import Any, TypeVar

from apps.core.src.agent.graphs.transfer.models.types import (
    TransferContext,
    TransferGates,
    TransferPayload,
)
from apps.core.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
from shared.utils.logging import get_logger

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
            result = await step.execute(data, context, gates, worker_context)

            if result.outcome != TransactionOutcome.OK:
                return self._finalize_result(result, data)

            last_result = result
            if result.patch:
                data = data.model_copy(update=result.patch)

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
