"""Data Worker (V3).

Stateless domain worker for Data tasks.
Executes a single pass through the data logic pipeline.
"""

import time
import uuid
from dataclasses import dataclass
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
from shared.i18n import LocaleManager, render_capability_limitation, render_message
from shared.policy import resolve_capability_alternative, resolve_capability_rule
from shared.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass(slots=True)
class DataWorkerContext:
    extractor: Any
    bill_provider: Any
    queue: Any
    transaction_repo: Any
    user_id: str | None
    required_fields: list[str]


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

    def _ensure_idempotency_key(self, data: DataPayload) -> DataPayload:
        if data.idempotency_key and data.idempotency_key != "no-key":
            return data
        return data.model_copy(update={"idempotency_key": f"data-{uuid.uuid4()}"})

    @staticmethod
    def _build_context(context: dict[str, Any]) -> DataContext:
        return DataContext(
            phone_number=context.get("phone_number", ""),
            language=LocaleManager.normalize(context.get("language")).value,
            beneficiaries=context.get("beneficiaries", []),
            accounts=context.get("accounts", []),
            user_id=context.get("user_id"),
        )

    @staticmethod
    def _is_confirmation_confirmed(data: DataPayload, pin_verified: bool) -> bool:
        if pin_verified:
            return True
        confirmation = data.confirmation or {}
        if isinstance(confirmation, dict):
            return bool(confirmation.get("confirmed"))
        return bool(getattr(confirmation, "confirmed", False))

    def _build_gates(self, data: DataPayload, pin_verified: bool) -> DataGates:
        return DataGates(
            pin_verified=pin_verified,
            confirmation_confirmed=self._is_confirmation_confirmed(data, pin_verified),
        )

    def _build_worker_context(self, context: dict[str, Any]) -> DataWorkerContext:
        required_fields = context.get("required_fields")
        return DataWorkerContext(
            extractor=self.extractor,
            bill_provider=self.bill_provider,
            queue=self.queue,
            transaction_repo=self.transaction_repo,
            user_id=context.get("user_id"),
            required_fields=required_fields if isinstance(required_fields, list) else [],
        )

    @staticmethod
    def _policy_gate_message(action: str, *, locale: str = "en") -> str | None:
        rule = resolve_capability_rule(domain="data", action=action)
        if rule is not None and rule.supported:
            return None

        alt = resolve_capability_alternative(domain="data", action=action)
        return render_capability_limitation(
            locale=locale,
            action_label=action.replace("_", " "),
            alternative_labels=[alt.replace("_", " ")] if alt else [],
        )

    @staticmethod
    def _build_pipeline(user_message: str | None) -> DataPipeline:
        return DataPipeline(
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

    async def run(
        self,
        payload: dict[str, Any],
        context: dict[str, Any],
        user_message: str | None = None,
        pin_verified: bool = False,
    ) -> TransactionResult:
        """Execute the data pipeline."""
        start_time = time.perf_counter()

        action = str(payload.get("action") or "buy_data")
        locale = LocaleManager.normalize(context.get("language")).value
        if limitation := self._policy_gate_message(action, locale=locale):
            logger.info("capability_blocked", domain="data", action=action)
            return TransactionResult(
                outcome=TransactionOutcome.FAILED,
                error=limitation,
                response=limitation,
                patch={"capability_blocked": True},
            )

        data = self._ensure_idempotency_key(DataPayload(**payload))
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
            logger.error("data_pipeline_failed", error=str(e), exc_info=True)
            return TransactionResult(
                outcome=TransactionOutcome.FAILED,
                error=render_message(
                    "data.error.pipeline_failed",
                    locale,
                    {"error": str(e)},
                ),
                retryable=True,
                patch={"idempotency_key": data.idempotency_key},
            )
        finally:
            duration = (time.perf_counter() - start_time) * 1000
            logger.info(
                "perf_timer_latency",
                gate="data_worker_total",
                duration_ms=round(duration, 2),
                phone_number=context.get("phone_number"),
            )
