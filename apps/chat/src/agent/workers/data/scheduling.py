"""Scheduled data requirements and creation actions."""

from typing import Any, Protocol, cast

from apps.chat.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
from apps.chat.src.agent.workers.__shared__.scheduling import (
    base_schedule_fields,
    missing_schedule_fields,
    schedule_recurrence_label,
    schedule_required_prompt,
)
from apps.chat.src.agent.workers.data.models.types import DataContext, DataGates, DataPayload
from apps.chat.src.agent.workers.data.pipeline.base import DataPipeline, PipelineStep
from banking.persistence.unit_of_work import UnitOfWork
from banking.scheduling.repositories.scheduled_instruction_repository import ScheduledInstructionRepository
from banking.scheduling.services.recurrence import (
    SCHEDULE_TIMEZONE,
    compute_initial_next_run_utc,
    format_lagos_schedule_datetime,
    today_lagos,
)
from shared.database.enums import ScheduledInstructionStatusEnum
from shared.formatters.currency import format_naira
from shared.i18n.renderer import render_message

SCHEDULING_ACTIONS = {"schedule_data", "recurring_data"}


class DataPipelineBuilder(Protocol):
    def __call__(
        self,
        user_message: str | None,
        *,
        include_execution: bool = True,
        require_schedule_fields: bool = False,
    ) -> DataPipeline: ...


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


class DataSchedulingHandler:
    """Handles scheduled data creation after pipeline validation/auth."""

    def __init__(self, *, build_pipeline: DataPipelineBuilder) -> None:
        self.build_pipeline = build_pipeline

    async def create_schedule_after_auth(
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
            "biller_code": data.biller_code,
            "plan_size_gb": data.plan_size_gb,
            "plan_validity_days": data.plan_validity_days,
            "plan_tags": data.plan_tags,
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

    async def handle_action(
        self,
        *,
        data: DataPayload,
        ctx: DataContext,
        worker_context: Any,
        user_message: str | None,
        gates: DataGates,
    ) -> TransactionResult:
        locale = ctx.language
        user_id = str(getattr(worker_context, "user_id", None) or "").strip()
        if not user_id:
            return TransactionResult(
                outcome=TransactionOutcome.FAILED,
                error=render_message("data.error.pipeline_failed", locale, {"error": "missing_user_id"}),
            )

        pipeline = self.build_pipeline(user_message, include_execution=False, require_schedule_fields=True)
        result = await pipeline.run(data, ctx, gates, worker_context)
        if result.outcome != TransactionOutcome.OK:
            return result

        scheduled_data = data.model_copy(update=result.patch or {})
        channel_identity = getattr(worker_context, "channel_identity", None)
        return await self.create_schedule_after_auth(
            data=scheduled_data,
            ctx=ctx,
            locale=locale,
            user_id=user_id,
            channel_identity=channel_identity if isinstance(channel_identity, str) else None,
        )
