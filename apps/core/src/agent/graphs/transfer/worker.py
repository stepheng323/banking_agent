"""Transfer Worker (V3).

Stateless domain worker for Transfer tasks.
Executes a single pass through the transfer logic pipeline:
Extract -> Resolve -> Validate -> Confirmation -> Authorization -> Execution.

Returns a standardized TransactionResult.
"""

import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

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
from shared.config.settings import settings
from shared.database.enums import ScheduledInstructionStatusEnum
from shared.i18n import LocaleManager, render_capability_limitation, render_message
from shared.policy.adapters import resolve_capability_alternative, resolve_capability_rule
from shared.repositories.scheduled_instruction_repository import ScheduledInstructionRepository
from shared.repositories.transaction_repository import (
    TransactionRepository,
)
from shared.repositories.unit_of_work import UnitOfWork
from shared.services.scheduling.recurrence import (
    DEFAULT_SCHEDULE_TIME_TEXT,
    SCHEDULE_TIMEZONE,
    compute_initial_next_run_utc,
)
from shared.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass(slots=True)
class TransferWorkerContext:
    extractor: Any
    resolver_provider: Any
    bank_cache: Any
    publisher: Any
    transaction_repo: TransactionRepository
    dd_provider: Any | None
    redis_client: Any
    user_id: str | None
    validation_service: Any
    required_fields: list[str]
    previous_response: str | None


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
    ) -> None:
        self.validation_service = validation_service or ValidationService()
        self.publisher = publisher
        self.extractor = extractor
        self.resolver_provider = resolver_provider
        self.bank_cache = bank_cache
        self.transaction_repo = transaction_repo
        self.dd_provider = dd_provider
        self.redis_client = redis_client

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
            recent_beneficiary_context=bool(context.get("recent_beneficiary_context")),
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
        return TransferWorkerContext(
            extractor=self.extractor,
            resolver_provider=self.resolver_provider,
            bank_cache=self.bank_cache,
            publisher=self.publisher,
            transaction_repo=self.transaction_repo,
            dd_provider=self.dd_provider,
            redis_client=self.redis_client,
            user_id=context.get("user_id"),
            validation_service=self.validation_service,
            required_fields=required_fields if isinstance(required_fields, list) else [],
            previous_response=previous_response if isinstance(previous_response, str) else None,
        )

    @staticmethod
    def _policy_gate_message(action: str, *, locale: str = "en") -> str | None:
        rule = resolve_capability_rule(domain="transfer", action=action)
        if rule is not None and rule.supported:
            return None

        alt = resolve_capability_alternative(domain="transfer", action=action)
        return cast(
            str,
            render_capability_limitation(
                locale=locale,
                action_label=action.replace("_", " "),
                alternative_labels=[alt.replace("_", " ")] if alt else [],
            ),
        )

    @staticmethod
    def _build_pipeline(user_message: str | None, *, include_execution: bool = True) -> TransferPipeline:
        steps: list[Any] = [
            ExtractionStep(user_message),
            ResolutionStep(),
            SourceSelectionStep(),
            ValidationStep(),
            FundingStep(),
            ConfirmationStep(),
            AuthorizationStep(),
        ]
        if include_execution:
            steps.append(ExecutionStep())
        return TransferPipeline(
            steps
        )

    @staticmethod
    def _schedule_fields_from_payload(data: TransferPayload) -> dict[str, Any]:
        return {
            "schedule_mode": data.schedule_mode or ("recurring" if data.recurrence_type and data.recurrence_type != "one_time" else "one_time"),
            "recurrence_type": data.recurrence_type or "one_time",
            "schedule_timezone": data.schedule_timezone or SCHEDULE_TIMEZONE,
            "schedule_start_date": data.schedule_start_date,
            "schedule_time_local": data.schedule_time_local or DEFAULT_SCHEDULE_TIME_TEXT,
            "schedule_day_of_week": data.schedule_day_of_week,
            "schedule_day_of_month": data.schedule_day_of_month,
            "schedule_end_date": data.schedule_end_date,
        }

    @staticmethod
    def _resolve_schedule_selector(data: TransferPayload, user_message: str | None) -> str | None:
        selector = (data.schedule_selector or "").strip()
        if selector:
            return selector
        if not user_message:
            return None
        text = user_message.strip()
        if text.isdigit():
            return text
        return None

    async def _list_schedules(self, *, user_id: str, locale: str) -> TransactionResult:
        async with UnitOfWork() as uow:
            if not uow.scheduled_instructions:
                return TransactionResult(
                    outcome=TransactionOutcome.FAILED,
                    error=render_message("transfer.error.pipeline_failed", locale, {"error": "schedule_repo_missing"}),
                )
            schedules = await uow.scheduled_instructions.get_active_by_user(user_id, limit=10)

        if not schedules:
            return TransactionResult(
                outcome=TransactionOutcome.OK,
                response="You have no active scheduled transfers.",
                patch={"is_scheduled_operation": True, "skip_finalize_summary": True},
            )

        lines = ["Scheduled transfers:"]
        for idx, schedule in enumerate(schedules, start=1):
            payload = schedule.payload_snapshot if isinstance(schedule.payload_snapshot, dict) else {}
            amount = float(payload.get("amount") or 0.0)
            recipient = payload.get("recipient_resolved_name") or payload.get("recipient_name") or "Recipient"
            recurrence = str(schedule.recurrence_type).replace("_", " ").title()
            time_local = payload.get("schedule_time_local") or schedule.local_time or DEFAULT_SCHEDULE_TIME_TEXT
            lines.append(f"{idx}. ₦{amount:,.0f} to {recipient} • {recurrence} at {time_local} (ID: {schedule.id})")

        return TransactionResult(
            outcome=TransactionOutcome.OK,
            response="\n".join(lines),
            patch={"is_scheduled_operation": True, "skip_finalize_summary": True},
        )

    async def _cancel_schedule(
        self,
        *,
        data: TransferPayload,
        user_id: str,
        locale: str,
        user_message: str | None,
    ) -> TransactionResult:
        selector = self._resolve_schedule_selector(data, user_message)
        async with UnitOfWork() as uow:
            repo = uow.scheduled_instructions
            if not repo:
                return TransactionResult(
                    outcome=TransactionOutcome.FAILED,
                    error=render_message("transfer.error.pipeline_failed", locale, {"error": "schedule_repo_missing"}),
                )
            schedules = await repo.get_active_by_user(user_id, limit=20)
            if not schedules:
                return TransactionResult(
                    outcome=TransactionOutcome.OK,
                    response="You have no active scheduled transfers to cancel.",
                    patch={"is_scheduled_operation": True, "skip_finalize_summary": True},
                )

            selected = None
            if selector:
                if selector.isdigit():
                    idx = int(selector)
                    if 1 <= idx <= len(schedules):
                        selected = schedules[idx - 1]
                if selected is None:
                    selected = next((item for item in schedules if str(item.id) == selector), None)

            if selected is None:
                lines = ["Reply with the schedule number to cancel:"]
                for idx, schedule in enumerate(schedules, start=1):
                    payload = schedule.payload_snapshot if isinstance(schedule.payload_snapshot, dict) else {}
                    recipient = payload.get("recipient_resolved_name") or payload.get("recipient_name") or "Recipient"
                    amount = float(payload.get("amount") or 0.0)
                    lines.append(f"{idx}. ₦{amount:,.0f} to {recipient}")
                return TransactionResult(
                    outcome=TransactionOutcome.NEEDS_INPUT,
                    required_fields=["schedule_selector"],
                    prompt="\n".join(lines),
                    patch={"is_scheduled_operation": True, "skip_finalize_summary": True},
                )

            selected.status = ScheduledInstructionStatusEnum.CANCELLED.value
            selected.cancelled_at = datetime.now(UTC).replace(tzinfo=None)
            await uow.commit()

        return TransactionResult(
            outcome=TransactionOutcome.OK,
            response=f"Scheduled transfer cancelled (ID: {selected.id}).",
            patch={"is_scheduled_operation": True, "skip_finalize_summary": True},
        )

    async def _create_schedule_after_auth(
        self,
        *,
        data: TransferPayload,
        ctx: TransferContext,
        locale: str,
        user_id: str,
    ) -> TransactionResult:
        schedule_fields = self._schedule_fields_from_payload(data)
        recurrence_type = str(schedule_fields["recurrence_type"] or "one_time")
        start_date = cast(str | None, schedule_fields["schedule_start_date"])
        time_local = cast(str | None, schedule_fields["schedule_time_local"])
        timezone = cast(str, schedule_fields["schedule_timezone"] or SCHEDULE_TIMEZONE)
        day_of_week = cast(int | None, schedule_fields["schedule_day_of_week"])
        day_of_month = cast(int | None, schedule_fields["schedule_day_of_month"])

        next_run_at = compute_initial_next_run_utc(
            recurrence_type=recurrence_type,
            start_date=start_date,
            local_time=time_local,
            day_of_week=day_of_week,
            day_of_month=day_of_month,
            timezone=timezone,
        )
        if next_run_at is None:
            return TransactionResult(
                outcome=TransactionOutcome.NEEDS_INPUT,
                required_fields=["schedule_start_date", "schedule_time_local"],
                prompt="Please provide a future schedule date and time.",
                patch={"is_scheduled_operation": True, "skip_finalize_summary": True},
            )

        snapshot = {
            "amount": data.amount,
            "recipient_name": data.recipient_name,
            "recipient_resolved_name": data.recipient_resolved_name,
            "recipient_account": data.recipient_account,
            "recipient_bank_code": data.recipient_bank_code,
            "recipient_bank_name": data.recipient_bank_name,
            "source_account_id": data.source_account_id,
            "source_account_number": data.source_account_number,
            "source_bank_name": data.source_bank_name,
            "narration": data.narration,
            "language": locale,
            **schedule_fields,
        }

        async with UnitOfWork() as uow:
            repo: ScheduledInstructionRepository | None = uow.scheduled_instructions
            if not repo:
                return TransactionResult(
                    outcome=TransactionOutcome.FAILED,
                    error=render_message("transfer.error.pipeline_failed", locale, {"error": "schedule_repo_missing"}),
                )
            schedule = await repo.create(
                user_id=user_id,
                domain="transfer",
                status=ScheduledInstructionStatusEnum.ACTIVE.value,
                action="send_money",
                payload_snapshot=snapshot,
                timezone=timezone,
                recurrence_type=recurrence_type,
                start_date=start_date or datetime.now(UTC).date().isoformat(),
                local_time=time_local or DEFAULT_SCHEDULE_TIME_TEXT,
                day_of_week=day_of_week,
                day_of_month=day_of_month,
                end_date=schedule_fields["schedule_end_date"],
                next_run_at_utc=next_run_at,
                channel=ctx.channel,
                channel_identity=ctx.channel_identity,
            )
            await uow.commit()

        amount = f"₦{float(data.amount or 0.0):,.0f}"
        recipient = data.recipient_name or data.recipient_resolved_name or "recipient"
        next_run_text = next_run_at.replace(tzinfo=UTC).strftime("%Y-%m-%d %H:%M UTC")
        response = (
            f"Scheduled successfully: {amount} to {recipient} "
            f"({recurrence_type.replace('_', ' ')}) in {timezone}. Next run: {next_run_text}. "
            f"Schedule ID: {schedule.id}"
        )
        return TransactionResult(
            outcome=TransactionOutcome.OK,
            response=response,
            patch={
                "is_scheduled_operation": True,
                "skip_finalize_summary": True,
                "schedule_id": str(schedule.id),
                "schedule_operation_note": response,
            },
        )

    async def _handle_scheduling_action(
        self,
        *,
        action: str,
        data: TransferPayload,
        ctx: TransferContext,
        worker_context: TransferWorkerContext,
        user_message: str | None,
        gates: TransferGates,
    ) -> TransactionResult:
        locale = ctx.language
        user_id = str(worker_context.user_id or "").strip()
        if not user_id:
            return TransactionResult(
                outcome=TransactionOutcome.FAILED,
                error=render_message("transfer.error.pipeline_failed", locale, {"error": "missing_user_id"}),
            )

        if action == "list_scheduled_transfers":
            return await self._list_schedules(user_id=user_id, locale=locale)

        if action == "cancel_scheduled_transfer":
            return await self._cancel_schedule(data=data, user_id=user_id, locale=locale, user_message=user_message)

        pipeline = self._build_pipeline(user_message, include_execution=False)
        result = await pipeline.run(data, ctx, gates, worker_context)
        if result.outcome != TransactionOutcome.OK:
            return result

        scheduled_data = data.model_copy(update=result.patch or {})
        return await self._create_schedule_after_auth(
            data=scheduled_data,
            ctx=ctx,
            locale=locale,
            user_id=user_id,
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
        scheduling_actions = {
            "schedule_transfer",
            "recurring_transfer",
            "list_scheduled_transfers",
            "cancel_scheduled_transfer",
        }
        if action in scheduling_actions and not settings.enable_transfer_scheduling:
            message = "Scheduled transfers are currently unavailable. You can send this transfer now."
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
            if action in scheduling_actions:
                return await self._handle_scheduling_action(
                    action=action,
                    data=data,
                    ctx=ctx,
                    worker_context=worker_context,
                    user_message=user_message,
                    gates=gates,
                )

            pipeline = self._build_pipeline(user_message, include_execution=True)
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
