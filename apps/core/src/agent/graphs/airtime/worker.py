"""Airtime Worker."""

from types import SimpleNamespace
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


class AirtimeWorker:
    """Stateless worker for airtime tasks."""

    def __init__(
        self,
        extractor,
        banking_provider,
        validation_service,
        transaction_repo,
        queue,
    ):
        self.extractor = extractor
        self.banking_provider = banking_provider
        self.validation_service = validation_service
        self.transaction_repo = transaction_repo
        self.queue = queue

    async def run(
        self,
        payload: dict[str, Any],
        context: dict[str, Any],
        user_message: str | None = None,
        pin_verified: bool = False,
    ) -> TransactionResult:
        """Execute the airtime pipeline."""

        data = AirtimePayload(**payload)
        
        if not data.idempotency_key or data.idempotency_key == "no-key":
             import uuid
             data = data.model_copy(update={"idempotency_key": f"airtime-{uuid.uuid4()}"})

        ctx = AirtimeContext(
            phone_number=context.get("phone_number", ""),
            beneficiaries=context.get("beneficiaries", []),
            accounts=context.get("accounts", []),
        )

        gates = AirtimeGates(
            pin_verified=pin_verified,
            confirmation_confirmed=(
                pin_verified or (data.confirmation.confirmed if hasattr(data, "confirmation") else False)
            ),
        )

        worker_context = SimpleNamespace(
            extractor=self.extractor,
            banking_provider=self.banking_provider,
            validation_service=self.validation_service,
            queue=self.queue,
            transaction_repo=self.transaction_repo,
            user_id=context.get("user_id"),
        )

        pipeline = AirtimePipeline(
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

        # 6. Run
        try:
             return await pipeline.run(data, ctx, gates, worker_context)
        except Exception as e:
            logger.error("airtime_pipeline_failed", error=str(e), exc_info=True)
            return TransactionResult(
                outcome=TransactionOutcome.FAILED,
                error=f"Pipeline failed: {str(e)}",
                retryable=True,
                patch={"idempotency_key": data.idempotency_key},
            )
