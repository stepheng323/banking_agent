"""Scheduled transfer requirements and management actions."""

from datetime import datetime
from typing import Any, Protocol, cast

from apps.chat.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
from apps.chat.src.agent.workers.__shared__.schedule_management import (
    apply_schedule_edit,
    build_schedule_context_items,
    build_schedule_edit_patch,
    build_schedule_edit_success_message,
    build_schedule_update_summary,
    cancelled_now,
    disambiguation_result,
    format_schedule_row,
    resolve_schedule_selection,
    schedule_edit_requires_auth,
)
from apps.chat.src.agent.workers.__shared__.scheduling import schedule_recurrence_label, schedule_required_prompt
from apps.chat.src.agent.workers.transfer.authorization.pin_token import persist_schedule_pin_token
from apps.chat.src.agent.workers.transfer.models.types import TransferContext, TransferGates, TransferPayload
from apps.chat.src.agent.workers.transfer.pipeline.base import TransferPipeline, TransferStep
from shared.database.enums import ScheduledInstructionStatusEnum
from shared.formatters.currency import format_naira
from shared.i18n.renderer import render_message
from shared.repositories.scheduled_instruction_repository import ScheduledInstructionRepository
from shared.repositories.unit_of_work import UnitOfWork
from shared.services.scheduling.recurrence import (
    SCHEDULE_TIMEZONE,
    compute_initial_next_run_utc,
    format_lagos_schedule_datetime,
    today_lagos,
)

SCHEDULING_ACTIONS = {
    "schedule_transfer",
    "recurring_transfer",
    "list_scheduled_transfers",
    "cancel_scheduled_transfer",
    "list_scheduled_transactions",
    "find_scheduled_transaction",
    "cancel_scheduled_transaction",
    "edit_scheduled_transaction",
}
CANCEL_SCHEDULE_ACTIONS = {"cancel_scheduled_transfer", "cancel_scheduled_transaction"}
EDIT_SCHEDULE_ACTIONS = {"edit_scheduled_transaction"}

class TransferPipelineBuilder(Protocol):
    def __call__(
        self,
        user_message: str | None,
        *,
        include_execution: bool = True,
        require_schedule_fields: bool = False,
    ) -> TransferPipeline: ...



def missing_schedule_fields(data: TransferPayload) -> list[str]:
    missing: list[str] = []
    recurrence_type = data.recurrence_type or "one_time"
    if recurrence_type == "one_time" and not data.schedule_start_date:
        missing.append("schedule_start_date")
    if not data.schedule_time_local:
        missing.append("schedule_time_local")
    return missing


def schedule_fields_from_payload(data: TransferPayload) -> dict[str, Any]:
    schedule_mode = data.schedule_mode
    if schedule_mode is None:
        schedule_mode = "recurring" if data.recurrence_type and data.recurrence_type != "one_time" else "one_time"
    return {
        "schedule_mode": schedule_mode,
        "recurrence_type": data.recurrence_type or "one_time",
        "schedule_timezone": data.schedule_timezone or SCHEDULE_TIMEZONE,
        "schedule_start_date": data.schedule_start_date,
        "schedule_time_local": data.schedule_time_local,
        "schedule_day_of_week": data.schedule_day_of_week,
        "schedule_day_of_month": data.schedule_day_of_month,
        "schedule_end_date": data.schedule_end_date,
    }


class ScheduleRequirementsStep(TransferStep):
    """Requires explicit schedule fields before confirmation."""

    async def execute(
        self,
        data: TransferPayload,
        context: TransferContext,
        gates: TransferGates,
        worker_context: Any = None,
    ) -> TransactionResult:
        del gates, worker_context
        missing_fields = missing_schedule_fields(data)
        if not missing_fields:
            return TransactionResult(outcome=TransactionOutcome.OK, patch={})
        return TransactionResult(
            outcome=TransactionOutcome.NEEDS_INPUT,
            required_fields=missing_fields,
            prompt=schedule_required_prompt(missing_fields, context.language),
            patch={"is_scheduled_operation": True, "skip_finalize_summary": True},
        )


