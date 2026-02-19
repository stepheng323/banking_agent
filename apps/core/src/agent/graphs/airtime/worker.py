"""Airtime Worker."""

import time
import uuid
from dataclasses import dataclass
from typing import Any

from apps.core.src.agent.graphs.airtime.models.types import (
    AirtimeContext,
    AirtimeGates,
    AirtimePayload,
)
from apps.core.src.agent.graphs.airtime.nodes.confirmation import ConfirmationStep
from apps.core.src.agent.graphs.airtime.nodes.execution import ExecutionStep
from apps.core.src.agent.graphs.airtime.nodes.extraction import ExtractionStep
from apps.core.src.agent.graphs.airtime.nodes.resolution import ResolutionStep
from apps.core.src.agent.graphs.airtime.nodes.security import AuthorizationStep
from apps.core.src.agent.graphs.airtime.nodes.selection import SourceSelectionStep
from apps.core.src.agent.graphs.airtime.nodes.validation import ValidationStep
from apps.core.src.agent.graphs.airtime.pipeline.base import AirtimePipeline
from apps.core.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
from shared.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass(slots=True)
class AirtimeWorkerContext:
    extractor: Any
    bill_provider: Any
    queue: Any
    transaction_repo: Any
    user_id: str | None


class AirtimeWorker:
    """Stateless worker for airtime tasks."""

    def __init__(
        self,
        extractor,
        bill_provider,
        transaction_repo,
        queue,
    ):
        self.extractor = extractor
        self.bill_provider = bill_provider
        self.transaction_repo = transaction_repo
        self.queue = queue

    def _ensure_idempotency_key(self, data: AirtimePayload) -> AirtimePayload:
        if data.idempotency_key and data.idempotency_key != "no-key":
            return data
        return data.model_copy(update={"idempotency_key": f"airtime-{uuid.uuid4()}"})

    @staticmethod
    def _build_context(context: dict[str, Any]) -> AirtimeContext:
        return AirtimeContext(
            phone_number=context.get("phone_number", ""),
            channel=context.get("channel", "whatsapp"),
            beneficiaries=context.get("beneficiaries", []),
            accounts=context.get("accounts", []),
        )

    @staticmethod
    def _build_gates(data: AirtimePayload, pin_verified: bool) -> AirtimeGates:
        return AirtimeGates(
            pin_verified=pin_verified,
            confirmation_confirmed=(pin_verified or data.confirmation.confirmed),
        )

    def _build_worker_context(self, context: dict[str, Any]) -> AirtimeWorkerContext:
        return AirtimeWorkerContext(
            extractor=self.extractor,
            bill_provider=self.bill_provider,
            queue=self.queue,
            transaction_repo=self.transaction_repo,
            user_id=context.get("user_id"),
        )

    @staticmethod
    def _build_pipeline(user_message: str | None) -> AirtimePipeline:
        return AirtimePipeline(
            [
                ExtractionStep(user_message),
                ResolutionStep(),
                ValidationStep(),
                SourceSelectionStep(),
                ConfirmationStep(),
                AuthorizationStep(),
                ExecutionStep(),
            ]
        )

    async def run(
        self,
        payload: dict[str, Any],
        context: dict[str, Any],
        user_message: str | None = None,
        pin_verified: bool = False,
    ) -> TransactionResult:
        """Execute the airtime pipeline."""
        start_time = time.perf_counter()
        data = self._ensure_idempotency_key(AirtimePayload(**payload))
        ctx = self._build_context(context)
        gates = self._build_gates(data, pin_verified)
        worker_context = self._build_worker_context(context)
        pipeline = self._build_pipeline(user_message)

        try:
            result = await pipeline.run(data, ctx, gates, worker_context)

            if data.idempotency_key:
                if result.patch is None:
                    result.patch = {}
                result.patch["idempotency_key"] = data.idempotency_key

            return result
        except Exception as e:
            logger.error("airtime_pipeline_failed", error=str(e), exc_info=True)
            return TransactionResult(
                outcome=TransactionOutcome.FAILED,
                error=f"Pipeline failed: {str(e)}",
                retryable=True,
                patch={"idempotency_key": data.idempotency_key},
            )
        finally:
            duration = (time.perf_counter() - start_time) * 1000
            logger.info(
                "perf_timer_latency",
                gate="airtime_worker_total",
                duration_ms=round(duration, 2),
                phone_number=context.get("phone_number"),
            )
