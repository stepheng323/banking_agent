"""Transfer Worker (V3).

Stateless domain worker for Transfer tasks.
Executes a single pass through the transfer logic pipeline:
Extract -> Resolve -> Validate -> Confirmation -> Authorization -> Execution.

Returns a standardized TransactionResult.
"""

import time
import uuid
from dataclasses import dataclass
from typing import Any, cast

from apps.chat.src.agent.orchestrator.models.domain import (
    TransactionOutcome,
    TransactionResult,
)
from apps.chat.src.agent.workers.transfer.models.types import (
    TransferContext,
    TransferGates,
    TransferPayload,
)
from apps.chat.src.agent.workers.transfer.pipeline_factory import build_transfer_pipeline
from apps.chat.src.agent.workers.transfer.scheduling import SCHEDULING_ACTIONS, TransferSchedulingHandler
from apps.chat.src.agent.workers.transfer.validation.service import ValidationService
from shared.config.settings import settings
from shared.i18n.locale import LocaleManager
from shared.i18n.renderer import render_message
from shared.policy.service import capability_block_message
from banking.transactions.repositories.transaction_repository import (
    TransactionRepository,
)
from shared.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass(slots=True)
class TransferWorkerContext:
    extractor: Any
    resolver_provider: Any
    bank_cache: Any
    payout_resolver_provider: Any | None
    payout_bank_cache: Any | None
    publisher: Any
    transaction_repo: TransactionRepository
    dd_provider: Any | None
    redis_client: Any
    user_id: str | None
    validation_service: Any
    required_fields: list[str]
    previous_response: str | None
    confirmation_task_count: int | None = None
    progress_tracker: Any | None = None


class TransferWorker:
    """Stateless worker for transfer tasks."""

    def __init__(
        self,
        validation_service: Any,
        publisher: Any,
        extractor: Any,
        resolver_provider: Any,
        bank_cache: Any,
        transaction_repo: TransactionRepository,
        dd_provider: Any | None = None,
        redis_client: Any | None = None,
        payout_resolver_provider: Any | None = None,
        payout_bank_cache: Any | None = None,
    ) -> None:
        self.validation_service = validation_service or ValidationService()
        self.publisher = publisher
        self.extractor = extractor
        self.resolver_provider = resolver_provider
        self.bank_cache = bank_cache
        self.payout_resolver_provider = payout_resolver_provider
        self.payout_bank_cache = payout_bank_cache
        self.transaction_repo = transaction_repo
        self.dd_provider = dd_provider
        self.redis_client = redis_client
        self.scheduling = TransferSchedulingHandler(build_pipeline=build_transfer_pipeline)

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
            all_accounts=context.get("all_accounts", []),
            referent_memory=context.get("referent_memory") if isinstance(context.get("referent_memory"), dict) else {},
            resolved_referents=(
                context.get("resolved_referents") if isinstance(context.get("resolved_referents"), dict) else {}
            ),
            channel=str(context.get("channel") or "whatsapp"),
            channel_identity=str(context.get("channel_identity")) if context.get("channel_identity") else None,
        )

    @staticmethod
    def _build_gates(data: TransferPayload, pin_verified: bool) -> TransferGates:
        return TransferGates(
            pin_verified=pin_verified,
            confirmation_confirmed=(pin_verified or data.confirmation.confirmed),
        )

    def _build_worker_context(self, context: dict[str, Any]) -> TransferWorkerContext:
        required_fields = context.get("required_fields")
        previous_response = context.get("previous_response")
        confirmation_task_count = context.get("confirmation_task_count")
        return TransferWorkerContext(
            extractor=self.extractor,
            resolver_provider=self.resolver_provider,
            bank_cache=self.bank_cache,
            payout_resolver_provider=self.payout_resolver_provider,
            payout_bank_cache=self.payout_bank_cache,
            publisher=self.publisher,
            transaction_repo=self.transaction_repo,
            dd_provider=self.dd_provider,
            redis_client=self.redis_client,
            user_id=context.get("user_id"),
            validation_service=self.validation_service,
            required_fields=required_fields if isinstance(required_fields, list) else [],
            previous_response=previous_response if isinstance(previous_response, str) else None,
            confirmation_task_count=confirmation_task_count if isinstance(confirmation_task_count, int) else None,
            progress_tracker=context.get("progress_tracker"),
        )

    @staticmethod
    def _policy_gate_message(action: str, *, locale: str = "en") -> str | None:
        domain = "schedule" if action in SCHEDULING_ACTIONS else "transfer"
        return cast(str, capability_block_message(domain=domain, action=action, locale=locale))

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
        if action in SCHEDULING_ACTIONS and not settings.enable_transfer_scheduling:
            message = render_message("schedule.unavailable.transfer", locale)
            return TransactionResult(
                outcome=TransactionOutcome.FAILED,
                error=message,
                response=message,
                patch={"capability_blocked": True},
            )
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
        try:
            if action in SCHEDULING_ACTIONS:
                result = await self.scheduling.handle_action(
                    action=action,
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

            pipeline = build_transfer_pipeline(user_message, include_execution=True)
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
