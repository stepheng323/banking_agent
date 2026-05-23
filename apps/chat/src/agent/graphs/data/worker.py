"""Data Worker (V3).

Stateless domain worker for Data tasks.
Executes a single pass through the data logic pipeline.
"""

import time
import uuid
from dataclasses import dataclass
from typing import Any, cast

from apps.chat.src.agent.graphs.__shared__.scheduling import (
    base_schedule_fields,
    missing_schedule_fields,
    schedule_recurrence_label,
    schedule_required_prompt,
)
from apps.chat.src.agent.graphs.data.models.types import (
    DataContext,
    DataGates,
    DataPayload,
)
from apps.chat.src.agent.graphs.data.nodes.confirmation import ConfirmationStep
from apps.chat.src.agent.graphs.data.nodes.execution import ExecutionStep
from apps.chat.src.agent.graphs.data.nodes.extraction import ExtractionStep
from apps.chat.src.agent.graphs.data.nodes.resolution import ResolutionStep
from apps.chat.src.agent.graphs.data.nodes.security import AuthorizationStep
from apps.chat.src.agent.graphs.data.nodes.selection import SourceSelectionStep
from apps.chat.src.agent.graphs.data.nodes.validation import ValidationStep
from apps.chat.src.agent.graphs.data.pipeline.base import DataPipeline, PipelineStep
from apps.chat.src.agent.orchestrator.models.domain import (
    TransactionOutcome,
    TransactionResult,
)
from shared.config.settings import settings
from shared.database.enums import ScheduledInstructionStatusEnum
from shared.formatters.currency import format_naira
from shared.i18n import LocaleManager, render_message
from shared.policy.service import capability_block_message
from shared.repositories.scheduled_instruction_repository import ScheduledInstructionRepository
from shared.repositories.unit_of_work import UnitOfWork
from shared.services.scheduling.recurrence import (
    SCHEDULE_TIMEZONE,
    compute_initial_next_run_utc,
    format_lagos_schedule_datetime,
    today_lagos,
)
from shared.utils.logging import get_logger

logger = get_logger(__name__)
SCHEDULING_ACTIONS = {"schedule_data", "recurring_data"}


class DataScheduleRequirementsStep(PipelineStep):
    """Requires explicit schedule fields before confirmation."""

    async def run(
        self,
        payload: DataPayload,
        context: DataContext,
        gates: DataGates,
        worker_context: Any,
    ) -> TransactionResult | None:
        del gates, worker_context
        missing_fields = missing_schedule_fields(payload)
        if not missing_fields:
            return None
        return TransactionResult(
            outcome=TransactionOutcome.NEEDS_INPUT,
            required_fields=missing_fields,
            prompt=schedule_required_prompt(missing_fields, context.language),
            patch={"is_scheduled_operation": True, "skip_finalize_summary": True},
        )


class DataScheduleCompleteStep(PipelineStep):
    """Terminates the schedule creation pipeline after PIN/auth succeeds."""

    async def run(
        self,
        payload: DataPayload,
        context: DataContext,
        gates: DataGates,
        worker_context: Any,
    ) -> TransactionResult | None:
        del context, gates, worker_context
        return TransactionResult(
            outcome=TransactionOutcome.OK,
            patch=payload.model_dump(exclude_none=True),
        )


