from typing import Any, cast

from apps.chat.src.agent.orchestrator.context.referents.resolution import build_resolved_referents
from apps.chat.src.agent.orchestrator.models.domain import TaskSpec
from apps.chat.src.agent.orchestrator.workflows.execution.async_grouping import _stamp_async_group_metadata
from apps.chat.src.agent.orchestrator.workflows.execution.beneficiary_resolution import (
    _beneficiary_cache_contains_recipient,
    _normalize_beneficiary_rows,
    _recipient_supports_targeted_beneficiary_lookup,
)
from apps.chat.src.agent.orchestrator.workflows.execution.context import ExecutionTurnContext
from apps.chat.src.agent.orchestrator.workflows.execution.context_frames import push_schedule_list_frame
from apps.chat.src.agent.orchestrator.workflows.execution.context_surface import context_surface
from apps.chat.src.agent.orchestrator.workflows.execution.last_interrupt import last_interrupt
from apps.chat.src.agent.orchestrator.workflows.execution.loaded_context import (
    loaded_context,
    set_loaded_context_value,
)
from apps.chat.src.agent.orchestrator.workflows.execution.locale import _state_locale
from apps.chat.src.agent.orchestrator.workflows.execution.result_reducer import (
    _apply_result_patch,
    _handle_transaction_outcome,
)
from apps.chat.src.agent.orchestrator.workflows.execution.session_stack import (
    SessionState,
    pop_active_session,
    upsert_active_session,
)
from apps.chat.src.agent.orchestrator.workflows.execution.task_input import _maybe_user_message
from apps.chat.src.agent.orchestrator.workflows.execution.task_mutations import remove_task_payload_values
from apps.chat.src.agent.orchestrator.workflows.execution.turn_metadata import turn_metadata
from apps.chat.src.agent.orchestrator.workflows.execution.worker_lookup import _get_worker
from banking.presentation.i18n.renderer import render_message
from banking.runtime.results import TransactionOutcome, TransactionResult
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class TransferTaskExecutor:
    async def execute(self, task: TaskSpec, task_id: str, ctx: ExecutionTurnContext) -> None:
        await _execute_transfer_task(task, task_id, ctx)


class ScheduleTaskExecutor:
    async def execute(self, task: TaskSpec, task_id: str, ctx: ExecutionTurnContext) -> None:
        await _execute_schedule_task(task, task_id, ctx)


