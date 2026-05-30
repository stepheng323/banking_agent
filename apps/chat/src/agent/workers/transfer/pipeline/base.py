"""Transfer Pipeline Abstractions."""

import time
from abc import ABC, abstractmethod
from typing import Any, TypeVar

from apps.chat.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
from apps.chat.src.agent.workers.transfer.models.types import (
    TransferConfirmation,
    TransferContext,
    TransferGates,
    TransferPayload,
)
from banking.presentation.formatters.transaction_copy_context import build_copy_context
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
        if "confirmation" in normalized_patch and isinstance(normalized_patch["confirmation"], dict):
            normalized_patch["confirmation"] = TransferConfirmation(**normalized_patch["confirmation"])
        return normalized_patch

    @staticmethod
    def _stage_key_for_step(step_name: str) -> str | None:
        return {
            "ExtractionStep": "transfer.resolving_recipient",
            "ResolutionStep": "transfer.resolving_recipient",
            "SourceSelectionStep": "transfer.confirming_details",
            "ValidationStep": "transfer.confirming_details",
            "FundingStep": "transfer.confirming_details",
            "PayoutPreparationStep": "transfer.confirming_details",
            "ConfirmationStep": "transfer.confirming_details",
            "AuthorizationStep": "transfer.authorizing_transfer",
            "ExecutionStep": "transfer.processing_transfer",
        }.get(step_name)

    @staticmethod
    def _build_stage_metadata(data: TransferPayload) -> dict[str, str] | None:
        metadata = build_copy_context(task_type="transfer", payload=data)
        return metadata or None

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
            progress_tracker = getattr(worker_context, "progress_tracker", None) if worker_context is not None else None
            if progress_tracker is not None:
                stage_key = self._stage_key_for_step(step_name)
                if stage_key:
                    await progress_tracker.set_stage(stage_key, stage_metadata=self._build_stage_metadata(data))
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
                data = TransferPayload(**{**data.model_dump(), **normalized_patch})

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