class TransferSchedulingHandler:
    """Handles scheduled transfer creation and schedule-management actions."""

    def __init__(self, *, build_pipeline: TransferPipelineBuilder) -> None:
        self._build_pipeline = build_pipeline

    async def list_schedules(
        self,
        *,
        data: TransferPayload | None = None,
        user_id: str,
        locale: str,
    ) -> TransactionResult:
        async with UnitOfWork() as uow:
            if not uow.scheduled_instructions:
                return TransactionResult(
                    outcome=TransactionOutcome.FAILED,
                    error=render_message("transfer.error.pipeline_failed", locale, {"error": "schedule_repo_missing"}),
                )
            schedules = await uow.scheduled_instructions.get_active_by_user(user_id, limit=10)

        if data and data.schedule_response_mode == "count":
            count = len(schedules)
            return TransactionResult(
                outcome=TransactionOutcome.OK,
                response=render_message("schedule.list.count", locale, {"count": count}),
                patch={
                    "is_scheduled_operation": True,
                    "skip_finalize_summary": True,
                    "schedule_context_items": build_schedule_context_items(schedules, locale=locale),
                },
            )

        if not schedules:
            return TransactionResult(
                outcome=TransactionOutcome.OK,
                response=render_message("schedule.list.empty", locale),
                patch={"is_scheduled_operation": True, "skip_finalize_summary": True},
            )

        lines = [render_message("schedule.list.header", locale)]
        lines.extend(format_schedule_row(idx, schedule, locale=locale) for idx, schedule in enumerate(schedules, start=1))

        return TransactionResult(
            outcome=TransactionOutcome.OK,
            response="\n".join(lines),
            patch={
                "is_scheduled_operation": True,
                "skip_finalize_summary": True,
                "schedule_context_items": build_schedule_context_items(schedules, locale=locale),
            },
        )

    async def find_schedules(
        self,
        *,
        data: TransferPayload,
        user_id: str,
        locale: str,
        user_message: str | None,
    ) -> TransactionResult:
        async with UnitOfWork() as uow:
            if not uow.scheduled_instructions:
                return TransactionResult(
                    outcome=TransactionOutcome.FAILED,
                    error=render_message("transfer.error.pipeline_failed", locale, {"error": "schedule_repo_missing"}),
                )
            schedules = await uow.scheduled_instructions.get_active_by_user(user_id, limit=20)

        selection = resolve_schedule_selection(schedules, data=data, user_message=user_message)
        matches = selection.schedules
        if not matches:
            return TransactionResult(
                outcome=TransactionOutcome.OK,
                response=render_message("schedule.find.not_found", locale),
                patch={"is_scheduled_operation": True, "skip_finalize_summary": True},
            )

        lines = [render_message("schedule.find.header", locale)]
        lines.extend(format_schedule_row(idx, schedule, locale=locale) for idx, schedule in enumerate(matches, start=1))
        return TransactionResult(
            outcome=TransactionOutcome.OK,
            response="\n".join(lines),
            patch={
                "is_scheduled_operation": True,
                "skip_finalize_summary": True,
                "schedule_context_items": build_schedule_context_items(matches, locale=locale),
            },
        )

    async def cancel_schedule(
        self,
        *,
        data: TransferPayload,
        user_id: str,
        locale: str,
        user_message: str | None,
    ) -> TransactionResult:
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
                    response=render_message("schedule.cancel.none", locale),
                    patch={"is_scheduled_operation": True, "skip_finalize_summary": True},
                )

            selection = resolve_schedule_selection(schedules, data=data, user_message=user_message)
            selected = selection.selected

            if selected is None:
                return disambiguation_result(selection.schedules, action_label="cancel", locale=locale)

            selected.status = ScheduledInstructionStatusEnum.CANCELLED.value
            selected.cancelled_at = cancelled_now()
            await uow.commit()

        return TransactionResult(
            outcome=TransactionOutcome.OK,
            response=render_message("schedule.cancel.success", locale),
            patch={"is_scheduled_operation": True, "skip_finalize_summary": True},
        )

    async def edit_schedule(
        self,
        *,
        data: TransferPayload,
        user_id: str,
        locale: str,
        user_message: str | None,
        gates: TransferGates,
        phone_number: str | None = None,
        worker_context: Any | None = None,
    ) -> TransactionResult:
        schedule_id = (data.schedule_id or data.schedule_selector or "").strip()
        edit_patch = dict(data.schedule_edit_patch or {})
        requires_auth = bool(getattr(data, "schedule_edit_requires_auth", False))
        next_run_at: datetime | None = None

        if schedule_id and edit_patch:
            try:
                next_run_at_text = data.schedule_edit_next_run_at_utc or ""
                next_run_at = datetime.fromisoformat(next_run_at_text) if next_run_at_text else None
            except ValueError:
                next_run_at = None

        async with UnitOfWork() as uow:
            repo = uow.scheduled_instructions
            if not repo:
                return TransactionResult(
                    outcome=TransactionOutcome.FAILED,
                    error=render_message("transfer.error.pipeline_failed", locale, {"error": "schedule_repo_missing"}),
                )

            schedules = await repo.get_active_by_user(user_id, limit=20)
            selection = resolve_schedule_selection(schedules, data=data, user_message=user_message)
            selected = selection.selected
            if selected is None:
                return disambiguation_result(selection.schedules, action_label="edit", locale=locale)

            domain = str(getattr(selected, "domain", None) or "transfer")
            if not edit_patch:
                edit_patch = build_schedule_edit_patch(data, domain=domain)
            if not edit_patch:
                return TransactionResult(
                    outcome=TransactionOutcome.NEEDS_INPUT,
                    required_fields=["schedule_edit_patch"],
                    prompt=render_message("schedule.edit.ask_change", locale),
                    patch={
                        "is_scheduled_operation": True,
                        "skip_finalize_summary": True,
                        "schedule_id": str(selected.id),
                    },
                )

            requires_auth = schedule_edit_requires_auth(domain, edit_patch)
            summary, snapshot, computed_next_run = build_schedule_update_summary(selected, edit_patch, locale=locale)
            if computed_next_run is None:
                return TransactionResult(
                    outcome=TransactionOutcome.NEEDS_INPUT,
                    required_fields=["schedule_start_date", "schedule_time_local"],
                    prompt=render_message("schedule.prompt.future_date_time", locale),
                    patch={"is_scheduled_operation": True, "skip_finalize_summary": True},
                )
            next_run_at = next_run_at or computed_next_run

            if requires_auth and not gates.pin_verified:
                if phone_number:
                    await persist_schedule_pin_token(
                        idempotency_key=data.idempotency_key,
                        phone_number=phone_number,
                        worker_context=worker_context,
                    )
                return TransactionResult(
                    outcome=TransactionOutcome.NEEDS_AUTH,
                    confirmation_summary=summary,
                    confirmation_snapshot=snapshot,
                    patch={
                        "is_scheduled_operation": True,
                        "skip_finalize_summary": True,
                        "schedule_id": str(selected.id),
                        "schedule_selector": str(selected.id),
                        "schedule_edit_patch": edit_patch,
                        "schedule_edit_requires_auth": True,
                        "schedule_edit_next_run_at_utc": next_run_at.isoformat(),
                    },
                )
            if not requires_auth and not gates.confirmation_confirmed:
                return TransactionResult(
                    outcome=TransactionOutcome.NEEDS_CONFIRMATION,
                    confirmation_summary=summary,
                    confirmation_snapshot=snapshot,
                    patch={
                        "is_scheduled_operation": True,
                        "skip_finalize_summary": True,
                        "schedule_id": str(selected.id),
                        "schedule_selector": str(selected.id),
                        "schedule_edit_patch": edit_patch,
                        "schedule_edit_requires_auth": False,
                        "schedule_edit_next_run_at_utc": next_run_at.isoformat(),
                    },
                )

            locked_selected = await repo.get_active_for_user_for_update(str(selected.id), user_id)
            if locked_selected is None:
                stale_message = render_message("schedule.edit.stale", locale)
                return TransactionResult(
                    outcome=TransactionOutcome.FAILED,
                    error=stale_message,
                    response=stale_message,
                    patch={"is_scheduled_operation": True, "skip_finalize_summary": True},
                )
            selected = locked_selected
            success_message = build_schedule_edit_success_message(selected, edit_patch, next_run_at, locale=locale)
            apply_schedule_edit(selected, edit_patch, next_run_at)
            await uow.commit()

        return TransactionResult(
            outcome=TransactionOutcome.OK,
            response=success_message,
            patch={
                "is_scheduled_operation": True,
                "skip_finalize_summary": True,
                "schedule_id": str(schedule_id or selected.id),
                "schedule_operation_note": success_message,
            },
        )

    async def create_schedule_after_auth(
        self,
        *,
        data: TransferPayload,
        ctx: TransferContext,
        locale: str,
        user_id: str,
    ) -> TransactionResult:
        schedule_fields = schedule_fields_from_payload(data)
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
                start_date=start_date or today_lagos().isoformat(),
                local_time=time_local,
                day_of_week=day_of_week,
                day_of_month=day_of_month,
                end_date=schedule_fields["schedule_end_date"],
                next_run_at_utc=next_run_at,
                channel=ctx.channel,
                channel_identity=ctx.channel_identity,
            )
            await uow.commit()

        amount = format_naira(data.amount)
        recipient = data.recipient_name or data.recipient_resolved_name or render_message(
            "schedule.fallback.recipient",
            locale,
        )
        next_run_text = format_lagos_schedule_datetime(next_run_at)
        response = render_message(
            "schedule.created.transfer",
            locale,
            {
                "amount": amount,
                "recipient": recipient,
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
        action: str,
        data: TransferPayload,
        ctx: TransferContext,
        worker_context: Any,
        user_message: str | None,
        gates: TransferGates,
    ) -> TransactionResult:
        locale = ctx.language
        user_id = str(getattr(worker_context, "user_id", None) or "").strip()
        if not user_id:
            return TransactionResult(
                outcome=TransactionOutcome.FAILED,
                error=render_message("transfer.error.pipeline_failed", locale, {"error": "missing_user_id"}),
            )

        if action in {"list_scheduled_transfers", "list_scheduled_transactions"}:
            return await self.list_schedules(data=data, user_id=user_id, locale=locale)

        if action == "find_scheduled_transaction":
            return await self.find_schedules(data=data, user_id=user_id, locale=locale, user_message=user_message)

        if action in CANCEL_SCHEDULE_ACTIONS:
            return await self.cancel_schedule(data=data, user_id=user_id, locale=locale, user_message=user_message)

        if action in EDIT_SCHEDULE_ACTIONS:
            return await self.edit_schedule(
                data=data,
                user_id=user_id,
                locale=locale,
                user_message=user_message,
                gates=gates,
                phone_number=ctx.phone_number,
                worker_context=worker_context,
            )

        pipeline = self._build_pipeline(user_message, include_execution=False, require_schedule_fields=True)
        result = await pipeline.run(data, ctx, gates, worker_context)
        if result.outcome != TransactionOutcome.OK:
            return result

        scheduled_data = data.model_copy(update=result.patch or {})
        return await self.create_schedule_after_auth(
            data=scheduled_data,
            ctx=ctx,
            locale=locale,
            user_id=user_id,
        )
