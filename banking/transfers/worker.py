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

from banking.policy.service import capability_block_message
from banking.presentation.i18n.locale import LocaleManager
from banking.presentation.i18n.renderer import render_message
from banking.runtime.results import TransactionOutcome, TransactionResult
from banking.security.authorization_context import is_task_authorized_by_pin
from banking.transactions.repositories.transaction_repository import (
    TransactionRepository,
)
from banking.transfers.extraction.updates import apply_transfer_amendment_update
from banking.transfers.models.types import (
    TransferContext,
    TransferGates,
    TransferPayload,
)
from banking.transfers.nodes.extraction import ExtractionStep
from banking.transfers.pipeline_factory import build_transfer_pipeline
from banking.transfers.scheduling import SCHEDULING_ACTIONS, TransferSchedulingHandler
from banking.transfers.validation.service import ValidationService
from shared.config.settings import settings
from shared.observability.llm import LLMCallDeadlineExceeded
from shared.utils.logging import get_logger, log_orchestrator_diagnostic

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
        referent_memory = context.get("referent_memory")
        resolved_referents = context.get("resolved_referents")
        return TransferContext(
            phone_number=context.get("phone_number", ""),
            language=LocaleManager.normalize(context.get("language")).value,
            beneficiaries=context.get("beneficiaries", []),
            accounts=context.get("accounts", []),
            all_accounts=context.get("all_accounts", []),
            referent_memory=referent_memory if isinstance(referent_memory, dict) else {},
            resolved_referents=resolved_referents if isinstance(resolved_referents, dict) else {},
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

    async def interpret_pending_confirmation_edit(
        self,
        *,
        payload: dict[str, Any],
        context: dict[str, Any],
        user_message: str,
    ) -> TransactionResult:
        """Interpret one pending transfer amendment without running the full pipeline.

        This is deliberately extraction-only: callers must still apply the
        resulting patch through the normal reset, funding, and confirmation
        path. It lets the interrupt workflow reuse the transfer extractor
        before it pays for broader batch-routing inference.
        """
        pending_payload = dict(payload)
        confirmation = pending_payload.get("confirmation")
        snapshot = confirmation.get("snapshot") if isinstance(confirmation, dict) else None
        if isinstance(snapshot, dict) and snapshot:
            pending_payload["previous_confirmation_snapshot"] = dict(snapshot)

        data = self._ensure_idempotency_key(TransferPayload(**pending_payload))
        worker_context = self._build_worker_context(context)
        amendment_interpreter = getattr(self.extractor, "extract_amendment", None)
        if callable(amendment_interpreter):
            amendment_context = {
                **context,
                "known_recipient": {
                    "recipient_name": data.recipient_name,
                    "recipient_resolved_name": data.recipient_resolved_name,
                    "recipient_account": data.recipient_account,
                    "recipient_bank_name": data.recipient_bank_name,
                },
            }
            amendment = await amendment_interpreter(user_message, smart_context=amendment_context)
            return await apply_transfer_amendment_update(
                data,
                amendment,
                user_message=user_message,
                context=context,
            )
        return await ExtractionStep(user_message).execute(
            data,
            self._build_context(context),
            self._build_gates(data, pin_verified=False),
            worker_context,
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
        bound_pin_verified = is_task_authorized_by_pin(
            context=context,
            idempotency_key=data.idempotency_key,
            pin_verified=pin_verified,
        )
        if pin_verified and not bound_pin_verified:
            logger.warning(
                "transfer_pin_verified_without_matching_authorization",
                has_idempotency_key=bool(data.idempotency_key),
            )
        gates = self._build_gates(data, bound_pin_verified)
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
        except LLMCallDeadlineExceeded as exc:
            logger.warning(
                "transfer_extractor_deadline_exceeded",
                role=exc.role,
                deadline_seconds=exc.deadline_seconds,
            )
            message = render_message("orchestrator.fallback.transfer_timeout", locale)
            return TransactionResult(
                outcome=TransactionOutcome.NEEDS_INPUT,
                response=message,
                prompt=message,
                retryable=True,
                patch={"idempotency_key": data.idempotency_key},
            )
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
            log_orchestrator_diagnostic(
                logger,
                "perf_timer_latency",
                gate="transfer_worker_total",
                duration_ms=round(duration, 2),
                phone_number=context.get("phone_number"),
            )
