"""Transfer Worker (V3).

Stateless domain worker for Transfer tasks.
Executes a single pass through the transfer logic pipeline:
Extract -> Resolve -> Validate -> Confirmation -> Authorization -> Execution.

Returns a standardized TransactionResult.
"""

import time
import uuid
from dataclasses import dataclass
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
from apps.core.src.agent.graphs.transfer.services.validation import ValidationService
from apps.core.src.agent.orchestrator.models.domain import (
    TransactionOutcome,
    TransactionResult,
)
from shared.i18n import LocaleManager, render_capability_limitation, render_message
from shared.policy import resolve_capability_alternative, resolve_capability_rule
from shared.repositories.transaction_repository import (
    TransactionRepository,
)
from shared.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass(slots=True)
class TransferWorkerContext:
    extractor: Any
    banking_provider: Any
    bank_cache: Any
    queue: Any
    transaction_repo: TransactionRepository
    dd_provider: Any | None
    user_id: str | None
    validation_service: Any


class TransferWorker:
    """Stateless worker for transfer tasks."""

    def __init__(
        self,
        validation_service,
        queue,
        extractor,
        banking_provider,
        bank_cache,
        transaction_repo: TransactionRepository,
        dd_provider: Any | None = None,
    ):
        self.validation_service = validation_service or ValidationService()
        self.queue = queue
        self.extractor = extractor
        self.banking_provider = banking_provider
        self.bank_cache = bank_cache
        self.transaction_repo = transaction_repo
        self.dd_provider = dd_provider

    def _ensure_idempotency_key(self, data: TransferPayload) -> TransferPayload:
        if data.idempotency_key and data.idempotency_key != "no-key":
            return data
        return data.model_copy(update={"idempotency_key": f"transfer-{uuid.uuid4()}"})

    @staticmethod
    def _build_context(context: dict[str, Any]) -> TransferContext:
        return TransferContext(
            phone_number=context.get("phone_number", ""),
            language=LocaleManager.normalize(context.get("language")).value,
            beneficiaries=context.get("beneficiaries", []),
            accounts=context.get("accounts", []),
        )

    @staticmethod
    def _build_gates(data: TransferPayload, pin_verified: bool) -> TransferGates:
        return TransferGates(
            pin_verified=pin_verified,
            confirmation_confirmed=(pin_verified or data.confirmation.confirmed),
        )

    def _build_worker_context(self, context: dict[str, Any]) -> TransferWorkerContext:
        return TransferWorkerContext(
            extractor=self.extractor,
            banking_provider=self.banking_provider,
            bank_cache=self.bank_cache,
            queue=self.queue,
            transaction_repo=self.transaction_repo,
            dd_provider=self.dd_provider,
            user_id=context.get("user_id"),
            validation_service=self.validation_service,
        )

    @staticmethod
    def _policy_gate_message(action: str, *, locale: str = "en") -> str | None:
        rule = resolve_capability_rule(domain="transfer", action=action)
        if rule is not None and rule.supported:
            return None

        alt = resolve_capability_alternative(domain="transfer", action=action)
        return render_capability_limitation(
            locale=locale,
            action_label=action.replace("_", " "),
            alternative_labels=[alt.replace("_", " ")] if alt else [],
        )

    @staticmethod
    def _build_pipeline(user_message: str | None) -> TransferPipeline:
        return TransferPipeline(
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

    async def run(
        self,
        payload: dict[str, Any],
        context: dict[str, Any],
        user_message: str | None = None,
        pin_verified: bool = False,
    ) -> TransactionResult:
        """Execute the transfer pipeline."""
        start_time = time.perf_counter()

        action = str(payload.get("action") or "send_money")
        locale = LocaleManager.normalize(context.get("language")).value
        if limitation := self._policy_gate_message(action, locale=locale):
            logger.info("capability_blocked", domain="transfer", action=action)
            return TransactionResult(
                outcome=TransactionOutcome.FAILED,
                error=limitation,
                response=limitation,
                patch={"capability_blocked": True},
            )

        data = self._ensure_idempotency_key(TransferPayload(**payload))
        ctx = self._build_context(context)
        gates = self._build_gates(data, pin_verified)
        worker_context = self._build_worker_context(context)
        pipeline = self._build_pipeline(user_message)

        try:
            return await pipeline.run(data, ctx, gates, worker_context)
        except Exception as e:
            logger.error("transfer_pipeline_failed", error=str(e), exc_info=True)
            return TransactionResult(
                outcome=TransactionOutcome.FAILED,
                error=render_message("transfer.error.pipeline_failed", locale, {"error": str(e)}),
                retryable=True,
                patch={"idempotency_key": data.idempotency_key},
            )
        finally:
            duration = (time.perf_counter() - start_time) * 1000
            logger.info(
                "perf_timer_latency",
                gate="transfer_worker_total",
                duration_ms=round(duration, 2),
                phone_number=context.get("phone_number"),
            )