@dataclass(slots=True)
class DataWorkerContext:
    extractor: Any
    bill_provider: Any
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
    ):
        self.extractor = extractor
        self.bill_provider = bill_provider
        self.transaction_repo = transaction_repo
        self.publisher = publisher

    def _ensure_idempotency_key(self, data: DataPayload) -> DataPayload:
        if data.idempotency_key and data.idempotency_key != "no-key":
            return data
        return data.model_copy(update={"idempotency_key": f"data-{uuid.uuid4()}"})

    @staticmethod
    def _build_context(context: dict[str, Any]) -> DataContext:
        return DataContext(
            phone_number=context.get("phone_number", ""),
            language=LocaleManager.normalize(context.get("language")).value,
            channel=context.get("channel", "whatsapp"),
            beneficiaries=context.get("beneficiaries", []),
            accounts=context.get("accounts", []),
            all_accounts=context.get("all_accounts", []),
            referent_memory=context.get("referent_memory") if isinstance(context.get("referent_memory"), dict) else {},
            resolved_referents=(
                context.get("resolved_referents") if isinstance(context.get("resolved_referents"), dict) else {}
            ),
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
            return capability_block_message(domain="schedule", action=action, locale=locale) or capability_block_message(
                domain="data",
                action="buy_data",
                locale=locale,
            )
        return capability_block_message(domain="data", action=action, locale=locale)

    @staticmethod
    def _build_pipeline(
        user_message: str | None,
        *,
        include_execution: bool = True,
        require_schedule_fields: bool = False,
    ) -> DataPipeline:
        steps: list[PipelineStep] = [
            ExtractionStep(user_message),
            ResolutionStep(),
            SourceSelectionStep(),
            ValidationStep(),
        ]
        if require_schedule_fields:
            steps.append(DataScheduleRequirementsStep())
        steps.extend([ConfirmationStep(), AuthorizationStep()])
        if include_execution:
            steps.append(ExecutionStep())
        else:
            steps.append(DataScheduleCompleteStep())
        return DataPipeline(steps)

    async def _create_schedule_after_auth(
        self,
        *,
        data: DataPayload,
        ctx: DataContext,
        locale: str,
        user_id: str,
        channel_identity: str | None,
    ) -> TransactionResult:
        schedule_fields = base_schedule_fields(data)
        recurrence_type = str(schedule_fields["recurrence_type"] or "one_time")
        start_date = cast(str | None, schedule_fields["schedule_start_date"])
        time_local = cast(str | None, schedule_fields["schedule_time_local"])
        timezone = cast(str, schedule_fields["schedule_timezone"] or SCHEDULE_TIMEZONE)
        day_of_week = cast(int | None, schedule_fields["schedule_day_of_week"])
        day_of_month = cast(int | None, schedule_fields["schedule_day_of_month"])
        missing_fields = missing_schedule_fields(data)
        if missing_fields:
            return TransactionResult(
                outcome=TransactionOutcome.NEEDS_INPUT,
                required_fields=missing_fields,
                prompt=schedule_required_prompt(missing_fields, locale),
                patch={"is_scheduled_operation": True, "skip_finalize_summary": True},
            )

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
                prompt=render_message("schedule.prompt.future_date_time", locale),
                patch={"is_scheduled_operation": True, "skip_finalize_summary": True},
            )

        snapshot = {
            "amount": data.amount,
            "network": data.network,
            "target_phone": data.target_phone,
            "plan_code": data.plan_code,
            "plan_name": data.plan_name,
            "is_self": data.is_self,
            "source_account_id": data.source_account_id,
            "source_account_number": data.source_account_number,
            "source_account_name": data.source_account_name,
            "source_bank_name": data.source_bank_name,
            "source_affinity_mode": None,
            "language": locale,
            **schedule_fields,
        }

        async with UnitOfWork() as uow:
            repo: ScheduledInstructionRepository | None = uow.scheduled_instructions
            if not repo:
                return TransactionResult(
                    outcome=TransactionOutcome.FAILED,
                    error=render_message("data.error.pipeline_failed", locale, {"error": "schedule_repo_missing"}),
                )
            schedule = await repo.create(
                user_id=user_id,
                domain="data",
                status=ScheduledInstructionStatusEnum.ACTIVE.value,
                action="buy_data",
                payload_snapshot=snapshot,
                timezone=timezone,
                recurrence_type=recurrence_type,
                start_date=start_date or today_lagos().isoformat(),
                local_time=time_local,
                day_of_week=day_of_week,
                day_of_month=day_of_month,
                end_date=schedule_fields["schedule_end_date"],
                next_run_at_utc=next_run_at,
                channel=ctx.channel,
                channel_identity=channel_identity or ctx.phone_number,
            )
            await uow.commit()

        amount = format_naira(data.amount)
        target = data.target_phone or render_message("schedule.fallback.recipient", locale)
        plan = data.plan_name or render_message("schedule.fallback.data_plan", locale)
        next_run_text = format_lagos_schedule_datetime(next_run_at)
        response = render_message(
            "schedule.created.data",
            locale,
            {
                "amount": amount,
                "plan": plan,
                "target": target,
                "recurrence": schedule_recurrence_label(recurrence_type, locale),
                "timezone": timezone,
                "next_run": next_run_text,
            },
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
        data: DataPayload,
        ctx: DataContext,
        worker_context: DataWorkerContext,
        user_message: str | None,
        gates: DataGates,
    ) -> TransactionResult:
        locale = ctx.language
        user_id = str(worker_context.user_id or "").strip()
        if not user_id:
            return TransactionResult(
                outcome=TransactionOutcome.FAILED,
                error=render_message("data.error.pipeline_failed", locale, {"error": "missing_user_id"}),
            )

        pipeline = self._build_pipeline(user_message, include_execution=False, require_schedule_fields=True)
        result = await pipeline.run(data, ctx, gates, worker_context)
        if result.outcome != TransactionOutcome.OK:
            return result

        scheduled_data = data.model_copy(update=result.patch or {})
        return await self._create_schedule_after_auth(
            data=scheduled_data,
            ctx=ctx,
            locale=locale,
            user_id=user_id,
            channel_identity=worker_context.channel_identity,
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
        gates = self._build_gates(data, pin_verified)
        worker_context = self._build_worker_context(context)
        pipeline = self._build_pipeline(user_message)

        try:
            if action in SCHEDULING_ACTIONS:
                result = await self._handle_scheduling_action(
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
