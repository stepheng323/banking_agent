"""Scheduled transfer requirements and management actions."""

import time
from datetime import UTC, date, datetime
from typing import Any, Literal, Protocol, cast

from banking.persistence.unit_of_work import UnitOfWork
from banking.presentation.formatters.currency import format_naira
from banking.presentation.i18n.message_keys import MessageKey
from banking.presentation.i18n.renderer import render_message
from banking.runtime.results import TransactionOutcome, TransactionResult
from banking.scheduling.repositories.scheduled_instruction_repository import ScheduledInstructionRepository
from banking.scheduling.services.recurrence import (
    SCHEDULE_TIMEZONE,
    compute_initial_next_run_utc,
    format_lagos_schedule_datetime,
    today_lagos,
)
from banking.transactions.shared.schedule_management import (
    apply_schedule_edit,
    build_schedule_context_items,
    build_schedule_edit_patch,
    build_schedule_edit_success_message,
    build_schedule_update_summary,
    cancelled_now,
    disambiguation_result,
    format_schedule_row,
    resolve_schedule_selection,
    schedule_edit_patch_supported,
    schedule_edit_requires_auth,
)
from banking.transactions.shared.scheduling import schedule_recurrence_label, schedule_required_prompt
from banking.transfers.authorization.pin_token import persist_schedule_pin_token
from banking.transfers.models.types import TransferContext, TransferGates, TransferPayload
from banking.transfers.pipeline.base import TransferPipeline, TransferStep
from shared.database.enums import ScheduledInstructionStatusEnum
from shared.types.conversation_sets import (
    BulkMutationRequest,
    BulkMutationReviewSnapshot,
    ScheduleQueryContract,
)
from shared.types.read import ReadRequest, ReadResult

SCHEDULING_ACTIONS = {
    "schedule_transfer",
    "recurring_transfer",
    "list_scheduled_transactions",
    "find_scheduled_transaction",
    "cancel_scheduled_transaction",
    "edit_scheduled_transaction",
    "pause_scheduled_transaction",
    "resume_scheduled_transaction",
    "list_scheduled_runs",
    "find_scheduled_run",
}
CANCEL_SCHEDULE_ACTIONS = {"cancel_scheduled_transaction"}
EDIT_SCHEDULE_ACTIONS = {"edit_scheduled_transaction"}
PAUSE_SCHEDULE_ACTIONS = {"pause_scheduled_transaction"}
RESUME_SCHEDULE_ACTIONS = {"resume_scheduled_transaction"}


def _schedule_version_token(schedule: Any) -> str | None:
    updated_at = getattr(schedule, "updated_at", None)
    if updated_at is None:
        return None
    isoformat = getattr(updated_at, "isoformat", None)
    return str(isoformat() if callable(isoformat) else updated_at)


