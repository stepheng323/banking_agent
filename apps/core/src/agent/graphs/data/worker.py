"""Data Worker (V3).

Stateless domain worker for Data tasks.
Executes a single pass through the data logic pipeline.
"""

from types import SimpleNamespace
from typing import Any

from apps.core.src.agent.graphs.data.models.types import (
    DataContext,
    DataGates,
    DataPayload,
)
from apps.core.src.agent.graphs.data.nodes.confirmation import ConfirmationStep
from apps.core.src.agent.graphs.data.nodes.execution import ExecutionStep
from apps.core.src.agent.graphs.data.nodes.extraction import ExtractionStep
from apps.core.src.agent.graphs.data.nodes.resolution import ResolutionStep
from apps.core.src.agent.graphs.data.nodes.security import AuthorizationStep
from apps.core.src.agent.graphs.data.nodes.selection import SourceSelectionStep
from apps.core.src.agent.graphs.data.nodes.validation import ValidationStep
from apps.core.src.agent.graphs.data.pipeline.base import DataPipeline
from apps.core.src.agent.orchestrator.models.domain import (
    TransactionOutcome,
    TransactionResult,
)
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class DataWorker:
    """Stateless worker for data tasks."""

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

    async def run(
        self,
        payload: dict[str, Any],
        context: dict[str, Any],
        user_message: str | None = None,
        pin_verified: bool = False,
    ) -> TransactionResult:
        """Execute the data pipeline."""

        data = DataPayload(**payload)

        if not data.idempotency_key or data.idempotency_key == "no-key":
            import uuid

            data = data.model_copy(update={"idempotency_key": f"data-{uuid.uuid4()}"})

        ctx = DataContext(
            phone_number=context.get("phone_number", ""),
            beneficiaries=context.get("beneficiaries", []),
            accounts=context.get("accounts", []),
            user_id=context.get("user_id"),
        )

        gates = DataGates(
            pin_verified=pin_verified,
            confirmation_confirmed=(
                pin_verified or (data.confirmation.get("confirmed") if data.confirmation else False)
            ),
        )

        worker_context = SimpleNamespace(
            extractor=self.extractor,
            bill_provider=self.bill_provider,
            queue=self.queue,
            transaction_repo=self.transaction_repo,
            user_id=context.get("user_id"),
        )

        pipeline = DataPipeline(
            [
                ExtractionStep(user_message),
                ResolutionStep(),
                SourceSelectionStep(),
                ValidationStep(),
                ConfirmationStep(),
                AuthorizationStep(),
                ExecutionStep(),
            ]
        )

        try:
            return await pipeline.run(data, ctx, gates, worker_context)
        except Exception as e:
            logger.error("data_pipeline_failed", error=str(e), exc_info=True)
            return TransactionResult(
                outcome=TransactionOutcome.FAILED,
                error=f"Pipeline failed: {str(e)}",
                retryable=True,
                patch={"idempotency_key": data.idempotency_key},
            )
