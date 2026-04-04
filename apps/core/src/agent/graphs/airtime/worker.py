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
from shared.i18n import LocaleManager, render_capability_limitation, render_message
from shared.policy.adapters import resolve_capability_alternative, resolve_capability_rule
from shared.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass(slots=True)
class AirtimeWorkerContext:
    extractor: Any
    bill_provider: Any
    publisher: Any
    transaction_repo: Any
    redis_client: Any
    user_id: str | None
    channel_identity: str | None
    required_fields: list[str]


class AirtimeWorker:
    """Stateless worker for airtime tasks."""

    def __init__(
        self,
        extractor: Any,
        bill_provider: Any,
        transaction_repo: Any,
        publisher: Any,
        redis_client: Any | None = None,
    ) -> None:
        self.extractor = extractor
        self.bill_provider = bill_provider
        self.transaction_repo = transaction_repo
        self.publisher = publisher
        self.redis_client = redis_client

    def _ensure_idempotency_key(self, data: AirtimePayload) -> AirtimePayload:
        if data.idempotency_key and data.idempotency_key != "no-key":
            return data
        return data.model_copy(update={"idempotency_key": f"airtime-{uuid.uuid4()}"})

    @staticmethod
    def _build_context(context: dict[str, Any]) -> AirtimeContext:
        return AirtimeContext(
            phone_number=context.get("phone_number", ""),
            language=LocaleManager.normalize(context.get("language")).value,
            channel=context.get("channel", "whatsapp"),
            beneficiaries=context.get("beneficiaries", []),
            accounts=context.get("accounts", []),
            all_accounts=context.get("all_accounts", []),
        )

    @staticmethod
    def _build_gates(data: AirtimePayload, pin_verified: bool) -> AirtimeGates:
        return AirtimeGates(
            pin_verified=pin_verified,
            confirmation_confirmed=(pin_verified or data.confirmation.confirmed),
        )

    def _build_worker_context(self, context: dict[str, Any]) -> AirtimeWorkerContext:
        required_fields = context.get("required_fields")
        return AirtimeWorkerContext(
            extractor=self.extractor,
            bill_provider=self.bill_provider,
            publisher=self.publisher,
            transaction_repo=self.transaction_repo,
            redis_client=self.redis_client,
            user_id=context.get("user_id"),
            channel_identity=str(context.get("channel_identity")) if context.get("channel_identity") else None,
            required_fields=required_fields if isinstance(required_fields, list) else [],
        )

    @staticmethod
    def _policy_gate_message(action: str, *, locale: str = "en") -> str | None:
        rule = resolve_capability_rule(domain="airtime", action=action)
        if rule is not None and rule.supported:
            return None

        alt = resolve_capability_alternative(domain="airtime", action=action)
        return render_capability_limitation(
            locale=locale,
            action_label=action.replace("_", " "),
            alternative_labels=[alt.replace("_", " ")] if alt else [],
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

        action = str(payload.get("action") or "buy_airtime")
        locale = LocaleManager.normalize(context.get("language")).value
        if limitation := self._policy_gate_message(action, locale=locale):
            logger.info("capability_blocked", domain="airtime", action=action)
            return TransactionResult(
                outcome=TransactionOutcome.FAILED,
                error=limitation,
                response=limitation,
                patch={"capability_blocked": True},
            )

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
                error=render_message("airtime.error.pipeline_failed", locale, {"error": str(e)}),
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