def _schedule_end_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


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
        if data is None or data.read_request is None or data.schedule_contract is None:
            return TransactionResult(
                outcome=TransactionOutcome.FAILED,
                error=render_message("transfer.error.pipeline_failed", locale, {"error": "schedule_contract_missing"}),
            )
        request = data.read_request
        contract = data.schedule_contract

        def parsed_boundary(value: str | None) -> datetime | None:
            if not value:
                return None
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None
            if parsed.tzinfo is not None:
                return parsed.astimezone(UTC).replace(tzinfo=None)
            return parsed

        starts_at = parsed_boundary(contract.starts_at)
        ends_at = parsed_boundary(contract.ends_at)

        async with UnitOfWork() as uow:
            if not uow.scheduled_instructions:
                return TransactionResult(
                    outcome=TransactionOutcome.FAILED,
                    error=render_message("transfer.error.pipeline_failed", locale, {"error": "schedule_repo_missing"}),
                )
            repo = uow.scheduled_instructions
            schedules = await repo.get_filtered_by_user(
                user_id,
                statuses=contract.statuses or ["active"],
                domains=contract.domains,
                recurrence=contract.recurrence,
                recipient_name=contract.recipient_name,
                starts_at=starts_at,
                ends_at=ends_at,
                selected_ids=data.selected_entity_ids,
                limit=request.page_size + 1,
                offset=request.offset,
            )
            total_count = await repo.count_filtered_by_user(
                user_id,
                statuses=contract.statuses or ["active"],
                domains=contract.domains,
                recurrence=contract.recurrence,
                recipient_name=contract.recipient_name,
                starts_at=starts_at,
                ends_at=ends_at,
                selected_ids=data.selected_entity_ids,
            )

        has_next = len(schedules) > request.page_size or request.offset + request.page_size < total_count
        page = schedules[: request.page_size]
        read_result = ReadResult(
            request=request,
            total_count=total_count,
            returned_count=0 if request.response_shape.startswith("fact_") else len(page),
            has_next=has_next,
            has_previous=request.offset > 0,
        )

        if request.response_shape == "fact_status":
            status = (
                str(getattr(page[0], "status", "") or "unknown")
                if total_count == 1 and page
                else (contract.statuses[0] if len(contract.statuses) == 1 else "mixed")
            )
            return TransactionResult(
                outcome=TransactionOutcome.OK,
                response=render_message(
                    "schedule.list.status_fact",
                    locale,
                    {"count": total_count, "status": status},
                ),
                patch={
                    "is_scheduled_operation": True,
                    "skip_finalize_summary": True,
                    "schedule_contract": contract.model_dump(mode="json", exclude_none=True),
                },
                read_result=read_result,
            )

        if request.response_shape in {"fact_count", "fact_bool"}:
            count = total_count
            if request.response_shape == "fact_bool":
                response = render_message(
                    "schedule.list.exists_yes" if count else "schedule.list.exists_no",
                    locale,
                )
            else:
                response = (
                    render_message("schedule.list.empty", locale)
                    if count == 0
                    else render_message("schedule.list.count", locale, {"count": count})
                )
            return TransactionResult(
                outcome=TransactionOutcome.OK,
                response=response,
                patch={
                    "is_scheduled_operation": True,
                    "skip_finalize_summary": True,
                    "schedule_contract": contract.model_dump(mode="json", exclude_none=True),
                },
                read_result=read_result,
            )

        if not page:
            return TransactionResult(
                outcome=TransactionOutcome.OK,
                response=render_message("schedule.list.empty", locale),
                patch={
                    "is_scheduled_operation": True,
                    "skip_finalize_summary": True,
                    "schedule_contract": contract.model_dump(mode="json", exclude_none=True),
                },
                read_result=read_result,
            )

        lines = [render_message("schedule.list.header", locale)]
        lines.extend(format_schedule_row(idx, schedule, locale=locale) for idx, schedule in enumerate(page, start=1))
        if has_next:
            lines.extend(["", render_message("common.pagination.more", locale)])

        return TransactionResult(
            outcome=TransactionOutcome.OK,
            response="\n".join(lines),
            patch={
                "is_scheduled_operation": True,
                "skip_finalize_summary": True,
                "schedule_context_items": build_schedule_context_items(page, locale=locale),
                "schedule_contract": contract.model_dump(mode="json", exclude_none=True),
            },
            read_result=read_result,
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

    async def list_runs(
        self,
        *,
        data: TransferPayload,
        user_id: str,
        locale: str,
        find_one: bool = False,
    ) -> TransactionResult:
        """Return user-scoped, read-only schedule execution history."""
        request = data.read_request or ReadRequest(subject="schedule", response_shape="surface_list")
        contract = data.schedule_contract or ScheduleQueryContract(
            surface="runs",
            operation="detail" if find_one else "list",
            response_shape=request.response_shape,
        )

        def parsed_boundary(value: str | None) -> datetime | None:
            if not value:
                return None
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None
            return parsed.astimezone(UTC).replace(tzinfo=None) if parsed.tzinfo else parsed

        async with UnitOfWork() as uow:
            repo = uow.scheduled_runs
            if not repo:
                return TransactionResult(
                    outcome=TransactionOutcome.FAILED,
                    error=render_message(
                        "transfer.error.pipeline_failed", locale, {"error": "schedule_run_repo_missing"}
                    ),
                )
            if find_one and data.schedule_id:
                run = await repo.get_for_user(data.schedule_id, user_id)
                runs = [run] if run is not None else []
                total_count = len(runs)
            else:
                runs = await repo.get_filtered_by_user(
                    user_id,
                    schedule_ids=data.selected_entity_ids,
                    domains=contract.domains,
                    statuses=contract.run_statuses,
                    starts_at=parsed_boundary(contract.starts_at),
                    ends_at=parsed_boundary(contract.ends_at),
                    limit=request.page_size + 1,
                    offset=request.offset,
                )
                total_count = await repo.count_filtered_by_user(
                    user_id,
                    schedule_ids=data.selected_entity_ids,
                    domains=contract.domains,
                    statuses=contract.run_statuses,
                    starts_at=parsed_boundary(contract.starts_at),
                    ends_at=parsed_boundary(contract.ends_at),
                )

        page = runs[: request.page_size]
        read_result = ReadResult(
            request=request,
            total_count=total_count,
            returned_count=0 if request.response_shape.startswith("fact_") else len(page),
            has_next=len(runs) > request.page_size or request.offset + request.page_size < total_count,
            has_previous=request.offset > 0,
        )
        if request.response_shape == "fact_count":
            response = render_message("schedule.run.count", locale, {"count": total_count})
        elif request.response_shape == "fact_bool":
            response = render_message(
                "schedule.run.exists_yes" if total_count else "schedule.run.exists_no",
                locale,
            )
        elif not page:
            response = render_message("schedule.run.empty", locale)
        else:
            lines = [render_message("schedule.run.header", locale)]
            context_items: list[dict[str, str]] = []
            for index, run in enumerate(page, start=1):
                due = format_lagos_schedule_datetime(run.due_at_utc)
                lines.append(
                    render_message(
                        "schedule.run.row",
                        locale,
                        {"index": index, "status": str(run.status), "due": due},
                    )
                )
                token = _schedule_version_token(run)
                if token:
                    context_items.append(
                        {
                            "id": str(run.id),
                            "version_token": token,
                            "display_label": f"{run.status} · {due}",
                        }
                    )
            if read_result.has_next:
                lines.extend(["", render_message("common.pagination.more", locale)])
            response = "\n".join(lines)
            return TransactionResult(
                outcome=TransactionOutcome.OK,
                response=response,
                patch={
                    "is_scheduled_operation": True,
                    "skip_finalize_summary": True,
                    "schedule_run_context_items": context_items,
                    "schedule_contract": contract.model_dump(mode="json", exclude_none=True),
                },
                read_result=read_result,
            )
        return TransactionResult(
            outcome=TransactionOutcome.OK,
            response=response,
            patch={
                "is_scheduled_operation": True,
                "skip_finalize_summary": True,
                "schedule_contract": contract.model_dump(mode="json", exclude_none=True),
            },
            read_result=read_result,
        )

    async def transition_schedule_state(
        self,
        *,
        data: TransferPayload,
        user_id: str,
        locale: str,
        user_message: str | None,
        target_status: str,
        gates: TransferGates,
        phone_number: str | None,
        worker_context: Any,
    ) -> TransactionResult:
        """Pause or resume one reviewed, versioned schedule set atomically."""
        is_resume = target_status == ScheduledInstructionStatusEnum.ACTIVE.value
        current_status = (
            ScheduledInstructionStatusEnum.PAUSED.value if is_resume else ScheduledInstructionStatusEnum.ACTIVE.value
        )
        action: Literal["resume", "pause"] = "resume" if is_resume else "pause"
        async with UnitOfWork() as uow:
            repo = uow.scheduled_instructions
            schedules = await repo.get_by_statuses_for_user(user_id, [current_status], limit=20)
            request = data.bulk_mutation
            if request is None or request.domain != "schedule" or request.action != action:
                selection = resolve_schedule_selection(schedules, data=data, user_message=user_message)
                selected = selection.selected
                if selected is None:
                    return disambiguation_result(selection.schedules, action_label=action, locale=locale)
                version_token = _schedule_version_token(selected)
                if not version_token:
                    return TransactionResult(
                        outcome=TransactionOutcome.FAILED,
                        error=render_message("conversation_set.stale_selection", locale),
                    )
                from shared.types.conversation_sets import EntitySelectionRef

                request = BulkMutationRequest(
                    domain="schedule",
                    action=action,
                    targets=[
                        EntitySelectionRef(
                            entity_type="schedule",
                            entity_id=str(selected.id),
                            frame_id="direct_schedule_selection",
                            display_label=format_schedule_row(1, selected, locale=locale),
                            version_token=version_token,
                        )
                    ],
                    idempotency_key=str(data.idempotency_key or f"{action}:{selected.id}"),
                )

            assert request is not None

            locked = await repo.get_ids_for_user_for_update(
                [ref.entity_id for ref in request.targets],
                user_id,
                statuses=[current_status],
            )
            by_id = {str(schedule.id): schedule for schedule in locked}
            if any(
                ref.entity_id not in by_id or _schedule_version_token(by_id[ref.entity_id]) != ref.version_token
                for ref in request.targets
            ):
                return TransactionResult(
                    outcome=TransactionOutcome.FAILED,
                    error=render_message("conversation_set.stale_selection", locale),
                )

            snapshot = BulkMutationReviewSnapshot(request=request, created_at_ts=time.time())
            summary_key: MessageKey = "schedule.resume.review" if is_resume else "schedule.pause.review"
            summary = render_message(
                summary_key,
                locale,
                {"count": len(request.targets), "items": "\n".join(ref.display_label for ref in request.targets)},
            )
            patch = {
                "is_scheduled_operation": True,
                "skip_finalize_summary": True,
                "bulk_mutation": request.model_dump(mode="json", exclude_none=True),
            }
            if not data.confirmation.confirmed:
                return TransactionResult(
                    outcome=TransactionOutcome.NEEDS_CONFIRMATION,
                    confirmation_summary=summary,
                    confirmation_snapshot=snapshot.model_dump(mode="json", exclude_none=True),
                    patch=patch,
                )
            if is_resume and not gates.pin_verified:
                if phone_number:
                    await persist_schedule_pin_token(
                        idempotency_key=data.idempotency_key,
                        phone_number=phone_number,
                        worker_context=worker_context,
                    )
                return TransactionResult(
                    outcome=TransactionOutcome.NEEDS_AUTH,
                    confirmation_summary=summary,
                    confirmation_snapshot=snapshot.model_dump(mode="json", exclude_none=True),
                    patch=patch,
                )

            now = datetime.now(UTC).replace(tzinfo=None)
            for schedule in locked:
                end_date = _schedule_end_date(schedule.end_date)
                if is_resume and schedule.end_date is not None and (end_date is None or end_date < now.date()):
                    return TransactionResult(
                        outcome=TransactionOutcome.FAILED,
                        error=render_message("schedule.resume.expired", locale),
                    )
                if is_resume and schedule.next_run_at_utc < now:
                    next_run = compute_initial_next_run_utc(
                        recurrence_type=schedule.recurrence_type,
                        start_date=schedule.start_date,
                        local_time=schedule.local_time,
                        day_of_week=schedule.day_of_week,
                        day_of_month=schedule.day_of_month,
                        timezone=schedule.timezone,
                        now_utc=now.replace(tzinfo=UTC),
                    )
                    if next_run is None or (end_date is not None and next_run.date() > end_date):
                        return TransactionResult(
                            outcome=TransactionOutcome.FAILED,
                            error=render_message("schedule.resume.expired", locale),
                        )
                    schedule.next_run_at_utc = next_run
                schedule.status = target_status
            await uow.commit()

        assert request is not None
        success_key: MessageKey = "schedule.resume.success" if is_resume else "schedule.pause.success"
        return TransactionResult(
            outcome=TransactionOutcome.OK,
            response=render_message(success_key, locale, {"count": len(request.targets)}),
            patch={
                "is_scheduled_operation": True,
                "skip_finalize_summary": True,
                "bulk_mutation": None,
                "invalidate_conversation_set_domain": "schedule",
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
            raw_request = data.bulk_mutation
            request = raw_request if raw_request and raw_request.domain == "schedule" else None
            if request is None:
                selected = selection.selected
                if selected is None:
                    return disambiguation_result(selection.schedules, action_label="cancel", locale=locale)
                version_token = _schedule_version_token(selected)
                if not version_token:
                    return TransactionResult(
                        outcome=TransactionOutcome.FAILED,
                        error=render_message("conversation_set.stale_selection", locale),
                    )
                from shared.types.conversation_sets import EntitySelectionRef

                request = BulkMutationRequest(
                    domain="schedule",
                    action="cancel",
                    targets=[
                        EntitySelectionRef(
                            entity_type="schedule",
                            entity_id=str(selected.id),
                            frame_id="direct_schedule_selection",
                            display_label=format_schedule_row(1, selected, locale=locale),
                            version_token=version_token,
                        )
                    ],
                    idempotency_key=str(data.idempotency_key or f"cancel:{selected.id}"),
                )

            assert request is not None
            target_ids = [ref.entity_id for ref in request.targets]
            locked = await repo.get_active_ids_for_user_for_update(target_ids, user_id)
            by_id = {str(schedule.id): schedule for schedule in locked}
            stale = [
                ref
                for ref in request.targets
                if ref.entity_id not in by_id or _schedule_version_token(by_id[ref.entity_id]) != ref.version_token
            ]
            if stale:
                return TransactionResult(
                    outcome=TransactionOutcome.FAILED,
                    error=render_message("conversation_set.stale_selection", locale),
                )

            if not data.confirmation.confirmed:
                snapshot = BulkMutationReviewSnapshot(
                    request=request,
                    created_at_ts=time.time(),
                )
                summary = render_message(
                    "schedule.cancel.review",
                    locale,
                    {"count": len(request.targets), "items": "\n".join(ref.display_label for ref in request.targets)},
                )
                return TransactionResult(
                    outcome=TransactionOutcome.NEEDS_CONFIRMATION,
                    confirmation_summary=summary,
                    confirmation_snapshot=snapshot.model_dump(mode="json", exclude_none=True),
                    patch={
                        "is_scheduled_operation": True,
                        "skip_finalize_summary": True,
                        "bulk_mutation": request.model_dump(mode="json", exclude_none=True),
                    },
                )

            for selected in locked:
                selected.status = ScheduledInstructionStatusEnum.CANCELLED.value
                selected.cancelled_at = cancelled_now()
            await uow.commit()
            remaining = await repo.get_active_by_user(user_id, limit=6)

        assert request is not None
        refreshed_request = ReadRequest(subject="schedule", response_shape="surface_list")
        refreshed_contract = ScheduleQueryContract(
            operation="list",
            response_shape="surface_list",
            statuses=["active"],
        )
        return TransactionResult(
            outcome=TransactionOutcome.OK,
            response=render_message("schedule.cancel.bulk_success", locale, {"count": len(request.targets)}),
            patch={
                "is_scheduled_operation": True,
                "skip_finalize_summary": True,
                "bulk_mutation": None,
                "invalidate_conversation_set_domain": "schedule",
                "schedule_context_items": build_schedule_context_items(remaining[:5], locale=locale),
                "schedule_contract": refreshed_contract.model_dump(mode="json", exclude_none=True),
            },
            read_result=ReadResult(
                request=refreshed_request,
                total_count=len(remaining),
                returned_count=min(5, len(remaining)),
                has_next=len(remaining) > 5,
            ),
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

            if (
                data.bulk_mutation is not None
                and data.bulk_mutation.domain == "schedule"
                and data.bulk_mutation.action == "edit"
            ):
                return await self._edit_schedule_set(
                    repo=repo,
                    uow=uow,
                    data=data,
                    user_id=user_id,
                    locale=locale,
                    gates=gates,
                    phone_number=phone_number,
                    worker_context=worker_context,
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

        if selected is None:
            raise RuntimeError("schedule_selection_unavailable")
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

    async def _edit_schedule_set(
        self,
        *,
        repo: Any,
        uow: Any,
        data: TransferPayload,
        user_id: str,
        locale: str,
        gates: TransferGates,
        phone_number: str | None,
        worker_context: Any | None,
    ) -> TransactionResult:
        request = data.bulk_mutation
        if request is None:
            raise RuntimeError("bulk_schedule_request_missing")
        target_ids = [ref.entity_id for ref in request.targets]
        selected = await repo.get_active_ids_for_user_for_update(target_ids, user_id)
        by_id = {str(schedule.id): schedule for schedule in selected}
        stale = [
            ref
            for ref in request.targets
            if ref.entity_id not in by_id or _schedule_version_token(by_id[ref.entity_id]) != ref.version_token
        ]
        if stale:
            return TransactionResult(
                outcome=TransactionOutcome.FAILED,
                error=render_message("conversation_set.stale_selection", locale),
            )

        edit_patch = dict(request.patch or data.schedule_edit_patch or {})
        if not edit_patch:
            domain_patches = [
                build_schedule_edit_patch(data, domain=str(getattr(schedule, "domain", "transfer")))
                for schedule in selected
            ]
            edit_patch = domain_patches[0] if domain_patches else {}
            if any(patch != edit_patch for patch in domain_patches[1:]):
                return TransactionResult(
                    outcome=TransactionOutcome.FAILED,
                    error=render_message("schedule.edit.incompatible_set", locale),
                )
        if not edit_patch:
            return TransactionResult(
                outcome=TransactionOutcome.NEEDS_INPUT,
                required_fields=["schedule_edit_patch"],
                prompt=render_message("schedule.edit.ask_change", locale),
                patch={"bulk_mutation": request.model_dump(mode="json", exclude_none=True)},
            )

        summaries: list[str] = []
        next_runs: dict[str, str] = dict(data.bulk_schedule_next_runs)
        requires_auth = False
        for ref in request.targets:
            schedule = by_id[ref.entity_id]
            domain = str(getattr(schedule, "domain", "transfer"))
            if not schedule_edit_patch_supported(domain, edit_patch):
                return TransactionResult(
                    outcome=TransactionOutcome.FAILED,
                    error=render_message("schedule.edit.incompatible_set", locale),
                )
            summary, _, computed_next_run = build_schedule_update_summary(schedule, edit_patch, locale=locale)
            if computed_next_run is None:
                return TransactionResult(
                    outcome=TransactionOutcome.NEEDS_INPUT,
                    required_fields=["schedule_start_date", "schedule_time_local"],
                    prompt=render_message("schedule.prompt.future_date_time", locale),
                )
            summaries.append(summary)
            next_runs[ref.entity_id] = computed_next_run.isoformat()
            requires_auth = requires_auth or schedule_edit_requires_auth(domain, edit_patch)

        reviewed_request = request.model_copy(update={"patch": edit_patch})
        review_summary = render_message(
            "schedule.edit.bulk_review",
            locale,
            {"count": len(selected), "items": "\n\n".join(summaries)},
        )
        snapshot = BulkMutationReviewSnapshot(
            request=reviewed_request,
            created_at_ts=time.time(),
        )
        confirmation_patch = {
            "bulk_mutation": reviewed_request.model_dump(mode="json", exclude_none=True),
            "bulk_schedule_next_runs": next_runs,
            "schedule_edit_patch": edit_patch,
            "schedule_edit_requires_auth": requires_auth,
            "is_scheduled_operation": True,
            "skip_finalize_summary": True,
        }
        if not data.confirmation.confirmed:
            return TransactionResult(
                outcome=TransactionOutcome.NEEDS_CONFIRMATION,
                confirmation_summary=review_summary,
                confirmation_snapshot=snapshot.model_dump(mode="json", exclude_none=True),
                patch=confirmation_patch,
            )
        if requires_auth and not gates.pin_verified:
            if phone_number:
                await persist_schedule_pin_token(
                    idempotency_key=data.idempotency_key,
                    phone_number=phone_number,
                    worker_context=worker_context,
                )
            return TransactionResult(
                outcome=TransactionOutcome.NEEDS_AUTH,
                confirmation_summary=review_summary,
                confirmation_snapshot=snapshot.model_dump(mode="json", exclude_none=True),
                patch=confirmation_patch,
            )

        for ref in request.targets:
            schedule = by_id[ref.entity_id]
            next_run = datetime.fromisoformat(next_runs[ref.entity_id])
            apply_schedule_edit(schedule, edit_patch, next_run)
        await uow.commit()
        refreshed = await repo.get_active_by_user(user_id, limit=6)
        refreshed_request = ReadRequest(subject="schedule", response_shape="surface_list")
        refreshed_contract = ScheduleQueryContract(
            operation="list",
            response_shape="surface_list",
            statuses=["active"],
        )
        return TransactionResult(
            outcome=TransactionOutcome.OK,
            response=render_message("schedule.edit.bulk_success", locale, {"count": len(selected)}),
            patch={
                "bulk_mutation": None,
                "invalidate_conversation_set_domain": "schedule",
                "is_scheduled_operation": True,
                "skip_finalize_summary": True,
                "schedule_context_items": build_schedule_context_items(refreshed[:5], locale=locale),
                "schedule_contract": refreshed_contract.model_dump(mode="json", exclude_none=True),
            },
            read_result=ReadResult(
                request=refreshed_request,
                total_count=len(refreshed),
                returned_count=min(5, len(refreshed)),
                has_next=len(refreshed) > 5,
            ),
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
        recipient = (
            data.recipient_name
            or data.recipient_resolved_name
            or render_message(
                "schedule.fallback.recipient",
                locale,
            )
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

        if action == "list_scheduled_transactions":
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

        if action in PAUSE_SCHEDULE_ACTIONS:
            return await self.transition_schedule_state(
                data=data,
                user_id=user_id,
                locale=locale,
                user_message=user_message,
                target_status=ScheduledInstructionStatusEnum.PAUSED.value,
                gates=gates,
                phone_number=ctx.phone_number,
                worker_context=worker_context,
            )

        if action in RESUME_SCHEDULE_ACTIONS:
            return await self.transition_schedule_state(
                data=data,
                user_id=user_id,
                locale=locale,
                user_message=user_message,
                target_status=ScheduledInstructionStatusEnum.ACTIVE.value,
                gates=gates,
                phone_number=ctx.phone_number,
                worker_context=worker_context,
            )

        if action in {"list_scheduled_runs", "find_scheduled_run"}:
            return await self.list_runs(
                data=data,
                user_id=user_id,
                locale=locale,
                find_one=action == "find_scheduled_run",
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
