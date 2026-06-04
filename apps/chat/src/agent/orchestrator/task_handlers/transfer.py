from typing import Any, Literal, cast

from apps.chat.src.agent.orchestrator.context.referents.resolution import build_resolved_referents
from apps.chat.src.agent.orchestrator.models.domain import ActiveSession, TaskSpec
from apps.chat.src.agent.orchestrator.task_handlers.context_frames import push_schedule_list_frame
from apps.chat.src.agent.orchestrator.workflows.execution.async_grouping import _stamp_async_group_metadata
from apps.chat.src.agent.orchestrator.workflows.execution.beneficiary_resolution import (
    _beneficiary_cache_contains_recipient,
    _normalize_beneficiary_rows,
    _recipient_supports_targeted_beneficiary_lookup,
)
from apps.chat.src.agent.orchestrator.workflows.execution.context import ExecutionTurnContext
from apps.chat.src.agent.orchestrator.workflows.execution.locale import _state_locale
from apps.chat.src.agent.orchestrator.workflows.execution.result_reducer import (
    _apply_result_patch,
    _handle_transaction_outcome,
)
from apps.chat.src.agent.orchestrator.workflows.execution.task_input import _maybe_user_message
from apps.chat.src.agent.orchestrator.workflows.execution.worker_lookup import _get_worker
from banking.presentation.i18n.renderer import render_message
from banking.runtime.results import TransactionOutcome, TransactionResult
from shared.utils.logging import get_logger

logger = get_logger(__name__)

SessionState = Literal["WAITING_FOR_INPUT", "WAITING_FOR_AUTH", "RUNNING"]


async def handle_transfer_task(task: TaskSpec, task_id: str, ctx: ExecutionTurnContext) -> None:
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
    if ctx.state.last_interrupt and task_id in ctx.state.last_interrupt.task_ids:
        raw_required_fields = ctx.state.last_interrupt.fields_by_task.get(task_id, [])
        required_fields = [field for field in raw_required_fields if isinstance(field, str)]
        previous_response = ctx.state.last_interrupt.prompt
        if ctx.state.last_interrupt.kind == "confirmation":
            confirmation_task_count = len(ctx.state.last_interrupt.task_ids)

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

    beneficiaries = ctx.state.loaded_context.get("beneficiaries", [])
    if not isinstance(beneficiaries, list):
        beneficiaries = []

    recipient_name = task.payload.get("recipient_name")
    has_recipient_hint = isinstance(recipient_name, str) and bool(recipient_name.strip())
    recipient_name_text = recipient_name.strip() if isinstance(recipient_name, str) else ""
    beneficiary_repo = ctx.dependencies.beneficiary_repo
    user_id = ctx.state.loaded_context.get("user_id")
    beneficiary_context_mode = str(ctx.state.loaded_context.get("beneficiary_context_mode") or "full")
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
            if isinstance(ctx.state.loaded_context, dict):
                ctx.state.loaded_context["beneficiaries"] = beneficiaries
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
        "phone_number": ctx.state.phone_number,
        "channel": ctx.state.channel,
        "channel_identity": ctx.state.channel_identity,
        "user_id": ctx.state.loaded_context.get("user_id"),
        "accounts": ctx.state.loaded_context.get("transaction_accounts", ctx.state.loaded_context.get("accounts", [])),
        "all_accounts": ctx.state.loaded_context.get("accounts", []),
        "beneficiaries": beneficiaries,
        "referent_memory": ctx.state.referent_memory.model_dump(mode="json"),
        "resolved_referents": resolved_referents,
        "language": _state_locale(ctx.state),
        "required_fields": required_fields,
        "previous_response": previous_response,
        "confirmation_task_count": confirmation_task_count,
        "progress_tracker": ctx.dependencies.progress_tracker,
    }
    _stamp_async_group_metadata(task, ctx)
    if task.payload.get("source_affinity_mode") is None:
        task.payload.pop("source_affinity_mode", None)

    logger.info("transfer_worker_start", payload=task.payload, task_id=task_id)
    result = cast(
        TransactionResult,
        await worker.run(
            payload=task.payload,
            context=context_data,
            user_message=user_msg,
            pin_verified=ctx.state.pin_verified,
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

    stack = list(ctx.state.session_stack)
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

        if stack and stack[-1].domain == "transfer":
            stack[-1].state = current_state
        else:
            stack.append(
                ActiveSession(
                    domain="transfer",
                    state=current_state,
                    interrupt_policy="BLOCK" if result.outcome == TransactionOutcome.NEEDS_AUTH else "CONFIRM",
                    resume_hint={"task_id": task_id},
                )
            )
        ctx.accumulator.set_update("session_stack", stack)

    elif result.outcome in (TransactionOutcome.OK, TransactionOutcome.FAILED) and result.is_terminal:
        if stack and stack[-1].domain == "transfer":
            stack.pop()
            ctx.accumulator.set_update("session_stack", stack)


async def handle_schedule_task(task: TaskSpec, task_id: str, ctx: ExecutionTurnContext) -> None:
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

    context_data = {
        "phone_number": ctx.state.phone_number,
        "channel": ctx.state.channel,
        "channel_identity": ctx.state.channel_identity,
        "user_id": ctx.state.loaded_context.get("user_id"),
        "accounts": ctx.state.loaded_context.get("accounts", []),
        "all_accounts": ctx.state.loaded_context.get("accounts", []),
        "beneficiaries": ctx.state.loaded_context.get("beneficiaries", []),
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
            pin_verified=ctx.state.pin_verified,
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
