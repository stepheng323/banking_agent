"""Data Worker (V3).

Stateless domain worker for Data tasks.
Executes a single pass through the data logic pipeline.
"""

import time
import uuid
from dataclasses import dataclass
from typing import Any

from banking.bills.data.models.types import (
    DataContext,
    DataGates,
    DataPayload,
)
from banking.bills.data.pipeline_factory import build_data_pipeline, build_data_plan_query_pipeline
from banking.bills.data.plans.service import DataPlanService
from banking.bills.data.scheduling import SCHEDULING_ACTIONS, DataSchedulingHandler
from banking.policy.service import capability_block_message
from banking.presentation.i18n.locale import LocaleManager
from banking.presentation.i18n.renderer import render_message
from banking.runtime.results import TransactionOutcome, TransactionResult
from banking.security.authorization_context import is_task_authorized_by_pin
from shared.config.settings import settings
from shared.utils.logging import get_logger, log_orchestrator_diagnostic

logger = get_logger(__name__)


@dataclass(slots=True)
class DataWorkerContext:
    extractor: Any
    bill_provider: Any
    plan_service: Any
    publisher: Any
    transaction_repo: Any
    user_id: str | None
    channel_identity: str | None
    required_fields: list[str]
    previous_response: str | None


class DataWorker:
    """Stateless worker for data tasks."""

    def __init__(
        self,
        extractor,
        bill_provider,
        transaction_repo,
        publisher,
        redis_client=None,
    ):
        self.extractor = extractor
        self.bill_provider = bill_provider
        self.transaction_repo = transaction_repo
        self.publisher = publisher
        self.plan_service = DataPlanService(bill_provider, redis_client)
        self.scheduling = DataSchedulingHandler(build_pipeline=build_data_pipeline)

    def _ensure_idempotency_key(self, data: DataPayload) -> DataPayload:
        if data.idempotency_key and data.idempotency_key != "no-key":
            return data
        return data.model_copy(update={"idempotency_key": f"data-{uuid.uuid4()}"})

    @staticmethod
    def _build_context(context: dict[str, Any]) -> DataContext:
        referent_memory = context.get("referent_memory")
        resolved_referents = context.get("resolved_referents")
        return DataContext(
            phone_number=context.get("phone_number", ""),
            language=LocaleManager.normalize(context.get("language")).value,
            channel=context.get("channel", "whatsapp"),
            beneficiaries=context.get("beneficiaries", []),
            accounts=context.get("accounts", []),
            all_accounts=context.get("all_accounts", []),
            referent_memory=referent_memory if isinstance(referent_memory, dict) else {},
            resolved_referents=resolved_referents if isinstance(resolved_referents, dict) else {},
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
        previous_response = context.get("previous_response")
        return DataWorkerContext(
            extractor=self.extractor,
            bill_provider=self.bill_provider,
            plan_service=self.plan_service,
            publisher=self.publisher,
            transaction_repo=self.transaction_repo,
            user_id=context.get("user_id"),
            channel_identity=str(context.get("channel_identity")) if context.get("channel_identity") else None,
            required_fields=required_fields if isinstance(required_fields, list) else [],
            previous_response=previous_response if isinstance(previous_response, str) else None,
        )

    @staticmethod
    def _policy_gate_message(action: str, *, locale: str = "en") -> str | None:
        if action in SCHEDULING_ACTIONS:
            return capability_block_message(
                domain="schedule", action=action, locale=locale
            ) or capability_block_message(
                domain="data",
                action="buy_data",
                locale=locale,
            )
        return capability_block_message(domain="data", action=action, locale=locale)

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
        if action in SCHEDULING_ACTIONS and not settings.enable_transfer_scheduling:
            message = render_message("schedule.unavailable.data", locale)
            return TransactionResult(
                outcome=TransactionOutcome.FAILED,
                error=message,
                response=message,
                patch={"capability_blocked": True},
            )
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
        bound_pin_verified = is_task_authorized_by_pin(
            context=context,
            idempotency_key=data.idempotency_key,
            pin_verified=pin_verified,
        )
        if pin_verified and not bound_pin_verified:
            logger.warning(
                "data_pin_verified_without_matching_authorization",
                has_idempotency_key=bool(data.idempotency_key),
            )
        gates = self._build_gates(data, bound_pin_verified)
        worker_context = self._build_worker_context(context)
        pipeline = build_data_pipeline(user_message)

        try:
            if action in SCHEDULING_ACTIONS:
                result = await self.scheduling.handle_action(
                    data=data,
                    ctx=ctx,
                    worker_context=worker_context,
                    user_message=user_message,
                    gates=gates,
                )
                if data.idempotency_key:
                    if result.patch is None:
                        result.patch = {}
                    result.patch["idempotency_key"] = data.idempotency_key
                return result

            if action == "data_plan_query":
                result = await build_data_plan_query_pipeline(user_message).run(data, ctx, gates, worker_context)
            else:
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
                ),
                retryable=True,
                patch={"idempotency_key": data.idempotency_key},
            )
        finally:
            duration = (time.perf_counter() - start_time) * 1000
            log_orchestrator_diagnostic(
                logger,
                "perf_timer_latency",
                gate="data_worker_total",
                duration_ms=round(duration, 2),
                phone_number=context.get("phone_number"),
            )
