"""Transfer Worker (V3).

Stateless domain worker for Transfer tasks.
Executes a single pass through the transfer logic pipeline:
Extract -> Resolve -> Validate -> Confirmation -> Authorization -> Execution.

Returns a standardized TransactionResult.
"""

import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from apps.chat.src.agent.graphs.__shared__.schedule_management import (
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
from apps.chat.src.agent.graphs.transfer.models.types import (
    TransferContext,
    TransferGates,
    TransferPayload,
)
from apps.chat.src.agent.graphs.transfer.nodes.confirmation import ConfirmationStep
from apps.chat.src.agent.graphs.transfer.nodes.execution import ExecutionStep
from apps.chat.src.agent.graphs.transfer.nodes.extraction import ExtractionStep
from apps.chat.src.agent.graphs.transfer.nodes.funding import FundingStep
from apps.chat.src.agent.graphs.transfer.nodes.payout_preparation import PayoutPreparationStep
from apps.chat.src.agent.graphs.transfer.nodes.resolver import ResolutionStep
from apps.chat.src.agent.graphs.transfer.nodes.security import AuthorizationStep
from apps.chat.src.agent.graphs.transfer.nodes.selection import SourceSelectionStep
from apps.chat.src.agent.graphs.transfer.nodes.validation import ValidationStep
from apps.chat.src.agent.graphs.transfer.pipeline.base import TransferPipeline, TransferStep
from apps.chat.src.agent.graphs.transfer.services.validation import ValidationService
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
from shared.repositories.transaction_repository import (
    TransactionRepository,
)
from shared.repositories.unit_of_work import UnitOfWork
from shared.services.scheduling.recurrence import (
    SCHEDULE_TIMEZONE,
    compute_initial_next_run_utc,
    format_lagos_schedule_datetime,
    today_lagos,
)
from shared.utils.logging import get_logger

logger = get_logger(__name__)
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
LIST_SCHEDULE_ACTIONS = {"list_scheduled_transfers", "list_scheduled_transactions", "find_scheduled_transaction"}
CANCEL_SCHEDULE_ACTIONS = {"cancel_scheduled_transfer", "cancel_scheduled_transaction"}
EDIT_SCHEDULE_ACTIONS = {"edit_scheduled_transaction"}


def _missing_schedule_fields(data: TransferPayload) -> list[str]:
    missing: list[str] = []
    recurrence_type = data.recurrence_type or "one_time"
    if recurrence_type == "one_time" and not data.schedule_start_date:
        missing.append("schedule_start_date")
    if not data.schedule_time_local:
        missing.append("schedule_time_local")
    return missing


def _schedule_required_prompt(missing_fields: list[str]) -> str:
    fields = set(missing_fields)
    if fields == {"schedule_time_local"}:
        return "What time should I send it?"
    if fields == {"schedule_start_date"}:
        return "What date should I send it?"
    return "Please provide a future schedule date and time."


class ScheduleRequirementsStep(TransferStep):
    """Requires explicit schedule fields before confirmation."""

    async def execute(
        self,
        data: TransferPayload,
        context: TransferContext,
        gates: TransferGates,
        worker_context: Any = None,
    ) -> TransactionResult:
        del context, gates, worker_context
        missing_fields = _missing_schedule_fields(data)
        if not missing_fields:
            return TransactionResult(outcome=TransactionOutcome.OK, patch={})
        return TransactionResult(
            outcome=TransactionOutcome.NEEDS_INPUT,
            required_fields=missing_fields,
            prompt=_schedule_required_prompt(missing_fields),
            patch={"is_scheduled_operation": True, "skip_finalize_summary": True},
        )


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
            previous_beneficiary=context.get("previous_beneficiary"),
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

    @staticmethod
    def _build_pipeline(
        user_message: str | None,
        *,
        include_execution: bool = True,
        require_schedule_fields: bool = False,
    ) -> TransferPipeline:
        steps: list[Any] = [
            ExtractionStep(user_message),
            ResolutionStep(),
            SourceSelectionStep(),
            ValidationStep(),
            FundingStep(),
            PayoutPreparationStep(),
        ]
        if require_schedule_fields:
            steps.append(ScheduleRequirementsStep())
        steps.extend([ConfirmationStep(), AuthorizationStep()])
        if include_execution:
            steps.append(ExecutionStep())
        return TransferPipeline(steps)

    @staticmethod
    def _schedule_fields_from_payload(data: TransferPayload) -> dict[str, Any]:
        return {
            "schedule_mode": data.schedule_mode or ("recurring" if data.recurrence_type and data.recurrence_type != "one_time" else "one_time"),
            "recurrence_type": data.recurrence_type or "one_time",
            "schedule_timezone": data.schedule_timezone or SCHEDULE_TIMEZONE,
            "schedule_start_date": data.schedule_start_date,
            "schedule_time_local": data.schedule_time_local,
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

    async def _list_schedules(
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
            noun = "transaction" if count == 1 else "transactions"
            return TransactionResult(
                outcome=TransactionOutcome.OK,
                response=f"You have {count} pending scheduled {noun}.",
                patch={
                    "is_scheduled_operation": True,
                    "skip_finalize_summary": True,
                    "schedule_context_items": build_schedule_context_items(schedules),
                },
            )

        if not schedules:
            return TransactionResult(
                outcome=TransactionOutcome.OK,
                response="You have no active scheduled transactions.",
                patch={"is_scheduled_operation": True, "skip_finalize_summary": True},
            )

        lines = ["Scheduled transactions:"]
        lines.extend(format_schedule_row(idx, schedule) for idx, schedule in enumerate(schedules, start=1))

        return TransactionResult(
            outcome=TransactionOutcome.OK,
            response="\n".join(lines),
            patch={
                "is_scheduled_operation": True,
                "skip_finalize_summary": True,
                "schedule_context_items": build_schedule_context_items(schedules),
            },
        )

    async def _find_schedules(
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
                response="I couldn't find an active scheduled transaction matching that.",
                patch={"is_scheduled_operation": True, "skip_finalize_summary": True},
            )

        lines = ["Matching scheduled transactions:"]
        lines.extend(format_schedule_row(idx, schedule) for idx, schedule in enumerate(matches, start=1))
        return TransactionResult(
            outcome=TransactionOutcome.OK,
            response="\n".join(lines),
            patch={
                "is_scheduled_operation": True,
                "skip_finalize_summary": True,
                "schedule_context_items": build_schedule_context_items(matches),
            },
        )

    async def _cancel_schedule(
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
                    response="You have no active scheduled transactions to cancel.",
                    patch={"is_scheduled_operation": True, "skip_finalize_summary": True},
                )

            selection = resolve_schedule_selection(schedules, data=data, user_message=user_message)
            selected = selection.selected

            if selected is None:
                return disambiguation_result(selection.schedules, action_label="cancel")

            selected.status = ScheduledInstructionStatusEnum.CANCELLED.value
            selected.cancelled_at = cancelled_now()
            await uow.commit()

        return TransactionResult(
            outcome=TransactionOutcome.OK,
            response="Scheduled transaction cancelled.",
            patch={"is_scheduled_operation": True, "skip_finalize_summary": True},
        )

    async def _edit_schedule(
        self,
        *,
        data: TransferPayload,
        user_id: str,
        locale: str,
        user_message: str | None,
        gates: TransferGates,
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
                return disambiguation_result(selection.schedules, action_label="edit")

            domain = str(getattr(selected, "domain", None) or "transfer")
            if not edit_patch:
                edit_patch = build_schedule_edit_patch(data, domain=domain)
            if not edit_patch:
                return TransactionResult(
                    outcome=TransactionOutcome.NEEDS_INPUT,
                    required_fields=["schedule_edit_patch"],
                    prompt="What should I change on this scheduled transaction?",
                    patch={
                        "is_scheduled_operation": True,
                        "skip_finalize_summary": True,
                        "schedule_id": str(selected.id),
                    },
                )

            requires_auth = schedule_edit_requires_auth(domain, edit_patch)
            summary, snapshot, computed_next_run = build_schedule_update_summary(selected, edit_patch)
            if computed_next_run is None:
                return TransactionResult(
                    outcome=TransactionOutcome.NEEDS_INPUT,
                    required_fields=["schedule_start_date", "schedule_time_local"],
                    prompt="Please provide a future schedule date and time.",
                    patch={"is_scheduled_operation": True, "skip_finalize_summary": True},
                )
            next_run_at = next_run_at or computed_next_run

            if requires_auth and not gates.pin_verified:
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
                return TransactionResult(
                    outcome=TransactionOutcome.FAILED,
                    error="This schedule is already being processed or is no longer active. Please try again.",
                    response="This schedule is already being processed or is no longer active. Please try again.",
                    patch={"is_scheduled_operation": True, "skip_finalize_summary": True},
                )
            selected = locked_selected
            success_message = build_schedule_edit_success_message(selected, edit_patch, next_run_at)
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
        missing_fields = _missing_schedule_fields(data)
        if missing_fields:
            return TransactionResult(
                outcome=TransactionOutcome.NEEDS_INPUT,
                required_fields=missing_fields,
                prompt=_schedule_required_prompt(missing_fields),
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
        recipient = data.recipient_name or data.recipient_resolved_name or "recipient"
        next_run_text = format_lagos_schedule_datetime(next_run_at)
        response = (
            f"Scheduled transfer created: {amount} to {recipient} "
            f"({recurrence_type.replace('_', ' ')}) in {timezone}. Next run: {next_run_text}."
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

        if action in {"list_scheduled_transfers", "list_scheduled_transactions"}:
            return await self._list_schedules(data=data, user_id=user_id, locale=locale)

        if action == "find_scheduled_transaction":
            return await self._find_schedules(data=data, user_id=user_id, locale=locale, user_message=user_message)

        if action in CANCEL_SCHEDULE_ACTIONS:
            return await self._cancel_schedule(data=data, user_id=user_id, locale=locale, user_message=user_message)

        if action in EDIT_SCHEDULE_ACTIONS:
            return await self._edit_schedule(
                data=data,
                user_id=user_id,
                locale=locale,
                user_message=user_message,
                gates=gates,
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
        if action in SCHEDULING_ACTIONS and not settings.enable_transfer_scheduling:
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
            if action in SCHEDULING_ACTIONS:
                result = await self._handle_scheduling_action(
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
