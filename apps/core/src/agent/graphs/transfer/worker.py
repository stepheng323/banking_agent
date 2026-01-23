"""Transfer Worker (V3).

Stateless domain worker for Transfer tasks.
Executes a single pass through the transfer logic pipeline:
Extract -> Resolve -> Validate -> Confirmation -> Authorization -> Execution.

Returns a standardized TransferResult.
"""

from types import SimpleNamespace
from typing import Any

from apps.core.src.agent.graphs.transfer.models.types import (
    TransferContext,
    TransferGates,
    TransferPayload,
)
from apps.core.src.agent.graphs.transfer.nodes.confirmation import ConfirmationStep
from apps.core.src.agent.graphs.transfer.nodes.execution import ExecutionStep
from apps.core.src.agent.graphs.transfer.nodes.extraction import ExtractionStep
from apps.core.src.agent.graphs.transfer.nodes.funding import FundingStep
from apps.core.src.agent.graphs.transfer.nodes.resolver import ResolutionStep
from apps.core.src.agent.graphs.transfer.nodes.security import AuthorizationStep
from apps.core.src.agent.graphs.transfer.nodes.selection import SourceSelectionStep
from apps.core.src.agent.graphs.transfer.nodes.validation import ValidationStep
from apps.core.src.agent.graphs.transfer.pipeline.base import TransferPipeline
from apps.core.src.agent.orchestrator.models.domain import (
    TransferOutcome,
    TransferResult,
)
from shared.repositories.transaction_repository import (
    TransactionRepository,
)
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class TransferWorker:
    """Stateless worker for transfer tasks."""

    def __init__(
        self,
        validation_service,
        beneficiary_repo,
        account_repo,
        queue,
        extractor,
        banking_provider,
        bank_cache,
        transaction_repo: TransactionRepository,
    ):
        self.validation_service = validation_service
        self.beneficiary_repo = beneficiary_repo
        self.account_repo = account_repo
        self.queue = queue
        self.extractor = extractor
        self.banking_provider = banking_provider
        self.bank_cache = bank_cache
        self.transaction_repo = transaction_repo

    async def run(
        self,
        payload: dict[str, Any],
        context: dict[str, Any],
        user_message: str | None = None,
        pin_verified: bool = False,
    ) -> TransferResult:
        """Execute the transfer pipeline."""
        data = TransferPayload(**payload)

        # Hydrate ID key if missing
        if not data.idempotency_key or data.idempotency_key == "no-key":
            import uuid

            data = data.model_copy(update={"idempotency_key": f"transfer-{uuid.uuid4()}"})

        ctx = TransferContext(
            phone_number=context.get("phone_number", ""),
            beneficiaries=context.get("beneficiaries", []),
            accounts=context.get("accounts", []),
        )

        gates = TransferGates(
            pin_verified=pin_verified,
            confirmation_confirmed=(
                pin_verified or (data.confirmation.confirmed if hasattr(data, "confirmation") else False)
            ),
        )

        worker_context = SimpleNamespace(
            extractor=self.extractor,
            banking_provider=self.banking_provider,
            bank_cache=self.bank_cache,
            queue=self.queue,
            transaction_repo=self.transaction_repo,
            dd_provider=getattr(self, "dd_provider", None),
            user_id=context.get("user_id"),
        )

        pipeline = TransferPipeline(
            [
                ExtractionStep(user_message),
                ResolutionStep(),
                SourceSelectionStep(),
                ValidationStep(),
                FundingStep(),
                ConfirmationStep(),
                AuthorizationStep(),
                ExecutionStep(),
            ]
        )

        try:
            return await pipeline.run(data, ctx, gates, worker_context)
        except Exception as e:
            logger.error("transfer_pipeline_failed", error=str(e), exc_info=True)
            return TransferResult(
                outcome=TransferOutcome.FAILED,
                error=f"Pipeline failed: {str(e)}",
                retryable=True,
                patch={"idempotency_key": data.idempotency_key},  # ensure key preservation
            )