async def _execute_transfer_task(task: TaskSpec, task_id: str, ctx: ExecutionTurnContext) -> None:
    worker = _get_worker(
        ctx.services,
        "transfer",
        task,
        log_key="transfer_worker_missing",
        error_message=render_message("orchestrator.error.transfer_worker_unavailable", _state_locale(ctx.state)),
    )
    if not worker:
        return

    required_fields: list[str] = []
    previous_response: str | None = None
    confirmation_task_count: int | None = None
    interrupt = last_interrupt(ctx.state)
    if interrupt.includes_task(task_id):
        required_fields = interrupt.fields_for_task(task_id)
        previous_response = interrupt.prompt
        if interrupt.is_kind("confirmation"):
            confirmation_task_count = interrupt.task_count

    user_msg = _maybe_user_message(task, ctx.state)

    awaiting_raw_slot_input = bool(required_fields)
    needs_account_selection = not task.payload.get("source_account_id")
    if (
        (not user_msg or not user_msg.strip())
        and task.payload.get("recipient_name")
        and not needs_account_selection
        and not awaiting_raw_slot_input
    ):
        r_name = task.payload["recipient_name"]
        if isinstance(r_name, str) and r_name.lower() not in ("him", "her", "them", "that", "it", "this", "previous"):
            amt = task.payload.get("amount") or ""
            user_msg = f"Send {amt} to {r_name}"
            logger.info("user_msg_synthesized", msg=user_msg)

    context = loaded_context(ctx.state)
    surface = context_surface(ctx.state)
    turn = turn_metadata(ctx.state)
    beneficiaries = context.beneficiaries

    recipient_name = task.payload.get("recipient_name")
    has_recipient_hint = isinstance(recipient_name, str) and bool(recipient_name.strip())
    recipient_name_text = recipient_name.strip() if isinstance(recipient_name, str) else ""
    beneficiary_repo = ctx.dependencies.beneficiary_repo
    user_id = context.user_id
    beneficiary_context_mode = context.beneficiary_context_mode
    if (
        beneficiary_repo
        and user_id
        and (
            not beneficiaries
            or (
                has_recipient_hint
                and beneficiary_context_mode == "cache_only"
                and _recipient_supports_targeted_beneficiary_lookup(recipient_name_text)
                and not _beneficiary_cache_contains_recipient(beneficiaries, recipient_name_text)
            )
        )
    ):
        try:
            fetched_rows: list[Any] = []
            reload_mode = "full"
            if (
                has_recipient_hint
                and beneficiary_context_mode == "cache_only"
                and _recipient_supports_targeted_beneficiary_lookup(recipient_name_text)
                and hasattr(beneficiary_repo, "search_by_name")
            ):
                fetched_rows = await beneficiary_repo.search_by_name(
                    str(user_id),
                    recipient_name_text,
                    beneficiary_type="transfer",
                )
                reload_mode = "targeted"

            if not fetched_rows:
                fetched_rows = await beneficiary_repo.get_by_user(str(user_id), beneficiary_type="transfer")
                if reload_mode == "targeted":
                    reload_mode = "targeted_fallback_full"
                elif beneficiary_context_mode == "cache_only" and not has_recipient_hint:
                    reload_mode = "full_no_recipient_hint"
                else:
                    reload_mode = "full"

            beneficiaries = _normalize_beneficiary_rows(fetched_rows if isinstance(fetched_rows, list) else [])
            set_loaded_context_value(ctx.state, "beneficiaries", beneficiaries)
            logger.info(
                "transfer_beneficiaries_reloaded_for_resolution",
                user_id=str(user_id),
                fetched_count=len(beneficiaries),
                reload_mode=reload_mode,
            )
        except Exception as e:
            logger.warning(
                "transfer_beneficiary_reload_failed",
                user_id=str(user_id),
                error=str(e),
            )

    resolved_referents = build_resolved_referents(ctx.state, user_msg)
    context_data = {
        "phone_number": turn.phone_number,
        "channel": turn.channel,
        "channel_identity": turn.channel_identity,
        "user_id": context.user_id,
        "accounts": context.transaction_accounts_or_accounts,
        "all_accounts": context.accounts,
        "beneficiaries": beneficiaries,
        "referent_memory": surface.referent_memory_payload(),
        "resolved_referents": resolved_referents,
        "language": _state_locale(ctx.state),
        "required_fields": required_fields,
        "previous_response": previous_response,
        "confirmation_task_count": confirmation_task_count,
        "progress_tracker": ctx.dependencies.progress_tracker,
    }
    _stamp_async_group_metadata(task, ctx)
    if task.payload.get("source_affinity_mode") is None:
        remove_task_payload_values(task, "source_affinity_mode")

    logger.info("transfer_worker_start", payload=task.payload, task_id=task_id)
    result = cast(
        TransactionResult,
        await worker.run(
            payload=task.payload,
            context=context_data,
            user_message=user_msg,
            pin_verified=turn.pin_verified,
        ),
    )
    logger.info(
        "transfer_worker_returned",
        outcome=result.outcome,
        has_receipt=bool(result.receipt),
        result_obj=str(result),
        task_id=task_id,
        patch_skip_ext=result.patch.get("skip_extraction") if result.patch else None,
    )

    _apply_result_patch(task, result)
    if isinstance(result.patch, dict):
        schedule_items = result.patch.get("schedule_context_items")
        if isinstance(schedule_items, list):
            push_schedule_list_frame(ctx, [item for item in schedule_items if isinstance(item, dict)])
    if result.response:
        ctx.accumulator.say(result.response)

    if result.outcome == TransactionOutcome.OK and result.receipt:
        logger.info("transfer_worker_ok_branch", has_receipt=bool(result.receipt))

    _handle_transaction_outcome(
        task,
        task_id,
        result,
        ctx.accumulator,
        confirmation_gate="snapshot",
        default_error=None,
    )

    if result.outcome in (
        TransactionOutcome.NEEDS_INPUT,
        TransactionOutcome.NEEDS_AUTH,
        TransactionOutcome.NEEDS_CONFIRMATION,
    ):
        state_map: dict[TransactionOutcome, SessionState] = {
            TransactionOutcome.NEEDS_INPUT: "WAITING_FOR_INPUT",
            TransactionOutcome.NEEDS_AUTH: "WAITING_FOR_AUTH",
            TransactionOutcome.NEEDS_CONFIRMATION: "WAITING_FOR_INPUT",
        }
        current_state: SessionState = state_map[result.outcome]

        upsert_active_session(
            ctx,
            domain="transfer",
            state=current_state,
            interrupt_policy="BLOCK" if result.outcome == TransactionOutcome.NEEDS_AUTH else "CONFIRM",
            task_id=task_id,
        )

    elif result.outcome in (TransactionOutcome.OK, TransactionOutcome.FAILED) and result.is_terminal:
        pop_active_session(ctx, domain="transfer")


async def _execute_schedule_task(task: TaskSpec, task_id: str, ctx: ExecutionTurnContext) -> None:
    """Run scheduled transaction management through the transfer scheduler worker.

    Schedule management is planner-owned and can be read-only. It must not pass
    through the transfer mandate gate just because the implementation currently
    lives on the transfer worker.
    """

    worker = _get_worker(
        ctx.services,
        "transfer",
        task,
        log_key="schedule_worker_missing",
        error_message=render_message("orchestrator.error.transfer_worker_unavailable", _state_locale(ctx.state)),
    )
    if not worker:
        return

    context = loaded_context(ctx.state)
    turn = turn_metadata(ctx.state)
    context_data = {
        "phone_number": turn.phone_number,
        "channel": turn.channel,
        "channel_identity": turn.channel_identity,
        "user_id": context.user_id,
        "accounts": context.accounts,
        "all_accounts": context.accounts,
        "beneficiaries": context.beneficiaries,
        "language": _state_locale(ctx.state),
        "required_fields": [],
        "previous_response": None,
        "confirmation_task_count": None,
        "progress_tracker": ctx.dependencies.progress_tracker,
    }
    user_msg = _maybe_user_message(task, ctx.state)
    logger.info("schedule_worker_start", payload=task.payload, task_id=task_id)
    result = cast(
        TransactionResult,
        await worker.run(
            payload=task.payload,
            context=context_data,
            user_message=user_msg,
            pin_verified=turn.pin_verified,
        ),
    )
    logger.info("schedule_worker_returned", outcome=result.outcome, task_id=task_id)

    _apply_result_patch(task, result)
    if isinstance(result.patch, dict):
        schedule_items = result.patch.get("schedule_context_items")
        if isinstance(schedule_items, list):
            push_schedule_list_frame(ctx, [item for item in schedule_items if isinstance(item, dict)])
    if result.response:
        ctx.accumulator.say(result.response)

    _handle_transaction_outcome(
        task,
        task_id,
        result,
        ctx.accumulator,
        confirmation_gate="snapshot",
        default_error=None,
    )


__all__ = ["ScheduleTaskExecutor", "TransferTaskExecutor"]
