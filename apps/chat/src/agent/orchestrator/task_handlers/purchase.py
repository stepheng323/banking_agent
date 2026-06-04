from typing import Literal, cast

from apps.chat.src.agent.orchestrator.context.referents.resolution import build_resolved_referents
from apps.chat.src.agent.orchestrator.models.domain import TaskSpec
from apps.chat.src.agent.orchestrator.task_handlers.context_frames import push_data_plan_frames_from_result
from apps.chat.src.agent.orchestrator.workflows.execution.runtime import (
    ExecutionTurnContext,
    _apply_result_patch,
    _get_worker,
    _handle_transaction_outcome,
    _maybe_user_message,
    _stamp_async_group_metadata,
    _state_locale,
)
from banking.presentation.i18n.renderer import render_message
from banking.runtime.results import TransactionOutcome, TransactionResult

PurchaseWorkerName = Literal["airtime", "data"]


async def handle_airtime_task(task: TaskSpec, task_id: str, ctx: ExecutionTurnContext) -> None:
    locale = _state_locale(ctx.state)
    await _handle_purchase_task(
        task,
        task_id,
        ctx,
        worker_name="airtime",
        worker_missing_log_key="airtime_worker_missing",
        worker_missing_error_message=render_message("orchestrator.error.airtime_worker_unavailable", locale),
        default_error=render_message("orchestrator.error.airtime_purchase_failed", locale),
        include_channel=True,
    )


async def _handle_purchase_task(
    task: TaskSpec,
    task_id: str,
    ctx: ExecutionTurnContext,
    *,
    worker_name: PurchaseWorkerName,
    worker_missing_log_key: str,
    worker_missing_error_message: str,
    default_error: str,
    include_channel: bool,
) -> None:
    worker = _get_worker(
        ctx.services,
        worker_name,
        task,
        log_key=worker_missing_log_key,
        error_message=worker_missing_error_message,
    )
    if not worker:
        return

    user_msg = _maybe_user_message(task, ctx.state)
    required_fields: list[str] = []
    previous_response: str | None = None
    if ctx.state.last_interrupt and task_id in ctx.state.last_interrupt.task_ids:
        raw_required_fields = ctx.state.last_interrupt.fields_by_task.get(task_id, [])
        required_fields = [field for field in raw_required_fields if isinstance(field, str)]
        previous_response = ctx.state.last_interrupt.prompt

    context_data = {
        "phone_number": ctx.state.phone_number,
        "user_id": ctx.state.loaded_context.get("user_id"),
        "accounts": ctx.state.loaded_context.get("transaction_accounts", ctx.state.loaded_context.get("accounts", [])),
        "all_accounts": ctx.state.loaded_context.get("accounts", []),
        "beneficiaries": ctx.state.loaded_context.get("beneficiaries", []),
        "referent_memory": ctx.state.referent_memory.model_dump(mode="json"),
        "resolved_referents": build_resolved_referents(ctx.state, user_msg),
        "language": _state_locale(ctx.state),
        "required_fields": required_fields,
        "previous_response": previous_response,
    }
    if include_channel:
        context_data["channel"] = ctx.state.channel
        context_data["channel_identity"] = ctx.state.channel_identity
    _stamp_async_group_metadata(task, ctx)

    result = cast(
        TransactionResult,
        await worker.run(
            payload=task.payload,
            context=context_data,
            user_message=user_msg,
            pin_verified=ctx.state.pin_verified,
        ),
    )

    _apply_result_patch(task, result)

    if worker_name == "data" and str(task.payload.get("action") or "") == "data_plan_query":
        task.payload["skip_finalize_summary"] = True
        if result.outcome == TransactionOutcome.OK and result.response:
            ctx.accumulator.say(result.response)

    if worker_name == "data":
        push_data_plan_frames_from_result(task, result, ctx)

    _handle_transaction_outcome(
        task,
        task_id,
        result,
        ctx.accumulator,
        confirmation_gate="summary",
        default_error=default_error,
    )


async def handle_data_task(task: TaskSpec, task_id: str, ctx: ExecutionTurnContext) -> None:
    locale = _state_locale(ctx.state)
    await _handle_purchase_task(
        task,
        task_id,
        ctx,
        worker_name="data",
        worker_missing_log_key="data_worker_missing",
        worker_missing_error_message=render_message("orchestrator.error.data_worker_unavailable", locale),
        default_error=render_message("orchestrator.error.data_purchase_failed", locale),
        include_channel=True,
    )
