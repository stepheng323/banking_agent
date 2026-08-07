from typing import Literal, cast

from apps.chat.src.agent.orchestrator.context.referents.resolution import build_resolved_referents
from apps.chat.src.agent.orchestrator.models.domain import TaskSpec
from apps.chat.src.agent.orchestrator.workflows.execution.async_grouping import _stamp_async_group_metadata
from apps.chat.src.agent.orchestrator.workflows.execution.context import ExecutionTurnContext
from apps.chat.src.agent.orchestrator.workflows.execution.context_frames import push_data_plan_frames_from_result
from apps.chat.src.agent.orchestrator.workflows.execution.context_surface import context_surface
from apps.chat.src.agent.orchestrator.workflows.execution.last_interrupt import last_interrupt
from apps.chat.src.agent.orchestrator.workflows.execution.loaded_context import loaded_context
from apps.chat.src.agent.orchestrator.workflows.execution.locale import _state_locale
from apps.chat.src.agent.orchestrator.workflows.execution.progress import enter_task_progress
from apps.chat.src.agent.orchestrator.workflows.execution.result_reducer import (
    _apply_result_patch,
    _handle_transaction_outcome,
)
from apps.chat.src.agent.orchestrator.workflows.execution.task_input import _maybe_user_message
from apps.chat.src.agent.orchestrator.workflows.execution.task_mutations import set_task_payload_value
from apps.chat.src.agent.orchestrator.workflows.execution.turn_metadata import turn_metadata
from apps.chat.src.agent.orchestrator.workflows.execution.worker_lookup import _get_worker
from banking.presentation.i18n.renderer import render_message
from banking.runtime.results import TransactionOutcome, TransactionResult
from shared.utils.logging import get_logger

PurchaseWorkerName = Literal["airtime", "data"]
logger = get_logger(__name__)


class AirtimeTaskExecutor:
    async def execute(self, task: TaskSpec, task_id: str, ctx: ExecutionTurnContext) -> None:
        await _execute_airtime_task(task, task_id, ctx)


class DataTaskExecutor:
    async def execute(self, task: TaskSpec, task_id: str, ctx: ExecutionTurnContext) -> None:
        await _execute_data_task(task, task_id, ctx)


async def _execute_airtime_task(task: TaskSpec, task_id: str, ctx: ExecutionTurnContext) -> None:
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
    interrupt = last_interrupt(ctx.state)
    if interrupt.includes_task(task_id):
        required_fields = interrupt.fields_for_task(task_id)
        previous_response = interrupt.prompt

    context = loaded_context(ctx.state)
    surface = context_surface(ctx.state)
    turn = turn_metadata(ctx.state)
    context_data = {
        "phone_number": turn.phone_number,
        "user_id": context.user_id,
        "accounts": context.transaction_accounts_or_accounts,
        "all_accounts": context.accounts,
        "beneficiaries": context.beneficiaries,
        "referent_memory": surface.referent_memory_payload(),
        "resolved_referents": build_resolved_referents(ctx.state, user_msg),
        "language": _state_locale(ctx.state),
        "required_fields": required_fields,
        "previous_response": previous_response,
        "authorization_context": turn.authorization_context_payload,
        "stashed_sessions": turn.stashed_sessions,
        "progress_tracker": ctx.dependencies.progress_tracker,
    }
    if include_channel:
        context_data["channel"] = turn.channel
        context_data["channel_identity"] = turn.channel_identity
    _stamp_async_group_metadata(task, ctx)
    await enter_task_progress(ctx, task)

    try:
        result = cast(
            TransactionResult,
            await worker.run(
                payload=task.payload,
                context=context_data,
                user_message=user_msg,
                pin_verified=turn.pin_verified,
            ),
        )
    except Exception as exc:
        logger.error(
            "purchase_worker_execution_exception",
            task_id=task_id,
            worker=worker_name,
            error_type=type(exc).__name__,
            exc_info=True,
        )
        result = TransactionResult(
            outcome=TransactionOutcome.FAILED,
            error=default_error,
            retryable=True,
            patch={},
        )

    _apply_result_patch(task, result)

    if worker_name == "data" and str(task.payload.get("action") or "") == "data_plan_query":
        set_task_payload_value(task, "skip_finalize_summary", True)
        is_part_of_batch = task.payload.get("async_group_size", 0) > 1
        if result.outcome == TransactionOutcome.OK and result.response and not is_part_of_batch:
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


async def _execute_data_task(task: TaskSpec, task_id: str, ctx: ExecutionTurnContext) -> None:
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


__all__ = ["AirtimeTaskExecutor", "DataTaskExecutor"]
